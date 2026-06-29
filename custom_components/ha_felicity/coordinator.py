"""Data update coordinator for Felicity with proper async handling."""

import asyncio
import dataclasses
import json
import logging
import math
import time
from datetime import timedelta, datetime
from typing import Dict, Any
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pymodbus.exceptions import ModbusException, ConnectionException
from .const import (
    DOMAIN, INVERTER_MODEL_TREX_TEN, CONF_INVERTER_MODEL,
    DEFAULT_INVERTER_MODEL, INVERTER_MAX_POWER_KW,
    INVERTER_MODEL_TREX_FIVE,
    INVERTER_MODEL_TREX_TWENTY_FIVE, INVERTER_MODEL_TREX_FIFTY,
)
from .type_specific import TypeSpecificHandler
from . import ems as ems_module

_LOGGER = logging.getLogger(__name__)

# Reduce noise from pymodbus
# Setting parent logger to CRITICAL to catch all sub-loggers
logging.getLogger("pymodbus").setLevel(logging.CRITICAL)
logging.getLogger("pymodbus.logging").setLevel(logging.CRITICAL)

class HA_FelicityCoordinator(DataUpdateCoordinator):
    """Felicity Solar Inverter Data Update Coordinator."""

    def __init__(
        self, 
        hass: HomeAssistant, 
        client: Any, 
        slave_id: int, 
        register_map: dict, 
        groups: list,
        model_combined: dict,
        inverter_model: str,
        config_entry: ConfigEntry,
        nordpool_entity: str | None = None,
        nordpool_override: str | None = None,
        forecast_entity: str | None = None,
        consumption_override_entity: str | None = None,
        update_interval: int = 10,
    ):
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Felicity",
            update_interval=timedelta(seconds=update_interval),
        )
        self.client = client
        self.slave_id = slave_id
        self.register_map = register_map
        self._address_groups = groups
        self.config_entry = config_entry
        self._last_register_set: str | None = None
        self.model_combined = model_combined
        self.inverter_model = inverter_model if inverter_model else INVERTER_MODEL_TREX_TEN
        self._inverter_max_power_kw = INVERTER_MAX_POWER_KW.get(self.inverter_model, 10)
        self.TypeSpecificHandler = TypeSpecificHandler(client=self.client, slave_id=self.slave_id, inverter_model=self.inverter_model, register_map=self.register_map)
        
        # Nordpool: override wins over entity
        self.nordpool_entity = nordpool_override or nordpool_entity
        self.original_nordpool_entity = nordpool_entity
        self.override_nordpool_entity = nordpool_override
        
        # Integration version (manifest), set by __init__ after construction;
        # surfaced in the EMS card footer.
        self.integration_version: str | None = None

        # Runtime state
        self.connected = False
        self._current_energy_state: str | None = None
        self._last_state_change: datetime | None = None
        self._current_day: int | None = None

        # Minimum charge commitment (anti flip-flop).  When the battery is
        # near the reserve target the schedule's marginal deficit can
        # oscillate in/out of "charge" every tick, producing a charge→off
        # storm that hammers the grid current (seconds-scale toggling).
        # Once charging starts we commit to it until SOC rises a meaningful
        # amount OR a minimum duration passes — so each charge episode is a
        # real block, never a micro-burst.
        self._charge_commit_start_soc: float | None = None
        self._charge_commit_until_ts: float = 0.0

        # Price tracking
        self.current_price: float | None = None
        self.max_price: float | None = None
        self.min_price: float | None = None
        self.avg_price: float | None = None
        self.price_threshold: float | None = None
        self.safe_max_power = 0 # used in setting rule 1 power checks toward max amperage
        self._last_known_max_amperage: float | None = None
        self.battery_soc: float | None = None  # resolved SOC (type-specific)
        # SOC history: {slot_index: soc_pct} for past slots today
        self._soc_history: dict[int, float] = {}
        self._last_recorded_slot: int = -1

        # Forecast & schedule
        self.forecast_entity = forecast_entity
        self.slot_prices_today: list | None = None
        self.slot_prices_tomorrow: list | None = None
        self.pv_forecast_today: float | None = None
        self.pv_forecast_remaining: float | None = None
        self.pv_forecast_tomorrow: float | None = None
        self.pv_hourly_kwh: dict[int, float] = {}  # {hour: kwh} from forecast entity
        self.pv_hourly_kwh_tomorrow: dict[int, float] = {}  # tomorrow's hourly PV
        self.scheduled_slots: dict[int, str] = {}  # {slot_idx: "charge" | "discharge"}
        self.slot_overrides: dict = config_entry.options.get("slot_overrides", {})  # manual overrides from card, persisted in entry.options
        self.cheap_slots_remaining: int = 0
        self.grid_energy_planned: float = 0.0
        self.schedule_status: str = "unknown"
        self.schedule_reason: str = ""
        self.scheduler_active: str = "greedy"

        # Consumption tracking & persistent storage
        self.consumption_override_entity = consumption_override_entity
        self._daily_consumption_history: list = []
        self._consumption_store = None
        self._consumption_store_loaded = False
        self._consumption_store_lock = asyncio.Lock()
        self.weekly_avg_consumption: float | None = None
        self._yesterday_deficit: float = 0.0
        # Hourly consumption profiles: {hour: avg_kwh} from 7-day history
        self._hourly_consumption_profile: dict[int, float] = {}
        self._hourly_consumption_history: list = []  # [{date, hours: {0: kwh, 1: kwh, ...}}]
        self.self_consumption_reserve: float = 0.0
        self._reserve_target_pct: float = 0.0  # computed reserve target as battery %
        self._last_net_pv: float = 0.0
        self._last_pv_confidence: float = 1.0
        self.tomorrow_precharge: float = 0.0
        self.tomorrow_planned_slots: int = 0
        self.tomorrow_planned_kwh: float = 0.0
        self._backend_soc_trajectory: list[float] = []
        self._tomorrow_scheduled_slots: dict[int, str] = {}
        self._backend_soc_trajectory_tomorrow: list[float] = []

        # Economic Rule 1 window warning.  The integration writes rule 1's
        # enable/voltage/soc/power/date registers but NOT its time-of-day
        # window or effective-weekday mask.  If those are restricted on the
        # inverter, it silently ignores our enable command outside the
        # window.  This holds a dict describing any conflict (or None).
        self.rule1_window_warning: dict | None = None

        # Always-visible slot info (regardless of price_mode)
        self.available_slots_at_threshold: int = 0
        self._available_total_with_tomorrow: int = 0
        self.available_energy_capacity: float = 0.0
        self.charge_likelihood: str = "unknown"

        # Modbus staleness tracking (#6).  Updated on each successful read.
        self._last_modbus_success_ts: float | None = None
        self._stale_data_threshold_sec: int = 120  # 2 min = ~12 ticks

        # Schedule recalc cache (#8).  Skip recompute when inputs unchanged.
        self._last_schedule_input_hash: int | None = None
        self._last_schedule_slot_idx: int = -1

        # Cycle counting + SOH (#13).  Persisted as part of consumption store.
        self._cycle_charged_kwh: float = 0.0
        self._cycle_discharged_kwh: float = 0.0
        self._battery_soh_factor: float = 1.0
        self._last_soc_for_cycles: float | None = None

        # Sustained consumption-deviation tracking.  The deviation correction
        # (ems._consumption_deviation_kwh) only engages when an unexpected load
        # has persisted for a significant time — not a transient spike.  This
        # timestamp marks when the actual SOC first fell significantly below
        # the predicted trajectory; it resets to None the moment consumption
        # returns to trend (so the extra charge stops on its own).
        self._deviation_since_ts: float | None = None

        # Software PV integration: TREX-25/50 with generator-port solar report
        # PV Today = 0.0 (the day-energy register doesn't track generator-port
        # production reliably).  We integrate instantaneous generator power
        # ourselves every tick (10s).  Reset at midnight.
        self._pv_integrated_today_kwh: float = 0.0
        self._last_pv_integrate_ts: float | None = None

        # Grid-mode change tracking (#10).  Detect transitions to reset
        # transient state that shouldn't survive a mode change.
        self._last_grid_mode: str | None = None

        # Anti-conflict hysteresis: count consecutive ticks of grid import
        # over the small-spike threshold.  Suppress discharge only when
        # the import is sustained (≥2 ticks ~ 30s) OR very large (>2 kW).
        # Without this filter a kettle/microwave/EV-start spike causes the
        # inverter to flip discharge → idle → discharge every ~16 seconds.
        self._anticonflict_import_ticks: int = 0
        self._anticonflict_suppress_until_ts: float = 0.0

        # Flexible load state tracking
        self._flex_load_states: dict[int, bool] = {}  # {load_idx: on/off}
        self._flex_load_current_step: int | None = None
        self._flex_load_scheduled: dict[int, dict[int, bool]] = {}  # from ScheduleResult.load_slots
        self._flex_load_scheduled_tomorrow: dict[int, dict[int, bool]] = {}
        self._ev_boost_until_ts: float = 0.0  # epoch ts — EV override active when now < this
        # Max current applied for the current boost session.  Applied once
        # (not every tick) so safe-power step-downs aren't fought; reset on
        # each boost press so a new press re-raises to max.
        self._ev_boost_max_applied: bool = False

    @property
    def pv_actual_today_kwh(self) -> float | None:
        """Return actual PV energy generated today in kWh from inverter registers.

        TREX-5/10: single register 'pv_generated_energy_day' in Wh.
        TREX-25/50: per-string registers 'pv{1-4}_day_energy' in kWh.

        Generator-port solar: Some installations have solar panels connected via
        the generator/micro-inverter port instead of the PV input. In these cases
        PV registers read 0 but generator_day_cost_energy tracks the actual
        production. When genmode is 'Micro Inv' or PV reads 0 with generator
        energy > 0, use the generator register as PV actual.
        """
        if not self.data:
            return None

        pv_kwh = 0.0
        has_pv_data = False

        # TREX-5 / TREX-10: single combined register in Wh
        wh_val = self.data.get("pv_generated_energy_day")
        if wh_val is not None:
            pv_kwh = wh_val / 1000.0
            has_pv_data = True
        else:
            # TREX-25 / TREX-50: sum per-string registers (already in kWh)
            string_keys = ["pv1_day_energy", "pv2_day_energy", "pv3_day_energy", "pv4_day_energy"]
            values = [self.data.get(k) for k in string_keys]
            valid = [v for v in values if v is not None]
            if valid:
                pv_kwh = sum(valid)
                has_pv_data = True

        # Generator-port solar detection:
        # If PV registers read near-zero but energy is flowing through the
        # generator port, solar is likely connected via micro-inverter on gen port.
        # Applies to all inverter types.
        # Check both generator and microinverter registers — TREX-25/50 uses
        # microinverter_day_cost_energy when genmode is 'Micro Inv'.
        if pv_kwh < 0.1:
            gen_energy = self.data.get("generator_day_cost_energy") or 0.0
            micro_energy = self.data.get("microinverter_day_cost_energy") or 0.0
            alt_energy = max(gen_energy, micro_energy)
            if alt_energy > 0.1:
                return round(alt_energy, 2)
            # Hardware registers all zero — fall back to our software-
            # integrated counter (from instantaneous power readings).
            if self._pv_integrated_today_kwh > 0.1:
                _LOGGER.debug(
                    "PV registers and gen-day all zero — using software-"
                    "integrated PV: %.2f kWh",
                    self._pv_integrated_today_kwh,
                )
                return round(self._pv_integrated_today_kwh, 2)

        return round(pv_kwh, 2) if has_pv_data else None

    def _integrate_pv_power(self) -> None:
        """Accumulate PV energy from instantaneous power readings.

        For generator-port solar installations where the day-energy register
        is unreliable, we integrate the real-time power ourselves.  Reads
        total_generator_power (kW) — available on TREX-25/50 — and adds
        power × dt to the running total.  Also includes PV string power for
        installations where both are present.

        Called every coordinator tick (~10s).  Reset at midnight.
        """
        now_ts = time.time()
        if self._last_pv_integrate_ts is None:
            self._last_pv_integrate_ts = now_ts
            return

        dt_hours = (now_ts - self._last_pv_integrate_ts) / 3600.0
        self._last_pv_integrate_ts = now_ts
        if dt_hours <= 0 or dt_hours > 0.1:
            return

        pv_kw = 0.0
        # PV string power (pv1-4, TREX-25/50)
        for key in ("pv1_power", "pv2_power", "pv3_power", "pv4_power"):
            val = self.data.get(key) if self.data else None
            if val is not None and val > 0:
                pv_kw += val
        # Generator-port power (micro-inverter solar)
        gen_kw = 0.0
        for key in ("total_generator_power",
                     "phase_a_generator_active_power",
                     "phase_b_generator_active_power",
                     "phase_c_generator_active_power"):
            val = self.data.get(key) if self.data else None
            if val is not None and val > 0:
                gen_kw += val
                if key == "total_generator_power":
                    break  # use total if available; skip per-phase
        # TREX-5/10: pv_power_conversion (W)
        pv_conv = self.data.get("pv_power_conversion") if self.data else None
        if pv_conv is not None and pv_conv > 0:
            pv_kw += pv_conv / 1000.0

        total_kw = pv_kw + gen_kw
        if total_kw > 0:
            self._pv_integrated_today_kwh += total_kw * dt_hours

    def _apply_scaling(self, raw: int, index: int, size: int = 1) -> int | float:
        """Apply scaling based on index and size."""
        if index == 1:  # /10 – only for size=1
            return raw / 10.0
        elif index == 2:  # /100 – only for size=1
            if size != 1:
                _LOGGER.warning("Index 2 (/100) used with size=%d – applying anyway", size)
            return raw / 100.0
        elif index == 3:  # signed
            if size == 1 and raw >= 0x8000:
                return raw - 0x10000
            elif size == 2 and raw >= 0x80000000:
                return raw - 0x100000000
            elif size == 4 and raw >= 0x8000000000000000:
                return raw - 0x10000000000000000
            return raw
        elif index == 4:  # /1000 – only for size=1
            if size != 1:
                _LOGGER.warning("Index 4 (/1000) used with size=%d – applying anyway", size)
            return raw / 1000.0
        elif index == 8: # /10 (and signed possible)
            # First make signed if needed
            if size == 1 and raw >= 0x8000:
                raw -= 0x10000
            elif size == 2 and raw >= 0x80000000:
                raw -= 0x100000000
            elif size == 4 and raw >= 0x8000000000000000:
                raw -= 0x10000000000000000
            return raw / 10.0
        elif index == 9: # /100 (and signed possible)
            # First make signed if needed
            if size == 1 and raw >= 0x8000:
                raw -= 0x10000
            elif size == 2 and raw >= 0x80000000:
                raw -= 0x100000000
            elif size == 4 and raw >= 0x8000000000000000:
                raw -= 0x10000000000000000
            return raw / 100.0
        else:
            return raw  # index 0,4,5,6,7 – raw

    #obsolete, think about removing.
    def _group_addresses(self, reg_map: dict) -> Dict[int, list]:
        """Group consecutive register addresses to minimize requests."""
        # Note: This method returns a Dict, but _async_update_data expects a list of dicts 
        # with "start", "count", and "keys". Ensure input 'groups' matches that structure.
        addresses = sorted([(info["address"], key) for key, info in reg_map.items()])
        groups = {}
        current_start = None
        current_keys = []

        for addr, key in addresses:
            if current_start is None:
                current_start = addr
                current_keys = [key]
            elif addr == current_start + len(current_keys) * 1: 
                # NOTE: Assuming 1 register per key here based on logic? 
                # If floats take 2 registers, the logic in this helper might need adjustment 
                # to look at the 'size' of the previous key.
                current_keys.append(key)
            else:
                # Save previous group
                groups[current_start] = current_keys
                current_start = addr
                current_keys = [key]

        # Save last group
        if current_start is not None:
            groups[current_start] = current_keys

        return groups

    async def _async_connect(self) -> bool:
        """Connect to the Modbus client if not already connected."""
        if not self.connected:
            try:
                await self.client.connect()
                self.connected = self.client.connected
            except Exception as err:
                _LOGGER.error("Failed to connect to Felicity: %s", err)
                return False
        return self.connected
            
    def _determine_energy_state(self, battery_soc: float | None) -> str:
        """Determine desired energy management state based on price_mode setting.

        manual: Original behavior — user sets price level 1-10, simple threshold comparison.
        auto: Schedule-based — optimizer picks cheapest/most expensive slots automatically.
        """
        opts = self.config_entry.options

        grid_mode = opts.get("grid_mode", "off")
        if grid_mode == "off":
            _LOGGER.info("grid_mode is off, returning idle")
            return "idle"

        if battery_soc is None:
            _LOGGER.info("Battery SOC state unknown, returning idle")
            return "idle"

        charge_max = opts.get("battery_charge_max_level", 100)
        discharge_min = opts.get("battery_discharge_min_level", 20)
        price_mode = opts.get("price_mode", "manual")

        if price_mode == "auto":
            # Auto mode: schedule-based decision
            if self.slot_prices_today and self.scheduled_slots:
                slot_idx = self._current_slot_index()
                if slot_idx is not None and slot_idx in self.scheduled_slots:
                    slot_action = self.scheduled_slots[slot_idx]
                    if slot_action == "charge" and battery_soc < charge_max:
                        # Execute every scheduled charge slot.  No per-slot
                        # "defer for a cheaper later slot" logic — it was
                        # removed because it could only ever HARM:
                        #
                        # The optimizer (ems.select_unified_charge_slots) is
                        # cheapest-first AND power-aware: it picks the cheapest
                        # N slots needed to cover the deficit, where N already
                        # accounts for the per-slot charge-rate cap
                        # (min(safe_power, inverter_max - pv) x slot_h x eff).
                        #   - If a cheaper LATER slot could cover the deficit,
                        #     the optimizer simply doesn't schedule the current
                        #     slot, and this branch isn't reached (idle below).
                        #   - The current slot is scheduled ONLY when it is one
                        #     of the cheapest-N slots REQUIRED.  In that case
                        #     every scheduled charge slot (including the cheaper
                        #     later ones) is needed, and the rate cap means a
                        #     skipped slot can't be made up later.
                        # So deferral never saved money (the optimizer already
                        # avoids expensive slots) and only ever stalled charging
                        # — real customer report: 23% SOC, 9-20 slots scheduled,
                        # battery sat IDLE draining toward discharge_min while
                        # the deferral kept "waiting for a cheaper slot" that was
                        # already committed.  Trust the schedule: charge now.
                        return "charging"
                    if slot_action == "discharge" and battery_soc > discharge_min:
                        # Guard against draining below reserve target.
                        # The schedule was planned assuming SOC stays above
                        # reserve_target, so enforce that here — not just
                        # the lower discharge_min hard floor.
                        discharge_floor = max(discharge_min, self._reserve_target_pct)
                        if battery_soc > discharge_floor:
                            return "discharging"
                        _LOGGER.info(
                            "Skipping discharge: SOC %.1f%% at or below "
                            "reserve target %.1f%% (discharge_min=%.1f%%)",
                            battery_soc, self._reserve_target_pct, discharge_min,
                        )
                return "idle"
            # Auto mode fallback when no slot data yet
            _LOGGER.debug("Auto mode: no slot data available, returning idle")
            return "idle"

        # Manual mode: price threshold comparison with hysteresis band
        if self.current_price is None or self.price_threshold is None:
            _LOGGER.info("current price or price threshold is unknown, returning idle")
            return "idle"

        # Hysteresis margin: 5% of price spread prevents oscillation near threshold
        margin = 0.0
        if self.max_price is not None and self.min_price is not None and self.max_price > self.min_price:
            margin = (self.max_price - self.min_price) * 0.05

        # If already in a state, favor staying (use raw threshold, no margin)
        if self._current_energy_state == "charging":
            if grid_mode in ("from_grid", "both") and self.current_price < self.price_threshold and battery_soc < charge_max:
                return "charging"
        elif self._current_energy_state == "discharging":
            if grid_mode in ("to_grid", "both") and self.current_price > self.price_threshold and battery_soc > discharge_min:
                return "discharging"

        # To enter a new state, price must cross the wider band (threshold ± margin)
        if grid_mode in ("from_grid", "both") and self.current_price < (self.price_threshold - margin) and battery_soc < charge_max:
            return "charging"
        if grid_mode in ("to_grid", "both") and self.current_price > (self.price_threshold + margin) and battery_soc > discharge_min:
            return "discharging"

        return "idle"

    async def _rotate_slot_overrides(self) -> None:
        """Move tomorrow's slot overrides to today and clear tomorrow.

        Called at midnight so that user-added slots for 'tomorrow' become
        the active 'today' overrides when the new day starts.
        """
        tomorrow = self.slot_overrides.get("tomorrow", {})
        if tomorrow:
            self.slot_overrides = {"today": tomorrow, "tomorrow": {}}
            _LOGGER.info(
                "Rotated %d tomorrow slot overrides to today", len(tomorrow)
            )
        else:
            # No tomorrow overrides — just clear today's stale overrides
            self.slot_overrides = {"today": {}, "tomorrow": {}}
            _LOGGER.debug("Cleared slot overrides for new day (no tomorrow overrides)")

        # Persist to config entry so it survives restarts
        entry = self.config_entry
        if entry:
            new_options = {**entry.options, "slot_overrides": self.slot_overrides}
            self.hass.config_entries.async_update_entry(entry, options=new_options)

    def _current_slot_index(self) -> int | None:
        """Get the current time slot index based on price array granularity.

        Automatically supports 15-min (96 slots), 30-min (48 slots), or hourly (24 slots).
        """
        if not self.slot_prices_today:
            return None
        now = datetime.now()
        num_slots = len(self.slot_prices_today)
        minutes_per_slot = (24 * 60) / num_slots
        current_slot = int((now.hour * 60 + now.minute) / minutes_per_slot)
        return min(current_slot, num_slots - 1)

    def _retrieve_slot_prices(self, price_state) -> None:
        """Extract full day's price slot array from Nordpool/energy entity attributes.

        Supports any granularity: 15-min (96 entries), hourly (24 entries), etc.
        """
        if not price_state:
            self.slot_prices_today = None
            self.slot_prices_tomorrow = None
            return

        attrs = price_state.attributes or {}

        def _extract_prices(attr_names):
            for key in attr_names:
                val = attrs.get(key)
                if isinstance(val, list) and len(val) > 0:
                    if isinstance(val[0], dict):
                        return [float(entry.get("value", 0)) for entry in val]
                    else:
                        return [float(v) if v is not None else 0.0 for v in val]
            return None

        self.slot_prices_today = _extract_prices(["today", "prices_today", "raw_today"])
        self.slot_prices_tomorrow = _extract_prices(["tomorrow", "prices_tomorrow", "raw_tomorrow"])

        if self.slot_prices_today:
            num = len(self.slot_prices_today)
            granularity = int((24 * 60) / num)
            _LOGGER.debug("Retrieved %d price slots for today (%d-min granularity)", num, granularity)
        if self.slot_prices_tomorrow:
            _LOGGER.debug("Retrieved %d price slots for tomorrow", len(self.slot_prices_tomorrow))

    def _retrieve_pv_forecast(self) -> None:
        """Retrieve PV production forecast from configured entity."""
        if not self.forecast_entity:
            self.pv_forecast_today = None
            self.pv_forecast_remaining = None
            self.pv_forecast_tomorrow = None
            self.pv_hourly_kwh = {}
            return

        state = self.hass.states.get(self.forecast_entity)
        if not state or state.state in ("unknown", "unavailable"):
            self.pv_forecast_today = None
            self.pv_forecast_remaining = None
            self.pv_hourly_kwh = {}
            return

        try:
            self.pv_forecast_today = float(state.state)
        except (ValueError, TypeError):
            self.pv_forecast_today = None
            self.pv_forecast_remaining = None
            self.pv_hourly_kwh = {}
            return

        now = datetime.now()
        today_date = now.date()
        tomorrow_date = today_date + timedelta(days=1)
        attrs = state.attributes or {}
        remaining = None
        hourly_kwh: dict[int, float] = {}
        hourly_kwh_tomorrow: dict[int, float] = {}

        # Try Forecast.Solar (wh_hours) or Solcast (detailedHourly) hourly breakdown
        wh_data = attrs.get("wh_hours") or attrs.get("detailedHourly")
        if isinstance(wh_data, dict):
            try:
                remaining_wh = 0.0
                for ts_str, value in wh_data.items():
                    ts = self._parse_forecast_time(ts_str)
                    if ts:
                        wh_val = float(value)
                        if ts.date() == today_date:
                            hourly_kwh[ts.hour] = hourly_kwh.get(ts.hour, 0.0) + wh_val / 1000.0
                        elif ts.date() == tomorrow_date:
                            hourly_kwh_tomorrow[ts.hour] = hourly_kwh_tomorrow.get(ts.hour, 0.0) + wh_val / 1000.0
                        if ts >= now:
                            remaining_wh += wh_val
                remaining = remaining_wh / 1000.0
            except Exception as err:
                _LOGGER.debug("Could not parse forecast hourly data: %s", err)

        self.pv_hourly_kwh = hourly_kwh
        self.pv_hourly_kwh_tomorrow = hourly_kwh_tomorrow

        # Derive today's total from the (date-filtered) hourly data.  This
        # recovers the correct forecast when the entity's state attribute is
        # stale right after midnight — common with Forecast.Solar's
        # "today_remaining" sensor which lingers at 0 until the integration
        # refreshes for the new day.
        hourly_total_today = sum(hourly_kwh.values())
        if hourly_total_today > 0:
            entity_today = self.pv_forecast_today or 0.0
            if entity_today < hourly_total_today * 0.5:
                _LOGGER.debug(
                    "pv_forecast_today (%.1f) looks stale vs hourly sum (%.1f); "
                    "using hourly sum",
                    entity_today, hourly_total_today,
                )
                self.pv_forecast_today = round(hourly_total_today, 2)

        # Fallback: estimate remaining PV using solar bell curve
        if remaining is None and self.pv_forecast_today:
            remaining = self._estimate_remaining_pv(self.pv_forecast_today, now)

        self.pv_forecast_remaining = round(remaining, 2) if remaining is not None else None

        # Tomorrow forecast: try separate entity
        self.pv_forecast_tomorrow = None
        tomorrow_entity = self.config_entry.options.get("forecast_entity_tomorrow")
        if tomorrow_entity:
            t_state = self.hass.states.get(tomorrow_entity)
            if t_state and t_state.state not in ("unknown", "unavailable"):
                try:
                    self.pv_forecast_tomorrow = float(t_state.state)
                except (ValueError, TypeError):
                    pass

    @staticmethod
    def _parse_forecast_time(time_str: str):
        """Try to parse a forecast timestamp string to naive datetime."""
        try:
            return datetime.fromisoformat(str(time_str).replace("Z", "+00:00")).replace(tzinfo=None)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _estimate_remaining_pv(total_kwh: float, now: datetime) -> float:
        """Estimate remaining PV production using a solar bell curve."""
        sunrise, sunset = 6, 20
        if now.hour >= sunset:
            return 0.0
        if now.hour < sunrise:
            return total_kwh
        total_minutes = (sunset - sunrise) * 60
        elapsed = (now.hour - sunrise) * 60 + now.minute
        fraction = max(0.0, min(1.0, elapsed / total_minutes))
        produced_fraction = (1 - math.cos(math.pi * fraction)) / 2
        return round(total_kwh * (1 - produced_fraction), 2)

    def _calculate_net_pv_surplus(self, remaining_slots: list[tuple[int, float]],
                                  num_slots: int, consumption_est: float) -> float:
        """Calculate net PV surplus. Delegates to ems module."""
        now = datetime.now()
        return ems_module.calculate_net_pv_surplus(
            remaining_slots, num_slots, consumption_est,
            self.pv_hourly_kwh, self.pv_forecast_remaining,
            self.pv_actual_today_kwh, self.pv_forecast_today,
            now.hour, now.minute,
            previous_pv_confidence=self._last_pv_confidence,
        )

    async def _calculate_schedule(self, battery_soc: float | None) -> None:
        """Calculate optimal charge/discharge schedule.

        Delegates entirely to ems.calculate_schedule() — the single source of
        truth for scheduling logic — and unpacks the result into coordinator
        attributes.  The pure calculation runs in an executor thread because
        the MILP solver performs blocking file I/O (writes .mps model to /tmp
        and runs the CBC subprocess).
        """
        opts = self.config_entry.options
        now = datetime.now()

        grid_mode = opts.get("grid_mode", "off")

        # Reset transient state on grid_mode change (#10).  yesterday_deficit
        # was computed for a different mode; carrying it over inflates today's
        # plan after a switch.  We keep the consumption profile (it's mode-
        # independent).
        if self._last_grid_mode is not None and self._last_grid_mode != grid_mode:
            _LOGGER.info(
                "Grid mode changed %s → %s — resetting yesterday_deficit",
                self._last_grid_mode, grid_mode,
            )
            self._yesterday_deficit = 0.0
            self._last_schedule_input_hash = None  # force recompute
        self._last_grid_mode = grid_mode

        # Stale-data guard (#6).  If we haven't had a successful Modbus read
        # in too long, keep the previous schedule rather than planning on
        # stale battery SOC / register values.
        if (self._last_modbus_success_ts is not None
                and time.time() - self._last_modbus_success_ts
                    > self._stale_data_threshold_sec):
            _LOGGER.warning(
                "Modbus data is stale (last successful read %.0fs ago) — "
                "keeping previous schedule",
                time.time() - self._last_modbus_success_ts,
            )
            self.schedule_status = "stale_data"
            self.schedule_reason = "Inverter communication lost — using last known state"
            return

        # Use safe_max_power (kW scale 1-10) for realistic slot energy, fallback to power_level
        safe_power_kw = max(1, self.safe_max_power) if self.safe_max_power > 0 else opts.get("power_level", 5)
        inverter_model = self.config_entry.data.get(CONF_INVERTER_MODEL, DEFAULT_INVERTER_MODEL)
        inverter_max_kw = INVERTER_MAX_POWER_KW.get(inverter_model, 10)

        # Apply SOH factor (#13): scale nominal capacity by ageing factor.
        nominal_capacity = opts.get("battery_capacity_kwh", 10) or 10
        effective_capacity = nominal_capacity * self._battery_soh_factor

        # Fallback PV (#4): coordinator computes a 7-day actual avg for
        # use when forecast.solar is unavailable.
        pv_fallback = self._compute_pv_fallback()

        config = ems_module.EMSConfig(
            grid_mode=grid_mode,
            battery_capacity_kwh=effective_capacity,
            battery_charge_max_pct=opts.get("battery_charge_max_level", 100),
            battery_discharge_min_pct=opts.get("battery_discharge_min_level", 20),
            efficiency=opts.get("efficiency_factor", 0.90),
            safe_power_kw=safe_power_kw,
            inverter_max_power_kw=inverter_max_kw,
            consumption_est_kwh=self._get_consumption_estimate(),
            yesterday_deficit_kwh=self._yesterday_deficit,
            reserve_target_pct=opts.get("reserve_target_pct", 0),
            arbitrage_price_delta=opts.get("arbitrage_price_delta", 0.0),
            battery_cycle_cost_eur_kwh=opts.get("battery_cycle_cost_eur_kwh", 0.0),
            optimization_priority=opts.get("optimization_priority", "cost"),
            block_export_on_negative_price=str(
                opts.get("block_export_on_negative_price", "on")
            ).lower() not in ("off", "false", "0"),
            charge_to_full_on_negative_price=str(opts.get(
                "charge_to_full_on_negative_price", "off"
            )).lower() in ("on", "true", "1"),
            discharge_to_make_room_for_negative_price=str(opts.get(
                "discharge_to_make_room_for_negative_price", "off"
            )).lower() in ("on", "true", "1"),
            flexible_loads=self._build_flex_load_configs(),
            ev_charge_strategy=str(opts.get("ev_charge_strategy", "smart")),
            scheduler_engine=str(opts.get("scheduler_engine", "milp")),
        )

        # What did the previous schedule predict the SOC would be at this slot?
        # Used by the consumption-deviation correction to detect unexpected
        # loads (air-conditioning, car charger, oven) draining the battery
        # faster than the profile-based prediction.
        #
        # SUSTAINED-ONLY GATE: we only hand the prediction to the algorithm
        # (which then sizes a compensating deficit) once the deviation has
        # PERSISTED for a significant time.  A transient spike — kettle, oven
        # preheat, an AC compressor cycling on for two minutes — must not
        # trigger extra grid charging.  The moment consumption returns to the
        # predicted trend the timer resets and the prediction is withheld, so
        # the extra charge stops on its own (energy already stored stays; we
        # never force a discharge).
        DEVIATION_MIN_DURATION_S = 1800        # 30 min sustained
        predicted_soc_raw = None
        if self._backend_soc_trajectory and self.slot_prices_today:
            slot_idx = int((now.hour * 60 + now.minute) / ((24 * 60) / len(self.slot_prices_today)))
            slot_idx = min(slot_idx, len(self._backend_soc_trajectory) - 1)
            if 0 <= slot_idx < len(self._backend_soc_trajectory):
                predicted_soc_raw = self._backend_soc_trajectory[slot_idx]

        predicted_soc = None
        if (predicted_soc_raw is not None and battery_soc is not None
                and effective_capacity > 0):
            deviation_kwh = ((predicted_soc_raw - battery_soc) / 100.0) * effective_capacity
            # "Significant" scales with battery size: ~3% of capacity, min
            # 1.5 kWh.  On 60 kWh that's ~1.8 kWh (≈ a sustained 3–4 kW excess
            # load over the window) — AC / EV territory, not a kettle.
            significant_kwh = max(1.5, 0.03 * effective_capacity)
            if deviation_kwh > significant_kwh:
                if self._deviation_since_ts is None:
                    self._deviation_since_ts = time.time()
            else:
                # Consumption back on trend → stop tracking → withhold the
                # prediction → extra charge stops next recalc.
                self._deviation_since_ts = None

            sustained = (
                self._deviation_since_ts is not None
                and time.time() - self._deviation_since_ts >= DEVIATION_MIN_DURATION_S
            )
            if sustained:
                predicted_soc = predicted_soc_raw
                _LOGGER.info(
                    "Sustained consumption deviation: actual %.1f%% vs predicted "
                    "%.1f%% (%.1f kWh) for >=%.0f min — engaging deviation "
                    "correction",
                    battery_soc, predicted_soc_raw, deviation_kwh,
                    DEVIATION_MIN_DURATION_S / 60.0,
                )
        else:
            self._deviation_since_ts = None

        state = ems_module.EMSState(
            battery_soc_pct=battery_soc,
            slot_prices_today=self.slot_prices_today,
            slot_prices_tomorrow=self.slot_prices_tomorrow,
            pv_hourly_kwh=self.pv_hourly_kwh or {},
            pv_forecast_remaining=self.pv_forecast_remaining,
            pv_forecast_today=self.pv_forecast_today,
            pv_forecast_tomorrow=self.pv_forecast_tomorrow,
            pv_actual_today_kwh=self.pv_actual_today_kwh,
            pv_hourly_kwh_tomorrow=self.pv_hourly_kwh_tomorrow or None,
            consumption_hourly_kwh=self._hourly_consumption_profile or None,
            previous_pv_confidence=self._last_pv_confidence,
            last_modbus_read_ts=self._last_modbus_success_ts,
            pv_fallback_today_kwh=pv_fallback,
            current_hour=now.hour,
            current_minute=now.minute,
            predicted_soc_pct=predicted_soc,
        )

        # Skip recalc when inputs unchanged (#8).  Hashing the inputs lets
        # us detect "nothing meaningful changed since last tick" — common
        # case between price updates.  Always recompute on slot boundary.
        current_slot_idx = int((now.hour * 60 + now.minute) / ((24 * 60) / len(self.slot_prices_today))) if self.slot_prices_today else -1
        input_hash = hash((
            grid_mode,
            round(battery_soc, 1) if battery_soc is not None else None,
            tuple(self.slot_prices_today) if self.slot_prices_today else None,
            tuple(self.slot_prices_tomorrow) if self.slot_prices_tomorrow else None,
            round(self.pv_forecast_today, 2) if self.pv_forecast_today else None,
            round(self.pv_actual_today_kwh, 2) if self.pv_actual_today_kwh else None,
            self._yesterday_deficit,
            json.dumps(self.slot_overrides, sort_keys=True) if self.slot_overrides else "",
            safe_power_kw,
            opts.get("ev_charge_strategy", "smart"),
            opts.get("scheduler_engine", "milp"),
        ))
        if (input_hash == self._last_schedule_input_hash
                and current_slot_idx == self._last_schedule_slot_idx):
            _LOGGER.debug(
                "Schedule recalc skipped — inputs unchanged (slot %d)",
                current_slot_idx,
            )
            return
        self._last_schedule_input_hash = input_hash
        self._last_schedule_slot_idx = current_slot_idx

        result = await self.hass.async_add_executor_job(
            ems_module.calculate_schedule, config, state
        )

        # Smoothed PV confidence for this tick — same EMA blend the
        # schedule used internally (previous_confidence keeps the chain
        # intact; storing the raw value would degrade the EMA to a weak
        # 2-tap blend).
        smoothed_pv_confidence = ems_module._calculate_pv_confidence(
            self.pv_hourly_kwh, self.pv_actual_today_kwh,
            now.hour, now.minute,
            previous_confidence=self._last_pv_confidence,
        )

        # Unpack ScheduleResult into coordinator attributes
        self.scheduled_slots = result.scheduled_slots

        # Merge manual slot overrides from the card, then re-validate (#9).
        # Without validation, a manual override could push SOC above
        # capacity (overflow) or below the floor (deep discharge).
        today_overrides = self.slot_overrides.get("today", {})
        if today_overrides:
            for idx_str, action in today_overrides.items():
                idx = int(idx_str)
                # Respect grid_mode: from_grid only allows charge, to_grid only discharge
                if grid_mode == "from_grid" and action != "charge":
                    continue
                if grid_mode == "to_grid" and action != "discharge":
                    continue
                self.scheduled_slots[idx] = action

            # Re-run SOC validation on the merged schedule.  Drops any
            # manually-added slot that would violate battery bounds.
            if self.slot_prices_today and battery_soc is not None:
                num_slots_t = len(self.slot_prices_today)
                minutes_per_slot_t = (24 * 60) / num_slots_t
                current_slot_t = int(
                    (now.hour * 60 + now.minute) / minutes_per_slot_t
                )
                current_slot_t = min(current_slot_t, num_slots_t - 1)
                remaining_t = [
                    (i, self.slot_prices_today[i])
                    for i in range(current_slot_t, num_slots_t)
                    if self.slot_prices_today[i] is not None
                ]
                charge_set = {
                    i for i, a in self.scheduled_slots.items() if a == "charge"
                }
                discharge_set = {
                    i for i, a in self.scheduled_slots.items() if a == "discharge"
                }
                current_kwh_t = (battery_soc / 100.0) * effective_capacity
                min_kwh_t = (config.battery_discharge_min_pct / 100.0) * effective_capacity
                # Match the main pass: discharges validate against the
                # reserve target, not the bare hardware floor — except in
                # make-room mode, where dips below reserve are intentional
                # (negative-window PV refills the battery).
                if config.discharge_to_make_room_for_negative_price:
                    floor_t = min_kwh_t
                else:
                    reserve_kwh_t = (result.reserve_target_pct / 100.0) * effective_capacity
                    floor_t = max(min_kwh_t, reserve_kwh_t)
                consumption_per_slot_t = config.consumption_est_kwh / num_slots_t
                energy_per_slot_t = config.safe_power_kw * (minutes_per_slot_t / 60.0)
                validated_charge, validated_discharge = ems_module._validate_schedule_soc(
                    remaining_t, charge_set, discharge_set,
                    current_kwh_t, consumption_per_slot_t,
                    self.pv_hourly_kwh or {}, minutes_per_slot_t,
                    smoothed_pv_confidence,
                    effective_capacity, floor_t,
                    energy_per_slot_t, config.efficiency,
                    consumption_hourly_kwh=self._hourly_consumption_profile or None,
                    inverter_max_power_kw=config.inverter_max_power_kw,
                    safe_power_kw=config.safe_power_kw,
                    keep_all_negative_charges=config.charge_to_full_on_negative_price,
                )
                # Drop any slot rejected by validation, including overrides
                dropped: list[tuple[int, str]] = []
                for idx in list(self.scheduled_slots.keys()):
                    action = self.scheduled_slots[idx]
                    if action == "charge" and idx not in validated_charge:
                        del self.scheduled_slots[idx]
                        dropped.append((idx, action))
                    elif action == "discharge" and idx not in validated_discharge:
                        del self.scheduled_slots[idx]
                        dropped.append((idx, action))
                if dropped:
                    _LOGGER.warning(
                        "Override SOC validation: dropped %d slot(s) that would "
                        "violate battery bounds: %s  (soc=%.1f kWh, cap=%.1f, "
                        "floor=%.1f, charge_count=%d, discharge_count=%d)",
                        len(dropped), [(s, a) for s, a in dropped],
                        current_kwh_t, effective_capacity, floor_t,
                        len(validated_charge), len(validated_discharge),
                    )
        self.cheap_slots_remaining = result.cheap_slots_remaining
        self.grid_energy_planned = result.grid_energy_planned
        self.self_consumption_reserve = result.self_consumption_reserve
        self._reserve_target_pct = result.reserve_target_pct
        self.tomorrow_precharge = result.tomorrow_precharge
        self.tomorrow_planned_slots = result.tomorrow_planned_slots
        self.tomorrow_planned_kwh = result.tomorrow_planned_kwh
        self.schedule_status = result.status
        self.schedule_reason = result.schedule_reason
        # Reflect manual overrides in the status/reason.  result.status was
        # computed by ems.py before overrides were merged, so it can read
        # "no_action_needed" while overrides actually drive a charge/discharge
        # this slot.  Correct it so the card chip isn't misleading.
        current_slot_now = self._current_slot_index()
        if (self.slot_overrides.get("today")
                and current_slot_now is not None
                and current_slot_now in self.scheduled_slots):
            action_now = self.scheduled_slots[current_slot_now]
            self.schedule_status = "active"
            self.schedule_reason = (
                f"Manual override: {action_now} this slot"
            )
        self.scheduler_active = result.scheduler_active
        # Recompute SOC trajectory with the finalized schedule (including
        # any merged manual overrides).  Without this, the trajectory shows
        # the pre-override plan — so manually-added charge slots don't
        # appear in the SOC line, and the graph flatlines while the inverter
        # is actively charging.
        if today_overrides and self.slot_prices_today and battery_soc is not None:
            num_slots_r = len(self.slot_prices_today)
            minutes_per_slot_r = (24 * 60) / num_slots_r
            current_slot_r = int(
                (now.hour * 60 + now.minute) / minutes_per_slot_r
            )
            current_slot_r = min(current_slot_r, num_slots_r - 1)
            current_kwh_r = (battery_soc / 100.0) * effective_capacity
            # Mirror calculate_schedule's PV synthesis: when the forecast has
            # no hourly breakdown, ems.calculate_schedule internally rebuilds
            # the state with a synthesized hourly PV curve (from the daily
            # total) so the trajectory accounts for solar.  That rebuilt state
            # is NOT returned, so this recompute must apply the same synthesis
            # — otherwise the override trajectory "forgets PV" and draws a
            # far-too-low curve (real customer report: 12 charge slots + big PV
            # remaining, yet the SOC line barely rose).
            traj_state = state
            if not state.pv_hourly_kwh:
                forecast_total = self.pv_forecast_today or pv_fallback
                if forecast_total and forecast_total > 0:
                    traj_state = dataclasses.replace(
                        state,
                        pv_hourly_kwh=ems_module._synthesize_pv_hourly(forecast_total),
                    )
            self._backend_soc_trajectory = ems_module._compute_scheduled_soc_trajectory(
                self.slot_prices_today, num_slots_r, minutes_per_slot_r,
                current_kwh_r, current_slot_r,
                self.scheduled_slots, config, traj_state,
            )
        else:
            self._backend_soc_trajectory = result.soc_trajectory
        self._tomorrow_scheduled_slots = result.tomorrow_scheduled_slots
        self._backend_soc_trajectory_tomorrow = result.tomorrow_soc_trajectory
        self._flex_load_scheduled = result.load_slots
        self._flex_load_scheduled_tomorrow = result.tomorrow_load_slots

        if result.price_threshold is not None:
            self.price_threshold = result.price_threshold

        # Expose net_pv and pv_confidence for card simulation
        if self.slot_prices_today:
            prices = self.slot_prices_today
            num_slots = len(prices)
            minutes_per_slot = (24 * 60) / num_slots
            current_slot = int((now.hour * 60 + now.minute) / minutes_per_slot)
            current_slot = min(current_slot, num_slots - 1)
            remaining = [(i, prices[i]) for i in range(current_slot, num_slots) if prices[i] is not None]
            self._last_net_pv = self._calculate_net_pv_surplus(remaining, num_slots, config.consumption_est_kwh)
        else:
            self._last_net_pv = 0.0

        self._last_pv_confidence = smoothed_pv_confidence

    _WEEKDAY_NAMES = [
        "Sunday", "Monday", "Tuesday", "Wednesday",
        "Thursday", "Friday", "Saturday",
    ]

    def _check_rule1_window_conflict(self) -> dict | None:
        """Check whether the planned schedule falls outside Economic Rule 1's
        time-of-day window or effective-weekday mask.

        The integration drives the inverter through rule 1 but only writes
        enable/voltage/soc/power/start_day/stop_day — it leaves the rule's
        start_time, stop_time and effective_week as configured on the
        inverter.  If those are restrictive, the inverter silently ignores
        our enable command when the current time/weekday is outside the
        window: the EMS plans to act, writes the register, and nothing
        happens.  This surfaces that mismatch as a UI warning.

        Returns a dict describing the conflict, or None when everything the
        EMS plans falls inside the rule 1 window (or the registers can't be
        read).
        """
        data = self.data or {}
        start_raw = data.get("econ_rule_1_start_time")
        stop_raw = data.get("econ_rule_1_stop_time")
        week_raw = data.get("econ_rule_1_effective_week")

        # Can't evaluate without the window registers (e.g. not in this
        # model's register set or not yet read).
        if start_raw is None or stop_raw is None or week_raw is None:
            return None

        start_min = (start_raw >> 8) * 60 + (start_raw & 0xFF)
        stop_min = (stop_raw >> 8) * 60 + (stop_raw & 0xFF)
        week_mask = int(week_raw) & 0x7F

        # start == stop is the inverter's "all day" convention (no time
        # restriction).  A full week mask means no weekday restriction.
        full_day = start_min == stop_min
        all_days = week_mask == 0x7F

        # Nothing to warn about when both dimensions are unrestricted.
        if full_day and all_days:
            return None

        def time_ok(slot_min: float) -> bool:
            if full_day:
                return True
            if start_min < stop_min:
                return start_min <= slot_min < stop_min
            # Window spans midnight (e.g. 22:00 → 06:00).
            return slot_min >= start_min or slot_min < stop_min

        def weekday_ok(d) -> bool:
            if all_days:
                return True
            # Inverter mask: bit0=Sunday .. bit6=Saturday.
            # Python isoweekday(): Mon=1 .. Sun=7 → Sun maps to 0.
            bit = d.isoweekday() % 7
            return bool(week_mask & (1 << bit))

        opts = self.config_entry.options
        grid_mode = opts.get("grid_mode", "off")
        if grid_mode == "off":
            return None

        num_slots = len(self.slot_prices_today) if self.slot_prices_today else 96
        minutes_per_slot = (24 * 60) / num_slots
        now = datetime.now()
        today = now.date()
        tomorrow = today + timedelta(days=1)

        # Build the set of slot indices the EMS intends to act on.
        #  - auto mode: the optimizer's scheduled_slots (today + tomorrow)
        #  - manual mode: slots crossing the threshold in the active
        #    direction (charge below / discharge above), today only —
        #    only future slots matter since past ones already happened.
        price_mode = opts.get("price_mode", "manual")
        today_slots: dict[int, str] = {}
        tomorrow_slots: dict[int, str] = {}
        if price_mode == "auto":
            today_slots = dict(self.scheduled_slots or {})
            tomorrow_slots = dict(self._tomorrow_scheduled_slots or {})
        elif self.price_threshold is not None and self.slot_prices_today:
            current_slot = min(
                int((now.hour * 60 + now.minute) / minutes_per_slot),
                num_slots - 1,
            )
            for idx in range(current_slot, num_slots):
                price = self.slot_prices_today[idx]
                if price is None:
                    continue
                if grid_mode in ("from_grid", "both") and price < self.price_threshold:
                    today_slots[idx] = "charge"
                elif grid_mode in ("to_grid", "both") and price > self.price_threshold:
                    today_slots[idx] = "discharge"

        affected = 0
        time_violation = False
        weekday_violation = False

        for day_date, slots in (
            (today, today_slots),
            (tomorrow, tomorrow_slots),
        ):
            wd_ok = weekday_ok(day_date)
            for idx in slots:
                slot_min = idx * minutes_per_slot
                tm_ok = time_ok(slot_min)
                if not (wd_ok and tm_ok):
                    affected += 1
                    if not tm_ok:
                        time_violation = True
                    if not wd_ok:
                        weekday_violation = True

        if affected == 0:
            return None

        enabled_days = [
            self._WEEKDAY_NAMES[i] for i in range(7) if week_mask & (1 << i)
        ]
        warning = {
            "conflict": True,
            "affected_slots": affected,
            "time_violation": time_violation,
            "weekday_violation": weekday_violation,
            "rule1_start_time": f"{start_raw >> 8:02d}:{start_raw & 0xFF:02d}",
            "rule1_stop_time": f"{stop_raw >> 8:02d}:{stop_raw & 0xFF:02d}",
            "rule1_effective_days": enabled_days,
        }
        _LOGGER.warning(
            "Economic Rule 1 window mismatch: %d scheduled slot(s) fall "
            "outside the inverter's rule 1 window (time %s-%s, days %s). "
            "The inverter will ignore charge/discharge commands outside "
            "this window. Adjust the rule 1 Start/Stop Time and Effective "
            "Week on the inverter, or the schedule won't execute.",
            affected, warning["rule1_start_time"], warning["rule1_stop_time"],
            ", ".join(enabled_days) or "none",
        )
        return warning

    # ── Flexible load helpers ────────────────────────────────────

    def _build_flex_load_configs(self) -> list:
        """Build FlexibleLoadConfig objects from entry.options."""
        opts = self.config_entry.options
        loads = []
        for n in range(1, 4):
            prefix = f"flexible_load_{n}_"
            enabled = str(opts.get(f"{prefix}enabled", "off")).lower() not in ("off", "false", "0")
            if not enabled:
                continue
            steps_str = opts.get(f"{prefix}current_steps", "") if n == 1 else ""
            steps = []
            if steps_str:
                steps = [int(s.strip()) for s in str(steps_str).split(",") if s.strip().isdigit()]
            loads.append(ems_module.FlexibleLoadConfig(
                enabled=True,
                name=opts.get(f"{prefix}name", "") or f"Load {n}",
                switch_entity=opts.get(f"{prefix}switch_entity", ""),
                rated_power_kw=float(opts.get(f"{prefix}power_kw", 3.7 if n == 1 else 2.0)),
                priority=int(opts.get(f"{prefix}priority", n)),
                current_entity=opts.get(f"{prefix}current_entity", "") if n == 1 else "",
                current_steps=steps,
                phases=int(opts.get(f"{prefix}phases", 1)) if n == 1 else 1,
                voltage=int(opts.get(f"{prefix}voltage", 230)) if n == 1 else 230,
                default_current=int(opts.get(f"{prefix}default_current", 16)) if n == 1 else 16,
            ))
        return loads

    @property
    def ev_boost_active(self) -> bool:
        return time.time() < self._ev_boost_until_ts

    @property
    def ev_boost_remaining_min(self) -> int:
        if not self.ev_boost_active:
            return 0
        return max(0, int((self._ev_boost_until_ts - time.time()) / 60))

    def ev_boost_add_hour(self) -> None:
        """Add 1 hour to the EV boost override timer."""
        now = time.time()
        base = max(now, self._ev_boost_until_ts)
        self._ev_boost_until_ts = base + 3600
        # Re-apply max current on the next tick, even if the charger was
        # already on (a fresh press signals urgency) or safe-power stepped
        # the current down earlier in this boost session.
        self._ev_boost_max_applied = False
        remaining = int((self._ev_boost_until_ts - now) / 60)
        _LOGGER.info("EV Boost: +1h → %d min remaining", remaining)

    def ev_boost_cancel(self) -> None:
        """Cancel the EV boost override."""
        self._ev_boost_until_ts = 0.0
        self._ev_boost_max_applied = False
        _LOGGER.info("EV Boost cancelled")

    async def _actuate_flex_loads(self) -> None:
        """Turn flexible loads on/off based on current schedule slot.

        Each load is actuated independently — a failure on one load must
        not prevent the others from being switched.  Exceptions from
        individual service calls are already caught by _set_flex_load /
        _set_ev_charger_current; the per-load try/except here guards
        against unexpected errors in the control flow itself (e.g. bad
        config parse, missing entity domain).
        """
        loads = self._build_flex_load_configs()
        ev_boost = self.ev_boost_active

        # EV Boost override: force EV charger (first EV-capable load) on at max
        if ev_boost:
            for load_idx, load in enumerate(loads):
                if load.is_ev_charger:
                    try:
                        if not self._flex_load_states.get(load_idx, False):
                            await self._set_flex_load(load_idx, True, load)
                        if not self._ev_boost_max_applied and load.current_steps:
                            await self._set_ev_charger_current(load, max(load.current_steps))
                            self._ev_boost_max_applied = True
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.error("EV boost actuation failed for '%s': %s", load.name, err)
                    break
        else:
            self._ev_boost_max_applied = False

        if not self._flex_load_scheduled and not ev_boost:
            for load_idx, is_on in list(self._flex_load_states.items()):
                if is_on:
                    try:
                        await self._set_flex_load(load_idx, False)
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.error("Failed to turn off flex load %d: %s", load_idx, err)
            return

        slot_idx = self._current_slot_index()
        if slot_idx is None:
            for load_idx, load in enumerate(loads):
                if ev_boost and load.is_ev_charger:
                    continue
                if self._flex_load_states.get(load_idx, False):
                    try:
                        await self._set_flex_load(load_idx, False, load)
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.error("Failed to turn off flex load '%s': %s", load.name, err)
            return

        for load_idx, load in enumerate(loads):
            if ev_boost and load.is_ev_charger:
                continue

            try:
                should_be_on = slot_idx in self._flex_load_scheduled.get(load_idx, {})
                currently_on = self._flex_load_states.get(load_idx, False)

                if should_be_on != currently_on:
                    await self._set_flex_load(load_idx, should_be_on, load)

                if should_be_on and load.is_ev_charger and not currently_on:
                    await self._set_ev_charger_current(load, load.default_current)
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Flex load '%s' actuation failed: %s", load.name, err)

    async def _set_flex_load(self, load_idx: int, turn_on: bool,
                             load: "ems_module.FlexibleLoadConfig | None" = None) -> None:
        """Turn a flexible load on or off via its switch entity."""
        if load is None:
            loads = self._build_flex_load_configs()
            if load_idx >= len(loads):
                return
            load = loads[load_idx]
        if not load.switch_entity:
            return
        domain = load.switch_entity.split(".")[0]
        service = "turn_on" if turn_on else "turn_off"
        try:
            await self.hass.services.async_call(
                domain, service, {"entity_id": load.switch_entity}
            )
            self._flex_load_states[load_idx] = turn_on
            _LOGGER.info(
                "Flex load '%s': %s via %s",
                load.name, service, load.switch_entity,
            )
        except Exception as err:
            _LOGGER.error("Failed to %s flex load '%s': %s", service, load.name, err)

    async def _set_ev_charger_current(self, load: "ems_module.FlexibleLoadConfig",
                                       target_amps: int) -> None:
        """Set EV charger current to the nearest available step at or below target_amps."""
        if not load.current_entity or not load.current_steps:
            return
        step = load.nearest_step_at_or_below(target_amps)
        if step is None:
            return
        domain = load.current_entity.split(".")[0]
        try:
            if domain in ("select", "input_select"):
                # Select entities expose a fixed list of option strings, and
                # different chargers format them differently ("16", "16 A",
                # "16A").  Passing the bare number ("16") raises
                # ServiceValidationError when the entity expects "16 A".
                # Resolve the actual option string by matching the numeric
                # value against the entity's real options.
                option = self._match_select_option(load.current_entity, step)
                if option is None:
                    _LOGGER.warning(
                        "EV charger current %dA has no matching option on %s "
                        "(options: %s) — skipping",
                        step, load.current_entity,
                        self._select_options(load.current_entity),
                    )
                    return
                await self.hass.services.async_call(
                    domain, "select_option",
                    {"entity_id": load.current_entity, "option": option},
                )
            else:
                await self.hass.services.async_call(
                    domain, "set_value",
                    {"entity_id": load.current_entity, "value": step},
                )
            self._flex_load_current_step = step
            _LOGGER.info("EV charger current → %dA via %s", step, load.current_entity)
        except Exception as err:
            _LOGGER.error("Failed to set EV charger current: %s", err)

    def _select_options(self, entity_id: str) -> list[str]:
        """Return a select entity's current option list (empty if unavailable)."""
        state = self.hass.states.get(entity_id)
        if state is None:
            return []
        opts = state.attributes.get("options")
        return list(opts) if isinstance(opts, (list, tuple)) else []

    def _match_select_option(self, entity_id: str, amps: int) -> str | None:
        """Find the option string on a select entity matching a numeric amperage.

        Charger integrations format current options inconsistently: "16",
        "16 A", "16A", "16.0".  Parse the leading number from each option and
        return the one equal to `amps`.  Returns None when the entity is
        unavailable or has no matching option (caller skips the write).
        """
        options = self._select_options(entity_id)
        if not options:
            # Entity not loaded yet — fall back to the bare number so the
            # service call still has a chance (matches the legacy behaviour).
            return str(amps)
        for opt in options:
            # Extract the leading numeric part (e.g. "16 A" → 16, "16A" → 16).
            num = ""
            for ch in str(opt).strip():
                if ch.isdigit() or ch == ".":
                    num += ch
                else:
                    break
            if not num:
                continue
            try:
                if abs(float(num) - amps) < 0.001:
                    return opt
            except ValueError:
                continue
        return None

    async def _safe_power_shed_loads(self, current_amps: float, max_amps: float) -> bool:
        """Shed flexible loads to reduce grid current.

        Priority chain (one action per tick to let current settle):
        1. EV charger: step down current
        2. Binary loads: shed by priority (highest number = shed first)
        Returns True if any action was taken.
        """
        loads = self._build_flex_load_configs()
        active = [(i, ld) for i, ld in enumerate(loads)
                  if self._flex_load_states.get(i, False)]
        if not active:
            return False

        # Step 1: EV current step-down
        for idx, ld in active:
            if ld.is_ev_charger and self._flex_load_current_step:
                steps = sorted(ld.current_steps)
                try:
                    pos = steps.index(self._flex_load_current_step)
                except ValueError:
                    pos = -1
                if pos > 0:
                    new_step = steps[pos - 1]
                    _LOGGER.warning(
                        "Safe power: EV current %dA → %dA (grid %.1fA / %dA max)",
                        self._flex_load_current_step, new_step, current_amps, max_amps,
                    )
                    await self._set_ev_charger_current(ld, new_step)
                    return True

        # Step 2: Shed binary loads (3=least important → shed first, 1=most important → shed last)
        # During EV boost, don't shed the EV charger — it's user-requested
        active.sort(key=lambda x: -x[1].priority)
        for idx, ld in active:
            if ld.is_ev_charger and self.ev_boost_active:
                continue
            _LOGGER.warning(
                "Safe power: shedding load '%s' (priority %d, grid %.1fA / %dA max)",
                ld.name, ld.priority, current_amps, max_amps,
            )
            await self._set_flex_load(idx, False, ld)
            return True

        return False

    def _get_consumption_estimate(self) -> float:
        """Get best available daily consumption estimate.

        Priority: 7-day rolling average > user-set estimate > default.
        """
        if self.weekly_avg_consumption is not None and self.weekly_avg_consumption > 0:
            return self.weekly_avg_consumption
        return self.config_entry.options.get("daily_consumption_estimate", 10)

    def _calculate_available_info(self, battery_soc: float | None) -> None:
        """Calculate available slots and charge likelihood (always visible, regardless of price_mode).

        Delegates to ems.calculate_available_info() — same config (including
        the SOH-scaled capacity and optimization_priority reserve boost) the
        scheduler uses, so charge_likelihood matches the actual plan.
        """
        opts = self.config_entry.options
        price_mode = opts.get("price_mode", "manual")

        # Set schedule_status for manual mode (schedule optimizer only runs in auto)
        if price_mode == "manual" and self.slot_prices_today and self.price_threshold is not None:
            self.schedule_status = "manual"
            self.schedule_reason = "Manual mode — schedule follows price threshold"

        if not self.slot_prices_today or self.price_threshold is None:
            self.available_slots_at_threshold = 0
            self.available_energy_capacity = 0.0
            self.charge_likelihood = "no_data"
            return

        now = datetime.now()
        safe_power_kw = max(1, self.safe_max_power) if self.safe_max_power > 0 else opts.get("power_level", 5)
        inverter_model = self.config_entry.data.get(CONF_INVERTER_MODEL, DEFAULT_INVERTER_MODEL)
        nominal_capacity = opts.get("battery_capacity_kwh", 10) or 10

        config = ems_module.EMSConfig(
            grid_mode=opts.get("grid_mode", "off"),
            battery_capacity_kwh=nominal_capacity * self._battery_soh_factor,
            battery_charge_max_pct=opts.get("battery_charge_max_level", 100),
            battery_discharge_min_pct=opts.get("battery_discharge_min_level", 20),
            efficiency=opts.get("efficiency_factor", 0.90),
            safe_power_kw=safe_power_kw,
            inverter_max_power_kw=INVERTER_MAX_POWER_KW.get(inverter_model, 10),
            consumption_est_kwh=self._get_consumption_estimate(),
            reserve_target_pct=opts.get("reserve_target_pct", 0),
            optimization_priority=opts.get("optimization_priority", "cost"),
        )
        state = ems_module.EMSState(
            battery_soc_pct=battery_soc,
            slot_prices_today=self.slot_prices_today,
            slot_prices_tomorrow=self.slot_prices_tomorrow,
            pv_hourly_kwh=self.pv_hourly_kwh or {},
            pv_forecast_remaining=self.pv_forecast_remaining,
            pv_forecast_today=self.pv_forecast_today,
            pv_actual_today_kwh=self.pv_actual_today_kwh,
            previous_pv_confidence=self._last_pv_confidence,
            current_hour=now.hour,
            current_minute=now.minute,
        )

        info = ems_module.calculate_available_info(
            config, state, self.price_threshold,
            grid_energy_planned=self.grid_energy_planned or 0.0,
        )
        self.available_slots_at_threshold = info.available_slots
        self._available_total_with_tomorrow = info.available_total_with_tomorrow
        self.available_energy_capacity = info.available_energy_capacity
        self.charge_likelihood = info.charge_likelihood

    async def _init_consumption_store(self) -> None:
        """Initialize persistent storage for 7-day consumption history."""
        if self._consumption_store_loaded:
            return
        async with self._consumption_store_lock:
            # Double-check after acquiring lock (another caller may have finished)
            if self._consumption_store_loaded:
                return
            from homeassistant.helpers.storage import Store
            self._consumption_store = Store(
                self.hass,
                version=1,
                key=f"{DOMAIN}_{self.config_entry.entry_id}_consumption",
            )
            data = await self._consumption_store.async_load()
            if data and "daily_history" in data:
                self._daily_consumption_history = data["daily_history"][-7:]
                self._calculate_weekly_avg()
            if data and "hourly_history" in data:
                self._hourly_consumption_history = data["hourly_history"][-7:]
                self._calculate_hourly_profile()
            # Cycle counting + SOH (#13).  Persisted across restarts so we
            # can estimate battery wear from cumulative throughput.
            if data and "cycle_charged_kwh" in data:
                self._cycle_charged_kwh = float(data.get("cycle_charged_kwh", 0))
                self._cycle_discharged_kwh = float(data.get("cycle_discharged_kwh", 0))
                self._battery_soh_factor = float(data.get("battery_soh_factor", 1.0))
            self._consumption_store_loaded = True

    async def _record_daily_consumption(self) -> None:
        """Record today's consumption and update 7-day rolling average.

        Priority: override entity > inverter daily register > skip.
        """
        await self._init_consumption_store()

        today_str = datetime.now().strftime("%Y-%m-%d")
        today_consumption = None

        # 1. Try override entity (P1 meter / utility meter / template sensor)
        if self.consumption_override_entity:
            state = self.hass.states.get(self.consumption_override_entity)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    today_consumption = float(state.state)
                except (ValueError, TypeError):
                    pass

        # 2. Try inverter daily energy registers
        if today_consumption is None and self.data:
            for key in ["daily_energy_consumed", "daily_load_energy", "total_load_energy_today",
                        "daily_consumption", "daily_energy_used",
                        "total_load_consumption_energy_day",
                        "load_consumption_energy_day",
                        "homeload_day_cost_energy",
                        "load_day_cost_energy"]:
                val = self.data.get(key)
                if val is not None and val > 0:
                    # Convert Wh to kWh if value seems to be in Wh (>100)
                    today_consumption = val / 1000.0 if val > 100 else val
                    break

        if today_consumption is None or today_consumption <= 0:
            _LOGGER.warning(
                "No daily consumption data found — weekly average will remain unknown. "
                "Configure a consumption_override_entity or check inverter registers."
            )
            return

        # Remove existing entry for today (if any), then append
        self._daily_consumption_history = [
            entry for entry in self._daily_consumption_history
            if entry["date"] != today_str
        ]
        self._daily_consumption_history.append({
            "date": today_str,
            "kwh": round(today_consumption, 2)
        })
        # Keep last 7 days
        self._daily_consumption_history = self._daily_consumption_history[-7:]

        # Record hourly breakdown from HA history
        await self._record_hourly_consumption(today_str)

        # Persist
        if self._consumption_store:
            await self._consumption_store.async_save({
                "daily_history": self._daily_consumption_history,
                "hourly_history": self._hourly_consumption_history,
                "cycle_charged_kwh": round(self._cycle_charged_kwh, 3),
                "cycle_discharged_kwh": round(self._cycle_discharged_kwh, 3),
                "battery_soh_factor": round(self._battery_soh_factor, 4),
            })

        self._calculate_weekly_avg()
        _LOGGER.info("Recorded daily consumption: %.2f kWh (7-day avg: %.2f kWh)",
                         today_consumption, self.weekly_avg_consumption or 0)

    def _calculate_weekly_avg(self) -> None:
        """Calculate 7-day rolling average from consumption history."""
        if not self._daily_consumption_history:
            self.weekly_avg_consumption = None
            return
        total = sum(entry["kwh"] for entry in self._daily_consumption_history)
        self.weekly_avg_consumption = round(total / len(self._daily_consumption_history), 2)

    def _compute_pv_fallback(self) -> float | None:
        """7-day rolling average of actual daily PV (#4).

        Used when forecast.solar is unavailable so the algorithm doesn't
        treat PV as zero — that would over-aggressively grid-charge on
        every clear day after a forecast service outage.

        Returns None when no actual PV history is available.
        """
        # We piggy-back on the daily consumption store: there is no separate
        # PV history yet, so return None for now and rely on yesterday's
        # actual when available via pv_actual_today_kwh on the previous tick.
        # Future: track _daily_pv_history alongside daily consumption.
        if self.pv_actual_today_kwh is None or self.pv_actual_today_kwh <= 0:
            return None
        # Rough fallback: assume today will produce similarly to today-so-far
        # extrapolated by remaining daylight.  This is a crude estimate but
        # better than zero.
        now = datetime.now()
        if now.hour < 6 or now.hour >= 20:
            return None  # outside daylight, can't extrapolate
        elapsed_daylight = max(1, now.hour - 6)
        total_daylight = 14
        return self.pv_actual_today_kwh * (total_daylight / elapsed_daylight)

    def _track_cycle_throughput(self, current_soc: float | None) -> None:
        """Accumulate charged/discharged kWh from SOC changes (#13).

        Called on each successful update.  Each positive delta counts as
        charging throughput; each negative as discharging.  Equivalent
        full cycles = min(charged, discharged) / capacity_kwh.
        """
        if current_soc is None or current_soc < 0 or current_soc > 100:
            return
        capacity = self.config_entry.options.get("battery_capacity_kwh", 10) or 10
        if self._last_soc_for_cycles is None:
            self._last_soc_for_cycles = current_soc
            return
        delta_pct = current_soc - self._last_soc_for_cycles
        # Ignore noise: only count changes >= 0.5% to avoid sensor jitter
        if abs(delta_pct) < 0.5:
            return
        delta_kwh = (delta_pct / 100.0) * capacity
        if delta_kwh > 0:
            self._cycle_charged_kwh += delta_kwh
        else:
            self._cycle_discharged_kwh += abs(delta_kwh)
        self._last_soc_for_cycles = current_soc
        self._update_soh_estimate()

    def _update_soh_estimate(self) -> None:
        """Estimate SOH from equivalent full cycles (#13).

        Uses a conservative LFP-style curve: ~0.005% capacity loss per
        equivalent full cycle, floored at 80% SOH.  For a 60 kWh battery,
        6000 cycles ≈ 80% SOH.  For NMC chemistries this would be more
        aggressive; the user can override via config later if needed.
        """
        capacity = self.config_entry.options.get("battery_capacity_kwh", 10) or 10
        if capacity <= 0:
            return
        equivalent_cycles = min(
            self._cycle_charged_kwh, self._cycle_discharged_kwh
        ) / capacity
        # 0.5% loss per 100 cycles, floor at 80%
        soh = max(0.80, 1.0 - equivalent_cycles * 0.00005)
        # Smooth: only update SOH when it changes by ≥ 0.1%
        if abs(soh - self._battery_soh_factor) >= 0.001:
            self._battery_soh_factor = soh
            _LOGGER.debug(
                "SOH update: equivalent_cycles=%.1f, soh=%.4f "
                "(charged=%.1f, discharged=%.1f kWh total)",
                equivalent_cycles, soh,
                self._cycle_charged_kwh, self._cycle_discharged_kwh,
            )

    async def _record_hourly_consumption(self, date_str: str) -> None:
        """Record hourly consumption breakdown using HA recorder history.

        Queries the consumption entity's history for the given date and bins
        energy usage into 24 hourly buckets.  Falls back to flat distribution
        from the daily total when history is unavailable.
        """
        hourly: dict[str, float] = {}  # str keys for JSON serialisation

        entity_id = self._resolve_consumption_entity()
        if entity_id:
            hourly = await self._query_hourly_from_history(entity_id, date_str)

        if not hourly:
            # Fallback: distribute daily total evenly across 24 hours
            today_entry = next(
                (e for e in self._daily_consumption_history if e["date"] == date_str), None
            )
            if today_entry:
                per_hour = round(today_entry["kwh"] / 24.0, 3)
                hourly = {str(h): per_hour for h in range(24)}

        if not hourly:
            return

        # Remove existing entry for this date
        self._hourly_consumption_history = [
            e for e in self._hourly_consumption_history if e["date"] != date_str
        ]
        self._hourly_consumption_history.append({"date": date_str, "hours": hourly})
        self._hourly_consumption_history = self._hourly_consumption_history[-7:]
        self._calculate_hourly_profile()

    def _resolve_consumption_entity(self) -> str | None:
        """Return the best entity_id to query for hourly consumption history.

        Resolution order:
          1. The user's consumption_override_entity (a real energy meter / P1).
          2. An inverter load-energy sensor this integration created — looked
             up via the ENTITY REGISTRY by the exact unique_id the sensor was
             registered with (`{entry_id}_{key}`).  The old code GUESSED the
             entity_id as `sensor.{title}_{key}`, but HA derives the entity_id
             from the slugified friendly NAME (e.g. "Homeload Day Cost Energy
             （0.1KWh）" → `..._homeload_day_cost_energy_0_1kwh`), so the guess
             never matched → resolution failed → the hourly profile silently
             fell back to a FLAT distribution.  That flat profile defeats the
             profile-aware overnight reserve (daytime-heavy / EV loads look
             like flat overnight consumption again).  The registry lookup is
             exact and rename-proof.
        """
        if self.consumption_override_entity:
            return self.consumption_override_entity

        from homeassistant.helpers import entity_registry as er
        ent_reg = er.async_get(self.hass)
        entry_id = self.config_entry.entry_id
        for key in ["daily_energy_consumed", "daily_load_energy",
                     "total_load_consumption_energy_day",
                     "load_consumption_energy_day",
                     "homeload_day_cost_energy",
                     "load_day_cost_energy"]:
            eid = ent_reg.async_get_entity_id("sensor", DOMAIN, f"{entry_id}_{key}")
            if not eid:
                continue
            state = self.hass.states.get(eid)
            if state and state.state not in ("unknown", "unavailable"):
                return eid

        # Legacy fallback: the old title-based guess (kept in case a future
        # entity isn't in the registry under the expected unique_id).
        for key in ["homeload_day_cost_energy", "load_day_cost_energy"]:
            eid = f"sensor.{self.config_entry.title.lower().replace(' ', '_')}_{key}"
            state = self.hass.states.get(eid)
            if state and state.state not in ("unknown", "unavailable"):
                return eid
        return None

    async def _query_hourly_from_history(self, entity_id: str, date_str: str) -> dict[str, float]:
        """Query HA recorder for hourly energy breakdown on a given date."""
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import (
                statistics_during_period,
            )
            from datetime import timezone

            start = datetime.strptime(date_str, "%Y-%m-%d").replace(
                hour=0, minute=0, second=0, tzinfo=timezone.utc
            )
            end = start + timedelta(hours=24)

            stats = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period,
                self.hass,
                start,
                end,
                {entity_id},
                "hour",
                None,
                {"change"},
            )

            if entity_id not in stats or not stats[entity_id]:
                return {}

            hourly: dict[str, float] = {}
            for entry in stats[entity_id]:
                hour = entry["start"].hour
                change = entry.get("change")
                if change is not None and change > 0:
                    # Convert Wh to kWh if needed
                    kwh = change / 1000.0 if change > 100 else change
                    hourly[str(hour)] = round(kwh, 3)

            return hourly
        except Exception as err:
            _LOGGER.debug("Could not query hourly consumption history: %s", err)
            return {}

    def _calculate_hourly_profile(self) -> None:
        """Build per-hour average consumption from 7-day hourly history."""
        if not self._hourly_consumption_history:
            self._hourly_consumption_profile = {}
            return

        hour_totals: dict[int, list[float]] = {h: [] for h in range(24)}
        for entry in self._hourly_consumption_history:
            hours = entry.get("hours", {})
            for h_str, kwh in hours.items():
                h = int(h_str)
                if 0 <= h <= 23 and kwh >= 0:
                    hour_totals[h].append(kwh)

        profile: dict[int, float] = {}
        for h in range(24):
            values = hour_totals[h]
            if values:
                profile[h] = round(sum(values) / len(values), 3)
            else:
                profile[h] = 0.0

        self._hourly_consumption_profile = profile
        _LOGGER.debug("Hourly consumption profile: %s", profile)

    def _record_soc_snapshot(self, battery_soc: float | None) -> None:
        """Record battery SOC at the current slot boundary (every 15 min)."""
        if battery_soc is None:
            return
        now = datetime.now()
        if not self.slot_prices_today:
            # No price slots — assume 96 slots (15-min granularity)
            num_slots = 96
        else:
            num_slots = len(self.slot_prices_today)
        minutes_per_slot = (24 * 60) / num_slots
        current_slot = int((now.hour * 60 + now.minute) / minutes_per_slot)
        current_slot = min(current_slot, num_slots - 1)

        if current_slot != self._last_recorded_slot:
            self._soc_history[current_slot] = round(battery_soc, 1)
            self._last_recorded_slot = current_slot

    def _calculate_yesterday_deficit(self, battery_soc: float | None) -> None:
        """At midnight, calculate how much energy target was missed yesterday."""
        if battery_soc is None:
            self._yesterday_deficit = 0.0
            return

        opts = self.config_entry.options
        grid_mode = opts.get("grid_mode", "off")

        if grid_mode not in ("from_grid", "both"):
            self._yesterday_deficit = 0.0
            return

        nominal_capacity = opts.get("battery_capacity_kwh", 10) or 10
        battery_capacity = nominal_capacity * self._battery_soh_factor
        charge_max = opts.get("battery_charge_max_level", 100)

        target_kwh = (charge_max / 100.0) * battery_capacity
        current_kwh = (battery_soc / 100.0) * battery_capacity

        deficit = max(0.0, target_kwh - current_kwh)
        self._yesterday_deficit = round(deficit, 2)

        if deficit > 0:
            _LOGGER.info("End-of-day deficit: %.2f kWh (SOC: %.1f%%, target: %d%%)",
                         deficit, battery_soc, charge_max)

    async def _check_safe_power(self, new_data: dict) -> int:
        """Return safe power level, temporarily reduced if current is high.
        Respects external changes (app/manual override) using fresh data.
        No extra reads needed — uses already-fetched new_data.

        Only active when safe_power_management is enabled (default: on when grid active).
        """

        opts = self.config_entry.options
        user_level = opts.get("power_level", 5)
        grid_mode = opts.get("grid_mode", "off")
        safe_power_enabled = opts.get("safe_power_management", "auto")

        # Determine if safe power management should be active
        # "auto" = active only when grid_mode is from_grid, to_grid, or both
        # "on" = always active (explicit override)
        # "off" = never active (external EMS handles everything)
        if safe_power_enabled == "off":
            self.safe_max_power = user_level
            return user_level
        if safe_power_enabled == "auto" and grid_mode == "off":
            self.safe_max_power = user_level
            return user_level

        max_amperage = opts.get("max_amperage_per_phase", 16)

        # --- 1. Safe base_level init ---
        base_level = getattr(self, "safe_max_power", 0)
        if base_level == 0:
            base_level = user_level

        # --- 2. Detect user power_level change → force write ---
        user_level_changed = False
        previous_user_level = getattr(self, "_last_user_power_level", None)
        if previous_user_level is not None and previous_user_level != user_level:
            _LOGGER.info(
                "Power level changed by user: %d → %d — applying immediately",
                previous_user_level, user_level
            )
            base_level = user_level
            user_level_changed = True
        self._last_user_power_level = user_level

        # Detect max_amperage config change
        previous_max = getattr(self, "_last_known_max_amperage", None)
        if previous_max is not None and previous_max != max_amperage:
            _LOGGER.info(
                "Max amperage changed: %.1fA → %.1fA — resetting to user level %d",
                previous_max, max_amperage, user_level
            )
            base_level = user_level
            user_level_changed = True
        self._last_known_max_amperage = max_amperage

        # --- 3. Early exit if no data ---
        if not new_data:
            _LOGGER.debug("No data yet")
            return base_level

        # --- 4. Get fresh currents and currently applied power limit ---
        max_current = self.TypeSpecificHandler.determine_max_amperage(new_data)
        if max_current is not None:
            new_data["highest_grid_current_now"] = max_current
        # This is the key: use the freshly read register value from new_data!
        applied_kwatts = self.TypeSpecificHandler.determine_rule_power(new_data) # works in kW
        if applied_kwatts is not None:
            detected_level = max(1, min(user_level, applied_kwatts))
            if abs(detected_level - base_level) >= 1:
                _LOGGER.info(
                    "External change detected: power limit %d → %d kW (likely via app) — syncing and re-evaluating safety",
                    base_level, detected_level
                )
                base_level = detected_level  # adopt the new higher (or lower) value

        # --- 5. Compute safe_level based on current grid draw ---
        safe_level = base_level

        if max_current is None or max_current == 0:
            # No grid current — safe to use user's desired level
            if base_level < user_level:
                safe_level = user_level
                _LOGGER.info("No grid current — recovering to user level %d (was %d)", user_level, base_level)
            else:
                _LOGGER.debug("No grid current — keeping current level %d", base_level)
        elif max_current > max_amperage * 0.95:
            # Try shedding flexible loads first before reducing battery power
            try:
                load_shed = await self._safe_power_shed_loads(max_current, max_amperage)
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Load shedding failed (non-fatal): %s", err)
                load_shed = False
            if not load_shed:
                safe_level = max(1, base_level - 2)
                _LOGGER.warning("High current %.1fA — reducing to level %d", max_current, safe_level)
        elif max_current > max_amperage * 0.8:
            safe_level = max(1, base_level - 1)
            _LOGGER.info("Moderate current %.1fA — reducing to level %d", max_current, safe_level)
        elif max_current < max_amperage * 0.7:
            new_level = min(user_level, base_level + 1)
            if new_level > base_level:
                safe_level = new_level
                _LOGGER.info("Low current %.1fA — recovering to level %d", max_current, safe_level)
            else:
                _LOGGER.debug("Low current but already at user max %d", base_level)
        else:
            _LOGGER.debug("Normal current %.1fA — maintaining level %d", max_current, base_level)

        # --- 6. Write if level changed OR user explicitly changed power_level ---
        if safe_level != base_level or user_level_changed:
            target_watts = int(round(safe_level * 1000))
            _LOGGER.info("Writing safe power limit: %dW (level %d)%s",
                         target_watts, safe_level,
                         " (user change)" if user_level_changed else "")
            try:
                await self.TypeSpecificHandler.write_type_specific_register("econ_rule_1_power", target_watts)
                self.safe_max_power = safe_level
            except Exception as err:
                _LOGGER.error("Failed to write power limit: %s", err)
                # Don't update internal state on failure → retry next cycle
        else:
            _LOGGER.debug("No change needed (level %d)", safe_level)
            self.safe_max_power = safe_level

        return safe_level
    
    async def _transition_to_state(self, new_state: str) -> bool:
        """Apply state change via economic rule 1. Returns True if critical writes succeeded."""
        opts = self.config_entry.options
        now = datetime.now()
        date_16bit = (now.month << 8) | now.day
        voltage_level = int(
            opts.get("voltage_level", 58)
            if new_state == "charging"
            else opts.get("discharge_min_voltage", 50)
        )
        if new_state == "charging":
            soc_limit = int(opts.get("battery_charge_max_level", 100))
        elif new_state == "discharging" and opts.get("price_mode", "manual") == "auto" and self._reserve_target_pct > 0:
            # In auto mode, use the computed reserve target as the inverter
            # SOC floor so the hardware enforces the same floor the schedule
            # was planned around — not just the lower discharge_min_level.
            soc_limit = int(round(self._reserve_target_pct))
            _LOGGER.info(
                "Discharge SOC floor set to reserve target %d%% "
                "(discharge_min=%d%%)",
                soc_limit, int(opts.get("battery_discharge_min_level", 20)),
            )
        else:
            soc_limit = int(opts.get("battery_discharge_min_level", 20))

        enable_value = {"charging": 1, "discharging": 2, "idle": 0}[new_state]

        _LOGGER.info(
            "Energy state → %s | Price: %.4f | Threshold: %.4f | SOC limit: %d%% | Voltage: %dV",
            new_state.upper(),
            self.current_price or 0,
            self.price_threshold or 0,
            soc_limit,
            voltage_level,
        )
        # Set operating mode FIRST (system_mode, sell_enable, eco_timeofuse) so
        # that when the economic rule is activated the inverter already sees the
        # correct mode.  On TREX-25/50, enabling the rule before sell_enable is
        # set causes the inverter to latch a "charge" interpretation when
        # sell_enable is stale from a previous charging cycle.  Writing mode
        # first eliminates the race.
        #
        # CRITICAL: the mode write is now checked.  For TREX-25/50 it sets
        # eco_timeofuse=1 (Economic mode) — without it the inverter stays in
        # General mode and silently ignores Rule 1.  If the mode write fails we
        # must NOT proceed to set econ_rule_1_enable: that would leave the
        # inverter with "rule 1 = charge" but "mode = General", i.e. inert,
        # exactly the reported failure.  Abort and let the next cycle retry
        # the whole transition atomically.
        mode_ok = await self.TypeSpecificHandler.write_type_specific_register("operating_mode", enable_value)
        if not mode_ok and new_state != "idle":
            _LOGGER.error(
                "CRITICAL: Failed to set operating mode (Economic) for state %s — "
                "skipping rule-1 enable to avoid an inert 'enable=%s, mode=General' "
                "state; will retry next cycle",
                new_state, new_state,
            )
            return False
        # The enable write is the critical one — if it fails, the inverter won't change state
        enable_ok = await self.TypeSpecificHandler.write_type_specific_register("econ_rule_1_enable", enable_value)
        if not enable_ok:
            _LOGGER.error(
                "CRITICAL: Failed to write econ_rule_1_enable=%d for state %s — inverter may be out of sync",
                enable_value, new_state,
            )
            return False
        if new_state != "idle":
            for reg, val in [
                ("econ_rule_1_soc", soc_limit),
                ("econ_rule_1_start_day", date_16bit),
                ("econ_rule_1_stop_day", date_16bit),
                ("econ_rule_1_voltage", voltage_level),
                ("econ_rule_1_power", int(round(self.safe_max_power * 1000))),
            ]:
                ok = await self.TypeSpecificHandler.write_type_specific_register(reg, val)
                if not ok:
                    _LOGGER.warning("Failed to write %s=%s during %s transition", reg, val, new_state)
        return True

    async def _apply_rule1_auto_settings(self) -> None:
        """If rule 1 auto settings are enabled, ensure the inverter's
        time-window and weekday-mask match the auto defaults.

        Writes only when the current register value doesn't match the
        target — so this is safe to call on every state activation.
        Felicity's 24-hour convention is start=00:00, stop=23:59 (the
        firmware doesn't accept stop=00:00 or stop=24:00).
        """
        if not self.data:
            return

        opts = self.config_entry.options

        if opts.get("rule1_time_window", "manual") == "auto":
            target_start = 0                       # 00:00
            target_stop = (23 << 8) | 59           # 23:59 (Felicity 24h)
            current_start = self.data.get("econ_rule_1_start_time")
            current_stop = self.data.get("econ_rule_1_stop_time")
            if current_start != target_start:
                ok = await self.TypeSpecificHandler.write_type_specific_register(
                    "econ_rule_1_start_time", target_start
                )
                if ok:
                    self.data["econ_rule_1_start_time"] = target_start
                    _LOGGER.info(
                        "Rule 1 auto: wrote start_time=00:00 (was %s)",
                        current_start,
                    )
            if current_stop != target_stop:
                ok = await self.TypeSpecificHandler.write_type_specific_register(
                    "econ_rule_1_stop_time", target_stop
                )
                if ok:
                    self.data["econ_rule_1_stop_time"] = target_stop
                    _LOGGER.info(
                        "Rule 1 auto: wrote stop_time=23:59 (was %s)",
                        current_stop,
                    )

        if opts.get("rule1_weekday", "manual") == "auto":
            target_week = 0x7F  # all 7 days enabled (bit0=Sun..bit6=Sat)
            current_week = self.data.get("econ_rule_1_effective_week")
            if current_week != target_week:
                ok = await self.TypeSpecificHandler.write_type_specific_register(
                    "econ_rule_1_effective_week", target_week
                )
                if ok:
                    self.data["econ_rule_1_effective_week"] = target_week
                    _LOGGER.info(
                        "Rule 1 auto: wrote effective_week=all days (was 0x%02X)",
                        current_week if isinstance(current_week, int) else 0,
                    )

    async def _ensure_economic_mode_when_active(self) -> None:
        """Self-heal: re-assert Economic mode if the inverter silently dropped it.

        The coordinator only calls _transition_to_state when the *desired*
        state changes.  But on TREX-25/50 the inverter can fall out of
        Economic mode (eco_timeofuse → 0, i.e. "General mode") while we still
        believe we're charging/discharging — a firmware quirk, the Felicity
        app, or a power blip can reset it.  When that happens Rule 1 goes
        inert: econ_rule_1_grid_charge_enable stays 1 but the inverter ignores
        it, so the battery just sits there (the exact reported failure:
        "enable=charge but mode=General, battery not charging").  No state
        change occurs, so nothing re-writes the mode.

        This runs every cycle.  When we're in an active state but the
        Economic-mode register reads disabled, re-write the operating mode to
        bring the inverter back under Rule 1 control.  Idempotent: only writes
        when the register actually shows Economic mode is off.

        The Economic-mode register differs by model:
          - TREX-25/50: `eco_timeofuse` (1 = Economic, 0 = General)
          - TREX-5/10:  `operating_mode` (2 = Economic, 0 = General,
                        1 = Backup)
        """
        if self._current_energy_state not in ("charging", "discharging"):
            return
        if not self.data:
            return

        enable_value = {"charging": 1, "discharging": 2}[self._current_energy_state]

        if self.inverter_model in (
            INVERTER_MODEL_TREX_TWENTY_FIVE, INVERTER_MODEL_TREX_FIFTY
        ):
            eco = self.data.get("eco_timeofuse")
            if eco is None or int(eco) == 1:
                return  # register not read, or Economic mode already active
            register_val = eco
        elif self.inverter_model in (
            INVERTER_MODEL_TREX_FIVE, INVERTER_MODEL_TREX_TEN
        ):
            mode = self.data.get("operating_mode")
            if mode is None or int(mode) == 2:
                return  # register not read, or Economic mode already active
            register_val = mode
        else:
            return

        _LOGGER.warning(
            "Self-heal: inverter dropped out of Economic mode (register=%s) "
            "while state=%s — re-asserting operating mode so Rule 1 resumes "
            "(battery was likely sitting inert)",
            register_val, self._current_energy_state,
        )
        ok = await self.TypeSpecificHandler.write_type_specific_register(
            "operating_mode", enable_value
        )
        if ok:
            # Reflect the re-assertion in cached data so we don't re-trigger
            # before the next read.
            if self.inverter_model in (
                INVERTER_MODEL_TREX_TWENTY_FIVE, INVERTER_MODEL_TREX_FIFTY
            ):
                self.data["eco_timeofuse"] = 1
            else:
                self.data["operating_mode"] = 2
        else:
            _LOGGER.error(
                "Self-heal: failed to re-assert Economic mode for state %s",
                self._current_energy_state,
            )

    def get_energy_state_info(self) -> dict:
        """Get current energy management state info (useful for debugging sensor)."""
        opts = self.config_entry.options
        info = {
            "current_state": self._current_energy_state,
            "price_mode": opts.get("price_mode", "manual"),
            "last_change": self._last_state_change.isoformat() if self._last_state_change else None,
            "current_price": self.current_price,
            "price_threshold": self.price_threshold,
            "max_price": self.max_price,
            "min_price": self.min_price,
            "avg_price": self.avg_price,
            "safe_max_power": self.safe_max_power,
            "pv_forecast_today": self.pv_forecast_today,
            "pv_forecast_remaining": self.pv_forecast_remaining,
            "pv_forecast_tomorrow": self.pv_forecast_tomorrow,
            "cheap_slots_remaining": self.cheap_slots_remaining,
            "grid_energy_planned": self.grid_energy_planned,
            "schedule_status": self.schedule_status,
            "scheduled_slot_count": len(self.scheduled_slots),
            "scheduled_charge_slots": sum(1 for v in self.scheduled_slots.values() if v == "charge"),
            "scheduled_discharge_slots": sum(1 for v in self.scheduled_slots.values() if v == "discharge"),
            "price_slots_today": len(self.slot_prices_today) if self.slot_prices_today else 0,
            "slot_granularity_min": int((24 * 60) / len(self.slot_prices_today)) if self.slot_prices_today else None,
            "available_slots_at_threshold": self.available_slots_at_threshold,
            "available_energy_capacity": self.available_energy_capacity,
            "charge_likelihood": self.charge_likelihood,
            "weekly_avg_consumption": self.weekly_avg_consumption,
            "yesterday_deficit": self._yesterday_deficit,
            "self_consumption_reserve": self.self_consumption_reserve,
            "reserve_target_pct": self._reserve_target_pct,
            "consumption_hourly_profile": self._hourly_consumption_profile or {},
            "soc_history": self._soc_history,
            "slot_overrides": self.slot_overrides if self.slot_overrides else {},
        }

        # Add kWh for all Wh registers
        if self.data:
            for key, value in self.data.items():
                info_key = self.register_map.get(key, {})
                if info_key.get("unit") == "Wh" and value is not None:
                    info[f"{key}_kwh"] = round(value / 1000.0, 3)
        return info
        

        
    async def _async_update_data(self) -> dict:
        """Fetch latest data from inverter."""
        if not await self._async_connect():
            raise UpdateFailed("Cannot connect to Felicity inverter")

        new_data = {}
        any_read_ok = False

        try:
            for group in self._address_groups:
                start_addr = group["start"]
                count = group["count"]
                try:
                    result = await self.client.read_holding_registers(
                        address=start_addr,
                        count=count,
                        device_id=self.slave_id,
                    )
                except Exception as err:
                    _LOGGER.error("Read error at address %d, count: %d error:%s", start_addr, count, err)
                    continue

                if result.isError():
                    _LOGGER.warning("Read error at address %d, skipping group", start_addr)
                    continue

                any_read_ok = True

                registers = result.registers
                pos = 0
                for key in group["keys"]:
                    info = self.register_map.get(key)
                    if info is None:
                        _LOGGER.warning("Key '%s' not in register_map, skipping", key)
                        continue
                    size = info.get("size", 1)
                    endian = info.get("endian", "big")
                    index = info.get("index", 0)
                    precision = info.get("precision", 0)

                    if pos + size > len(registers):
                        _LOGGER.warning("Insufficient registers for %s", key)
                        break

                    reg_slice = registers[pos:pos + size]
                    pos += size
                    # Reconstruct raw value
                    raw = 0
                    if size == 1:
                        raw = reg_slice[0]
                    elif size == 2:
                        if endian == "big":
                            raw = (reg_slice[0] << 16) | reg_slice[1]
                        else:
                            raw = (reg_slice[1] << 16) | reg_slice[0]
                    elif size == 4:
                        if endian == "big":
                            raw = (reg_slice[0] << 48) | (reg_slice[1] << 32) | (reg_slice[2] << 16) | reg_slice[3]
                        else:
                            raw = (reg_slice[3] << 48) | (reg_slice[2] << 32) | (reg_slice[1] << 16) | reg_slice[0]
                        if index == 3 and raw >= (1 << 63):
                            raw -= (1 << 64)
                    else:
                        _LOGGER.warning("Unsupported register size %d for key %s", size, key)
                        continue

                    value = self._apply_scaling(raw, index, size)
                    if isinstance(value, float):
                        value = round(value, precision)

                    new_data[key] = value
            # dynamically check which system we have an appropriated settings.
            operational_mode = self.TypeSpecificHandler.determine_operational_mode(new_data)
            new_data["operational_mode"] = operational_mode
            raw_system_voltage = self.TypeSpecificHandler.determine_battery_voltage(new_data)
            new_data["battery_nominal_voltage"] = raw_system_voltage
#             _LOGGER.debug("Battery voltage retrieved: %dV", raw_system_voltage)
            safe_power_level = await self._check_safe_power(new_data) # check if current power is safe with settings only when integration is regulating power.
            new_data["safe_max_power"] = int(safe_power_level * 1000) # convert from 1-10 scale to watts
            # === Nordpool price update & dynamic logic ===
            if self.nordpool_entity: # do we have any price state information?
                price_state = None
                try: # when nordpool or override is disabled or uninstalled during runtime you get an exception here
                  if self.override_nordpool_entity: # override has precedence
                      price_state = self.hass.states.get(self.override_nordpool_entity)
                  elif self.original_nordpool_entity:
                      price_state = self.hass.states.get(self.original_nordpool_entity)
                  else:    
                      price_state = None # Should not happen as there would not be any entity declared on init
                except Exception:
                    _LOGGER.exception("Felicity coordinator error, price state not retreivable!")
                    if self.original_nordpool_entity:
                        try: # let's try to go back to the default in case override was there but no longer is
                          price_state = self.hass.states.get(self.original_nordpool_entity)
                        except Exception:
                            _LOGGER.exception("Felicity coordinator error, price state nordpool no longer available!")
                            self.current_price = self.min_price = self.avg_price = self.max_price = self.price_threshold = None
                            return new_data # return with what we do have
                    else:
                        self.current_price = self.min_price = self.avg_price = self.max_price = self.price_threshold = None
                        return new_data # return with what we do have
                if price_state and price_state.state not in ("unknown", "unavailable", "none"):
                    try:
                        self.current_price = float(price_state.state)
                        new_data["current_price"] = self.current_price
                        attrs = price_state.attributes

                        def get_attr(names):
                            for name in names:
                                val = attrs.get(name)
                                if val is not None:
                                    return val
                            return None

                        self.max_price = get_attr(["max", "max_price", "Max price", "max price"])
                        self.min_price = get_attr(["min", "min_price", "Min price", "min price"])
                        self.avg_price = get_attr(["average", "average_price", "avg_price", "Avg price", "avg"])
                        new_data["max_price"] = self.max_price
                        new_data["min_price"] = self.min_price
                        new_data["avg_price"] = self.avg_price
                        if self.avg_price is not None and self.min_price is not None and self.max_price is not None:
                            # Always calculate manual threshold (used in manual mode, and for available_info in both)
                            level = self.config_entry.options.get("price_threshold_level", 5)
                            if level <= 5:
                                ratio = (level - 1) / 4.0
                                manual_threshold = self.min_price + (self.avg_price - self.min_price) * ratio
                            else:
                                ratio = (level - 5) / 5.0
                                manual_threshold = self.avg_price + (self.max_price - self.avg_price) * ratio

                            price_mode = self.config_entry.options.get("price_mode", "manual")

                            if price_mode == "manual":
                                self.price_threshold = manual_threshold
                            elif self.price_threshold is None:
                                # Auto mode first-run: use manual as initial value
                                # until the schedule computes its own threshold.
                                # Once the schedule has run, its threshold is
                                # authoritative and must NOT be overwritten here
                                # every tick — that caused the threshold to bounce
                                # between the manual calculation and the schedule's
                                # max-charge-price on every 10s cycle.
                                self.price_threshold = manual_threshold

                            new_data["price_threshold"] = self.price_threshold

                            # Initialize consumption store on first run
                            await self._init_consumption_store()

                            # Midnight bookkeeping (once per day change)
                            now = datetime.now()
                            if self._current_day != now.day:
                                first_boot = self._current_day is None
                                if first_boot:
                                    _LOGGER.info(
                                        "First tick — initializing day tracking "
                                        "(skipping consumption recording for partial day)"
                                    )
                                else:
                                    _LOGGER.info(
                                        "New day detected — running midnight bookkeeping"
                                    )
                                battery_soc = self.TypeSpecificHandler.determine_battery_soc(new_data)
                                self.battery_soc = battery_soc
                                if not first_boot:
                                    # Record deficit before rolling over (for next-day compensation)
                                    self._calculate_yesterday_deficit(battery_soc)
                                    # Record daily consumption for rolling average
                                    await self._record_daily_consumption()
                                self._soc_history = {}
                                self._last_recorded_slot = -1
                                self._pv_integrated_today_kwh = 0.0
                                self._current_day = now.day

                                # Propagate tomorrow's slot overrides → today
                                await self._rotate_slot_overrides()
                                # Do NOT force-idle the inverter here.  A discharge
                                # that's valid at 23:59 is usually still valid at
                                # 00:01 (e.g., high evening price extending into
                                # early morning, or a customer selling overnight
                                # before negative-midday PV refills the battery).
                                # The normal cycle below re-determines state from
                                # current prices and only writes a transition if
                                # the state actually changes.

                            # Normal cycle: retrieve data, calculate, determine state
                            self._retrieve_slot_prices(price_state)
                            self._retrieve_pv_forecast()

                            battery_soc = self.TypeSpecificHandler.determine_battery_soc(new_data)
                            self.battery_soc = battery_soc
                            self._record_soc_snapshot(battery_soc)
                            # Refresh staleness ts (#6) only when at least one
                            # register group actually read AND SOC parsed —
                            # otherwise the guard could never trigger.
                            if any_read_ok and battery_soc is not None:
                                self._last_modbus_success_ts = time.time()
                            # Cycle counting + SOH update (#13)
                            self._track_cycle_throughput(battery_soc)
                            # PV power integration (generator-port solar fix)
                            self._integrate_pv_power()

                            # In auto mode, run the schedule optimizer
                            if price_mode == "auto":
                                await self._calculate_schedule(battery_soc)
                                # Schedule may have updated self.price_threshold
                                new_data["price_threshold"] = self.price_threshold

                            # Always calculate available info (visible in both modes)
                            self._calculate_available_info(battery_soc)

                            # Apply rule 1 time-window / weekday auto settings
                            # if enabled.  Writes are idempotent — only happens
                            # when the register doesn't already match the target.
                            await self._apply_rule1_auto_settings()

                            # Warn if the planned schedule falls outside the
                            # inverter's Economic Rule 1 time/weekday window
                            # (we don't write those registers in manual mode,
                            # so the inverter would silently ignore our enable
                            # command there).
                            self.rule1_window_warning = self._check_rule1_window_conflict()

                            # Update new_data with all results
                            new_data["pv_forecast_today"] = self.pv_forecast_today
                            new_data["pv_forecast_remaining"] = self.pv_forecast_remaining
                            new_data["pv_forecast_tomorrow"] = self.pv_forecast_tomorrow
                            new_data["cheap_slots_remaining"] = self.cheap_slots_remaining
                            new_data["grid_energy_planned"] = self.grid_energy_planned
                            new_data["schedule_status"] = self.schedule_status
                            new_data["available_slots_at_threshold"] = self.available_slots_at_threshold
                            new_data["available_energy_capacity"] = self.available_energy_capacity
                            new_data["charge_likelihood"] = self.charge_likelihood
                            new_data["weekly_avg_consumption"] = self.weekly_avg_consumption

                            desired_state = self._determine_energy_state(battery_soc)

                            # Anti-conflict guard: don't export while the house is
                            # importing (e.g. EV charging pulls from grid while we'd
                            # be selling battery — wasteful round-trip).  Uses
                            # hysteresis to avoid flipping discharge → idle → discharge
                            # on transient load spikes (kettle, microwave, EV start):
                            #   - small/moderate import (200-2000W) must persist for
                            #     ≥ANTICONFLICT_MIN_TICKS consecutive cycles
                            #   - large import (>2000W) suppresses immediately
                            #   - once suppression ends, hold a cooldown window
                            #     before allowing re-suppression
                            ANTICONFLICT_SOFT_THRESHOLD_W = 200
                            ANTICONFLICT_HARD_THRESHOLD_W = 2000
                            ANTICONFLICT_MIN_TICKS = 2
                            ANTICONFLICT_COOLDOWN_S = 60
                            grid_power = None
                            if desired_state == "discharging":
                                if hasattr(self.TypeSpecificHandler, 'determine_grid_power'):
                                    grid_power = self.TypeSpecificHandler.determine_grid_power(new_data)
                                if grid_power is not None and grid_power > ANTICONFLICT_SOFT_THRESHOLD_W:
                                    self._anticonflict_import_ticks += 1
                                    in_cooldown = time.time() < self._anticonflict_suppress_until_ts
                                    sustained = self._anticonflict_import_ticks >= ANTICONFLICT_MIN_TICKS
                                    large = grid_power > ANTICONFLICT_HARD_THRESHOLD_W
                                    if (sustained or large) and not in_cooldown:
                                        _LOGGER.info(
                                            "Anti-conflict: suppressing discharge — grid importing "
                                            "%.0fW (sustained=%d ticks, large=%s) — would sell "
                                            "battery while buying from grid",
                                            grid_power, self._anticonflict_import_ticks, large,
                                        )
                                        desired_state = "idle"
                                        self._anticonflict_suppress_until_ts = (
                                            time.time() + ANTICONFLICT_COOLDOWN_S
                                        )
                                    else:
                                        _LOGGER.debug(
                                            "Anti-conflict: tolerating brief import %.0fW "
                                            "(tick %d/%d, cooldown=%s) — keeping discharge",
                                            grid_power,
                                            self._anticonflict_import_ticks,
                                            ANTICONFLICT_MIN_TICKS,
                                            in_cooldown,
                                        )
                                else:
                                    if self._anticonflict_import_ticks > 0:
                                        _LOGGER.debug(
                                            "Anti-conflict: import cleared (was %d ticks), "
                                            "grid_power=%.0fW",
                                            self._anticonflict_import_ticks,
                                            grid_power if grid_power is not None else 0,
                                        )
                                    self._anticonflict_import_ticks = 0
                            else:
                                # Not trying to discharge — reset the counter so a
                                # past spike doesn't carry over into the next
                                # discharge window.
                                self._anticonflict_import_ticks = 0

                            # Minimum charge commitment (anti flip-flop).
                            # When we're charging and the schedule suddenly
                            # wants idle, hold the charge until the commitment
                            # is satisfied.  This kills the seconds-scale
                            # charge→off storm that occurs when SOC hovers
                            # near the reserve target and the marginal deficit
                            # oscillates in and out of the plan each tick.
                            MIN_CHARGE_SOC_GAIN = 5.0      # %
                            MIN_CHARGE_DURATION_S = 900    # 15 min (one slot)
                            _commit_opts = self.config_entry.options
                            charge_max_pct = _commit_opts.get("battery_charge_max_level", 100)
                            commit_grid_mode = _commit_opts.get("grid_mode", "off")
                            if (self._current_energy_state == "charging"
                                    and desired_state == "idle"
                                    and commit_grid_mode in ("from_grid", "both")
                                    and battery_soc is not None
                                    and battery_soc < charge_max_pct
                                    and self._charge_commit_start_soc is not None):
                                soc_gain = battery_soc - self._charge_commit_start_soc
                                time_held = time.time() < self._charge_commit_until_ts
                                if soc_gain < MIN_CHARGE_SOC_GAIN and time_held:
                                    _LOGGER.info(
                                        "Charge commitment: holding charge (gain %.1f%% "
                                        "< %.1f%%, %.0fs left) — preventing flip-flop",
                                        soc_gain, MIN_CHARGE_SOC_GAIN,
                                        self._charge_commit_until_ts - time.time(),
                                    )
                                    desired_state = "charging"

                            _LOGGER.debug(
                                "State decision: desired=%s, current=%s, soc=%s%%, "
                                "price=%s, threshold=%s, grid_power=%s",
                                desired_state, self._current_energy_state,
                                f"{battery_soc:.1f}" if battery_soc is not None else "?",
                                f"{self.current_price:.4f}" if self.current_price is not None else "?",
                                f"{self.price_threshold:.4f}" if self.price_threshold is not None else "?",
                                f"{grid_power:.0f}W" if grid_power is not None else "?",
                            )

                            if desired_state != self._current_energy_state:
                                success = await self._transition_to_state(desired_state)
                                if success:
                                    # Arm / disarm the charge commitment on the
                                    # transition edge so each charge episode is
                                    # a real block (anti flip-flop).
                                    if desired_state == "charging":
                                        self._charge_commit_start_soc = battery_soc
                                        self._charge_commit_until_ts = (
                                            time.time() + MIN_CHARGE_DURATION_S
                                        )
                                    else:
                                        self._charge_commit_start_soc = None
                                        self._charge_commit_until_ts = 0.0
                                    self._current_energy_state = desired_state
                                    self._last_state_change = now
                                else:
                                    _LOGGER.warning(
                                        "State transition to %s failed — will retry next cycle (inverter may still be in %s)",
                                        desired_state, self._current_energy_state,
                                    )
                            else:
                                # No state change this cycle, but verify the
                                # inverter hasn't silently dropped out of
                                # Economic mode while we believe we're active.
                                await self._ensure_economic_mode_when_active()
                        else:
                            _LOGGER.debug(
                                "Cannot calculate price threshold: missing data (min=%s, avg=%s, max=%s)",
                                self.min_price, self.avg_price, self.max_price
                            )
                            self.price_threshold = None

                    except ValueError:
                        self.current_price = None
                        self.price_threshold = None
            else:
                self.current_price = None
                self.price_threshold = None

            # Actuate flexible loads based on current schedule slot.
            # Wrapped in try/except so a flex-load failure (entity
            # unavailable, service call error, bad config) never kills
            # the main update cycle — the inverter must keep running its
            # charge/discharge schedule regardless of accessory loads.
            try:
                await self._actuate_flex_loads()
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Flex load actuation failed (non-fatal): %s", err)

            return new_data

        except ConnectionException as err:
            self.connected = False
            await self.client.close()
            raise UpdateFailed(f"Connection lost: {err}")
        except ModbusException as err:
            raise UpdateFailed(f"Modbus error: {err}")
        except Exception as err:
            _LOGGER.exception("Unexpected error in Felicity coordinator update")
            raise UpdateFailed(f"Unexpected update error: {err}")
