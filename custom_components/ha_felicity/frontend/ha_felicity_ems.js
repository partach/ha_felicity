import { LitElement, html, css } from "https://unpkg.com/lit?module";

class FelicityEMSCard extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      config: { type: Object },
      _deviceEntities: { type: Array },
      _simOverrides: { type: Object },  // local slider overrides for live preview
      _simResult: { type: Object },     // latest simulation output
      _viewTomorrow: { type: Boolean }, // manual today/tomorrow toggle (null = auto)
      _pastSlotActions: { type: Object }, // past slot actions from HA history
      _slotOverrides: { type: Object }, // manual slot overrides: { today: {idx: action}, tomorrow: {idx: action} }
      _pendingClick: { type: Object },  // first click for two-click override selection
      _showAdvanced: { type: Boolean }, // toggle for advanced controls
    };
  }

  constructor() {
    super();
    this._deviceEntities = [];
    this._simOverrides = {};
    this._simResult = null;
    this._viewTomorrow = null;  // null = auto, true = tomorrow, false = today
    this._showingTomorrow = false; // tracks what's actually displayed
    this._hasTomorrowData = false; // tracks if tomorrow price data is available
    this._pastSlotActions = {};    // slot index → "charging"/"discharging" from HA history
    this._lastHistoryFetch = 0;   // timestamp of last history fetch
    this._slotOverrides = { today: {}, tomorrow: {} };  // manual slot overrides
    this._pendingClick = null;     // { slotIdx, action, day } — first click of two-click selection
    this._showAdvanced = false;     // advanced controls hidden by default
  }

  static getConfigElement() {
    return document.createElement("felicity-ems-card-editor");
  }

  setConfig(config) {
    this.config = {
      currency: "\u20AC",
      generator_as_pv: true,
      ...config,
    };
    this.requestUpdate();
  }

  updated(changedProps) {
    super.updated(changedProps);
    if (changedProps.has("hass")) {
      this._resolveDeviceEntities();
      this._fetchEnergyHistory();
      this._loadSlotOverridesFromBackend();
      this._drawSlotTimeline();
      this._attachCanvasClickHandler();
    }
  }

  _loadSlotOverridesFromBackend() {
    // Always sync overrides from the backend so they survive page reloads
    // and are visible across multiple browser tabs/sessions.
    const backendOverrides = this._getAttr("schedule_status", "slot_overrides");
    if (backendOverrides && typeof backendOverrides === "object") {
      const newToday = backendOverrides.today || {};
      const newTomorrow = backendOverrides.tomorrow || {};
      const backendJson = JSON.stringify({ today: newToday, tomorrow: newTomorrow });
      const localJson = JSON.stringify(this._slotOverrides);
      if (backendJson !== localJson) {
        this._slotOverrides = { today: newToday, tomorrow: newTomorrow };
      }
    }
  }

  _resolveDeviceEntities() {
    if (!this.hass || !this.config.device_id) return;
    const entityReg = this.hass.entities;
    if (!entityReg) return;
    this._deviceEntities = Object.values(entityReg)
      .filter((e) => e.device_id === this.config.device_id)
      .map((e) => e.entity_id)
      .sort();
  }

  async _fetchEnergyHistory() {
    // Throttle: fetch at most once per 60 seconds
    const now = Date.now();
    if (now - this._lastHistoryFetch < 60000) return;
    this._lastHistoryFetch = now;

    const entityId = this._getEntityId("energy_state");
    if (!entityId || !this.hass) return;

    const granularity = this._getAttr("schedule_status", "slot_granularity_min") || 60;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const startTime = today.toISOString();

    try {
      const history = await this.hass.callApi(
        "GET",
        `history/period/${startTime}?filter_entity_id=${entityId}&minimal_response&no_attributes`
      );
      if (!history?.[0]?.length) return;

      const states = history[0];
      const slotActions = {};
      const nowDate = new Date();
      const currentSlot = Math.floor((nowDate.getHours() * 60 + nowDate.getMinutes()) / granularity);

      // For each past slot, find the dominant state
      for (let slot = 0; slot < currentSlot; slot++) {
        const slotStartMin = slot * granularity;
        const slotEndMin = slotStartMin + granularity;
        const slotStart = new Date(today.getTime() + slotStartMin * 60000);
        const slotEnd = new Date(today.getTime() + slotEndMin * 60000);

        // Find state changes that overlap this slot
        let chargingMs = 0, dischargingMs = 0;
        for (let j = 0; j < states.length; j++) {
          const stateStart = new Date(states[j].last_changed);
          const stateEnd = j + 1 < states.length ? new Date(states[j + 1].last_changed) : nowDate;
          const overlapStart = Math.max(stateStart.getTime(), slotStart.getTime());
          const overlapEnd = Math.min(stateEnd.getTime(), slotEnd.getTime());
          if (overlapStart >= overlapEnd) continue;
          const duration = overlapEnd - overlapStart;
          const state = states[j].state;
          if (state === "charging") chargingMs += duration;
          else if (state === "discharging") dischargingMs += duration;
        }

        // Only mark if meaningful activity (>10% of slot duration)
        const threshold = granularity * 60000 * 0.1;
        if (chargingMs > threshold) slotActions[slot] = "charging";
        else if (dischargingMs > threshold) slotActions[slot] = "discharging";
      }

      this._pastSlotActions = slotActions;
    } catch (e) {
      // History API not available or failed — no past coloring
    }
  }

  _getEntityId(key) {
    if (!this._deviceEntities?.length) return null;
    // Exact suffix match (most common)
    const exact = this._deviceEntities.find((eid) => eid.endsWith(`_${key}`));
    if (exact) return exact;
    // Fallback: key parts appear in order in entity ID (handles name-based IDs
    // where extra words like "inquiry" are inserted, e.g. key "pv_generated_energy_day"
    // matches "sensor.xxx_pv_generated_energy_inquiry_day")
    const re = new RegExp(key.split("_").join("_(?:\\w+_)*"));
    return this._deviceEntities.find((eid) => re.test(eid));
  }

  _getState(key) {
    const eid = this._getEntityId(key);
    if (!eid) return null;
    const entity = this.hass.states[eid];
    if (!entity || entity.state === "unknown" || entity.state === "unavailable") return null;
    return entity.state;
  }

  _getNumericState(key) {
    const val = this._getState(key);
    if (val == null) return null;
    const num = parseFloat(val);
    return isNaN(num) ? null : num;
  }

  _getAttr(key, attr) {
    const eid = this._getEntityId(key);
    if (!eid) return undefined;
    const entity = this.hass.states[eid];
    if (!entity) return undefined;
    return entity.attributes?.[attr];
  }

  // Helper: safely format a number, returns fallback string if value is null/undefined
  _fmt(val, decimals, fallback = "\u2014") {
    if (val == null || (typeof val === "number" && isNaN(val))) return fallback;
    return Number(val).toFixed(decimals);
  }

  // Mirror of ems.calculate_self_consumption_reserve(). Client-side fallback
  // for the "overnight need" stat when the backend hasn't computed
  // self_consumption_reserve (e.g. price_mode manual).
  _overnightReserveKwh(consumptionEst, pvHourly) {
    const consumptionPerHour = (consumptionEst || 0) / 24.0;
    let sunsetHour = 19;
    let sunriseHour = 7;
    if (pvHourly) {
      const pvHours = Object.entries(pvHourly)
        .filter(([, kwh]) => parseFloat(kwh) > 0.1)
        .map(([h]) => parseInt(h, 10))
        .filter((h) => !isNaN(h));
      if (pvHours.length) {
        sunsetHour = Math.max(...pvHours) + 1;
        sunriseHour = Math.min(...pvHours);
      }
    }
    const overnightHours = (24 - sunsetHour) + sunriseHour;
    return consumptionPerHour * overnightHours;
  }

  // Projected overnight-minimum SOC (%) — the lowest point of the predicted
  // SOC trajectory over the relevant horizon.  For the Today view that's from
  // "now" to end of day PLUS tomorrow's early-morning slots (the true
  // overnight low usually sits just before tomorrow's sunrise).  For the
  // Tomorrow view it's the minimum across tomorrow's trajectory.
  _projectedOvernightMinPct(socTraj, currentSlot, numSlots, showTomorrow) {
    if (!Array.isArray(socTraj) || socTraj.length < 2) return null;
    const vals = [];
    const pushVals = (arr, from, to) => {
      for (let i = from; i < to && i < arr.length; i++) {
        const v = parseFloat(arr[i]);
        if (!isNaN(v)) vals.push(v);
      }
    };
    if (showTomorrow) {
      pushVals(socTraj, 0, socTraj.length);
    } else {
      pushVals(socTraj, Math.max(0, currentSlot), socTraj.length);
      // Extend into tomorrow morning so the cross-midnight low is captured.
      const simParams = this._getAttr("schedule_status", "sim_params") || {};
      const tmr = simParams.backend_soc_trajectory_tomorrow;
      if (Array.isArray(tmr) && tmr.length) {
        const cut = Math.ceil((8 / 24) * tmr.length); // first ~8h (to ~sunrise)
        pushVals(tmr, 0, cut);
      }
    }
    if (!vals.length) return null;
    return Math.min(...vals);
  }

  // ── Client-side schedule simulation ──────────────────────────
  // Mirrors coordinator._calculate_schedule logic for live preview

  _simulateSchedule(slotData, overrides = {}) {
    if (!slotData || !slotData.length) return null;

    const sim = this._getAttr("schedule_status", "sim_params") || {};
    const gridMode = overrides.gridMode ?? this._getState("grid_mode") ?? "off";
    const safeMaxPower = this._getNumericState("safe_max_power") || 5000;

    // Use override power if slider is being dragged, else safe_max_power
    const powerKw = overrides.powerKw ?? Math.max(1, safeMaxPower / 1000);

    const batteryCapacity = sim.battery_capacity_kwh || 10;
    const chargeMax = (overrides.chargeMax ?? sim.battery_charge_max_pct ?? 100) / 100;
    const dischargeMin = (overrides.dischargeMin ?? sim.battery_discharge_min_pct ?? 20) / 100;
    const reserveTargetPct = sim.reserve_target_pct || 0;
    const efficiency = sim.efficiency || 0.90;
    const batterySoc = sim.battery_soc_pct;
    const netPv = sim.net_pv_kwh || 0;
    const consumption = sim.consumption_est_kwh || 10;
    const inverterMaxKw = sim.inverter_max_power_kw || 10;
    const yesterdayDeficit = this._getAttr("schedule_status", "yesterday_deficit_kwh") || 0;

    // Mirrors ems._compute_reserve_target: a fixed reserve (reserveTargetPct
    // > 0) can only RAISE the target above the dynamic overnight-survival
    // reserve, never lower it below it.  (The 1.25× self_consumption boost on
    // the dynamic value is a backend-only refinement, not mirrored here.)
    const computeReserveTarget = (minKwh, reserveKwh) => {
      const dynamic = minKwh + reserveKwh;
      if (reserveTargetPct > 0) {
        const fixedFloor = (reserveTargetPct / 100) * batteryCapacity;
        return Math.min(batteryCapacity, Math.max(fixedFloor, dynamic));
      }
      return Math.min(batteryCapacity, dynamic);
    };

    const numSlots = slotData.length;
    const granularity = this._getAttr("schedule_status", "slot_granularity_min") || Math.round((24 * 60) / numSlots);
    const slotDuration = granularity / 60;

    const now = new Date();
    const currentSlotIdx = Math.floor((now.getHours() * 60 + now.getMinutes()) / granularity);

    // Remaining future slots with prices
    const remaining = slotData
      .filter((s, i) => i >= currentSlotIdx && s.price != null)
      .map(s => ({ idx: s.slot, price: s.price }));

    if (gridMode === "off" || !remaining.length) {
      return { slots: this._markAll(slotData, null), chargeCount: 0, dischargeCount: 0, planned: 0, threshold: null };
    }

    const currentKwh = batterySoc != null ? (batterySoc / 100) * batteryCapacity : 0;
    const energyPerSlot = powerKw * slotDuration;
    const effectivePerSlot = energyPerSlot * efficiency;

    // Arbitrage: when today's remaining price spread >= arbitrage_price_delta,
    // charge to full capacity so the extra energy can be sold at peak.
    // Mirrors ems._schedule_both lines 976-1003.
    const arbitrageDelta = sim.arbitrage_price_delta || 0;
    const maxBatteryKwh = chargeMax * batteryCapacity;
    let arbitrageActive = false;
    if (arbitrageDelta > 0 && remaining.length) {
      const prices = remaining.map(s => s.price);
      const spread = Math.max(...prices) - Math.min(...prices);
      if (spread >= arbitrageDelta) {
        arbitrageActive = true;
      }
    }

    // Calculate threshold from price level override
    let threshold = null;
    if (overrides.priceLevel != null) {
      const prices = slotData.map(s => s.price).filter(p => p != null);
      const minP = Math.min(...prices);
      const maxP = Math.max(...prices);
      const avgP = prices.reduce((a, b) => a + b, 0) / prices.length;
      const level = overrides.priceLevel;
      if (level <= 5) {
        threshold = minP + ((avgP - minP) * (level - 1) / 4);
      } else {
        threshold = avgP + ((maxP - avgP) * (level - 5) / 5);
      }
    }

    const result = { slots: slotData.map(s => ({ ...s, action: null })), chargeCount: 0, dischargeCount: 0, planned: 0, plannedChargeKwh: 0, plannedDischargeKwh: 0, tomorrowChargeCount: 0, tomorrowPlanned: 0, threshold };

    if (gridMode === "from_grid" || gridMode === "both") {
      // Solar-first: target = min SOC floor + overnight reserve (dynamic)
      // or fixed percentage floor when reserveTargetPct > 0. Matches ems.py.
      const minKwh = dischargeMin * batteryCapacity;
      const reserveKwh = parseFloat(this._getAttr("schedule_status", "self_consumption_reserve")) || 0;
      const reserveTarget = computeReserveTarget(minKwh, reserveKwh);
      const shortfall = Math.max(0, reserveTarget - currentKwh);
      let deficit = Math.max(0, shortfall - netPv);
      if (yesterdayDeficit > 0 && shortfall > deficit) {
        deficit += Math.min(yesterdayDeficit, shortfall - deficit);
      }
      // Arbitrage: charge to full capacity when spread is profitable.
      if (arbitrageActive) {
        const fullCharge = Math.max(0, maxBatteryKwh - currentKwh);
        deficit = Math.max(deficit, Math.max(0, fullCharge - netPv));
      }

      // Unified slot selection: combine today + tomorrow into one pool
      // and pick the cheapest slots from both days together.
      let tomorrowDeficit = 0;
      const tomorrowSlotData = this._tomorrowSlotData;
      // Only real tomorrow PRICE data forms a two-day pool.  The PV-only
      // preview synthesizes slots with price=null — those must not inflate
      // today's deficit (the backend computes tomorrow_deficit only when a
      // real tomorrow price pool exists).
      const hasTomorrow = tomorrowSlotData && tomorrowSlotData.length > 0
        && tomorrowSlotData.some(s => s.price != null);
      if (hasTomorrow) {
        const consumption = sim.consumption_est_kwh || 10;
        const pvTmr = this._getNumericState("pv_forecast_tomorrow") || 0;
        const tmrReserve = reserveKwh;

        // Estimate battery at midnight based on actual state
        const hoursToMidnight = Math.max(1, 24 - now.getHours());
        const drainToMidnight = (consumption / 24) * hoursToMidnight;
        const projectedMidnight = Math.max(minKwh, Math.min(
          batteryCapacity,
          currentKwh + netPv + deficit - drainToMidnight
        ));

        const tmrReserveTarget = computeReserveTarget(minKwh, tmrReserve);
        // Daytime gap: on low-PV days battery drains during the day too
        const daytimeGap = Math.max(0, consumption - pvTmr);
        // PV surplus beyond consumption charges the battery during the day,
        // reducing overnight grid need (mirrors backend).
        const tmrPvSurplus = Math.max(0, pvTmr - consumption);
        // grid_charge >= reserve_target + daytime_gap - projected_midnight - pv_surplus
        const tmrShortfall = Math.max(
          0, tmrReserveTarget + daytimeGap - projectedMidnight - tmrPvSurplus);
        tomorrowDeficit = tmrShortfall;
      }
      const totalDeficit = deficit + tomorrowDeficit;

      if (totalDeficit > 0) {
        // Build combined pool: today remaining + tomorrow all
        const todayPool = remaining.map(s => ({ price: s.price, day: 0, idx: s.idx }));
        let tomorrowPool = [];
        if (hasTomorrow) {
          tomorrowPool = tomorrowSlotData
            .filter(s => s.price != null)
            .map(s => ({ price: s.price, day: 1, idx: s.slot }));
        }
        const combined = [...todayPool, ...tomorrowPool];
        const neg = combined.filter(s => s.price < 0);
        const nonNeg = combined.filter(s => s.price >= 0).sort((a, b) => a.price - b.price);
        const negEnergy = neg.length * effectivePerSlot;
        const remDeficit = Math.max(0, totalDeficit - negEnergy);
        const needed = effectivePerSlot > 0 ? Math.ceil(remDeficit / effectivePerSlot) : 0;
        const allSelected = [...neg, ...nonNeg.slice(0, needed)];

        // Split into today and tomorrow
        let todayCharge = allSelected.filter(s => s.day === 0);
        let tomorrowCharge = allSelected.filter(s => s.day === 1);

        // Battery headroom cap: today can only charge what the battery can absorb
        const pvFill = Math.max(0, netPv);
        const headroom = Math.max(0, chargeMax * batteryCapacity - currentKwh - pvFill);
        let maxTodaySlots = effectivePerSlot > 0 ? Math.floor(headroom / effectivePerSlot) : 0;
        const negTodayCount = todayCharge.filter(s => s.price < 0).length;
        if (pvFill <= 0) {
          maxTodaySlots = Math.max(maxTodaySlots,
            effectivePerSlot > 0 && deficit > 0 ? Math.ceil(deficit / effectivePerSlot) : 0,
            negTodayCount
          );
        } else {
          maxTodaySlots = Math.max(maxTodaySlots, negTodayCount);
        }
        if (todayCharge.length > maxTodaySlots) {
          todayCharge.sort((a, b) => a.price - b.price);  // cheapest first
          const excess = todayCharge.slice(maxTodaySlots);
          todayCharge = todayCharge.slice(0, maxTodaySlots);
          // Replace with next cheapest tomorrow slots
          const tmrSelectedIdx = new Set(tomorrowCharge.map(s => s.idx));
          const availTmr = tomorrowPool
            .filter(s => s.price >= 0 && !tmrSelectedIdx.has(s.idx))
            .sort((a, b) => a.price - b.price);
          tomorrowCharge = [...tomorrowCharge, ...availTmr.slice(0, excess.length)];
        }

        // Safety: ensure battery survives until tomorrow's first charge slot
        if (tomorrowCharge.length > 0 && hasTomorrow) {
          const consumption = sim.consumption_est_kwh || 10;
          const tmrGranularity = Math.round((24 * 60) / tomorrowSlotData.length);
          const earliestTmrSlot = Math.min(...tomorrowCharge.map(s => s.idx));
          const earliestTmrHour = Math.floor((earliestTmrSlot * tmrGranularity) / 60);
          const hoursUntilTmrCharge = Math.max(1, (24 - now.getHours()) + earliestTmrHour);
          const bridgeConsumption = (consumption / 24) * hoursUntilTmrCharge;
          const todayChargeEnergy = todayCharge.length * effectivePerSlot;
          // Clamp: inverter stops providing house power at min SOC,
          // so battery cannot drain below minKwh from consumption alone.
          const projected = Math.max(minKwh, currentKwh + netPv + todayChargeEnergy - bridgeConsumption);

          if (projected < minKwh) {
            // Swap: replace most expensive tomorrow slots with cheapest available today slots
            const shortfallKwh = minKwh - projected;
            const extraNeeded = Math.ceil(shortfallKwh / effectivePerSlot);
            const todaySelectedIdx = new Set(todayCharge.map(s => s.idx));
            const availableToday = todayPool
              .filter(s => s.price >= 0 && !todaySelectedIdx.has(s.idx))
              .sort((a, b) => a.price - b.price);
            const tmrByPrice = [...tomorrowCharge].sort((a, b) => b.price - a.price);
            const swaps = Math.min(extraNeeded, availableToday.length, tmrByPrice.length);
            for (let j = 0; j < swaps; j++) {
              todayCharge.push(availableToday[j]);
              tomorrowCharge = tomorrowCharge.filter(s => s.idx !== tmrByPrice[j].idx);
            }
          }
        }

        for (const s of todayCharge) {
          result.slots[s.idx].action = "charge";
        }
        result.chargeCount = todayCharge.length;
        result.plannedChargeKwh += todayCharge.length * effectivePerSlot;
        result.planned += todayCharge.length * effectivePerSlot;
        result.tomorrowChargeCount = tomorrowCharge.length;
        result.tomorrowPlanned = tomorrowCharge.length * effectivePerSlot;
        result.tomorrowChargeIndices = new Set(tomorrowCharge.map(s => s.idx));

        if (todayCharge.length && threshold == null) {
          threshold = Math.max(...todayCharge.map(s => s.price));
        }
      }
    }

    if (gridMode === "to_grid" || gridMode === "both") {
      const minKwh = dischargeMin * batteryCapacity;
      const reserveKwh = parseFloat(this._getAttr("schedule_status", "self_consumption_reserve")) || 0;
      const reserveTarget = computeReserveTarget(minKwh, reserveKwh);
      // Include planned charge energy in peak estimate so the sell side
      // knows the battery will have surplus.  SOC validation prunes any
      // sells that would actually drain below reserve.
      const chargeEnergyPlanned = result.chargeCount * effectivePerSlot;
      const peakWithCharge = Math.min(maxBatteryKwh, currentKwh + chargeEnergyPlanned);
      const peakKwh = arbitrageActive ? maxBatteryKwh : peakWithCharge;
      const sellable = Math.max(0, peakKwh - reserveTarget) * efficiency * 0.85;
      const roundTrip = efficiency * efficiency;

      if (sellable > 0) {
        const chargeIdxSet = new Set(result.slots.filter(s => s.action === "charge").map(s => s.slot));
        let available = remaining.filter(s => s.price > 0 && !chargeIdxSet.has(s.idx));

        if (gridMode === "both") {
          let minSell = 0;
          let refBuy = null;
          if (result.chargeCount > 0) {
            refBuy = Math.max(...result.slots.filter(s => s.action === "charge").map(s => s.price));
            minSell = refBuy / roundTrip;
          } else if (remaining.length) {
            refBuy = Math.min(...remaining.map(s => s.price));
          }
          // Arbitrage delta sell gate (mirrors backend): each sell must beat
          // the buy reference by at least the user's delta.
          if (arbitrageDelta > 0 && refBuy != null) {
            minSell = Math.max(minSell, refBuy + arbitrageDelta);
          }
          if (minSell > 0) {
            available = available.filter(s => s.price >= minSell);
          }
        }

        available.sort((a, b) => b.price - a.price);
        const needed = energyPerSlot > 0 ? Math.ceil(sellable / energyPerSlot) : 0;
        let sellSlots = available.slice(0, needed);

        // Per-slot SOC validation: drop sells that push SOC below reserve
        {
          const consumptionProfile = sim.consumption_hourly_profile || null;
          const pvHourly = sim.pv_hourly_kwh || null;
          const pvConfidence = sim.pv_confidence ?? 1.0;
          const now = new Date();
          const currentSlotI = Math.floor((now.getHours() * 60 + now.getMinutes()) / granularity);
          let validating = true;
          while (validating) {
            validating = false;
            const sellSet = new Set(sellSlots.map(s => s.idx));
            let soc = currentKwh;
            let violationIdx = -1;
            for (let ii = currentSlotI; ii < numSlots; ii++) {
              const hr = Math.floor((ii * granularity) / 60);
              const cons = consumptionProfile
                ? ((consumptionProfile[hr] ?? consumptionProfile[String(hr)] ?? (consumption / 24)) * slotDuration)
                : (consumption / 24) * slotDuration;
              const pvHrKw = pvHourly ? ((pvHourly[hr] || pvHourly[String(hr)] || 0) * pvConfidence) : 0;
              const pvKwh = pvHrKw * slotDuration;
              let delta = pvKwh - cons;
              if (chargeIdxSet.has(ii)) {
                const gridKw = Math.min(powerKw, Math.max(0, inverterMaxKw - pvHrKw));
                delta += gridKw * slotDuration * efficiency;
              }
              if (sellSet.has(ii)) delta -= energyPerSlot;
              soc += delta;
              if (soc < reserveTarget - 0.01) {
                violationIdx = ii;
                break;
              }
              soc = Math.max(0, Math.min(batteryCapacity, soc));
            }
            if (violationIdx >= 0 && sellSlots.length > 0) {
              const candidates = sellSlots.filter(s => s.idx <= violationIdx);
              const toDrop = (candidates.length > 0 ? candidates : sellSlots)
                .reduce((min, s) => s.price < min.price ? s : min, sellSlots[0]);
              sellSlots = sellSlots.filter(s => s !== toDrop);
              validating = true;
            }
          }
        }

        for (const s of sellSlots) {
          result.slots[s.idx].action = "discharge";
        }
        result.dischargeCount = sellSlots.length;
        result.plannedDischargeKwh += Math.min(sellable, sellSlots.length * energyPerSlot);
        result.planned += Math.min(sellable, sellSlots.length * energyPerSlot);

        if (sellSlots.length && threshold == null) {
          threshold = Math.min(...sellSlots.map(s => s.price));
        }
      }
    }

    // Apply manual slot overrides (from click-to-override)
    const todayOverrides = this._slotOverrides?.today || {};
    for (const [idx, action] of Object.entries(todayOverrides)) {
      const i = parseInt(idx, 10);
      if (i >= 0 && i < result.slots.length) {
        result.slots[i].action = action;
      }
    }
    // Recalculate counts after overrides
    result.chargeCount = result.slots.filter(s => s.action === "charge").length;
    result.dischargeCount = result.slots.filter(s => s.action === "discharge").length;
    result.plannedChargeKwh = result.chargeCount * effectivePerSlot;
    result.plannedDischargeKwh = result.dischargeCount * energyPerSlot;
    result.planned = Math.round((result.plannedChargeKwh + result.plannedDischargeKwh) * 100) / 100;

    result.threshold = threshold;
    return result;
  }

  _markAll(slotData, action) {
    return slotData.map(s => ({ ...s, action }));
  }

  // ── Slot Timeline (Canvas) ──────────────────────────────────

  // Build slot data array from raw price list (fallback when schedule_status has no data)
  _buildSlotDataFromPrices(prices) {
    if (!Array.isArray(prices) || !prices.length) return null;
    return prices.map((p, i) => ({
      slot: i,
      price: typeof p === "object" ? (p?.value ?? null) : (p != null ? Number(p) : null),
      action: null,
    }));
  }

  // Try to get raw price arrays from the source Nordpool/price entity
  _getRawPriceSlots(dayKey) {
    // Auto-discover the Nordpool entity from current_price sensor attribute
    const sourceEid = this._getAttr("current_price", "price_source_entity");
    if (!sourceEid) return null;
    const entity = this.hass?.states?.[sourceEid];
    if (!entity) return null;
    const attrs = entity.attributes || {};
    for (const key of (dayKey === "today"
      ? ["today", "prices_today", "raw_today"]
      : ["tomorrow", "prices_tomorrow", "raw_tomorrow"])) {
      if (Array.isArray(attrs[key]) && attrs[key].length > 0) {
        return this._buildSlotDataFromPrices(attrs[key]);
      }
    }
    return null;
  }

  _drawSlotTimeline() {
    const canvas = this.shadowRoot?.querySelector("#slot-timeline");
    if (!canvas) return;

    let todaySlotData = this._getAttr("schedule_status", "slot_schedule");
    let tomorrowSlotData = this._getAttr("schedule_status", "slot_schedule_tomorrow");
    let granularity = this._getAttr("schedule_status", "slot_granularity_min") || 60;

    // Whether the slot data came from the backend scheduler.  An empty
    // backend schedule (all actions null — e.g. solar protection dropped
    // everything) is still authoritative; only the raw-price fallback
    // below lacks backend actions.
    const backendTodayData = !!(todaySlotData && todaySlotData.length);
    const backendTomorrowData = !!(tomorrowSlotData && tomorrowSlotData.length);

    // Fallback: read raw prices from source Nordpool entity if schedule_status has no slot data
    if (!todaySlotData || !todaySlotData.length) {
      todaySlotData = this._getRawPriceSlots("today");
      if (todaySlotData) granularity = Math.round((24 * 60) / todaySlotData.length);
    }
    if (!tomorrowSlotData || !tomorrowSlotData.length) {
      tomorrowSlotData = this._getRawPriceSlots("tomorrow");
    }

    // When tomorrow prices are not available but a PV forecast exists,
    // build synthetic slot data so the Tomorrow tab can show a solar-only
    // preview with SOC trajectory (no price axis, no charge/discharge actions).
    const pvTomorrowKwh = this._getNumericState("pv_forecast_tomorrow") || 0;
    const tomorrowPvOnly = (!tomorrowSlotData || !tomorrowSlotData.length) && pvTomorrowKwh > 0;
    if (tomorrowPvOnly) {
      const numSlotsToday = todaySlotData?.length || 24;
      tomorrowSlotData = [];
      const simEarly = this._getAttr("schedule_status", "sim_params") || {};
      const pvHourlyTmr = simEarly.pv_hourly_kwh_tomorrow || {};
      const hasHourlyData = Object.keys(pvHourlyTmr).length > 0;
      const slotsPerHour = numSlotsToday / 24;
      for (let i = 0; i < numSlotsToday; i++) {
        const hour = Math.floor((i * granularity) / 60);
        let pvKwh = 0;
        if (hasHourlyData) {
          pvKwh = (pvHourlyTmr[hour] ?? pvHourlyTmr[String(hour)] ?? 0) / slotsPerHour;
        } else {
          // Fallback: distribute evenly across 05–21 (summer-safe)
          if (hour >= 5 && hour < 21) pvKwh = pvTomorrowKwh / (16 * slotsPerHour);
        }
        tomorrowSlotData.push({ slot: i, price: null, action: null, pvKwh });
      }
    }
    this._tomorrowPvOnly = tomorrowPvOnly;

    // Determine which data to show: today or tomorrow (fallback)
    const now = new Date();
    const currentSlotIdx = Math.floor((now.getHours() * 60 + now.getMinutes()) / granularity);

    // Store tomorrow data for unified simulation access
    this._tomorrowSlotData = tomorrowSlotData;

    // Run client-side simulation on today's data (uses _tomorrowSlotData internally)
    const simResult = this._simulateSchedule(todaySlotData, this._simOverrides);

    // When no slider overrides are active, prefer the backend's validated schedule
    // for bar colors. The client-side sim may produce different sell slots
    // than the backend (which has SOC validation + reserve target protection).
    // Slot overrides (manual clicks) are already merged into the backend schedule,
    // so they don't require falling back to client-side simulation.
    const hasSliderOverrides = this._simOverrides && Object.keys(this._simOverrides).length > 0;
    const hasTodaySlotOverrides = this._slotOverrides
      && Object.keys(this._slotOverrides.today || {}).length > 0;
    const hasTomorrowSlotOverrides = this._slotOverrides
      && Object.keys(this._slotOverrides.tomorrow || {}).length > 0;
    const useBackendSchedule = !hasSliderOverrides && backendTodayData;

    // When using backend schedule with slot overrides, overlay local overrides
    // for immediate visual feedback (backend may not have refreshed yet).
    if (useBackendSchedule && hasTodaySlotOverrides) {
      const merged = todaySlotData.map(s => ({ ...s }));
      for (const [idx, action] of Object.entries(this._slotOverrides.today)) {
        const i = parseInt(idx, 10);
        if (i >= 0 && i < merged.length) merged[i].action = action;
      }
      this._simResult = { ...simResult, slots: merged };
    } else {
      this._simResult = useBackendSchedule ? { ...simResult, slots: todaySlotData } : simResult;
    }

    // When using backend schedule, recompute counts from the authoritative
    // slot data so the textual counters (X charge / Y sell / Z kWh planned)
    // match the colored bars.  Without this, simResult's stale 0 values
    // from the client-side simulation would win over backend reality.
    if (useBackendSchedule) {
      const displayedSlots = this._simResult.slots;
      const sim = this._getAttr("schedule_status", "sim_params") || {};
      const granMin = this._getAttr("schedule_status", "slot_granularity_min")
        || Math.round((24 * 60) / displayedSlots.length);
      const slotDur = granMin / 60;
      const safeMaxPower = this._getNumericState("safe_max_power") || 5000;
      const powerKw = Math.max(1, safeMaxPower / 1000);
      const eff = sim.efficiency || 0.90;
      const effectivePerSlot = powerKw * slotDur * eff;
      const energyPerSlot = powerKw * slotDur;

      const chargeCount = displayedSlots.filter(s => s.action === "charge").length;
      const dischargeCount = displayedSlots.filter(s => s.action === "discharge").length;
      const plannedChargeKwh = chargeCount * effectivePerSlot;
      const plannedDischargeKwh = dischargeCount * energyPerSlot;

      // Also recompute tomorrow counts from backend's slot_schedule_tomorrow
      // so the today-view's "tomorrow" summary stays consistent.
      let tomorrowChargeCount = this._simResult.tomorrowChargeCount;
      let tomorrowPlanned = this._simResult.tomorrowPlanned;
      if (backendTomorrowData) {
        tomorrowChargeCount = tomorrowSlotData.filter(s => s.action === "charge").length;
        const tmrDischargeCount = tomorrowSlotData.filter(s => s.action === "discharge").length;
        tomorrowPlanned = tomorrowChargeCount * effectivePerSlot + tmrDischargeCount * energyPerSlot;
      }

      this._simResult = {
        ...this._simResult,
        chargeCount,
        dischargeCount,
        plannedChargeKwh,
        plannedDischargeKwh,
        planned: Math.round((plannedChargeKwh + plannedDischargeKwh) * 100) / 100,
        tomorrowChargeCount,
        tomorrowPlanned,
      };
    }

    // Manual override or auto-switch to tomorrow when no actions remain
    const hasTomorrow = tomorrowSlotData?.length > 0;
    let showTomorrow;
    if (this._viewTomorrow !== null) {
      // Manual toggle — respect user choice (but only if tomorrow data exists)
      showTomorrow = this._viewTomorrow && hasTomorrow;
    } else {
      // Auto: always default to today
      showTomorrow = false;
    }

    // Keep today's sim for unified stats (tomorrowChargeCount, tomorrowPlanned)
    this._todaySimResult = this._simResult;
    let displayData, displayThreshold;
    if (showTomorrow && tomorrowPvOnly) {
      // PV-only preview: no prices, no actions — just solar forecast + SOC
      displayData = tomorrowSlotData;
      displayThreshold = null;
      this._simResult = { slots: tomorrowSlotData, chargeCount: 0, dischargeCount: 0, planned: 0, threshold: null };
    } else if (showTomorrow) {
      const useBackendTomorrow = !hasSliderOverrides && backendTomorrowData;
      if (useBackendTomorrow) {
        // Backend is authoritative — use its schedule directly
        const sim = this._getAttr("schedule_status", "sim_params") || {};
        const safeMaxPower = this._getNumericState("safe_max_power") || 5000;
        const powerKw = Math.max(1, safeMaxPower / 1000);
        const granMin = this._getAttr("schedule_status", "slot_granularity_min") || Math.round((24 * 60) / tomorrowSlotData.length);
        const slotDur = granMin / 60;
        const eff = sim.efficiency || 0.90;
        const effectivePerSlot = powerKw * slotDur * eff;
        // Overlay local tomorrow overrides for immediate feedback
        let tmrSlots = tomorrowSlotData;
        if (hasTomorrowSlotOverrides) {
          tmrSlots = tomorrowSlotData.map(s => ({ ...s }));
          for (const [idx, action] of Object.entries(this._slotOverrides.tomorrow)) {
            const i = parseInt(idx, 10);
            if (i >= 0 && i < tmrSlots.length) tmrSlots[i].action = action;
          }
        }
        const chargeCount = tmrSlots.filter(s => s.action === "charge").length;
        const dischargeCount = tmrSlots.filter(s => s.action === "discharge").length;
        displayData = tmrSlots;
        displayThreshold = null;
        this._simResult = {
          slots: tmrSlots,
          chargeCount,
          dischargeCount,
          planned: Math.round((chargeCount * effectivePerSlot + dischargeCount * powerKw * slotDur) * 100) / 100,
          threshold: null,
        };
      } else {
        // Slider overrides active — run client-side simulation for preview
        const tmrSim = this._simulateScheduleTomorrow(tomorrowSlotData, this._simOverrides);
        displayData = tmrSim?.slots ?? tomorrowSlotData;
        displayThreshold = tmrSim?.threshold;
        this._simResult = tmrSim;
      }
    } else {
      displayData = useBackendSchedule
        ? (this._simResult?.slots ?? todaySlotData)
        : (simResult?.slots ?? todaySlotData);
      displayThreshold = simResult?.threshold;
    }

    // Track what's actually displayed for toggle button state
    this._showingTomorrow = showTomorrow;
    this._hasTomorrowData = hasTomorrow;

    // Update the timeline label
    const label = this.shadowRoot?.querySelector(".timeline-label");
    if (label) {
      label.textContent = showTomorrow
        ? (tomorrowPvOnly ? "Tomorrow \u2014 Solar Forecast Only" : "Tomorrow\u2019s Forecast")
        : "Today\u2019s Schedule";
    }

    // Update toggle button active states
    const toggleBtns = this.shadowRoot?.querySelectorAll(".toggle-btn");
    if (toggleBtns?.length === 2) {
      toggleBtns[0].classList.toggle("active", !showTomorrow);
      toggleBtns[1].classList.toggle("active", showTomorrow);
    }

    if (!displayData || !displayData.length) {
      const ctx = canvas.getContext("2d");
      const dpr = window.devicePixelRatio || 1;
      canvas.width = canvas.offsetWidth * dpr;
      canvas.height = canvas.offsetHeight * dpr;
      ctx.scale(dpr, dpr);
      ctx.fillStyle = getComputedStyle(this).getPropertyValue("--secondary-text-color") || "#888";
      ctx.font = "12px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("No slot data available", canvas.offsetWidth / 2, canvas.offsetHeight / 2);
      return;
    }

    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.offsetWidth;
    const h = canvas.offsetHeight;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.scale(dpr, dpr);

    const numSlots = displayData.length;
    const marginLeft = 30;
    const marginRight = 30;
    const marginTop = 15;
    const marginBottom = 20;
    const chartH = h - marginTop - marginBottom;
    const barW = Math.max(1, (w - marginLeft - marginRight) / numSlots);

    // PV-only mode: draw PV production bars instead of price bars
    const isPvOnlyView = showTomorrow && tomorrowPvOnly;

    // Find price range — add padding below minimum so bars at the lowest
    // price still have visible height (otherwise they're clipped to 0px).
    const prices = displayData.map((s) => s.price).filter((p) => p != null);
    const actualMinPrice = prices.length ? Math.min(...prices) : 0;
    const actualMaxPrice = prices.length ? Math.max(...prices) : 0;
    const rawMin = Math.min(...prices, 0);
    const maxPrice = Math.max(...prices, 0.01);
    const rawRange = maxPrice - rawMin || 0.01;
    const minPrice = rawMin - rawRange * 0.05;
    const range = maxPrice - minPrice || 0.01;

    // PV-only range for bar heights (kWh per slot)
    let pvBarMax = 0;
    if (isPvOnlyView) {
      pvBarMax = Math.max(...displayData.map(s => s.pvKwh || 0), 0.01);
    }

    // Current time marker (only for today view)
    const currentSlot = showTomorrow ? -1 : currentSlotIdx;

    // Flexible load schedule: {slot_index: [load indices]} from the backend
    const flexSchedule = showTomorrow
      ? (this._getAttr("schedule_status", "flex_load_schedule_tomorrow") || {})
      : (this._getAttr("schedule_status", "flex_load_schedule") || {});

    // Draw bars
    for (let i = 0; i < numSlots; i++) {
      const slot = displayData[i];
      const x = marginLeft + i * barW;

      let barH, y;
      if (isPvOnlyView) {
        const pvVal = slot.pvKwh || 0;
        barH = (pvVal / pvBarMax) * chartH;
        y = marginTop + chartH - barH;
        ctx.fillStyle = pvVal > 0 ? "rgba(255, 223, 120, 0.55)" : "rgba(100, 140, 200, 0.15)";
        ctx.fillRect(x + 0.5, y, Math.max(1, barW - 1), barH);
        continue;
      }

      const price = slot.price ?? 0;
      barH = ((price - minPrice) / range) * chartH;
      y = marginTop + chartH - barH;

      if (showTomorrow) {
        // Tomorrow: color by simulated action
        if (slot.action === "charge") {
          ctx.fillStyle = price < 0 ? "rgba(33, 150, 243, 0.6)" : "rgba(76, 175, 80, 0.6)";
        } else if (slot.action === "discharge") {
          ctx.fillStyle = "rgba(255, 152, 0, 0.6)"; // orange, slightly softer
        } else {
          ctx.fillStyle = "rgba(100, 140, 200, 0.35)";
        }
      } else {
        // Today: color based on simulated action, dim past slots
        const isPast = i < currentSlot;
        const pastAction = isPast ? this._pastSlotActions?.[i] : null;
        if (isPast && pastAction === "charging") {
          ctx.fillStyle = "rgba(76, 175, 80, 0.3)";  // dim green — actually charged
        } else if (isPast && pastAction === "discharging") {
          ctx.fillStyle = "rgba(255, 152, 0, 0.3)";  // dim orange — actually discharged
        } else if (slot.action === "charge") {
          ctx.fillStyle = isPast ? "rgba(150, 150, 150, 0.2)" : (price < 0 ? "#2196F3" : "#4CAF50");
        } else if (slot.action === "discharge") {
          ctx.fillStyle = isPast ? "rgba(150, 150, 150, 0.2)" : "#FF9800";
        } else {
          ctx.fillStyle = isPast ? "rgba(150, 150, 150, 0.2)" : "rgba(150, 150, 150, 0.4)";
        }

        // Current slot highlight
        if (i === currentSlot) {
          ctx.fillStyle = slot.action === "charge" ? "#66BB6A"
            : slot.action === "discharge" ? "#FFB74D"
            : "#BBDEFB";
        }
      }

      ctx.fillRect(x + 0.5, y, Math.max(1, barW - 1), barH);

      // Current slot border
      if (i === currentSlot) {
        ctx.strokeStyle = "#efbdbd";
        ctx.lineWidth = 2;
        ctx.strokeRect(x, marginTop, barW, chartH);
      }

      // Flexible load indicator: cyan strip at bottom of bar
      const loadAtSlot = flexSchedule[String(i)];
      if (loadAtSlot && loadAtSlot.length > 0 && !isPvOnlyView) {
        const stripH = Math.max(3, Math.min(5, barH * 0.12));
        ctx.fillStyle = "rgba(0, 188, 212, 0.8)";
        ctx.fillRect(x + 0.5, marginTop + chartH - stripH, Math.max(1, barW - 1), stripH);
      }

      // Override slot indicator: white dashed border
      const day = showTomorrow ? "tomorrow" : "today";
      const overrideAction = this._slotOverrides?.[day]?.[i];
      if (overrideAction != null) {
        ctx.strokeStyle = overrideAction === "charge" ? "#00ff88" : "#ffaa00";
        ctx.lineWidth = 2;
        ctx.setLineDash([3, 2]);
        ctx.strokeRect(x + 1, y, Math.max(1, barW - 2), barH);
        ctx.setLineDash([]);
      }

      // Pending first-click indicator: pulsing border
      if (this._pendingClick && this._pendingClick.day === day && this._pendingClick.slotIdx === i) {
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 2.5;
        ctx.strokeRect(x, marginTop, barW, chartH);
      }
    }

    // Threshold line: use simulated threshold, fall back to entity value
    const threshold = displayThreshold ?? this._getNumericState("price_threshold");
    if (!isPvOnlyView && threshold != null && threshold >= minPrice && threshold <= maxPrice) {
      const thresholdY = marginTop + chartH - ((threshold - minPrice) / range) * chartH;
      ctx.strokeStyle = "#a8a209";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.moveTo(marginLeft, thresholdY);
      ctx.lineTo(w - marginRight, thresholdY);
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.fillStyle = "#a8a209";
      ctx.font = "9px sans-serif";
      ctx.textAlign = "right";
      ctx.fillText(`${threshold.toFixed(2)}`, marginLeft - 2, thresholdY + 3);
    }

    // Zero line (if negative prices exist)
    if (!isPvOnlyView && minPrice < 0) {
      const zeroY = marginTop + chartH - ((0 - minPrice) / range) * chartH;
      ctx.strokeStyle = "rgba(236, 210, 17, 0.88)";
      ctx.lineWidth = 0.5;
      ctx.beginPath();
      ctx.moveTo(marginLeft, zeroY);
      ctx.lineTo(w - marginRight, zeroY);
      ctx.stroke();
    }

    // Lowest-price indicator line + label (only meaningful when strictly above the axis floor)
    if (!isPvOnlyView && prices.length && actualMinPrice > minPrice + 1e-6) {
      const minY = marginTop + chartH - ((actualMinPrice - minPrice) / range) * chartH;
      ctx.strokeStyle = "rgba(76, 175, 80, 0.75)";
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 3]);
      ctx.beginPath();
      ctx.moveTo(marginLeft, minY);
      ctx.lineTo(w - marginRight, minY);
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.fillStyle = "rgba(76, 175, 80, 0.95)";
      ctx.font = "9px sans-serif";
      ctx.textAlign = "right";
      ctx.fillText(actualMinPrice.toFixed(2), marginLeft - 2, minY + 3);
    }

    // ── SOC trajectory (solid past / dotted future) ──────────────────
    // Prefer backend-computed trajectory when no slider overrides are active.
    // Slot overrides (manual clicks) are handled by the backend, so its
    // trajectory remains authoritative. Only slider previews need client-side.
    let backendTrajectory = null;
    if (!hasSliderOverrides) {
      const simParams = this._getAttr("schedule_status", "sim_params") || {};
      backendTrajectory = showTomorrow
        ? (simParams.backend_soc_trajectory_tomorrow || null)
        : (simParams.backend_soc_trajectory || null);
    }
    const socTrajectory = (backendTrajectory && backendTrajectory.length >= numSlots)
      ? backendTrajectory
      : this._computeSocTrajectory(displayData, showTomorrow);
    const socHistory = this._getAttr("schedule_status", "soc_history") || {};

    if (socTrajectory && socTrajectory.length > 1) {
      const socMin = 0;
      const socMax = 100;
      const toY = (soc) => marginTop + chartH - ((soc - socMin) / (socMax - socMin)) * chartH;

      // Draw solid line for actual past SOC (from soc_history)
      if (!showTomorrow && Object.keys(socHistory).length > 0) {
        ctx.strokeStyle = "#08b2c9";
        ctx.lineWidth = 2.5;
        ctx.setLineDash([]);
        ctx.beginPath();
        let started = false;
        for (let i = 0; i <= currentSlotIdx && i <= numSlots; i++) {
          const histSoc = socHistory[i] ?? socHistory[String(i)];
          const soc = histSoc != null ? histSoc : (socTrajectory[i] ?? socTrajectory[socTrajectory.length - 1]);
          const x = marginLeft + i * barW;
          const y = toY(soc);
          if (!started) { ctx.moveTo(x, y); started = true; }
          else ctx.lineTo(x, y);
        }
        ctx.stroke();
      }

      // Draw dotted line for projected future SOC
      ctx.strokeStyle = "#08b2c9";
      ctx.lineWidth = 2;
      ctx.setLineDash([5, 3]);
      ctx.beginPath();
      const startIdx = showTomorrow ? 0 : Math.max(0, currentSlotIdx);
      for (let i = startIdx; i <= numSlots; i++) {
        const soc = socTrajectory[i] ?? socTrajectory[socTrajectory.length - 1];
        const x = marginLeft + i * barW;
        const y = toY(soc);
        if (i === startIdx) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.setLineDash([]);

      // Right Y-axis SOC labels
      ctx.fillStyle = "#08b2c9";
      ctx.font = "9px sans-serif";
      ctx.textAlign = "left";
      const rightEdge = w - marginRight + 2;
      ctx.fillText("100%", rightEdge, marginTop + 8);
      ctx.fillText("0%", rightEdge, marginTop + chartH);
      // 50% midline label
      const mid50Y = marginTop + chartH - (0.5 * chartH);
      ctx.fillText("50%", rightEdge, mid50Y + 3);
    }

    // Projected overnight-minimum line (light-purple dashed) — the LOWEST SOC
    // the battery is predicted to reach before tomorrow's sun refills it.
    // This replaces the old "reserve target" line, which showed an
    // aspirational floor the SOC frequently sat *below* (cost-optimisation
    // declines to top off at peak prices), so it told the user little.  The
    // projected low directly answers "how low will my battery actually get?"
    const overnightLowPct = this._projectedOvernightMinPct(
      socTrajectory, currentSlotIdx, numSlots, showTomorrow);
    if (overnightLowPct != null && overnightLowPct > 0 && overnightLowPct < 100) {
      const toY = (soc) => marginTop + chartH - ((soc - 0) / 100) * chartH;
      const lowY = toY(overnightLowPct);
      ctx.strokeStyle = "rgba(186, 145, 255, 0.75)";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([6, 3]);
      ctx.beginPath();
      ctx.moveTo(marginLeft, lowY);
      ctx.lineTo(w - marginRight, lowY);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "rgba(186, 145, 255, 0.95)";
      ctx.font = "9px sans-serif";
      ctx.textAlign = "left";
      ctx.fillText(`${Math.round(overnightLowPct)}% projected low`, marginLeft + 3, lowY - 3);
    }

    // PV-only banner: inform user this is a solar-only preview
    if (isPvOnlyView) {
      ctx.fillStyle = "rgba(255, 223, 120, 0.9)";
      ctx.font = "bold 11px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("Without grid actions · Prices expected ~13:00", w / 2, marginTop + 14);
    }

    // Y-axis labels
    ctx.fillStyle = getComputedStyle(this).getPropertyValue("--secondary-text-color") || "#e7d91d";
    ctx.font = "9px sans-serif";
    ctx.textAlign = "right";
    if (isPvOnlyView) {
      ctx.fillText(`${pvBarMax.toFixed(1)} kWh`, marginLeft - 2, marginTop + 8);
      ctx.fillText("0", marginLeft - 2, marginTop + chartH);
    } else {
      ctx.fillText(maxPrice.toFixed(2), marginLeft - 2, marginTop + 8);
      ctx.fillText(minPrice.toFixed(2), marginLeft - 2, marginTop + chartH);
    }

    // X-axis hour labels
    ctx.textAlign = "center";
    ctx.fillStyle = getComputedStyle(this).getPropertyValue("--secondary-text-color") || "#e7d91d";
    const slotsPerHour = 60 / granularity;
    const labelInterval = granularity <= 15 ? 4 : granularity <= 30 ? 2 : 1;
    const hourLabelEvery = Math.max(1, Math.ceil(3 / (slotsPerHour * labelInterval)));
    for (let hour = 0; hour < 24; hour += hourLabelEvery) {
      const slotIdx = hour * slotsPerHour;
      if (slotIdx < numSlots) {
        const x = marginLeft + slotIdx * barW + barW / 2;
        ctx.fillText(`${hour}`, x, h - 3);
      }
    }

    // Legend at top-right
    ctx.font = "9px sans-serif";
    ctx.textAlign = "right";
    const legendX = w - marginRight - 5;
    const textColor = getComputedStyle(this).getPropertyValue("--primary-text-color") || "#fff";

    // SOC legend (dashed line)
    let lx = legendX;
    ctx.strokeStyle = "#1dbfe7";
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 2]);
    ctx.beginPath();
    ctx.moveTo(lx - 52, 6);
    ctx.lineTo(lx - 38, 6);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = textColor;
    ctx.fillText("SOC", lx - 52 + 34, 9);

    // Reserve / night-target legend (purple dashed line)
    lx -= 58;
    ctx.strokeStyle = "rgba(186, 145, 255, 0.85)";
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 2]);
    ctx.beginPath();
    ctx.moveTo(lx - 52, 6);
    ctx.lineTo(lx - 38, 6);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = textColor;
    ctx.fillText("night", lx - 52 + 38, 9);

    if (isPvOnlyView) {
      // PV forecast legend
      lx -= 55;
      ctx.fillStyle = "rgba(255, 223, 120, 0.55)";
      ctx.fillRect(lx - 55, 2, 8, 8);
      ctx.fillStyle = textColor;
      ctx.fillText("solar", lx - 58 + 32, 9);
    } else {
      // Charge legend
      lx -= 55;
      ctx.fillStyle = "#4CAF50";
      ctx.fillRect(lx - 55, 2, 8, 8);
      ctx.fillStyle = textColor;
      ctx.fillText("charge", lx - 58 + 40, 9);

      // Sell legend
      lx -= 60;
      ctx.fillStyle = "#FF9800";
      ctx.fillRect(lx - 55, 2, 8, 8);
      ctx.fillStyle = textColor;
      ctx.fillText("sell", lx - 58 + 30, 9);

      // Loads legend (only show if any flex loads configured)
      const flexConfigs = this._getAttr("schedule_status", "flex_load_configs") || [];
      if (flexConfigs.length > 0) {
        lx -= 55;
        ctx.fillStyle = "rgba(0, 188, 212, 0.8)";
        ctx.fillRect(lx - 55, 2, 8, 8);
        ctx.fillStyle = textColor;
        ctx.fillText("loads", lx - 58 + 35, 9);
      }

      if (showTomorrow) {
        lx -= 55;
        ctx.fillStyle = "rgba(100, 140, 200, 0.35)";
        ctx.fillRect(lx - 55, 2, 8, 8);
        ctx.fillStyle = textColor;
        ctx.fillText("idle", lx - 58 + 30, 9);
      }
    }
  }

  // Simulate schedule for tomorrow (all slots are "future")
  _simulateScheduleTomorrow(slotData, overrides = {}) {
    if (!slotData || !slotData.length) return null;

    const sim = this._getAttr("schedule_status", "sim_params") || {};
    const gridMode = overrides.gridMode ?? this._getState("grid_mode") ?? "off";
    const safeMaxPower = this._getNumericState("safe_max_power") || 5000;
    const powerKw = overrides.powerKw ?? Math.max(1, safeMaxPower / 1000);
    const batteryCapacity = sim.battery_capacity_kwh || 10;
    const chargeMax = (overrides.chargeMax ?? sim.battery_charge_max_pct ?? 100) / 100;
    const dischargeMin = (overrides.dischargeMin ?? sim.battery_discharge_min_pct ?? 20) / 100;
    const reserveTargetPct = sim.reserve_target_pct || 0;
    const efficiency = sim.efficiency || 0.90;
    const pvTomorrow = this._getNumericState("pv_forecast_tomorrow") || 0;
    const consumption = sim.consumption_est_kwh || 10;
    const inverterMaxKw = sim.inverter_max_power_kw || 10;

    // Mirrors ems._compute_reserve_target: a fixed reserve (reserveTargetPct
    // > 0) can only RAISE the target above the dynamic overnight-survival
    // reserve, never lower it below it.  (The 1.25× self_consumption boost on
    // the dynamic value is a backend-only refinement, not mirrored here.)
    const computeReserveTarget = (minKwhArg, reserveKwhArg) => {
      const dynamic = minKwhArg + reserveKwhArg;
      if (reserveTargetPct > 0) {
        const fixedFloor = (reserveTargetPct / 100) * batteryCapacity;
        return Math.min(batteryCapacity, Math.max(fixedFloor, dynamic));
      }
      return Math.min(batteryCapacity, dynamic);
    };

    const numSlots = slotData.length;
    const granularity = Math.round((24 * 60) / numSlots);
    const slotDuration = granularity / 60;
    const energyPerSlot = powerKw * slotDuration;
    const effectivePerSlot = energyPerSlot * efficiency;

    // For tomorrow, estimate battery at midnight based on actual state.
    // If today deferred charging, battery will be higher than min_kwh;
    // if today charged fully, overnight drain brings it to ~min_kwh.
    const batterySoc = sim.battery_soc_pct;
    const currentKwh = batterySoc != null ? (batterySoc / 100) * batteryCapacity : dischargeMin * batteryCapacity;
    const netPv = sim.net_pv_kwh || 0;
    const todayChargeKwh = this._simResult?.plannedChargeKwh || 0;
    const todayDischargeKwh = this._simResult?.plannedDischargeKwh || 0;
    const hoursToMidnight = Math.max(1, 24 - new Date().getHours());
    const drainToMidnight = (consumption / 24) * hoursToMidnight;
    // Battery at midnight = current + PV remaining + grid charging - grid selling - consumption drain
    // Clamp between min_kwh and max battery
    const minKwh = dischargeMin * batteryCapacity;
    const projectedMidnight = Math.max(minKwh, Math.min(batteryCapacity, currentKwh + netPv + todayChargeKwh - todayDischargeKwh - drainToMidnight));
    const startKwh = projectedMidnight;
    // Overnight reserve: estimate hours from sunset to next sunrise using today's pattern
    const reserveKwh = parseFloat(this._getAttr("schedule_status", "self_consumption_reserve")) || (consumption / 24 * 12);
    const reserveTarget = computeReserveTarget(dischargeMin * batteryCapacity, reserveKwh);
    // Daytime gap: on low-PV days battery drains during the day too
    const daytimeGap = Math.max(0, consumption - pvTomorrow);

    // All slots are "future"
    const remaining = slotData
      .filter(s => s.price != null)
      .map(s => ({ idx: s.slot, price: s.price }));

    if (gridMode === "off" || !remaining.length) {
      return { slots: this._markAll(slotData, null), chargeCount: 0, dischargeCount: 0, planned: 0, threshold: null };
    }

    // Arbitrage: when tomorrow's price spread >= arbitrage_price_delta,
    // charge to full capacity so the extra energy can be sold at peak.
    // Mirrors ems._schedule_both lines 976-1003.
    const arbitrageDelta = sim.arbitrage_price_delta || 0;
    const maxBatteryKwh = chargeMax * batteryCapacity;
    let arbitrageActive = false;
    if (arbitrageDelta > 0 && remaining.length) {
      const prices = remaining.map(s => s.price);
      const spread = Math.max(...prices) - Math.min(...prices);
      if (spread >= arbitrageDelta) {
        arbitrageActive = true;
      }
    }

    let threshold = null;
    if (overrides.priceLevel != null) {
      const prices = slotData.map(s => s.price).filter(p => p != null);
      const minP = Math.min(...prices);
      const maxP = Math.max(...prices);
      const avgP = prices.reduce((a, b) => a + b, 0) / prices.length;
      const level = overrides.priceLevel;
      threshold = level <= 5
        ? minP + ((avgP - minP) * (level - 1) / 4)
        : avgP + ((maxP - avgP) * (level - 5) / 5);
    }

    const result = { slots: slotData.map(s => ({ ...s, action: null })), chargeCount: 0, dischargeCount: 0, planned: 0, threshold };

    if (gridMode === "from_grid" || gridMode === "both") {
      // If today's unified simulation already assigned slots to tomorrow, use those.
      // Otherwise fall back to standalone calculation.
      const unifiedTomorrowIndices = this._simResult?.tomorrowChargeIndices;
      if (unifiedTomorrowIndices && unifiedTomorrowIndices.size > 0) {
        // Use the unified assignment from today's simulation
        for (const s of remaining) {
          if (unifiedTomorrowIndices.has(s.idx)) {
            result.slots[s.idx].action = "charge";
            result.chargeCount++;
            result.planned += effectivePerSlot;
          }
        }
        if (result.chargeCount && threshold == null) {
          threshold = Math.max(
            ...remaining.filter(s => unifiedTomorrowIndices.has(s.idx)).map(s => s.price)
          );
        }
      } else {
        // Standalone tomorrow calculation (no unified data available)
        const shortfall = Math.max(0, reserveTarget + daytimeGap - startKwh);
        let deficit = shortfall;
        // Arbitrage: charge to full capacity when spread is profitable.
        // PV surplus (pv - consumption) offsets the grid charge needed.
        if (arbitrageActive) {
          const fullCharge = maxBatteryKwh - startKwh;
          const pvSurplus = Math.max(0, pvTomorrow - consumption);
          const arbitrageDeficit = Math.max(0, fullCharge - pvSurplus);
          deficit = Math.max(deficit, arbitrageDeficit);
        }
        if (deficit > 0) {
          const neg = remaining.filter(s => s.price < 0);
          const nonNeg = remaining.filter(s => s.price >= 0).sort((a, b) => a.price - b.price);
          const negEnergy = neg.length * effectivePerSlot;
          const needed = effectivePerSlot > 0 ? Math.ceil(Math.max(0, deficit - negEnergy) / effectivePerSlot) : 0;
          const chargeSlots = [...neg, ...nonNeg.slice(0, needed)];
          for (const s of chargeSlots) result.slots[s.idx].action = "charge";
          result.chargeCount = chargeSlots.length;
          result.planned += Math.min(deficit, chargeSlots.length * effectivePerSlot);
          if (chargeSlots.length && threshold == null) threshold = Math.max(...chargeSlots.map(s => s.price));
        }
      }
    }

    if (gridMode === "to_grid" || gridMode === "both") {
      // Include planned charge energy + PV in peak estimate so sell side
      // sees the surplus.  SOC validation prunes overcommitted sells.
      const chargeEnergyPlanned = result.chargeCount * effectivePerSlot;
      const projectedKwh = arbitrageActive
        ? maxBatteryKwh
        : Math.min(maxBatteryKwh, startKwh + pvTomorrow + chargeEnergyPlanned);
      const sellable = Math.max(0, projectedKwh - reserveTarget) * efficiency * 0.85;
      const roundTrip = efficiency * efficiency;

      if (sellable > 0) {
        const chargeIdxSet = new Set(result.slots.filter(s => s.action === "charge").map(s => s.slot));
        let available = remaining.filter(s => s.price > 0 && !chargeIdxSet.has(s.idx));
        if (gridMode === "both") {
          let minSell = 0;
          let refBuy = null;
          if (result.chargeCount > 0) {
            refBuy = Math.max(...result.slots.filter(s => s.action === "charge").map(s => s.price));
            minSell = refBuy / roundTrip;
          } else if (remaining.length) {
            refBuy = Math.min(...remaining.map(s => s.price));
          }
          // Arbitrage delta sell gate (mirrors backend)
          if (arbitrageDelta > 0 && refBuy != null) {
            minSell = Math.max(minSell, refBuy + arbitrageDelta);
          }
          if (minSell > 0) {
            available = available.filter(s => s.price >= minSell);
          }
        }
        available.sort((a, b) => b.price - a.price);
        const needed = energyPerSlot > 0 ? Math.ceil(sellable / energyPerSlot) : 0;
        let sellSlots = available.slice(0, needed);

        // Per-slot SOC validation: simulate forward and drop discharge slots
        // that would push SOC below reserve_target. Matches backend
        // _validate_schedule_soc behavior.
        const consumptionProfile = sim.consumption_hourly_profile || null;
        const pvHourlyTmr = sim.pv_hourly_kwh_tomorrow || null;
        const hasHourlyTmr = pvHourlyTmr && Object.keys(pvHourlyTmr).length > 0;
        const pvFallbackTotal = pvTomorrow;
        const daylightSlots = [];
        for (let ii = 0; ii < numSlots; ii++) {
          const hr = Math.floor((ii * granularity) / 60);
          if (hr >= 5 && hr < 21) daylightSlots.push(ii);
        }
        const pvPerDaylightSlot = daylightSlots.length > 0 ? pvFallbackTotal / daylightSlots.length : 0;
        const daylightSet = new Set(daylightSlots);

        let validated = true;
        while (validated) {
          validated = false;
          const sellSet = new Set(sellSlots.map(s => s.idx));
          const chargeSet = chargeIdxSet;
          let soc = startKwh;
          let violationIdx = -1;
          for (let ii = 0; ii < numSlots; ii++) {
            const hr = Math.floor((ii * granularity) / 60);
            const cons = consumptionProfile
              ? ((consumptionProfile[hr] ?? consumptionProfile[String(hr)] ?? (consumption / 24)) * slotDuration)
              : (consumption / 24) * slotDuration;
            const pv = hasHourlyTmr
              ? ((pvHourlyTmr[hr] ?? pvHourlyTmr[String(hr)] ?? 0) * slotDuration)
              : (daylightSet.has(ii) ? pvPerDaylightSlot : 0);
            const pvKwRate = slotDuration > 0 ? pv / slotDuration : 0;
            let delta = pv - cons;
            if (chargeSet.has(ii)) {
              const gridKw = Math.min(powerKw, Math.max(0, inverterMaxKw - pvKwRate));
              delta += gridKw * slotDuration * efficiency;
            }
            if (sellSet.has(ii)) delta -= energyPerSlot;
            soc += delta;
            if (soc < reserveTarget - 0.01) {
              violationIdx = ii;
              break;
            }
            soc = Math.max(0, Math.min(batteryCapacity, soc));
          }
          if (violationIdx >= 0 && sellSlots.length > 0) {
            // Drop least profitable sell slot at or before violation
            const candidates = sellSlots.filter(s => s.idx <= violationIdx);
            const toDrop = (candidates.length > 0 ? candidates : sellSlots)
              .reduce((min, s) => s.price < min.price ? s : min, sellSlots[0]);
            sellSlots = sellSlots.filter(s => s !== toDrop);
            validated = true;
          }
        }

        for (const s of sellSlots) result.slots[s.idx].action = "discharge";
        result.dischargeCount = sellSlots.length;
        result.planned += Math.min(sellable, sellSlots.length * energyPerSlot);
        if (sellSlots.length && threshold == null) threshold = Math.min(...sellSlots.map(s => s.price));
      }
    }

    // Apply manual slot overrides for tomorrow
    const tmrOverrides = this._slotOverrides?.tomorrow || {};
    for (const [idx, action] of Object.entries(tmrOverrides)) {
      const i = parseInt(idx, 10);
      if (i >= 0 && i < result.slots.length) {
        result.slots[i].action = action;
      }
    }
    result.chargeCount = result.slots.filter(s => s.action === "charge").length;
    result.dischargeCount = result.slots.filter(s => s.action === "discharge").length;
    result.planned = Math.round(
      (result.chargeCount * effectivePerSlot + result.dischargeCount * energyPerSlot) * 100
    ) / 100;

    result.threshold = threshold;
    return result;
  }

  // ── SOC Trajectory ─────────────────────────────────────────
  // Forward-simulate battery SOC% through each slot for the displayed day.
  // Returns an array of SOC% values (one per slot).

  _computeSocTrajectory(displayData, showTomorrow) {
    const sim = this._getAttr("schedule_status", "sim_params") || {};
    const batteryCapacity = sim.battery_capacity_kwh || 10;
    const efficiency = sim.efficiency || 0.90;
    const consumption = sim.consumption_est_kwh || 10;
    const overrides = this._simOverrides || {};
    const chargeMax = (overrides.chargeMax ?? sim.battery_charge_max_pct ?? 100) / 100;
    const dischargeMin = (overrides.dischargeMin ?? sim.battery_discharge_min_pct ?? 20) / 100;

    const numSlots = displayData.length;
    const granularity = this._getAttr("schedule_status", "slot_granularity_min") || Math.round((24 * 60) / numSlots);
    const slotDuration = granularity / 60;  // hours per slot

    const safeMaxPower = this._getNumericState("safe_max_power") || 5000;
    const powerKw = overrides.powerKw ?? Math.max(1, safeMaxPower / 1000);
    const inverterMaxKw = sim.inverter_max_power_kw || 10;
    const energyPerSlot = powerKw * slotDuration;

    // Consumption drain per slot — use hourly profile when available
    const consumptionProfile = sim.consumption_hourly_profile || null;
    const consumptionPerSlotFlat = (consumption / 24) * slotDuration;

    // PV production per slot — use per-hour data when available (matches backend)
    // Apply pv_confidence from backend to scale forecast on cloudy days
    const pvConfidence = (!showTomorrow && sim.pv_confidence != null) ? sim.pv_confidence : 1.0;
    const pvHourlyRaw = showTomorrow
      ? (sim.pv_hourly_kwh_tomorrow || null)
      : (sim.pv_hourly_kwh || null);
    const pvHourly = (pvHourlyRaw && Object.keys(pvHourlyRaw).length > 0) ? pvHourlyRaw : null;
    // Fallback when hourly data unavailable: distribute total across 05–21
    let pvFallbackPerSlot = 0;
    const pvFallbackSlotSet = new Set();
    if (!pvHourly) {
      const pvTotal = showTomorrow
        ? (this._getNumericState("pv_forecast_tomorrow") || 0)
        : (this._getNumericState("pv_forecast_remaining") || 0);
      const daylightSlots = [];
      for (let i = 0; i < numSlots; i++) {
        const hour = Math.floor((i * granularity) / 60);
        if (hour >= 5 && hour < 21) daylightSlots.push(i);
      }
      pvFallbackPerSlot = daylightSlots.length > 0 ? pvTotal / daylightSlots.length : 0;
      daylightSlots.forEach(s => pvFallbackSlotSet.add(s));
    }

    // Starting SOC
    let currentKwh;
    if (showTomorrow) {
      // Estimate battery at midnight from today's sim
      const batterySoc = sim.battery_soc_pct;
      const todayKwh = batterySoc != null ? (batterySoc / 100) * batteryCapacity : dischargeMin * batteryCapacity;
      const netPv = sim.net_pv_kwh || 0;
      const todayChargeKwh = this._todaySimResult?.plannedChargeKwh || 0;
      const todayDischargeKwh = this._todaySimResult?.plannedDischargeKwh || 0;
      const hoursToMidnight = Math.max(1, 24 - new Date().getHours());
      const drainToMidnight = (consumption / 24) * hoursToMidnight;
      const minKwh = dischargeMin * batteryCapacity;
      currentKwh = Math.max(minKwh, Math.min(batteryCapacity, todayKwh + netPv + todayChargeKwh - todayDischargeKwh - drainToMidnight));
    } else {
      const batterySoc = sim.battery_soc_pct;
      const now = new Date();
      const currentSlotIdx = Math.floor((now.getHours() * 60 + now.getMinutes()) / granularity);
      // For past slots, estimate midnight SOC and simulate forward
      const reserveKwh = parseFloat(this._getAttr("schedule_status", "self_consumption_reserve")) || 0;
      const minKwh = dischargeMin * batteryCapacity;
      currentKwh = Math.min(batteryCapacity, minKwh + reserveKwh);
      // We'll snap to actual SOC at currentSlotIdx during the loop below
      var snapSlotIdx = currentSlotIdx;
      var snapKwh = batterySoc != null ? (batterySoc / 100) * batteryCapacity : null;
    }

    // Forward simulate
    const trajectory = [];
    for (let i = 0; i < numSlots; i++) {
      // At the current time slot, snap to actual battery SOC
      if (!showTomorrow && snapKwh != null && i === snapSlotIdx) {
        currentKwh = snapKwh;
      }
      trajectory.push((currentKwh / batteryCapacity) * 100);

      const slot = displayData[i];
      // Consumption drain (per-hour profile or flat)
      const hour = Math.floor((i * granularity) / 60);
      const consThisSlot = consumptionProfile
        ? ((consumptionProfile[hour] ?? consumptionProfile[String(hour)] ?? (consumption / 24)) * slotDuration)
        : consumptionPerSlotFlat;
      currentKwh -= consThisSlot;
      // PV production (self-consumed first — charges battery)
      if (pvHourly) {
        // Per-hour gross PV data from backend, scaled by pv_confidence
        const hour = Math.floor((i * granularity) / 60);
        const pvKwh = (pvHourly[hour] || pvHourly[String(hour)] || 0) * pvConfidence;
        currentKwh += pvKwh * slotDuration;
      } else if (pvFallbackSlotSet.has(i)) {
        currentKwh += pvFallbackPerSlot;
      }
      // Scheduled actions — respect battery bounds like the real inverter does
      const minKwhFloor = dischargeMin * batteryCapacity;
      if (slot.action === "charge") {
        // PV rate for this slot (kW, not kWh) to cap grid charge at inverter limit
        let pvKwRate = 0;
        if (pvHourly) {
          const hrC = Math.floor((i * granularity) / 60);
          pvKwRate = (pvHourly[hrC] || pvHourly[String(hrC)] || 0) * pvConfidence;
        } else if (pvFallbackSlotSet.has(i) && slotDuration > 0) {
          pvKwRate = pvFallbackPerSlot / slotDuration;
        }
        const gridKw = Math.min(powerKw, Math.max(0, inverterMaxKw - pvKwRate));
        currentKwh += gridKw * slotDuration * efficiency;
      } else if (slot.action === "discharge" && currentKwh > minKwhFloor) {
        // Inverter stops discharging at the SOC floor — don't drain below it
        currentKwh -= Math.min(energyPerSlot, currentKwh - minKwhFloor);
      }
      // Clamp to physical limits (discharge_min floor, capacity ceiling)
      currentKwh = Math.max(minKwhFloor, Math.min(batteryCapacity, currentKwh));
    }
    // Add final point (end of last slot)
    trajectory.push((currentKwh / batteryCapacity) * 100);

    return trajectory;
  }

  _toggleDayView(e) {
    // Ignore clicks on the disabled tomorrow button
    if (e?.target?.classList?.contains("disabled")) return;
    // Select the view named by the clicked button (re-clicking the
    // active button is a no-op, not a flip).
    const label = (e?.target?.textContent || "").trim().toLowerCase();
    if (label === "today") {
      this._viewTomorrow = false;
    } else if (label === "tomorrow") {
      this._viewTomorrow = true;
    } else {
      this._viewTomorrow = !this._viewTomorrow;
    }
    this._drawSlotTimeline();
    this.requestUpdate();
  }

  // ── Canvas click handler for slot overrides ─────────────────

  _attachCanvasClickHandler() {
    const canvas = this.shadowRoot?.querySelector("#slot-timeline");
    if (!canvas || canvas._clickHandlerAttached) return;
    canvas._clickHandlerAttached = true;
    canvas.style.cursor = "pointer";
    canvas.addEventListener("click", (e) => this._handleCanvasClick(e));
  }

  _handleCanvasClick(e) {
    const canvas = e.target;
    const rect = canvas.getBoundingClientRect();
    const clickX = e.clientX - rect.left;

    const gridMode = this._simOverrides.gridMode ?? this._getState("grid_mode") ?? "off";
    if (gridMode === "off") return;
    if (this._tomorrowPvOnly && this._showingTomorrow) return;

    // Determine which slot was clicked using same layout as _drawSlotTimeline
    const day = this._showingTomorrow ? "tomorrow" : "today";
    const displayData = this._showingTomorrow
      ? (this._tomorrowSlotData || [])
      : (this._getAttr("schedule_status", "slot_schedule") || this._getRawPriceSlots("today") || []);
    if (!displayData.length) return;

    const numSlots = displayData.length;
    const w = canvas.offsetWidth;
    const marginLeft = 30;
    const marginRight = 30;
    const barW = Math.max(1, (w - marginLeft - marginRight) / numSlots);

    const slotIdx = Math.floor((clickX - marginLeft) / barW);
    if (slotIdx < 0 || slotIdx >= numSlots) return;

    const slot = displayData[slotIdx];
    if (slot == null || slot.price == null) return;

    const threshold = this._simResult?.threshold ?? this._getNumericState("price_threshold") ?? 0;
    const overrides = this._slotOverrides[day] || {};

    // Click on an already-overridden slot → cancel that override
    if (overrides[slotIdx] != null) {
      delete overrides[slotIdx];
      this._slotOverrides = { ...this._slotOverrides, [day]: { ...overrides } };
      this._pendingClick = null;
      this._persistSlotOverrides();
      this._drawSlotTimeline();
      this.requestUpdate();
      return;
    }

    // Determine click intent: charge (below threshold) or sell (above threshold)
    const clickAction = slot.price <= threshold ? "charge" : "discharge";

    // Enforce grid_mode restrictions
    if (gridMode === "from_grid" && clickAction === "discharge") return;
    if (gridMode === "to_grid" && clickAction === "charge") return;

    // Two-click selection logic
    if (!this._pendingClick) {
      // First click — store pending
      this._pendingClick = { slotIdx, action: clickAction, day };
      this._drawSlotTimeline();  // show pending indicator
      this.requestUpdate();
      return;
    }

    // Second click
    const pending = this._pendingClick;
    this._pendingClick = null;

    // If different day or different action type → cancel
    if (pending.day !== day || pending.action !== clickAction) {
      this._drawSlotTimeline();
      this.requestUpdate();
      return;
    }

    // Same day, same action type → mark range
    const startSlot = Math.min(pending.slotIdx, slotIdx);
    const endSlot = Math.max(pending.slotIdx, slotIdx);
    const newOverrides = { ...(this._slotOverrides[day] || {}) };
    for (let i = startSlot; i <= endSlot; i++) {
      if (displayData[i]?.price != null) {
        newOverrides[i] = clickAction;
      }
    }
    this._slotOverrides = { ...this._slotOverrides, [day]: newOverrides };
    this._persistSlotOverrides();
    this._drawSlotTimeline();
    this.requestUpdate();
  }

  async _persistSlotOverrides() {
    // Persist overrides to the coordinator via a HA service call
    const eid = this._getEntityId("schedule_status");
    if (!eid || !this.hass) return;
    try {
      await this.hass.callService("ha_felicity", "set_slot_overrides", {
        entity_id: eid,
        overrides: JSON.stringify(this._slotOverrides),
      });
    } catch (err) {
      console.warn("Failed to persist slot overrides:", err);
    }
  }

  _hasSlotOverrides() {
    const t = this._slotOverrides || {};
    return Object.keys(t.today || {}).length > 0 || Object.keys(t.tomorrow || {}).length > 0;
  }

  _clearSlotOverrides() {
    this._slotOverrides = { today: {}, tomorrow: {} };
    this._pendingClick = null;
    this._persistSlotOverrides();
    this._drawSlotTimeline();
    this.requestUpdate();
  }

  // ── Service calls for controls ──────────────────────────────

  async _setSelect(key, option) {
    const eid = this._getEntityId(key);
    if (!eid) return;
    try {
      await this.hass.callService("select", "select_option", {
        entity_id: eid,
        option: option,
      });
    } catch (err) {
      console.error(`Failed to set ${key}:`, err);
    }
  }

  async _setNumber(key, value) {
    const eid = this._getEntityId(key);
    if (!eid) return;
    try {
      await this.hass.callService("number", "set_value", {
        entity_id: eid,
        value: parseFloat(value),
      });
    } catch (err) {
      console.error(`Failed to set ${key}:`, err);
    }
  }

  // ── Render ──────────────────────────────────────────────────

  render() {
    if (!this.hass || !this.config) {
      return html`<ha-card><div class="card-content">Loading...</div></ha-card>`;
    }

    const energyState = this._getState("energy_state") || "unknown";
    const scheduleStatus = this._getState("schedule_status") || "unknown";
    const currentPrice = this._getNumericState("current_price");
    const threshold = this._simResult?.threshold ?? this._getNumericState("price_threshold");
    const gridMode = this._simOverrides.gridMode ?? this._getState("grid_mode") ?? "off";
    const priceMode = this._getState("price_mode") || "manual";
    const likelihood = this._getState("charge_likelihood") || "unknown";
    const currency = this.config.currency || "\u20AC";

    // Battery info
    const sim = this._getAttr("schedule_status", "sim_params") || {};
    const batterySoc = sim.battery_soc_pct;
    const batteryCapacity = sim.battery_capacity_kwh || 10;
    const chargeMax = sim.battery_charge_max_pct ?? 100;
    const dischargeMin = sim.battery_discharge_min_pct ?? 20;

    // Schedule info: use simulation result if available, else fall back to entity attributes
    const simR = this._simResult;
    const todaySimR = this._todaySimResult;
    const chargeSlots = simR?.chargeCount ?? this._getAttr("schedule_status", "scheduled_charge_slots") ?? 0;
    const tomorrowChargeSlots = todaySimR?.tomorrowChargeCount ?? this._getAttr("schedule_status", "tomorrow_planned_slots") ?? 0;
    const dischargeSlots = simR?.dischargeCount ?? this._getAttr("schedule_status", "scheduled_discharge_slots") ?? 0;
    const todayPlanned = (todaySimR?.planned ?? this._getAttr("schedule_status", "grid_energy_planned_kwh")) || 0;
    const tomorrowPlannedKwh = (todaySimR?.tomorrowPlanned ?? this._getAttr("schedule_status", "tomorrow_planned_kwh")) || 0;
    const gridPlanned = todayPlanned + tomorrowPlannedKwh;
    const flexLoadConfigs = this._getAttr("schedule_status", "flex_load_configs") || [];
    const flexLoadStates = this._getAttr("schedule_status", "flex_load_states") || {};
    const activeLoadCount = Object.values(flexLoadStates).filter(v => v).length;
    const evBoostActive = this._getAttr("schedule_status", "ev_boost_active") || false;
    const evBoostMin = this._getAttr("schedule_status", "ev_boost_remaining_min") || 0;
    const evBoostHours = Math.floor(evBoostMin / 60);
    const evBoostMins = evBoostMin % 60;
    const evBoostText = evBoostMin > 0
      ? `${evBoostHours > 0 ? `${evBoostHours}h ` : ""}${evBoostMins}m remaining`
      : "";
    const operationalMode = this._getState("operational_mode") || null;
    const schedulerActive = this._getAttr("schedule_status", "scheduler_active") || null;
    const schedulerEngine = this._getState("scheduler_engine") || "greedy";
    // Active power limit + grid current (for throttle display in status bar)
    const powerLevelKw = this._getNumericState("power_level");
    const safeMaxKw = this._getNumericState("safe_max_power") != null
      ? this._getNumericState("safe_max_power") / 1000 : null;
    const peakAmp = this._getNumericState("peak_grid_current_now");
    const isThrottled = powerLevelKw != null && safeMaxKw != null
      && safeMaxKw < powerLevelKw - 0.05;
    // Show the EV override button only when an EV charger is configured
    const evChargerConfigured = flexLoadConfigs.some((c) => c.is_ev);
    const pvRemaining = this._getNumericState("pv_forecast_remaining");
    const pvToday = this._getNumericState("pv_forecast_today");
    const pvTomorrow = this._getNumericState("pv_forecast_tomorrow");
    // Actual PV today: prefer schedule_status attr, fall back to dedicated sensor entity
    let pvActualToday = this._getAttr("schedule_status", "pv_actual_today_kwh");
    if (pvActualToday == null) {
      let pvSum = 0;

      // TREX-5/10: entity key is pv_generated_energy_day (value in Wh)
      const whVal = this._getNumericState("pv_generated_energy_day");
      if (whVal != null) {
        pvSum = whVal / 1000;
      } else {
        // TREX-25/50: sum per-string day energy entities (already in kWh)
        const strings = ["pv1_day_energy", "pv2_day_energy", "pv3_day_energy", "pv4_day_energy"];
        const vals = strings.map(k => this._getNumericState(k)).filter(v => v != null);
        if (vals.length) pvSum = vals.reduce((a, b) => a + b, 0);
      }

      if (pvSum > 0.1) {
        pvActualToday = pvSum;
      } else if (this.config.generator_as_pv) {
        // Generator-port solar: PV registers read ~0 but solar enters via gen port
        // Check both generator and microinverter registers (genmode Micro Inv uses the latter)
        const genEnergy = this._getNumericState("generator_day_cost_energy") || 0;
        const microEnergy = this._getNumericState("microinverter_day_cost_energy") || 0;
        const altEnergy = Math.max(genEnergy, microEnergy);
        if (altEnergy > 0) {
          pvActualToday = altEnergy;
        } else {
          pvActualToday = pvSum;
        }
      } else {
        pvActualToday = pvSum;
      }
    }
    // Generator-port solar: if backend attr returned near-zero but generator has energy
    if (this.config.generator_as_pv && (pvActualToday == null || pvActualToday < 0.1)) {
      const genEnergy = this._getNumericState("generator_day_cost_energy") || 0;
      const microEnergy = this._getNumericState("microinverter_day_cost_energy") || 0;
      const altEnergy = Math.max(genEnergy, microEnergy);
      if (altEnergy > 0) {
        pvActualToday = altEnergy;
      }
    }
    let reserve = this._getAttr("schedule_status", "self_consumption_reserve")
      ?? this._getAttr("energy_state", "self_consumption_reserve");
    if (!(parseFloat(reserve) > 0)) {
      const simR = this._getAttr("schedule_status", "sim_params") || {};
      reserve = this._overnightReserveKwh(
        simR.consumption_est_kwh, simR.pv_hourly_kwh || null);
    }
    const weeklyConsumption = this._getNumericState("weekly_avg_consumption");
    const safeMaxPower = this._getNumericState("safe_max_power");
    const dailyConsumptionEst = this._getNumericState("daily_consumption_estimate");

    // Economic Rule 1 window mismatch warning (integration doesn't write
    // rule 1 start/stop time or weekday — a restricted window silently
    // blocks the EMS).
    const rule1Warning = this._getAttr("schedule_status", "rule1_window_warning");

    // Battery SOC bars (10 segments)
    const socPct = batterySoc ?? 0;
    const filledBars = Math.round(socPct / 10);

    return html`
      <ha-card>
        <div class="card-header">
          <div class="battery-indicator">
            <div class="battery-bars">
              ${[...Array(10)].map((_, i) => html`
                <div class="bar ${i < filledBars ? 'filled' : ''} ${i < filledBars && socPct <= 20 ? 'low' : ''} ${i < filledBars && socPct > 20 && socPct <= 50 ? 'mid' : ''}"></div>
              `)}
            </div>
            <span class="battery-text">
              <span class="battery-soc">${this._fmt(socPct, 0)}%</span>
              <span class="battery-cap">${batteryCapacity} kWh</span>
            </span>
          </div>
          ${evChargerConfigured ? html`
            <button class="override-btn ${evBoostActive ? 'active' : ''}"
              title="Force the EV charger on. Each press adds +1 hour of 'always on' (the EMS still throttles current if the grid limit is hit)."
              @click=${() => this._pressEvBoost()}>
              <ha-icon icon="mdi:ev-station"></ha-icon>
              <span>Override +1h</span>
            </button>
          ` : ''}
          <div class="status-badges">
            <span class="badge ${energyState}">${energyState}</span>
            <span class="badge schedule-${scheduleStatus}">${scheduleStatus.replace(/_/g, ' ')}</span>
          </div>
        </div>

        ${rule1Warning && rule1Warning.conflict ? html`
          <div class="rule1-warning">
            <span class="rule1-warning-icon">⚠️</span>
            <span class="rule1-warning-text">
              ${rule1Warning.affected_slots} scheduled slot(s) fall outside the
              inverter's Economic Rule 1 window
              ${rule1Warning.time_violation
                ? html`(active ${rule1Warning.rule1_start_time}–${rule1Warning.rule1_stop_time})`
                : ""}
              ${rule1Warning.weekday_violation
                ? html`(days: ${(rule1Warning.rule1_effective_days || []).join(", ") || "none"})`
                : ""}.
              The inverter will ignore charge/discharge outside this window —
              adjust Rule 1 Start/Stop Time and Effective Week on the inverter.
            </span>
          </div>
        ` : ""}

        ${evBoostActive ? html`
          <div class="ev-boost-banner">
            <ha-icon icon="mdi:ev-station"></ha-icon>
            <span>EV Boost active — ${evBoostText}</span>
            <button class="boost-cancel" @click=${() => this._cancelEvBoost()}>Cancel</button>
          </div>
        ` : ""}

        <div class="card-content">
          <!-- Price & State summary -->
          <div class="summary-row">
            <div class="summary-item">
              <span class="label">Price</span>
              <span class="value price">${this._fmt(currentPrice, 3)} ${currency}/kWh</span>
            </div>
            <div class="summary-item">
              <span class="label">Threshold</span>
              <span class="value">${this._fmt(threshold, 3)} ${currency}/kWh</span>
            </div>
            <div class="summary-item">
              <span class="label">Likelihood</span>
              <span class="value likelihood-${likelihood}">${likelihood}</span>
            </div>
          </div>

          <!-- Slot Timeline -->
          <div class="timeline-container">
            <div class="timeline-header">
              <div class="timeline-label">Today's Schedule</div>
              <div class="timeline-toggle" @click=${this._toggleDayView}>
                <span class="toggle-btn ${!this._showingTomorrow ? 'active' : ''}">Today</span>
                <span class="toggle-btn ${this._showingTomorrow ? 'active' : ''} ${!this._hasTomorrowData ? 'disabled' : ''}">Tomorrow</span>
              </div>
            </div>
            <canvas id="slot-timeline"></canvas>
            ${this._hasSlotOverrides() ? html`
              <div class="override-bar">
                <span class="override-hint">${this._pendingClick ? 'Click second slot to complete range' : 'Manual overrides active'}</span>
                <span class="override-clear" @click=${() => this._clearSlotOverrides()}>Clear</span>
              </div>
            ` : this._pendingClick ? html`
              <div class="override-bar">
                <span class="override-hint">Click second slot to complete range (same type)</span>
                <span class="override-clear" @click=${() => { this._pendingClick = null; this._drawSlotTimeline(); this.requestUpdate(); }}>Cancel</span>
              </div>
            ` : ''}
          </div>

          <!-- Schedule stats -->
          <div class="stats-row">
            <div class="stat">
              <ha-icon icon="mdi:battery-charging"></ha-icon>
              <span>${chargeSlots}${tomorrowChargeSlots > 0 ? `+${tomorrowChargeSlots}` : ''} charge</span>
            </div>
            <div class="stat">
              <ha-icon icon="mdi:transmission-tower-export"></ha-icon>
              <span>${dischargeSlots} sell</span>
            </div>
            <div class="stat">
              <ha-icon icon="mdi:lightning-bolt"></ha-icon>
              <span>${this._fmt(gridPlanned, 1)} kWh planned</span>
            </div>
            <div class="stat">
              <ha-icon icon="mdi:weather-night"></ha-icon>
              <span>${this._fmt(reserve, 1)} kWh overnight need</span>
            </div>
            ${flexLoadConfigs.length > 0 ? html`
            <div class="stat">
              <ha-icon icon="mdi:power-plug-outline"></ha-icon>
              <span>${activeLoadCount}/${flexLoadConfigs.length} loads active</span>
            </div>
            ` : ""}
          </div>

          ${this._renderFlexLoads(flexLoadConfigs)}

          <!-- PV Actual & Forecast -->
          <div class="pv-row">
            <div class="pv-item">
              <ha-icon icon="mdi:solar-power"></ha-icon>
              <div>
                <span class="pv-label">PV Today</span>
                <span class="pv-value">${this._fmt(pvActualToday, 1)} kWh</span>
              </div>
            </div>
            <div class="pv-item">
              <ha-icon icon="mdi:sun-clock"></ha-icon>
              <div>
                <span class="pv-label">Remaining</span>
                <span class="pv-value">${this._fmt(pvRemaining, 1)} kWh</span>
              </div>
            </div>
            <div class="pv-item">
              <ha-icon icon="mdi:weather-sunny"></ha-icon>
              <div>
                <span class="pv-label">Forecast Today</span>
                <span class="pv-value">${this._fmt(pvToday, 1)} kWh</span>
              </div>
            </div>
            <div class="pv-item">
              <ha-icon icon="mdi:weather-sunny-alert"></ha-icon>
              <div>
                <span class="pv-label">Tomorrow</span>
                <span class="pv-value">${this._fmt(pvTomorrow, 1)} kWh</span>
              </div>
            </div>
          </div>

          <!-- Strategy & Status -->
          <div class="strategy-section">
            <div class="strategy-row">
              ${this._renderStrategyControl()}
              ${this._renderEvStrategyControl(evChargerConfigured)}
            </div>
            <div class="status-bar">
              ${operationalMode ? html`<span class="status-chip mode">${operationalMode}</span>` : ''}
              ${schedulerEngine === "milp" ? html`
                <span class="status-chip engine ${schedulerActive === 'greedy_fallback' ? 'fallback' : ''}">${schedulerActive === 'milp' ? 'MILP' : schedulerActive === 'greedy_fallback' ? 'Greedy (fallback)' : 'Greedy'}</span>
              ` : ''}
              ${safeMaxKw != null ? html`
                <span class="status-chip power ${isThrottled ? 'throttled' : ''}">Active power ${this._fmt(safeMaxKw, 1)} kW</span>
              ` : ''}
              ${peakAmp != null ? html`
                <span class="status-chip amp ${isThrottled ? 'throttled' : ''}">Peak Amp. ${this._fmt(peakAmp, 0)} A</span>
              ` : ''}
              ${evBoostActive ? html`
                <span class="status-chip boost">Boost ${evBoostHours}h${evBoostMins}m</span>
              ` : ''}
            </div>
          </div>

          ${this._renderScheduleReason()}

          <!-- Advanced toggle & controls -->
          <div class="advanced-toggle" @click=${() => { this._showAdvanced = !this._showAdvanced; this.requestUpdate(); }}>
            <ha-icon icon="${this._showAdvanced ? 'mdi:chevron-up' : 'mdi:chevron-down'}"></ha-icon>
            <span>Advanced settings</span>
          </div>
          ${this._showAdvanced ? html`
          <div class="controls-section">
            <div class="controls-grid">
              ${this._renderGridModeControl(gridMode)}
              ${this._renderPriceModeControl(priceMode)}
              ${this._renderChargeMaxControl(chargeMax)}
              ${this._renderDischargeMinControl(dischargeMin)}
              ${this._renderSchedulerEngineControl()}
            </div>
          </div>
          <div class="controls-section">
            <div class="controls-grid-slider">
              ${this._renderPowerLevelControl()}
              ${this._renderPriceThresholdControl()}
            </div>
          </div>
          ` : ''}

          <!-- Info footer -->
          <div class="info-footer">
            ${safeMaxPower != null ? html`<span>Safe: ${this._fmt(safeMaxPower / 1000, 1)} kW</span>` : ""}
            ${dailyConsumptionEst != null ? html`<span>Est: ${this._fmt(dailyConsumptionEst, 1)} kWh/d</span>` : ""}
            ${weeklyConsumption != null ? html`<span>Avg: ${this._fmt(weeklyConsumption, 1)} kWh/d</span>` : ""}
          </div>
        </div>
      </ha-card>
    `;
  }

  _renderGridModeControl(current) {
    const options = ["off", "from_grid", "to_grid", "both"];
    return html`
      <div class="control-item">
        <span class="control-label">Grid Mode</span>
        <select @change=${(e) => {
          this._simOverrides = { ...this._simOverrides, gridMode: e.target.value };
          this._setSelect("grid_mode", e.target.value);
          // Manual grid_mode change → strategy becomes "custom"
          this._setSelect("ems_strategy", "custom");
          this._drawSlotTimeline();
          this.requestUpdate();
        }}>
          ${options.map((o) => html`<option value="${o}" ?selected=${o === current}>${o}</option>`)}
        </select>
      </div>
    `;
  }

  _renderSchedulerEngineControl() {
    const current = this._getState("scheduler_engine") || "greedy";
    const labels = { greedy: "Greedy (default)", milp: "Optimizer (MILP)" };
    const options = ["greedy", "milp"];
    return html`
      <div class="control-item">
        <span class="control-label">Scheduler</span>
        <select @change=${(e) => this._setSelect("scheduler_engine", e.target.value)}>
          ${options.map((o) => html`<option value="${o}" ?selected=${o === current}>${labels[o]}</option>`)}
        </select>
      </div>
    `;
  }

  _renderPriceModeControl(current) {
    const options = ["manual", "auto"];
    return html`
      <div class="control-item">
        <span class="control-label">Price Mode</span>
        <select @change=${(e) => this._setSelect("price_mode", e.target.value)}>
          ${options.map((o) => html`<option value="${o}" ?selected=${o === current}>${o}</option>`)}
        </select>
      </div>
    `;
  }

  _renderChargeMaxControl(current) {
    const options = [];
    for (let v = 100; v >= 30; v -= 5) options.push(v);
    return html`
      <div class="control-item">
        <span class="control-label">Max SOC ${current}%</span>
        <select @change=${(e) => {
          const val = parseInt(e.target.value);
          this._simOverrides = { ...this._simOverrides, chargeMax: val };
          this._setNumber("battery_charge_max_level", val);
          this._drawSlotTimeline();
          this.requestUpdate();
        }}>
          ${options.map((v) => html`<option value="${v}" ?selected=${v === current}>${v}%</option>`)}
        </select>
      </div>
    `;
  }

  _renderDischargeMinControl(current) {
    const options = [];
    for (let v = 10; v <= 70; v += 5) options.push(v);
    return html`
      <div class="control-item">
        <span class="control-label">Min SOC ${current}%</span>
        <select @change=${(e) => {
          const val = parseInt(e.target.value);
          this._simOverrides = { ...this._simOverrides, dischargeMin: val };
          this._setNumber("battery_discharge_min_level", val);
          this._drawSlotTimeline();
          this.requestUpdate();
        }}>
          ${options.map((v) => html`<option value="${v}" ?selected=${v === current}>${v}%</option>`)}
        </select>
      </div>
    `;
  }

  _renderPowerLevelControl() {
    const eid = this._getEntityId("power_level");
    if (!eid) return html``;
    const entity = this.hass.states[eid];
    if (!entity) return html``;
    const sim = this._getAttr("schedule_status", "sim_params") || {};
    const maxPower = sim.inverter_max_power_kw || 10;
    const parsedPower = parseFloat(entity.state);
    const display = this._simOverrides.powerKw ?? (isNaN(parsedPower) ? 5 : parsedPower);

    return html`
      <div class="control-item">
        <span class="control-label">Power ${display} kW</span>
        <input type="range" min="1" max="${maxPower}" step="0.5" .value=${display}
          @input=${(e) => this._previewPower(parseFloat(e.target.value))}
          @change=${(e) => this._commitPower(parseFloat(e.target.value))} />
      </div>
    `;
  }

  _renderPriceThresholdControl() {
    const eid = this._getEntityId("price_threshold_level");
    if (!eid) return html``;
    const entity = this.hass.states[eid];
    if (!entity) return html``;
    const parsedLevel = parseInt(entity.state);
    const display = this._simOverrides.priceLevel ?? (isNaN(parsedLevel) ? 5 : parsedLevel);
    // In auto price mode the threshold is computed automatically — the manual
    // level slider has no effect, so disable it.
    const autoMode = this._getState("price_mode") === "auto";

    return html`
      <div class="control-item ${autoMode ? 'disabled' : ''}">
        <span class="control-label">Price Level ${autoMode ? 'auto' : `${display}/10`}</span>
        <input type="range" min="1" max="10" step="1" .value=${display}
          ?disabled=${autoMode}
          @input=${(e) => this._previewPriceLevel(parseInt(e.target.value))}
          @change=${(e) => this._commitPriceLevel(parseInt(e.target.value))} />
      </div>
    `;
  }

  _renderStrategyControl() {
    const strategyEid = this._getEntityId("ems_strategy");
    const currentStrategy = strategyEid && this.hass.states[strategyEid]
      ? this.hass.states[strategyEid].state
      : "custom";
    const options = [
      { value: "save_money", label: "Save Money" },
      { value: "self_sufficiency", label: "Self-Sufficiency" },
      { value: "battery_care", label: "Battery Care" },
      { value: "trader", label: "Trader" },
      { value: "custom", label: "Custom" },
    ];
    // Strategy → grid_mode mapping so the card can update the local override
    // immediately (before the HA entity state roundtrip completes).
    const strategyGridMode = {
      save_money: "from_grid",
      self_sufficiency: "from_grid",
      battery_care: "from_grid",
      trader: "both",
    };
    return html`
      <div class="strategy-control">
        <span class="strategy-label">Strategy</span>
        <select class="strategy-select" @change=${(e) => {
          const val = e.target.value;
          this._setSelect("ems_strategy", val);
          // Immediately update local grid mode override so the chart and
          // advanced dropdown reflect the change without waiting for
          // the entity state roundtrip.
          const gm = strategyGridMode[val];
          if (gm) {
            this._simOverrides = { ...this._simOverrides, gridMode: gm };
            this._drawSlotTimeline();
            this.requestUpdate();
            // Clear the override once HA entity state has caught up
            setTimeout(() => {
              delete this._simOverrides.gridMode;
              this._drawSlotTimeline();
              this.requestUpdate();
            }, 3000);
          }
        }}>
          ${options.map((o) => html`
            <option value="${o.value}" ?selected=${o.value === currentStrategy}>${o.label}</option>
          `)}
        </select>
      </div>
    `;
  }

  _renderEvStrategyControl(show) {
    if (!show) return html``;
    const eid = this._getEntityId("ev_charge_strategy");
    const current = eid && this.hass.states[eid]
      ? this.hass.states[eid].state : "smart";
    const options = [
      { value: "smart", label: "EV: Smart" },
      { value: "solar_only", label: "EV: Solar only" },
      { value: "cheap_only", label: "EV: Cheapest" },
      { value: "always_on", label: "EV: Always on" },
    ];
    return html`
      <div class="strategy-control">
        <select class="strategy-select"
          title="How the EV charger is scheduled. Smart: cheap/solar/negative slots. Solar only: PV surplus. Cheapest: only at/below the price threshold. Always on: charge now, the EMS only throttles current if the grid limit is hit."
          @change=${(e) => this._setSelect("ev_charge_strategy", e.target.value)}>
          ${options.map((o) => html`
            <option value="${o.value}" ?selected=${o.value === current}>${o.label}</option>
          `)}
        </select>
      </div>
    `;
  }

  // Find the EV Boost (+1h) button entity and press it.  Each press adds an
  // hour of forced "always on" charging (the backend throttles current under
  // safe-power if the grid limit is hit).
  _pressEvBoost() {
    const eid = (this._deviceEntities || []).find(
      (e) => e.startsWith("button.") && e.includes("ev_boost") && !e.includes("cancel"),
    );
    if (!eid) return;
    this.hass.callService("button", "press", { entity_id: eid });
  }

  _cancelEvBoost() {
    const eid = (this._deviceEntities || []).find(
      (e) => e.startsWith("button.") && e.includes("ev_boost") && e.includes("cancel"),
    );
    if (!eid) return;
    this.hass.callService("button", "press", { entity_id: eid });
  }

  _renderScheduleReason() {
    const reason = this._getAttr("schedule_status", "schedule_reason");
    if (!reason) return html``;
    return html`
      <div class="schedule-reason">
        <ha-icon icon="mdi:information-outline"></ha-icon>
        <span>${reason}</span>
      </div>
    `;
  }

  // Flexible-loads panel: per-load on/off, live power draw, and the order
  // in which loads are shed when grid current gets too high.
  _renderFlexLoads(configs) {
    if (!configs || configs.length === 0) return html``;
    const evBoost = this._getAttr("schedule_status", "ev_boost_active") || false;
    const totalActive = configs.reduce((a, c) => a + (c.active_power_kw || 0), 0);
    const anyOn = configs.some((c) => c.on);

    // priority 3 = least important (shed first); 1 = most important (shed last)
    const prioInfo = (p) => {
      if (p >= 3) return { label: "Sheds 1st", cls: "prio-first" };
      if (p === 2) return { label: "Sheds 2nd", cls: "prio-mid" };
      return { label: "Sheds last", cls: "prio-last" };
    };

    return html`
      <div class="loads-panel">
        <div class="loads-panel-head">
          <span class="loads-title">
            <ha-icon icon="mdi:power-plug"></ha-icon> Flexible Loads
            <span class="loads-shed-note">· shed before battery power is reduced</span>
          </span>
          <span class="loads-total ${anyOn ? 'on' : ''}">${this._fmt(totalActive, 1)} kW now</span>
        </div>
        <div class="loads-list">
          ${configs.map((c) => {
            const max = c.max_power_kw > 0 ? c.max_power_kw : (c.active_power_kw || 1);
            const fillPct = c.on ? Math.max(6, Math.min(100, (c.active_power_kw / max) * 100)) : 0;
            const pi = prioInfo(c.priority);
            const isEvBoost = c.is_ev && evBoost;
            return html`
              <div class="load-row ${c.on ? 'active' : ''}">
                <ha-icon class="load-glyph"
                  icon="${c.is_ev ? 'mdi:ev-station' : 'mdi:power-plug-outline'}"></ha-icon>
                <div class="load-main">
                  <div class="load-line">
                    <span class="load-name">${c.name}</span>
                    ${isEvBoost ? html`<span class="load-boost">BOOST</span>` : ''}
                    <span class="load-power">${c.on ? `${this._fmt(c.active_power_kw, 1)} kW` : 'off'}</span>
                  </div>
                  <div class="load-bar"><div class="load-bar-fill" style="width:${fillPct}%"></div></div>
                  ${c.is_ev && c.on && c.current_a != null ? html`
                    <div class="load-sub">${c.current_a} A · ${c.phases}φ · ${c.voltage} V</div>
                  ` : ''}
                </div>
                <div class="load-prio ${pi.cls}"
                  title="Order loads are switched off when grid current is too high">
                  <ha-icon icon="mdi:shield-flash-outline"></ha-icon>
                  <span>${pi.label}</span>
                </div>
              </div>
            `;
          })}
        </div>
      </div>
    `;
  }

  // Live preview: update local override and redraw immediately
  _previewPower(kw) {
    this._simOverrides = { ...this._simOverrides, powerKw: kw };
    this._drawSlotTimeline();
    this.requestUpdate();
  }

  _previewPriceLevel(level) {
    this._simOverrides = { ...this._simOverrides, priceLevel: level };
    this._drawSlotTimeline();
    this.requestUpdate();
  }

  // Commit: send to HA and clear local override
  _commitPower(kw) {
    this._setNumber("power_level", kw);
    // Keep override until HA state catches up
    setTimeout(() => {
      this._simOverrides = { ...this._simOverrides };
      delete this._simOverrides.powerKw;
      this._drawSlotTimeline();
      this.requestUpdate();
    }, 2000);
  }

  _commitPriceLevel(level) {
    this._setNumber("price_threshold_level", level);
    setTimeout(() => {
      this._simOverrides = { ...this._simOverrides };
      delete this._simOverrides.priceLevel;
      this._drawSlotTimeline();
      this.requestUpdate();
    }, 2000);
  }

  // ── Styles ──────────────────────────────────────────────────

  static get styles() {
    return css`
      ha-card {
        padding: 0;
        overflow: hidden;
      }
      .card-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 10px 16px 8px;
        border-bottom: 1px solid var(--divider-color);
      }

      /* Economic Rule 1 window mismatch warning */
      .rule1-warning {
        display: flex;
        align-items: flex-start;
        gap: 8px;
        margin: 8px 12px 0;
        padding: 8px 12px;
        background: rgba(255, 152, 0, 0.12);
        border: 1px solid #FF9800;
        border-radius: 6px;
        font-size: 0.8rem;
        line-height: 1.3;
        color: var(--primary-text-color);
      }
      .rule1-warning-icon {
        flex: 0 0 auto;
        font-size: 1rem;
      }
      .rule1-warning-text {
        flex: 1 1 auto;
      }
      .ev-boost-banner {
        display: flex;
        align-items: center;
        gap: 8px;
        margin: 8px 12px 0;
        padding: 8px 12px;
        background: rgba(0, 188, 212, 0.15);
        border: 1px solid #00BCD4;
        border-radius: 6px;
        font-size: 0.85rem;
        font-weight: 500;
        color: #00BCD4;
      }
      .ev-boost-banner ha-icon {
        --mdc-icon-size: 18px;
      }
      .boost-cancel {
        margin-left: auto;
        padding: 2px 10px;
        border: 1px solid #00BCD4;
        border-radius: 4px;
        background: transparent;
        color: #00BCD4;
        font-size: 0.8rem;
        font-weight: 600;
        cursor: pointer;
        white-space: nowrap;
      }
      .boost-cancel:hover {
        background: rgba(0, 188, 212, 0.25);
      }

      /* Flexible loads panel */
      .loads-panel {
        margin-bottom: 14px;
        padding: 10px;
        border-radius: 8px;
        background: var(--secondary-background-color);
      }
      .loads-panel-head {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 8px;
      }
      .loads-title {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 0.82rem;
        font-weight: 600;
        color: var(--primary-text-color);
      }
      .loads-title ha-icon {
        --mdc-icon-size: 18px;
        color: #00BCD4;
      }
      .loads-total {
        font-size: 0.8rem;
        font-weight: 600;
        color: var(--secondary-text-color);
        font-variant-numeric: tabular-nums;
      }
      .loads-total.on {
        color: #00BCD4;
      }
      .loads-list {
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .load-row {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 6px 8px;
        border-radius: 6px;
        background: var(--card-background-color);
        border: 1px solid transparent;
        opacity: 0.6;
        transition: opacity 0.3s ease, box-shadow 0.3s ease;
      }
      .load-row.active {
        opacity: 1;
        border-color: rgba(0, 188, 212, 0.5);
        box-shadow: 0 0 6px rgba(0, 188, 212, 0.22);
      }
      .load-glyph {
        --mdc-icon-size: 22px;
        flex: 0 0 auto;
        color: var(--secondary-text-color);
      }
      .load-row.active .load-glyph {
        color: #00BCD4;
      }
      .load-main {
        flex: 1 1 auto;
        min-width: 0;
      }
      .load-line {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 0.8rem;
      }
      .load-name {
        font-weight: 500;
        color: var(--primary-text-color);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .load-power {
        margin-left: auto;
        color: var(--secondary-text-color);
        font-variant-numeric: tabular-nums;
      }
      .load-row.active .load-power {
        color: #00BCD4;
        font-weight: 600;
      }
      .load-boost {
        font-size: 0.6rem;
        font-weight: 700;
        letter-spacing: 0.5px;
        padding: 1px 4px;
        border-radius: 3px;
        background: #00BCD4;
        color: #00282d;
      }
      .load-bar {
        margin-top: 4px;
        height: 5px;
        border-radius: 3px;
        background: rgba(150, 150, 150, 0.25);
        overflow: hidden;
      }
      .load-bar-fill {
        height: 100%;
        border-radius: 3px;
        background: linear-gradient(90deg, #00BCD4, #4CAF50);
        transition: width 0.4s ease;
      }
      .load-sub {
        margin-top: 3px;
        font-size: 0.68rem;
        color: var(--secondary-text-color);
        font-variant-numeric: tabular-nums;
      }
      .load-prio {
        flex: 0 0 auto;
        display: flex;
        align-items: center;
        gap: 3px;
        font-size: 0.66rem;
        font-weight: 600;
        padding: 2px 6px;
        border-radius: 10px;
        white-space: nowrap;
      }
      .load-prio ha-icon {
        --mdc-icon-size: 13px;
      }
      .load-prio.prio-first {
        color: #ef5350;
        background: rgba(239, 83, 80, 0.12);
      }
      .load-prio.prio-mid {
        color: #FFB74D;
        background: rgba(255, 152, 0, 0.12);
      }
      .load-prio.prio-last {
        color: #66BB6A;
        background: rgba(76, 175, 80, 0.12);
      }
      .loads-shed-note {
        font-size: 0.66rem;
        font-weight: 400;
        color: var(--secondary-text-color);
        opacity: 0.8;
      }

      /* Battery SOC indicator */
      .battery-indicator {
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .battery-bars {
        display: flex;
        gap: 0.5px;
        align-items: center;
      }
      .battery-bars .bar {
        width: 1.5px;
        height: 13px;
        border: 1px solid rgba(255, 255, 255, 0.5);
        border-radius: 1px;
        background: transparent;
      }
      .battery-bars .bar.filled {
        background: #4CAF50;
      }
      .battery-bars .bar.filled.low {
        background: #F44336;
      }
      .battery-bars .bar.filled.mid {
        background: #FF9800;
      }
      .battery-text {
        display: flex;
        flex-direction: column;
        line-height: 1.05;
        font-size: 0.44em;
        color: var(--secondary-text-color);
        white-space: nowrap;
      }
      .battery-text .battery-soc {
        font-weight: 600;
        color: var(--primary-text-color);
      }

      .status-badges {
        display: flex;
        gap: 4px;
      }
      .badge {
        font-size: 0.4em;
        padding: 2px 6px;
        border-radius: 8px;
        text-transform: uppercase;
        font-weight: 600;
        letter-spacing: 0.3px;
        white-space: nowrap;
      }
      .badge.charging { background: #4CAF50; color: #fff; }
      .badge.discharging { background: #FF9800; color: #fff; }
      .badge.idle { background: var(--secondary-background-color); color: var(--secondary-text-color); }
      .badge.unknown { background: var(--secondary-background-color); color: var(--secondary-text-color); }
      .badge.schedule-active { background: #2196F3; color: #fff; }
      .badge.schedule-waiting { background: #607D8B; color: #fff; }
      .badge.schedule-manual { background: #9C27B0; color: #fff; }
      .badge.schedule-off { background: var(--secondary-background-color); color: var(--secondary-text-color); }
      .badge.schedule-no_action_needed,
      .badge.schedule-no\\ action\\ needed { background: var(--secondary-background-color); color: var(--secondary-text-color); }

      .card-content {
        padding: 12px 16px 16px;
      }

      /* Summary row */
      .summary-row {
        display: flex;
        justify-content: space-between;
        margin-bottom: 12px;
      }
      .summary-item {
        display: flex;
        flex-direction: column;
        align-items: center;
      }
      .summary-item .label {
        font-size: 0.75em;
        color: var(--secondary-text-color);
        text-transform: uppercase;
      }
      .summary-item .value {
        font-size: 0.95em;
        font-weight: 500;
      }
      .value.price { color: #FFD600; }
      .likelihood-on_track { color: #4CAF50; }
      .likelihood-tight { color: #FF9800; }
      .likelihood-at_risk { color: #F44336; }
      .likelihood-insufficient { color: #F44336; font-weight: 700; }

      /* Timeline */
      .timeline-container {
        margin-bottom: 12px;
      }
      .timeline-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 4px;
      }
      .timeline-label {
        font-size: 0.8em;
        color: var(--secondary-text-color);
      }
      .timeline-toggle {
        display: flex;
        gap: 0;
        border: 1px solid var(--divider-color);
        border-radius: 12px;
        overflow: hidden;
        cursor: pointer;
      }
      .toggle-btn {
        font-size: 0.7em;
        padding: 2px 10px;
        color: var(--secondary-text-color);
        transition: all 0.2s;
      }
      .toggle-btn.active {
        background: var(--primary-color);
        color: #fff;
        font-weight: 600;
      }
      .toggle-btn.disabled {
        opacity: 0.35;
        cursor: default;
        pointer-events: none;
      }
      #slot-timeline {
        width: 100%;
        height: 120px;
        border-radius: 6px;
        background: var(--secondary-background-color);
      }
      .override-bar {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 3px 8px;
        margin-top: 2px;
        background: rgba(255, 255, 255, 0.08);
        border-radius: 4px;
        font-size: 0.7em;
      }
      .override-hint {
        color: var(--secondary-text-color);
        font-style: italic;
      }
      .override-clear {
        color: #ff6b6b;
        cursor: pointer;
        font-weight: 600;
        padding: 1px 6px;
        border: 1px solid #ff6b6b;
        border-radius: 4px;
      }
      .override-clear:hover {
        background: rgba(255, 107, 107, 0.2);
      }

      /* Stats row */
      .stats-row {
        display: flex;
        justify-content: space-around;
        margin-bottom: 12px;
        flex-wrap: wrap;
        gap: 4px;
      }
      .stat {
        display: flex;
        align-items: center;
        gap: 4px;
        font-size: 0.8em;
        color: var(--secondary-text-color);
      }
      .stat ha-icon {
        --mdc-icon-size: 16px;
        color: var(--secondary-text-color);
      }

      /* PV row */
      .pv-row {
        display: flex;
        flex-wrap: nowrap;
        justify-content: space-between;
        gap: 4px;
        margin-bottom: 14px;
        padding: 6px 8px;
        border-radius: 8px;
        background: var(--secondary-background-color);
      }
      .pv-item {
        display: flex;
        align-items: center;
        gap: 4px;
        min-width: 0;
      }
      .pv-item ha-icon {
        --mdc-icon-size: 17px;
        color: #FFD600;
        flex: 0 0 auto;
      }
      .pv-label {
        display: block;
        font-size: 0.62em;
        color: var(--secondary-text-color);
        white-space: nowrap;
      }
      .pv-value {
        display: block;
        font-size: 0.82em;
        font-weight: 500;
        white-space: nowrap;
        font-variant-numeric: tabular-nums;
      }

      /* Controls */
      .controls-section {
        border-top: 1px solid var(--divider-color);
        padding-top: 10px;
        margin-bottom: 8px;
      }
      .controls-label {
        font-size: 0.8em;
        color: var(--secondary-text-color);
        margin-bottom: 8px;
        text-transform: uppercase;
      }
      .controls-grid {
        display: grid;
        grid-template-columns: 1fr 1fr 1fr 1fr;
        gap: 8px;
      }
      .controls-grid-slider {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px;
      }
      .control-item {
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .control-label {
        font-size: 0.70em;
        color: var(--secondary-text-color);
      }
      .control-item select,
      .control-item input[type="range"] {
        width: 100%;
        padding: 4px;
        border: 1px solid var(--divider-color);
        border-radius: 4px;
        background: var(--secondary-background-color);
        color: var(--primary-text-color);
        font-size: 0.75em;
      }
      .control-item input[type="range"] {
        padding: 0;
        height: 24px;
        accent-color: var(--primary-color);
      }

      /* Strategy section */
      .strategy-section {
        margin-bottom: 8px;
      }
      .strategy-control {
        display: flex;
        align-items: center;
        gap: 10px;
      }
      .strategy-label {
        font-size: 0.85em;
        font-weight: 500;
        color: var(--primary-text-color);
        white-space: nowrap;
      }
      .strategy-select {
        flex: 1;
        padding: 6px 8px;
        border: 1px solid var(--divider-color);
        border-radius: 6px;
        background: var(--secondary-background-color);
        color: var(--primary-text-color);
        font-size: 0.85em;
        font-weight: 500;
      }
      .status-bar {
        display: flex;
        align-items: center;
        gap: 6px;
        margin-top: 6px;
        flex-wrap: wrap;
      }
      .status-chip {
        font-size: 0.64em;
        font-weight: 600;
        padding: 2px 8px;
        border-radius: 10px;
        white-space: nowrap;
        letter-spacing: 0.2px;
        text-transform: capitalize;
      }
      .status-chip.mode {
        background: rgba(100, 140, 200, 0.18);
        color: var(--primary-text-color);
      }
      .status-chip.power,
      .status-chip.amp {
        background: rgba(76, 175, 80, 0.16);
        color: #4CAF50;
        font-variant-numeric: tabular-nums;
        text-transform: none;
      }
      .status-chip.power.throttled,
      .status-chip.amp.throttled {
        background: rgba(244, 67, 54, 0.18);
        color: #ef5350;
      }
      .status-chip.engine {
        background: rgba(156, 39, 176, 0.16);
        color: #AB47BC;
        text-transform: none;
      }
      .status-chip.engine.fallback {
        background: rgba(255, 152, 0, 0.18);
        color: #FF9800;
      }
      .status-chip.boost {
        background: rgba(0, 188, 212, 0.18);
        color: #00BCD4;
        text-transform: none;
      }

      /* Strategy dropdown row + EV override button */
      .strategy-row {
        display: flex;
        gap: 8px;
        align-items: center;
      }
      .strategy-row .strategy-control {
        flex: 1;
      }
      .override-btn {
        display: flex;
        align-items: center;
        gap: 3px;
        padding: 2px 6px;
        border: 1px solid #00BCD4;
        border-radius: 8px;
        background: transparent;
        color: #00BCD4;
        font-size: 0.4em;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.3px;
        cursor: pointer;
        white-space: nowrap;
      }
      .override-btn:hover {
        background: rgba(0, 188, 212, 0.12);
      }
      .override-btn.active {
        background: #00BCD4;
        color: #00282d;
      }
      .override-btn ha-icon {
        --mdc-icon-size: 11px;
      }
      .control-item.disabled {
        opacity: 0.45;
      }
      .control-item.disabled input[type="range"] {
        cursor: not-allowed;
      }

      /* Schedule reason ("why" line) */
      .schedule-reason {
        display: flex;
        align-items: flex-start;
        gap: 6px;
        padding: 6px 10px;
        margin-bottom: 8px;
        background: rgba(var(--rgb-primary-color, 33, 150, 243), 0.08);
        border-radius: 6px;
        font-size: 0.78em;
        line-height: 1.35;
        color: var(--primary-text-color);
      }
      .schedule-reason ha-icon {
        --mdc-icon-size: 16px;
        color: var(--primary-color);
        flex: 0 0 auto;
        margin-top: 1px;
      }

      /* Advanced toggle */
      .advanced-toggle {
        display: flex;
        align-items: center;
        gap: 4px;
        padding: 4px 0;
        cursor: pointer;
        font-size: 0.75em;
        color: var(--secondary-text-color);
        user-select: none;
      }
      .advanced-toggle:hover {
        color: var(--primary-text-color);
      }
      .advanced-toggle ha-icon {
        --mdc-icon-size: 16px;
      }

      /* Info footer */
      .info-footer {
        display: flex;
        justify-content: space-between;
        flex-wrap: wrap;
        gap: 4px;
        font-size: 0.7em;
        color: var(--secondary-text-color);
        padding-top: 6px;
        border-top: 1px solid var(--divider-color);
      }
    `;
  }
}

// ── Editor ──────────────────────────────────────────────────

class FelicityEMSCardEditor extends LitElement {
  static get properties() {
    return {
      hass: {},
      _config: {},
    };
  }

  setConfig(config) {
    this._config = config;
  }

  render() {
    if (!this.hass || !this._config) return html``;

    return html`
      <div class="editor">
        <ha-textfield
          label="Card Name"
          .value=${this._config.name || ""}
          @change=${(e) => this._valueChanged("name", e.target.value)}
        ></ha-textfield>

        <ha-selector
          .hass=${this.hass}
          .selector=${{ device: { integration: "ha_felicity" } }}
          .value=${this._config.device_id || ""}
          .label=${"Device"}
          @value-changed=${(e) => this._valueChanged("device_id", e.detail.value)}
        ></ha-selector>

        <ha-textfield
          label="Currency Symbol"
          .value=${this._config.currency || "\u20AC"}
          @change=${(e) => this._valueChanged("currency", e.target.value)}
        ></ha-textfield>

        <div style="display:flex;align-items:center;margin-top:12px;">
          <ha-checkbox
            .checked=${this._config.generator_as_pv !== false}
            @change=${(e) => this._valueChanged("generator_as_pv", e.target.checked)}
          ></ha-checkbox>
          <label style="font-size:0.9em;cursor:pointer;"
            @click=${() => {
              const cur = this._config.generator_as_pv !== false;
              this._valueChanged("generator_as_pv", !cur);
            }}
          >Treat generator port as PV (micro-inverter solar)</label>
        </div>
      </div>
    `;
  }

  _valueChanged(field, value) {
    const newConfig = { ...this._config, [field]: value };
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: newConfig },
        bubbles: true,
        composed: true,
      })
    );
  }

  static get styles() {
    return css`
      .editor {
        display: flex;
        flex-direction: column;
        gap: 12px;
        padding: 16px;
      }
    `;
  }
}

customElements.define("felicity-ems-card", FelicityEMSCard);
customElements.define("felicity-ems-card-editor", FelicityEMSCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "felicity-ems-card",
  name: "Felicity EMS Card",
  description: "Energy Management System \u2014 slot timeline, schedule, and controls",
  preview: true,
});
