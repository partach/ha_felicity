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
      this._drawSlotTimeline();
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
    const efficiency = sim.efficiency || 0.90;
    const batterySoc = sim.battery_soc_pct;
    const netPv = sim.net_pv_kwh || 0;
    const yesterdayDeficit = this._getAttr("schedule_status", "yesterday_deficit_kwh") || 0;

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

    const result = { slots: slotData.map(s => ({ ...s, action: null })), chargeCount: 0, dischargeCount: 0, planned: 0, tomorrowChargeCount: 0, tomorrowPlanned: 0, threshold };

    if (gridMode === "from_grid" || gridMode === "both") {
      // Solar-first: target = min SOC floor + overnight reserve.
      // After overnight drain the battery should still be at min SOC.
      const minKwh = dischargeMin * batteryCapacity;
      const reserveKwh = parseFloat(this._getAttr("schedule_status", "self_consumption_reserve")) || 0;
      const reserveTarget = Math.min(batteryCapacity, minKwh + reserveKwh);
      const shortfall = Math.max(0, reserveTarget - currentKwh);
      let deficit = Math.max(0, shortfall - netPv);
      if (yesterdayDeficit > 0 && shortfall > deficit) {
        deficit += Math.min(yesterdayDeficit, shortfall - deficit);
      }

      // Unified slot selection: combine today + tomorrow into one pool
      // and pick the cheapest slots from both days together.
      let tomorrowDeficit = 0;
      const tomorrowSlotData = this._tomorrowSlotData;
      const hasTomorrow = tomorrowSlotData && tomorrowSlotData.length > 0;
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

        const tmrReserveTarget = Math.min(batteryCapacity, minKwh + tmrReserve);
        // Daytime gap: on low-PV days battery drains during the day too
        const daytimeGap = Math.max(0, consumption - pvTmr);
        // grid_charge >= reserve_target + daytime_gap - projected_midnight
        const tmrShortfall = Math.max(0, tmrReserveTarget + daytimeGap - projectedMidnight);
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
        const headroom = Math.max(0, chargeMax * batteryCapacity - currentKwh);
        const maxTodaySlots = Math.max(
          effectivePerSlot > 0 ? Math.floor(headroom / effectivePerSlot) : 0,
          effectivePerSlot > 0 && deficit > 0 ? Math.ceil(deficit / effectivePerSlot) : 0
        );
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
          const projected = currentKwh + netPv + todayChargeEnergy - bridgeConsumption;

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
      const reserveTarget = Math.min(batteryCapacity, minKwh + reserveKwh);
      const sellable = Math.max(0, currentKwh - reserveTarget) * efficiency;
      const roundTrip = efficiency * efficiency;

      if (sellable > 0) {
        const chargeIdxSet = new Set(result.slots.filter(s => s.action === "charge").map(s => s.slot));
        let available = remaining.filter(s => s.price > 0 && !chargeIdxSet.has(s.idx));

        if (gridMode === "both" && result.chargeCount > 0) {
          const maxBuy = Math.max(...result.slots.filter(s => s.action === "charge").map(s => s.price));
          const minSell = maxBuy / roundTrip;
          available = available.filter(s => s.price >= minSell);
        }

        available.sort((a, b) => b.price - a.price);
        const needed = energyPerSlot > 0 ? Math.ceil(sellable / energyPerSlot) : 0;
        const sellSlots = available.slice(0, needed);

        for (const s of sellSlots) {
          result.slots[s.idx].action = "discharge";
        }
        result.dischargeCount = sellSlots.length;
        result.planned += Math.min(sellable, sellSlots.length * energyPerSlot);

        if (sellSlots.length && threshold == null) {
          threshold = Math.min(...sellSlots.map(s => s.price));
        }
      }
    }

    result.planned = Math.round(result.planned * 100) / 100;
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

    // Fallback: read raw prices from source Nordpool entity if schedule_status has no slot data
    if (!todaySlotData || !todaySlotData.length) {
      todaySlotData = this._getRawPriceSlots("today");
      if (todaySlotData) granularity = Math.round((24 * 60) / todaySlotData.length);
    }
    if (!tomorrowSlotData || !tomorrowSlotData.length) {
      tomorrowSlotData = this._getRawPriceSlots("tomorrow");
    }

    // Determine which data to show: today or tomorrow (fallback)
    const now = new Date();
    const currentSlotIdx = Math.floor((now.getHours() * 60 + now.getMinutes()) / granularity);

    // Store tomorrow data for unified simulation access
    this._tomorrowSlotData = tomorrowSlotData;

    // Run client-side simulation on today's data (uses _tomorrowSlotData internally)
    const simResult = this._simulateSchedule(todaySlotData, this._simOverrides);
    this._simResult = simResult;

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

    // For tomorrow, run a forecast simulation too (no current-slot offset)
    // Keep today's sim for unified stats (tomorrowChargeCount, tomorrowPlanned)
    this._todaySimResult = simResult;
    let displayData, displayThreshold;
    if (showTomorrow) {
      const tmrSim = this._simulateScheduleTomorrow(tomorrowSlotData, this._simOverrides);
      displayData = tmrSim?.slots ?? tomorrowSlotData;
      displayThreshold = tmrSim?.threshold;
      this._simResult = tmrSim;  // stats reflect tomorrow preview
    } else {
      displayData = simResult?.slots ?? todaySlotData;
      displayThreshold = simResult?.threshold;
    }

    // Track what's actually displayed for toggle button state
    this._showingTomorrow = showTomorrow;
    this._hasTomorrowData = hasTomorrow;

    // Update the timeline label
    const label = this.shadowRoot?.querySelector(".timeline-label");
    if (label) {
      label.textContent = showTomorrow ? "Tomorrow\u2019s Forecast" : "Today\u2019s Schedule";
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
    const barW = Math.max(1, (w - 40) / numSlots);
    const marginLeft = 30;
    const marginTop = 15;
    const marginBottom = 20;
    const chartH = h - marginTop - marginBottom;

    // Find price range
    const prices = displayData.map((s) => s.price).filter((p) => p != null);
    const minPrice = Math.min(...prices, 0);
    const maxPrice = Math.max(...prices, 0.01);
    const range = maxPrice - minPrice || 0.01;

    // Current time marker (only for today view)
    const currentSlot = showTomorrow ? -1 : currentSlotIdx;

    // Draw bars
    for (let i = 0; i < numSlots; i++) {
      const slot = displayData[i];
      const x = marginLeft + i * barW;
      const price = slot.price ?? 0;
      const barH = ((price - minPrice) / range) * chartH;
      const y = marginTop + chartH - barH;

      if (showTomorrow) {
        // Tomorrow: color by simulated action
        if (slot.action === "charge") {
          ctx.fillStyle = "rgba(76, 175, 80, 0.6)"; // green, slightly softer
        } else if (slot.action === "discharge") {
          ctx.fillStyle = "rgba(255, 152, 0, 0.6)"; // orange, slightly softer
        } else if (price < 0) {
          ctx.fillStyle = "rgba(33, 150, 243, 0.6)";
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
          ctx.fillStyle = isPast ? "rgba(150, 150, 150, 0.2)" : "#4CAF50";
        } else if (slot.action === "discharge") {
          ctx.fillStyle = isPast ? "rgba(150, 150, 150, 0.2)" : "#FF9800";
        } else if (price < 0) {
          ctx.fillStyle = isPast ? "rgba(33, 150, 243, 0.3)" : "#2196F3";
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
        ctx.strokeStyle = "#FFF";
        ctx.lineWidth = 2;
        ctx.strokeRect(x, marginTop, barW, chartH);
      }
    }

    // Threshold line: use simulated threshold, fall back to entity value
    const threshold = displayThreshold ?? this._getNumericState("price_threshold");
    if (threshold != null && threshold >= minPrice && threshold <= maxPrice) {
      const thresholdY = marginTop + chartH - ((threshold - minPrice) / range) * chartH;
      ctx.strokeStyle = "#F44336";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.moveTo(marginLeft, thresholdY);
      ctx.lineTo(w - 10, thresholdY);
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.fillStyle = "#F44336";
      ctx.font = "9px sans-serif";
      ctx.textAlign = "right";
      ctx.fillText(`${threshold.toFixed(2)}`, marginLeft - 2, thresholdY + 3);
    }

    // Zero line (if negative prices exist)
    if (minPrice < 0) {
      const zeroY = marginTop + chartH - ((0 - minPrice) / range) * chartH;
      ctx.strokeStyle = "rgba(255,255,255,0.3)";
      ctx.lineWidth = 0.5;
      ctx.beginPath();
      ctx.moveTo(marginLeft, zeroY);
      ctx.lineTo(w - 10, zeroY);
      ctx.stroke();
    }

    // Y-axis labels
    ctx.fillStyle = getComputedStyle(this).getPropertyValue("--secondary-text-color") || "#aaa";
    ctx.font = "9px sans-serif";
    ctx.textAlign = "right";
    ctx.fillText(maxPrice.toFixed(2), marginLeft - 2, marginTop + 8);
    ctx.fillText(minPrice.toFixed(2), marginLeft - 2, marginTop + chartH);

    // X-axis hour labels
    ctx.textAlign = "center";
    ctx.fillStyle = getComputedStyle(this).getPropertyValue("--secondary-text-color") || "#aaa";
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
    const legendX = w - 5;
    const textColor = getComputedStyle(this).getPropertyValue("--primary-text-color") || "#fff";
    ctx.fillStyle = "#4CAF50";
    ctx.fillRect(legendX - 55, 2, 8, 8);
    ctx.fillStyle = textColor;
    ctx.fillText("charge", legendX - 58 + 40, 9);
    ctx.fillStyle = "#FF9800";
    ctx.fillRect(legendX - 115, 2, 8, 8);
    ctx.fillStyle = textColor;
    ctx.textAlign = "right";
    ctx.fillText("sell", legendX - 118 + 30, 9);
    if (showTomorrow) {
      ctx.fillStyle = "rgba(100, 140, 200, 0.35)";
      ctx.fillRect(legendX - 168, 2, 8, 8);
      ctx.fillStyle = textColor;
      ctx.textAlign = "right";
      ctx.fillText("idle", legendX - 171 + 30, 9);
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
    const efficiency = sim.efficiency || 0.90;
    const pvTomorrow = this._getNumericState("pv_forecast_tomorrow") || 0;
    const consumption = sim.consumption_est_kwh || 10;

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
    const todayPlanned = this._simResult?.planned || 0;
    const hoursToMidnight = Math.max(1, 24 - new Date().getHours());
    const drainToMidnight = (consumption / 24) * hoursToMidnight;
    // Battery at midnight = current + today's planned charging - drain to midnight
    // Clamp between min_kwh and max battery
    const minKwh = dischargeMin * batteryCapacity;
    const projectedMidnight = Math.max(minKwh, Math.min(batteryCapacity, currentKwh + todayPlanned - drainToMidnight));
    const startKwh = projectedMidnight;
    // Overnight reserve: estimate hours from sunset to next sunrise using today's pattern
    const reserveKwh = parseFloat(this._getAttr("schedule_status", "self_consumption_reserve")) || (consumption / 24 * 12);
    const reserveTarget = Math.min(batteryCapacity, dischargeMin * batteryCapacity + reserveKwh);
    // Daytime gap: on low-PV days battery drains during the day too
    const daytimeGap = Math.max(0, consumption - pvTomorrow);

    // All slots are "future"
    const remaining = slotData
      .filter(s => s.price != null)
      .map(s => ({ idx: s.slot, price: s.price }));

    if (gridMode === "off" || !remaining.length) {
      return { slots: this._markAll(slotData, null), chargeCount: 0, dischargeCount: 0, planned: 0, threshold: null };
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
        const deficit = shortfall;
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
      // For selling tomorrow: estimate available energy after solar charges the battery
      // Battery fills from startKwh + pvTomorrow, capped at charge_max
      const projectedKwh = Math.min(chargeMax * batteryCapacity, startKwh + pvTomorrow);
      const sellable = Math.max(0, projectedKwh - reserveTarget) * efficiency;
      const roundTrip = efficiency * efficiency;

      if (sellable > 0) {
        const chargeIdxSet = new Set(result.slots.filter(s => s.action === "charge").map(s => s.slot));
        let available = remaining.filter(s => s.price > 0 && !chargeIdxSet.has(s.idx));
        if (gridMode === "both" && result.chargeCount > 0) {
          const maxBuy = Math.max(...result.slots.filter(s => s.action === "charge").map(s => s.price));
          available = available.filter(s => s.price >= maxBuy / roundTrip);
        }
        available.sort((a, b) => b.price - a.price);
        const needed = energyPerSlot > 0 ? Math.ceil(sellable / energyPerSlot) : 0;
        const sellSlots = available.slice(0, needed);
        for (const s of sellSlots) result.slots[s.idx].action = "discharge";
        result.dischargeCount = sellSlots.length;
        result.planned += Math.min(sellable, sellSlots.length * energyPerSlot);
        if (sellSlots.length && threshold == null) threshold = Math.min(...sellSlots.map(s => s.price));
      }
    }

    result.planned = Math.round(result.planned * 100) / 100;
    result.threshold = threshold;
    return result;
  }

  _toggleDayView(e) {
    // Ignore clicks on the disabled tomorrow button
    if (e?.target?.classList?.contains("disabled")) return;
    if (this._viewTomorrow) {
      this._viewTomorrow = false;
    } else {
      this._viewTomorrow = true;
    }
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
    const gridMode = this._getState("grid_mode") || "off";
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
        const genEnergy = this._getNumericState("generator_day_cost_energy");
        if (genEnergy != null && genEnergy > 0) {
          pvActualToday = genEnergy;
        } else {
          pvActualToday = pvSum;
        }
      } else {
        pvActualToday = pvSum;
      }
    }
    // Generator-port solar: if backend attr returned near-zero but generator has energy
    if (this.config.generator_as_pv && (pvActualToday == null || pvActualToday < 0.1)) {
      const genEnergy = this._getNumericState("generator_day_cost_energy");
      if (genEnergy != null && genEnergy > 0) {
        pvActualToday = genEnergy;
      }
    }
    const reserve = this._getAttr("schedule_status", "self_consumption_reserve")
      ?? this._getAttr("energy_state", "self_consumption_reserve");
    const weeklyConsumption = this._getNumericState("weekly_avg_consumption");
    const safeMaxPower = this._getNumericState("safe_max_power");
    const dailyConsumptionEst = this._getNumericState("daily_consumption_estimate");

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
            <span class="battery-text">${this._fmt(socPct, 0)}% / ${batteryCapacity} kWh</span>
          </div>
          <div class="status-badges">
            <span class="badge ${energyState}">${energyState}</span>
            <span class="badge schedule-${scheduleStatus}">${scheduleStatus.replace(/_/g, ' ')}</span>
          </div>
        </div>

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
              <ha-icon icon="mdi:shield-sun"></ha-icon>
              <span>${this._fmt(reserve, 1)} kWh reserve</span>
            </div>
          </div>

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

          <!-- Controls -->
          <div class="controls-section">
            <div class="controls-grid">
              ${this._renderGridModeControl(gridMode)}
              ${this._renderPriceModeControl(priceMode)}
              ${this._renderChargeMaxControl(chargeMax)}
              ${this._renderDischargeMinControl(dischargeMin)}
            </div>
          </div>
          <div class="controls-section">
            <div class="controls-grid-slider">
              ${this._renderPowerLevelControl()}
              ${this._renderPriceThresholdControl()}
            </div>
          </div>

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
          this._drawSlotTimeline();
          this.requestUpdate();
        }}>
          ${options.map((o) => html`<option value="${o}" ?selected=${o === current}>${o}</option>`)}
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
    const display = this._simOverrides.powerKw ?? parseFloat(entity.state) ?? 5;

    return html`
      <div class="control-item">
        <span class="control-label">Power ${display} kW</span>
        <input type="range" min="1" max="10" step="0.5" .value=${display}
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
    const display = this._simOverrides.priceLevel ?? parseInt(entity.state) ?? 5;

    return html`
      <div class="control-item">
        <span class="control-label">Price Level ${display}/10</span>
        <input type="range" min="1" max="10" step="1" .value=${display}
          @input=${(e) => this._previewPriceLevel(parseInt(e.target.value))}
          @change=${(e) => this._commitPriceLevel(parseInt(e.target.value))} />
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

      /* Battery SOC indicator */
      .battery-indicator {
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .battery-bars {
        display: flex;
        gap: 2px;
        align-items: center;
      }
      .battery-bars .bar {
        width: 4px;
        height: 14px;
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
        font-size: 0.5em;
        color: var(--secondary-text-color);
        white-space: nowrap;
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
        flex-wrap: wrap;
        justify-content: space-around;
        gap: 8px 0;
        margin-bottom: 14px;
        padding: 8px;
        border-radius: 8px;
        background: var(--secondary-background-color);
      }
      .pv-item {
        display: flex;
        align-items: center;
        gap: 6px;
      }
      .pv-item ha-icon {
        --mdc-icon-size: 20px;
        color: #FFD600;
      }
      .pv-label {
        display: block;
        font-size: 0.7em;
        color: var(--secondary-text-color);
      }
      .pv-value {
        display: block;
        font-size: 0.9em;
        font-weight: 500;
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
