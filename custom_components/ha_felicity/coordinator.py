"""Data update coordinator for Felicity with proper async handling."""

import asyncio
import dataclasses
import json
import logging
import math
import time
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pymodbus.exceptions import ConnectionException, ModbusException

from . import ems as ems_module
from .const import (
    CONF_INVERTER_MODEL,
    DEFAULT_INVERTER_MODEL,
    DOMAIN,
    ECO_TIMEOFUSE_MODELS,
    INVERTER_MAX_POWER_KW,
    INVERTER_MODEL_TREX_TEN,
    INVERTER_PHASES,
    MONITOR_ONLY_MODELS,
    OPERATING_MODE_MODELS,
)
from .plausibility import PlausibilityGuard
from .type_specific import TypeSpecificHandler

_LOGGER = logging.getLogger(__name__)


def _milp_status_snapshot() -> dict | None:
    """Read milp.py's solver diagnosis, tolerating a missing/broken module.

    Imported lazily and defensively: milp.py is optional (it needs pulp), and
    the whole point of this call is to explain a failure — it must never be
    able to cause one.
    """
    try:
        from . import milp as milp_module

        return milp_module.milp_status()
    except Exception as err:  # pragma: no cover - diagnostic must never raise
        return {
            "state": "disabled",
            "reason": f"MILP module unavailable: {type(err).__name__}: {err}",
            "solver": "",
            "tried": [],
        }

# Unresponsive-inverter protection.  A read that gets NO response costs the
# client's full timeout x retries (~20 s), so without a limit a silent inverter
# turns one poll into 74 groups x 20 s = ~25 min — and the first refresh, which
# HA waits on during setup, never finishes.  After this many consecutive
# no-response reads the cycle is abandoned and polling pauses, doubling from
# the initial pause up to the max, until the inverter answers again.
MAX_CONSECUTIVE_READ_TIMEOUTS = 3
READ_BACKOFF_INITIAL_S = 30
READ_BACKOFF_MAX_S = 300

# Non-responding read groups.  Some firmware (or gateway) stays SILENT on part
# of a block instead of returning a Modbus exception — e.g. a T-REX-5 behind a
# Waveshare TCP-to-RTU gateway at 2400 baud never answered the 36-register read
# at 8568 while every other read worked, costing ~20 s every poll.  When a
# multi-register group times out while other reads in the same poll succeed,
# it is split in half for the next poll (bisecting to the culprit); a single
# register that still times out in DEAD_REGISTER_STRIKES polls is dropped from
# polling until the integration reloads.  When BOTH halves stay silent the
# whole block is dropped instead (the model lacks it) — see _adapt_read_groups.
# Strikes count CONSECUTIVE misses, and a register that has answered even once
# is never dropped: the same reporter later showed the block exists and answers
# intermittently (a flaky link fails long reads more often than short ones).
DEAD_REGISTER_STRIKES = 3
# A block that has answered before but now misses is split no further than this
# many registers (one T-REX-5 ECO rule): small enough for a flaky link to get
# through within pymodbus's retries, without fragmenting into single reads.
FLAKY_MIN_REGISTERS = 9
# A poll slower than this many update intervals logs one warning (per hour)
# with the statistics needed to diagnose it.
SLOW_POLL_FACTOR = 2

# Consumption history (the hourly profile behind the reserve and trajectory).
# A day counts as real only when the recorder has statistics for most of its
# hours; fewer means the recorder was down and the day would bend the shape.
CONSUMPTION_HISTORY_DAYS = 7
MIN_HOURS_FOR_HISTORY_DAY = 20

# Daily mean grid prices: this week's typical price.  Greedy and MILP buy
# extra energy for the coming days only below it after losses (so the cheapest
# day of the week fills the battery).  Needs a few days before it means anything.
PRICE_HISTORY_DAYS = 7
MIN_PRICE_HISTORY_DAYS = 3


def _number(value) -> float | None:
    """A real number from a recorder row field, else None (bool is not a number)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


CONSUMPTION_BACKFILL_RETRY_S = 300

# Seconds safe power waits after CUTTING the rule-1 power limit before it acts
# on the grid current again.  The inverter ramps to a new limit, so the next
# poll still measures the old, higher power: 22 Oct 2026, "20.7 A — cutting to
# level 3", 16 s later "19.5 A — cutting to level 1", where level 3 was enough.
# (After a raise the stale reading is LOW, which cannot cause an over-cut, so
# a raise starts no wait — a charge that overloads a phase is cut at once.)
SAFE_POWER_SETTLE_S = 30
# A cut aims the busiest phase at this share of max_amperage_per_phase, and the
# Power Level returns once it would keep every phase under it: a margin for a
# load that moves, without cutting before the limit is actually exceeded.
SAFE_POWER_TARGET = 0.9

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
        self._address_groups = list(groups)   # own copy: adaptive splitting replaces entries
        self._group_timeout_strikes: dict[tuple[int, int], int] = {}
        self.dead_register_keys: set[str] = set()
        self._answered_keys: set[str] = set()   # read successfully at least once
        self.poll_stats: dict = {}
        self._last_slow_poll_warning: float = 0.0
        self.config_entry = config_entry
        self._last_register_set: str | None = None
        self.model_combined = model_combined
        self.inverter_model = inverter_model if inverter_model else INVERTER_MODEL_TREX_TEN
        self._inverter_max_power_kw = INVERTER_MAX_POWER_KW.get(self.inverter_model, 10)
        # Impossible readings never reach the sensors or the EMS (issue #220).
        self._plausibility = PlausibilityGuard(register_map, self._inverter_max_power_kw)
        self._reply_count_mismatches = 0
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
        self._safe_power_cut_ts = 0.0  # when safe power last cut the rule-1 power
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
        # Which forecast attribute the hourly curves came from ("wh_hours",
        # "wh_period", ...), "synthetic" when only a daily total exists, or
        # "none".  Shown on the card: a synthetic curve is a guess.
        self.pv_curve_source: str = "none"
        self.pv_curve_source_tomorrow: str = "none"
        self.sunrise_hour: float | None = None
        self.sunset_hour: float | None = None
        self.scheduled_slots: dict[int, str] = {}  # {slot_idx: "charge" | "discharge"}
        self.slot_overrides: dict = config_entry.options.get("slot_overrides", {})  # manual overrides from card, persisted in entry.options
        self.cheap_slots_remaining: int = 0
        self.grid_energy_planned: float = 0.0
        self.schedule_status: str = "unknown"
        self.schedule_reason: str = ""
        self.scheduler_active: str = "greedy"
        # {"state","reason","solver","tried"} when scheduler_engine == "milp",
        # else None.  Surfaced as the `milp_status` schedule_status attribute so
        # a "Greedy (fallback)" chip can explain itself in the UI.
        self.milp_status: dict | None = None

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
        self._hourly_consumption_history: list = []  # [{date, hours: {0: kwh, 1: kwh, ...}, source}]
        # The recorder backfill (see _backfill_consumption_history) runs once
        # the consumption entity can be resolved; retried every few minutes
        # until then.
        self._consumption_backfilled = False
        self._history_query_error: str | None = None
        # Daily mean price per day ([{date, mean}]), persisted with the
        # consumption history; rebuilt from the recorder once at startup.
        self._daily_price_history: list = []
        self._price_backfilled = False
        self._consumption_backfill_attempt_ts = 0.0
        self.consumption_entity_used: str | None = None
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

        # Unresponsive-inverter backoff (see MAX_CONSECUTIVE_READ_TIMEOUTS).
        self._read_backoff_s: float = 0.0
        self._read_backoff_until: float = 0.0

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
        # Loads switched off by safe power stay off until this epoch ts, even
        # when the schedule wants them on (see _safe_power_shed_loads).
        self._flex_load_shed_until: dict[int, float] = {}
        self._last_max_grid_current: float | None = None
        self._last_phase_currents: list[float] | None = None
        self._flex_load_current_step: int | None = None
        self._flex_load_scheduled: dict[int, dict[int, bool]] = {}  # from ScheduleResult.load_slots
        # Planned EV current per slot (ScheduleResult.load_currents) and the
        # slot each load's planned current was last applied in.
        self._flex_load_currents: dict[int, dict[int, int]] = {}
        self._flex_load_current_slot: dict[int, int] = {}
        self._flex_load_scheduled_tomorrow: dict[int, dict[int, bool]] = {}
        self._ev_boost_until_ts: float = 0.0  # epoch ts — EV override active when now < this
        # Max current applied for the current boost session.  Applied once
        # (not every tick) so safe-power step-downs aren't fought; reset on
        # each boost press so a new press re-raises to max.
        self._ev_boost_max_applied: bool = False
        # The boost's end time is persisted: a restart (an update install)
        # during a boost used to cancel it silently.  Restored lazily on the
        # first actuation, because the store is async and __init__ is not.
        self._ev_boost_store = None
        self._ev_boost_restored: bool = False

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
    def _group_addresses(self, reg_map: dict) -> dict[int, list]:
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
        # Full battery during a charge slot: STAY charging.  Rule 1's SOC
        # register (= charge_max) already stops the inverter there and the
        # house runs on grid, as the slot intended.  Dropping to idle instead
        # put the house back on the battery, SOC fell to 99.9 %, the next tick
        # re-armed the charge — a full rule rewrite every poll for the rest of
        # the slot (found by the day-replay harness).
        room_or_charging = (battery_soc < charge_max
                            or self._current_energy_state == "charging")

        if price_mode == "auto":
            # Auto mode: schedule-based decision
            if self.slot_prices_today and self.scheduled_slots:
                slot_idx = self._current_slot_index()
                if slot_idx is not None and slot_idx in self.scheduled_slots:
                    slot_action = self.scheduled_slots[slot_idx]
                    if slot_action == "charge" and room_or_charging:
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

        # Manual slot overrides from the card are explicit user intent and win
        # over the price threshold.  Manual mode used to ignore them entirely:
        # the card accepted the click and drew the slot, but nothing executed.
        slot_idx = self._current_slot_index()
        override = (self.slot_overrides.get("today", {}) or {}).get(
            str(slot_idx)) if slot_idx is not None else None
        if (override == "charge" and grid_mode in ("from_grid", "both")
                and room_or_charging):
            return "charging"
        if (override == "discharge" and grid_mode in ("to_grid", "both")
                and battery_soc > discharge_min):
            return "discharging"

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
            if grid_mode in ("from_grid", "both") and self.current_price < self.price_threshold:
                return "charging"   # full is fine: rule 1's SOC holds it (see above)
        elif self._current_energy_state == "discharging":
            if grid_mode in ("to_grid", "both") and self.current_price > self.price_threshold and battery_soc > discharge_min:
                return "discharging"

        # To enter a new state, price must cross the wider band (threshold ± margin)
        if grid_mode in ("from_grid", "both") and self.current_price < (self.price_threshold - margin) and battery_soc < charge_max:
            return "charging"
        if grid_mode in ("to_grid", "both") and self.current_price > (self.price_threshold + margin) and battery_soc > discharge_min:
            return "discharging"

        return "idle"

    def set_slot_overrides(self, overrides: dict) -> None:
        """Store the card's overrides, stamped with the day they were set on.

        The card sends only {"today": …, "tomorrow": …}.  Without a date the
        coordinator cannot tell, after a restart, whether "tomorrow" still
        means tomorrow — see _rotate_slot_overrides.
        """
        self.slot_overrides = {
            "today": dict(overrides.get("today") or {}),
            "tomorrow": dict(overrides.get("tomorrow") or {}),
            "date": datetime.now().date().isoformat(),
        }
        self._persist_slot_overrides()

    def _persist_slot_overrides(self) -> None:
        entry = self.config_entry
        if entry:
            new_options = {**entry.options, "slot_overrides": self.slot_overrides}
            self.hass.config_entries.async_update_entry(entry, options=new_options)

    async def _rotate_slot_overrides(self, first_boot: bool = False) -> None:
        """Bring the overrides up to date: tomorrow's become today's at midnight.

        Driven by the date stamped on the overrides, not by "a new day was
        detected".  The day-change check also fires on the FIRST tick after
        every HA start, and rotating then moved tomorrow's overrides onto
        today — on the wrong day — after which the real midnight cleared them.
        Overrides set in the evening for the night, followed by any restart
        (installing an update) before midnight, therefore never ran.

        Unstamped overrides (saved by an older version) are assumed to be
        today's on a restart, and rotated at a real midnight as before.
        """
        today = datetime.now().date()
        stamped = self.slot_overrides.get("date")
        if stamped == today.isoformat() or (stamped is None and first_boot):
            if stamped is None and self.slot_overrides:
                self.slot_overrides = {**self.slot_overrides, "date": today.isoformat()}
                self._persist_slot_overrides()
            return

        tomorrow = self.slot_overrides.get("tomorrow", {})
        yesterday = (today - timedelta(days=1)).isoformat()
        if tomorrow and stamped in (yesterday, None):
            self.slot_overrides = {"today": tomorrow, "tomorrow": {}}
            _LOGGER.info(
                "Rotated %d tomorrow slot overrides to today", len(tomorrow)
            )
        else:
            # No tomorrow overrides, or they are older than yesterday's
            # tomorrow (HA was off for a day) — today starts clean.
            self.slot_overrides = {"today": {}, "tomorrow": {}}
            _LOGGER.debug("Cleared slot overrides for new day (no tomorrow overrides)")
        self.slot_overrides["date"] = today.isoformat()

        # Persist to config entry so it survives restarts
        self._persist_slot_overrides()

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
        self._retrieve_sun_times()
        self.pv_curve_source = "none"
        self.pv_curve_source_tomorrow = "none"
        if not self.forecast_entity:
            self.pv_forecast_today = None
            self.pv_forecast_remaining = None
            self.pv_forecast_tomorrow = None
            self.pv_hourly_kwh = {}
            self.pv_hourly_kwh_tomorrow = {}
            return

        state = self.hass.states.get(self.forecast_entity)
        if not state or state.state in ("unknown", "unavailable"):
            self.pv_forecast_today = None
            self.pv_forecast_remaining = None
            self.pv_hourly_kwh = {}
            self.pv_hourly_kwh_tomorrow = {}
            return

        try:
            self.pv_forecast_today = float(state.state)
        except (ValueError, TypeError):
            self.pv_forecast_today = None
            self.pv_forecast_remaining = None
            self.pv_hourly_kwh = {}
            self.pv_hourly_kwh_tomorrow = {}
            return

        now = datetime.now()
        today_date = now.date()
        tomorrow_date = today_date + timedelta(days=1)
        remaining = None
        hourly_kwh: dict[int, float] = {}
        hourly_kwh_tomorrow: dict[int, float] = {}

        # Hourly breakdown: Forecast.Solar (wh_hours), Open-Meteo Solar
        # Forecast (wh_period), Solcast (detailedHourly / detailedForecast).
        attr_name, points = self._forecast_points(state.attributes or {})
        if points:
            remaining_kwh = 0.0
            for ts, kwh in points:
                if ts.date() == today_date:
                    hourly_kwh[ts.hour] = hourly_kwh.get(ts.hour, 0.0) + kwh
                    # TODAY's remaining only.  wh_hours spans several
                    # days; summing every future hour counted all of
                    # tomorrow's sun as still to come today (~2x), so
                    # the EMS under-bought on a day the sun fell short
                    # (found by the day-replay harness).
                    if ts >= now:
                        remaining_kwh += kwh
                elif ts.date() == tomorrow_date:
                    hourly_kwh_tomorrow[ts.hour] = hourly_kwh_tomorrow.get(ts.hour, 0.0) + kwh
            if hourly_kwh:
                remaining = remaining_kwh
                self.pv_curve_source = attr_name
            if hourly_kwh_tomorrow:
                self.pv_curve_source_tomorrow = attr_name

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
                # Open-Meteo puts tomorrow's hours on the TOMORROW entity
                # only; the today entity's attribute stops at midnight.
                if not hourly_kwh_tomorrow:
                    t_name, t_points = self._forecast_points(t_state.attributes or {})
                    for ts, kwh in t_points:
                        if ts.date() == tomorrow_date:
                            hourly_kwh_tomorrow[ts.hour] = hourly_kwh_tomorrow.get(ts.hour, 0.0) + kwh
                    if hourly_kwh_tomorrow:
                        self.pv_curve_source_tomorrow = t_name

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

        if not hourly_kwh and self.pv_forecast_today:
            self.pv_curve_source = "synthetic"
        if not hourly_kwh_tomorrow and self.pv_forecast_tomorrow:
            self.pv_curve_source_tomorrow = "synthetic"

        # Fallback: estimate remaining PV using solar bell curve
        if remaining is None and self.pv_forecast_today:
            remaining = self._estimate_remaining_pv(
                self.pv_forecast_today, now, self.sunrise_hour, self.sunset_hour)

        self.pv_forecast_remaining = round(remaining, 2) if remaining is not None else None

    # Forecast attributes holding an hourly (or finer) breakdown, in order of
    # preference.  A dict maps a timestamp to Wh; a list holds Solcast
    # {period_start, pv_estimate (kW)} entries.
    _FORECAST_HOURLY_ATTRS: ClassVar[tuple[str, ...]] = (
        "wh_hours", "wh_period", "detailedHourly", "detailedForecast",
    )

    @classmethod
    def _forecast_points(cls, attrs) -> tuple[str, list[tuple[datetime, float]]]:
        """(attribute used, [(local start, kWh)]) from a forecast entity's attributes.

        Only ``wh_hours`` used to be read (``detailedHourly`` was looked up but
        only accepted as a dict, which Solcast's list never is), so Open-Meteo
        Solar Forecast and Solcast installs planned on the synthetic curve.
        """
        for name in cls._FORECAST_HOURLY_ATTRS:
            data = attrs.get(name)
            points: list[tuple[datetime, float]] = []
            try:
                if isinstance(data, dict):
                    for ts_str, value in data.items():
                        ts = cls._parse_forecast_time(ts_str)
                        if ts is not None and value is not None:
                            points.append((ts, float(value) / 1000.0))
                elif isinstance(data, list):
                    # Solcast: kW averaged over the period; detailedForecast
                    # is half-hourly, detailedHourly hourly.
                    period_h = 0.5 if name == "detailedForecast" else 1.0
                    for item in data:
                        if not isinstance(item, dict):
                            continue
                        ts = cls._parse_forecast_time(item.get("period_start"))
                        kw = item.get("pv_estimate")
                        if ts is not None and kw is not None:
                            points.append((ts, float(kw) * period_h))
            except (ValueError, TypeError) as err:
                _LOGGER.debug("Could not parse forecast attribute %s: %s", name, err)
                points = []
            if points:
                return name, points
        return "", []

    def _retrieve_sun_times(self) -> None:
        """Today's sunrise / sunset (local hours) from ``sun.sun``, if available.

        Feeds the synthetic PV curve used when the forecast has no hourly
        breakdown: a fixed 06:00-20:00 window put PV into the dark hours of an
        October morning and evening.  The next rising/setting differ from
        today's by a minute or two, which the curve does not notice.
        """
        self.sunrise_hour = None
        self.sunset_hour = None
        try:
            sun = self.hass.states.get("sun.sun")
            attrs = getattr(sun, "attributes", None)
            if not isinstance(attrs, dict):
                return
            rising = self._parse_forecast_time(attrs.get("next_rising"))
            setting = self._parse_forecast_time(attrs.get("next_setting"))
            if rising is None or setting is None:
                return
            sunrise = rising.hour + rising.minute / 60.0
            sunset = setting.hour + setting.minute / 60.0
            if 0 < sunrise < sunset < 24:
                self.sunrise_hour = round(sunrise, 2)
                self.sunset_hour = round(sunset, 2)
        except Exception as err:   # never let the sun break a poll
            _LOGGER.debug("Could not read sun.sun: %s", err)

    @staticmethod
    def _parse_forecast_time(value):
        """Parse a forecast timestamp to a NAIVE LOCAL datetime.

        An offset-aware timestamp is converted to local time first.  It used to
        have its offset stripped, so a forecast published in UTC landed 1-2 h
        early.  Naive values are taken as local, like datetime.now().
        """
        try:
            ts = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        except (ValueError, TypeError):
            return None
        if ts.tzinfo is not None:
            ts = ts.astimezone().replace(tzinfo=None)
        return ts

    @staticmethod
    def _estimate_remaining_pv(total_kwh: float, now: datetime,
                               sunrise: float | None = None,
                               sunset: float | None = None) -> float:
        """Estimate remaining PV production using a solar bell curve."""
        sunrise = 6.0 if sunrise is None else sunrise
        sunset = 20.0 if sunset is None else sunset
        now_h = now.hour + now.minute / 60.0
        if now_h >= sunset:
            return 0.0
        if now_h < sunrise:
            return total_kwh
        fraction = max(0.0, min(1.0, (now_h - sunrise) / (sunset - sunrise)))
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

    def _plan_power_kw(self) -> float:
        """Charge power the PLAN assumes for a slot: the user's Power Level.

        Not ``safe_max_power``.  That is the grid-current protection's answer
        for THIS moment — cut to 1 kW while the house draws 14 A on a phase —
        and it recovers by itself the moment the load goes.  Planning every
        remaining slot of today AND tomorrow at it made each slot deliver an
        eighth of its energy, so the selector booked eight times the slots and
        reached into the evening and the night to find them (customer card,
        Oct 2026: 20 slots today and 28 tomorrow "to cover 0.0 kWh deficit"
        at a 1 kW throttle; at Power Level the same need is three midday
        slots).  The throttle still governs what the inverter is told now;
        if it persists, the battery lags its plan and the next re-plan books
        what is missing — which is the self-correcting direction, where a
        throttled plan over-buys at prices nobody would choose.
        """
        level = self.config_entry.options.get("power_level", 5)
        try:
            return max(1.0, float(level))
        except (TypeError, ValueError):
            return 5.0

    def _build_manual_schedule(self) -> None:
        """Build `scheduled_slots` from the price threshold (manual mode).

        Manual price mode is a simple rule — NOT the optimizer:
          - from_grid / both: charge every remaining slot whose price is
            BELOW the threshold.
          - to_grid / both:   sell every remaining slot whose price is
            ABOVE the threshold.
        This keeps the card's displayed schedule consistent with what the
        manual-mode `_determine_energy_state` actually executes, and ensures
        we never show a charge slot above the threshold (the optimizer's plan
        is irrelevant in manual mode).  Past slots are left unscheduled.
        """
        opts = self.config_entry.options
        grid_mode = opts.get("grid_mode", "off")
        prices = self.slot_prices_today
        if grid_mode == "off" or not prices or self.price_threshold is None:
            self.scheduled_slots = {}
            self._tomorrow_scheduled_slots = {}
            self.schedule_status = "manual"
            self.schedule_reason = "Manual mode — follows price threshold"
            return

        current_slot = self._current_slot_index() or 0
        allow_charge = grid_mode in ("from_grid", "both")
        allow_sell = grid_mode in ("to_grid", "both")
        threshold = self.price_threshold

        # SOC-aware: the inverter physically stops charging at charge_max and
        # stops discharging at discharge_min.  Forward-simulate so the DISPLAYED
        # manual schedule only marks a slot 'charge' while the battery isn't
        # full, and 'sell' while it isn't empty — instead of painting green/
        # orange on slots that can't execute ("charging while full / selling
        # while empty").  PV + consumption drift are included so the picture
        # matches the SOC line.
        num = len(prices)
        mps = (24 * 60) / num
        slot_h = mps / 60.0
        eff = opts.get("efficiency_factor", 0.90)
        cap = (opts.get("battery_capacity_kwh", 10) or 10) * self._battery_soh_factor
        soc = (self.battery_soc / 100.0) * cap if self.battery_soc is not None else 0.0
        max_kwh = (opts.get("battery_charge_max_level", 100) / 100.0) * cap
        min_kwh = (opts.get("battery_discharge_min_level", 20) / 100.0) * cap
        safe_kw = self._plan_power_kw()
        inv_max = self._inverter_max_power_kw
        cons_profile = self._hourly_consumption_profile or {}
        cons_flat = self._get_consumption_estimate() / 24.0
        pv_hourly = self.pv_hourly_kwh or {}
        conf = self._last_pv_confidence or 1.0

        def _step(i, soc_in, sched_out, p_arr, pv_src, _slot_h, _mps, tomorrow):
            hour = int((i * _mps) / 60) % 24
            pv_kw = pv_src.get(hour, 0.0) * (1.0 if tomorrow else conf)
            pv_e = pv_kw * _slot_h
            cons = (cons_profile.get(hour, cons_flat) if cons_profile else cons_flat) * _slot_h
            price = p_arr[i] if i < len(p_arr) else None
            charge_ok = price is not None and allow_charge and price < threshold
            sell_ok = price is not None and allow_sell and price > threshold
            near_full = soc_in >= max_kwh - 0.1
            near_empty = soc_in <= min_kwh + 0.1
            if charge_ok and not near_full:
                sched_out[i] = "charge"
                grid_kw = min(safe_kw, max(0.0, inv_max - pv_kw))
                soc_v = soc_in + grid_kw * _slot_h * eff + pv_e - cons
            elif sell_ok and not near_empty:
                sched_out[i] = "discharge"
                soc_v = soc_in - safe_kw * _slot_h + pv_e - cons
            elif charge_ok:
                # Battery full + low price → inverter holds it full (grid serves
                # the load).  NOT a charge — no green bar, SOC stays at max.
                soc_v = soc_in + pv_e
            else:
                # Empty+sell (grid serves load) or idle (between thresholds).
                soc_v = soc_in + pv_e - cons
            return max(min_kwh, min(max_kwh, soc_v))

        sched: dict[int, str] = {}
        for i in range(current_slot, num):
            soc = _step(i, soc, sched, prices, pv_hourly, slot_h, mps, False)
        # Show the card's manual overrides too — _determine_energy_state
        # executes them in manual mode, so the displayed plan must include them.
        for idx_str, action in (self.slot_overrides.get("today", {}) or {}).items():
            idx = int(idx_str)
            if idx >= current_slot and (
                    (action == "charge" and allow_charge)
                    or (action == "discharge" and allow_sell)):
                sched[idx] = action

        self.scheduled_slots = sched
        # Tomorrow: continue the forward sim across midnight (same threshold rule).
        tmr: dict[int, str] = {}
        if self.slot_prices_tomorrow:
            tprices = self.slot_prices_tomorrow
            tnum = len(tprices)
            tmps = (24 * 60) / tnum
            tslot_h = tmps / 60.0
            pv_tmr = self.pv_hourly_kwh_tomorrow or {}
            soc_t = soc
            for i in range(tnum):
                soc_t = _step(i, soc_t, tmr, tprices, pv_tmr, tslot_h, tmps, True)
        self._tomorrow_scheduled_slots = tmr

        # Clear the optimizer's SOC trajectory so the card doesn't draw a
        # stale auto-mode line; it falls back to its own client-side
        # trajectory built from the threshold schedule above.
        self._backend_soc_trajectory = []
        self._backend_soc_trajectory_tomorrow = []

        n_charge = sum(1 for v in sched.values() if v == "charge")
        n_sell = sum(1 for v in sched.values() if v == "discharge")
        self.schedule_status = "manual"
        self.schedule_reason = (
            f"Manual mode: {n_charge} charge / {n_sell} sell slot(s) "
            f"vs threshold {threshold:.3f}"
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

        # The PLAN charges at Power Level — see _plan_power_kw.
        safe_power_kw = self._plan_power_kw()
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
            scheduler_engine=str(opts.get("scheduler_engine", "greedy")),
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
            sunrise_hour=self.sunrise_hour,
            sunset_hour=self.sunset_hour,
            predicted_soc_pct=predicted_soc,
            price_reference_eur_kwh=self.price_reference,
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
            # Tomorrow's forecast is a first-class input to TODAY's plan: the
            # self-consumption top-off is capped by the horizon energy need
            # (see ems._topoff_horizon_need_kwh), so a front moving in — 47 kWh
            # of forecast sun dropping to 10 — must re-plan today immediately,
            # not wait for the next slot boundary.
            round(self.pv_forecast_tomorrow, 2) if self.pv_forecast_tomorrow else None,
            round(self.pv_actual_today_kwh, 2) if self.pv_actual_today_kwh else None,
            self._yesterday_deficit,
            json.dumps(self.slot_overrides, sort_keys=True) if self.slot_overrides else "",
            safe_power_kw,
            opts.get("ev_charge_strategy", "smart"),
            opts.get("scheduler_engine", "greedy"),
            self.price_reference,
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

        # Merge manual slot overrides from the card and re-validate (#9).
        # ems.merge_slot_overrides owns the rule: overrides are the user's
        # explicit intent and are never pruned (the inverter's rule-1 SOC ends
        # a charge at max SOC); on a projected bound violation only the EMS's
        # own slots give way.
        today_overrides = self.slot_overrides.get("today", {})
        if today_overrides and self.slot_prices_today and battery_soc is not None:
            num_slots_t = len(self.slot_prices_today)
            minutes_per_slot_t = (24 * 60) / num_slots_t
            current_slot_t = min(
                int((now.hour * 60 + now.minute) / minutes_per_slot_t), num_slots_t - 1)
            remaining_t = [
                (i, self.slot_prices_today[i])
                for i in range(current_slot_t, num_slots_t)
                if self.slot_prices_today[i] is not None
            ]
            current_kwh_t = (battery_soc / 100.0) * effective_capacity
            min_kwh_t = (config.battery_discharge_min_pct / 100.0) * effective_capacity
            # Match the main pass: discharges validate against the reserve
            # target, not the bare hardware floor — except in make-room mode,
            # where dips below reserve are intentional (negative-window PV
            # refills the battery).
            if config.discharge_to_make_room_for_negative_price:
                floor_t = min_kwh_t
            else:
                reserve_kwh_t = (result.reserve_target_pct / 100.0) * effective_capacity
                floor_t = max(min_kwh_t, reserve_kwh_t)

            def _validate(charge_set, discharge_set):
                return ems_module._validate_schedule_soc(
                    remaining_t, charge_set, discharge_set,
                    current_kwh_t, config.consumption_est_kwh / num_slots_t,
                    self.pv_hourly_kwh or {}, minutes_per_slot_t,
                    smoothed_pv_confidence,
                    effective_capacity, floor_t,
                    config.safe_power_kw * (minutes_per_slot_t / 60.0), config.efficiency,
                    consumption_hourly_kwh=self._hourly_consumption_profile or None,
                    inverter_max_power_kw=config.inverter_max_power_kw,
                    safe_power_kw=config.safe_power_kw,
                    keep_all_negative_charges=config.charge_to_full_on_negative_price,
                )

            self.scheduled_slots, dropped_ems = ems_module.merge_slot_overrides(
                self.scheduled_slots, today_overrides, grid_mode,
                dict(remaining_t), _validate,
            )
            if dropped_ems:
                # Re-evaluated on every recalc (SOC moves 0.1 % → new hash), so debug.
                _LOGGER.debug(
                    "Manual overrides: gave up %d EMS slot(s) to keep the plan "
                    "within battery bounds: %s", len(dropped_ems), dropped_ems)
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
        # Capture WHY when the MILP engine is selected but greedy is running.
        # The reason previously lived only in a one-off WARNING that has usually
        # rotated away by the time anyone asks "why does my card say fallback?".
        if opts.get("scheduler_engine", "greedy") == "milp":
            self.milp_status = _milp_status_snapshot()
        else:
            self.milp_status = None
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
                        pv_hourly_kwh=ems_module._synthesize_pv_for(state, forecast_total),
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
        self._flex_load_currents = result.load_currents or {}
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

    # Inverter bit0=Sunday..bit6=Saturday (see _check_rule1_window_conflict).
    _WEEKDAY_NAMES: ClassVar[list[str]] = [
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
        self._persist_ev_boost()
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
        self._persist_ev_boost()
        _LOGGER.info("EV Boost cancelled")

    def _boost_store(self):
        if self._ev_boost_store is None:
            from homeassistant.helpers.storage import Store
            self._ev_boost_store = Store(
                self.hass, version=1,
                key=f"{DOMAIN}_{self.config_entry.entry_id}_ev_boost")
        return self._ev_boost_store

    def _persist_ev_boost(self) -> None:
        """Save the boost's end time (wall-clock, so it means something after
        a reboot).  Best effort: a boost that cannot be saved still runs."""
        until = self._ev_boost_until_ts

        async def _save() -> None:
            try:
                await self._boost_store().async_save({"until": until})
            except Exception as err:
                _LOGGER.debug("EV boost not persisted: %s", err)
        try:
            self.hass.async_create_task(_save())
        except Exception as err:
            _LOGGER.debug("EV boost not persisted: %s", err)

    async def _restore_ev_boost(self) -> None:
        """Bring back a boost that was running when Home Assistant stopped."""
        if self._ev_boost_restored:
            return
        self._ev_boost_restored = True
        try:
            data = await self._boost_store().async_load()
        except Exception as err:
            _LOGGER.debug("EV boost not restored: %s", err)
            return
        until = data.get("until") if isinstance(data, dict) else None
        if isinstance(until, (int, float)) and not isinstance(until, bool) \
                and until > time.time() and until > self._ev_boost_until_ts:
            self._ev_boost_until_ts = float(until)
            self._ev_boost_max_applied = False
            _LOGGER.info("EV Boost restored after restart: %d min remaining",
                         int((until - time.time()) / 60))

    def _seed_flex_load_states(self, loads) -> None:
        """Learn each load's real on/off state the first time it is seen.

        The tracked state is what this integration last commanded, and it is
        in memory — so after a restart every load read as off whatever it was
        really doing.  A charger left running then stayed on through slots it
        was not scheduled in (nothing would turn off a load believed off), and
        the card showed it off.  The switch entity is the truth; ask it once.
        Only a definite 'on'/'off' seeds — 'unavailable' leaves it unknown.
        """
        for load_idx, load in enumerate(loads):
            if load_idx in self._flex_load_states or not load.switch_entity:
                continue
            state = self.hass.states.get(load.switch_entity)
            value = getattr(state, "state", None)
            if value in ("on", "off"):
                self._flex_load_states[load_idx] = value == "on"
                _LOGGER.debug("Flex load '%s' found %s at startup", load.name, value)

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
        await self._restore_ev_boost()
        self._seed_flex_load_states(loads)
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
                    except Exception as err:
                        _LOGGER.error("EV boost actuation failed for '%s': %s", load.name, err)
                    break
        else:
            self._ev_boost_max_applied = False

        if not self._flex_load_scheduled and not ev_boost:
            for load_idx, is_on in list(self._flex_load_states.items()):
                if is_on:
                    try:
                        await self._set_flex_load(load_idx, False)
                    except Exception as err:
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
                    except Exception as err:
                        _LOGGER.error("Failed to turn off flex load '%s': %s", load.name, err)
            return

        for load_idx, load in enumerate(loads):
            if ev_boost and load.is_ev_charger:
                continue

            try:
                should_be_on = slot_idx in self._flex_load_scheduled.get(load_idx, {})
                if should_be_on and not self._shed_load_may_return(load_idx, load):
                    should_be_on = False      # shed by safe power — held off
                currently_on = self._flex_load_states.get(load_idx, False)

                if should_be_on != currently_on:
                    await self._set_flex_load(load_idx, should_be_on, load)

                if should_be_on and load.is_ev_charger:
                    # The plan's current for this slot: in a slot the charger
                    # runs for the sun alone, the step the forecast surplus
                    # covers; otherwise its default.  Applied when it switches
                    # on and once per slot after that — within a slot, safe
                    # power's step-down has the last word.
                    planned = self._flex_load_currents.get(load_idx, {}).get(slot_idx)
                    new_slot = self._flex_load_current_slot.get(load_idx) != slot_idx
                    if not currently_on:
                        await self._set_ev_charger_current(load, planned or load.default_current)
                    elif (new_slot and planned is not None
                          and planned != self._flex_load_current_step
                          and self._ev_current_raise_fits(load, planned)):
                        await self._set_ev_charger_current(load, planned)
                    self._flex_load_current_slot[load_idx] = slot_idx
            except Exception as err:
                _LOGGER.error("Flex load '%s' actuation failed: %s", load.name, err)

    def _ev_current_raise_fits(self, load, target_amps: int) -> bool:
        """May the charger go to ``target_amps`` without crowding the grid limit?

        Lowering always may.  Raising — a new slot's plan asking for more than
        safe power left it at — only when the measured current on its phase
        plus the extra amps stays under 95 % of the limit, the same test a shed
        load's return uses.  Otherwise safe power steps it down and the next
        slot raises it again: a spike every 15 minutes.
        """
        step = self._flex_load_current_step
        if step is None or target_amps <= step:
            return True
        max_amps = self.config_entry.options.get("max_amperage_per_phase", 16)
        current = self._current_on_load_phase(load, self._last_max_grid_current or 0.0)
        return current + (target_amps - step) <= 0.95 * max_amps

    def _shed_load_waiting_to_fit(self, current: float, max_amps: float) -> bool:
        """Is a load that safe power shed waiting, and would it fit right now?"""
        if not self._flex_load_shed_until:
            return False
        loads = self._build_flex_load_configs()
        for idx in self._flex_load_shed_until:
            if idx < len(loads):
                ld = loads[idx]
                amps = ld.rated_power_kw * 1000.0 / (max(1, ld.voltage) * max(1, ld.phases))
                if self._current_on_load_phase(ld, current) + amps <= 0.95 * max_amps:
                    return True
        return False

    def _current_on_load_phase(self, load, worst: float) -> float:
        """The grid current on the phase this load adds to.

        Unknown (the default) means the busiest phase — safe, and wrong in the
        direction that bit: on an 18 A connection a 16 A one-phase charger then
        fits back only when EVERY phase carries under ~1 A, so once safe power
        had switched it off it stayed off while its own phase had room.  Only
        load 1 has a phase setting; a three-phase load adds to every phase, so
        the busiest one is right for it anyway.
        """
        if load.phases != 1:
            return worst
        opts = self.config_entry.options
        if not load.switch_entity or load.switch_entity != opts.get("flexible_load_1_switch_entity"):
            return worst
        idx = {"L1": 0, "L2": 1, "L3": 2}.get(str(opts.get("flexible_load_1_phase", "unknown")))
        currents = self._last_phase_currents
        if idx is None or not currents or idx >= len(currents):
            return worst
        value = currents[idx]
        return float(value) if isinstance(value, (int, float)) else worst

    def _shed_load_may_return(self, load_idx: int, load) -> bool:
        """May a load that safe power shed be switched on again?

        Not during its hold, and afterwards only when the grid current leaves
        room for it: switching a 16 A charger back on next to a 13 A phase just
        to shed it again one poll later is a 29 A spike every hold period.
        """
        until = self._flex_load_shed_until.get(load_idx)
        if until is None:
            return True
        if time.time() < until:
            return False
        max_amps = self.config_entry.options.get("max_amperage_per_phase", 16)
        load_amps = load.rated_power_kw * 1000.0 / (max(1, load.voltage) * max(1, load.phases))
        current = self._current_on_load_phase(load, self._last_max_grid_current or 0.0)
        if current + load_amps > 0.95 * max_amps:
            self._flex_load_shed_until[load_idx] = time.time() + 60   # look again in a minute
            return False
        del self._flex_load_shed_until[load_idx]
        _LOGGER.info("Safe power: '%s' may run again (grid %.1fA + %.1fA < %.0fA)",
                     load.name, current, load_amps, max_amps)
        return True

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

        Only called once the battery power limit cannot help any more (it is
        at its 1 kW minimum, or the battery is idle) — the battery always goes
        first (maintainer decision, Oct 2026).  Then, one action per tick to
        let the current settle:
        1. EV charger: step down current
        2. Binary loads: shed by priority (highest number = shed first)
        Returns True if any action was taken.
        """
        loads = self._build_flex_load_configs()
        active = [(i, ld) for i, ld in enumerate(loads)
                  if self._flex_load_states.get(i, False)]
        if not active:
            return False
        SHED_HOLD_S = 300

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
            # Keep it off: _actuate_flex_loads runs later in the SAME poll and
            # used to switch it straight back on because the schedule still
            # wanted it — so every poll "shed" the load, skipped the battery
            # reduction, and the load came back.  The current never fell
            # (field report: 23 A, then 28 A, on an 18 A limit).
            self._flex_load_shed_until[idx] = time.time() + SHED_HOLD_S
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
        safe_power_kw = self._plan_power_kw()
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
            sunrise_hour=self.sunrise_hour,
            sunset_hour=self.sunset_hour,
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
            if data and "hourly_history" in data:
                self._hourly_consumption_history = data["hourly_history"][-7:]
            if data and isinstance(data.get("price_history"), list):
                self._daily_price_history = [
                    e for e in data["price_history"]
                    if isinstance(e, dict) and _number(e.get("mean")) is not None
                ][-PRICE_HISTORY_DAYS:]
            # Days stored by an earlier version can carry the #220 rollover
            # glitch (a whole day booked into the 04:00 hour).  They are
            # cleaned here, not only when the recorder backfill replaces them:
            # the backfill keeps a stored day whenever the recorder cannot
            # account for it, and that is exactly how a polluted day survived
            # the fix that was meant to remove it.
            self._sanitize_consumption_history()
            self._calculate_weekly_avg()
            self._calculate_hourly_profile()
            # Cycle counting + SOH (#13).  Persisted across restarts so we
            # can estimate battery wear from cumulative throughput.
            if data and "cycle_charged_kwh" in data:
                self._cycle_charged_kwh = float(data.get("cycle_charged_kwh", 0))
                self._cycle_discharged_kwh = float(data.get("cycle_discharged_kwh", 0))
                self._battery_soh_factor = float(data.get("battery_soh_factor", 1.0))
            self._consumption_store_loaded = True

    async def _record_daily_consumption(self) -> None:
        """Record the day that just ended and update the 7-day averages.

        Runs on the first tick after midnight, so the day to record is
        YESTERDAY.  It used to be stamped with the new day's date and to ask
        the recorder for the hours of a day that had only just begun — which
        returned nothing, so even a working statistics parser fell back to a
        flat profile, and a daily meter that had already reset to 0 left the
        weekly average with no entry at all.

        Priority: the recorder's hourly statistics for yesterday (exact, and
        the hourly shape comes with it) > the override entity / inverter day
        register read now (the previous poll's value, i.e. yesterday's total)
        spread flat over the day.
        """
        await self._init_consumption_store()

        day = (datetime.now() - timedelta(days=1)).date()
        day_str = day.strftime("%Y-%m-%d")

        entity_id = self._resolve_consumption_entity()
        if entity_id:
            self.consumption_entity_used = entity_id
            hourly = await self._query_hourly_from_history(entity_id, day)
            if len(hourly) >= MIN_HOURS_FOR_HISTORY_DAY:
                self._store_consumption_day(day_str, hourly, "history")
                await self._save_consumption_store()
                _LOGGER.info(
                    "Recorded consumption for %s from the recorder: %.2f kWh "
                    "(7-day avg: %.2f kWh)", day_str, sum(hourly.values()),
                    self.weekly_avg_consumption or 0,
                )
                return

        day_consumption = None

        # 1. Try override entity (P1 meter / utility meter / template sensor)
        if self.consumption_override_entity:
            state = self.hass.states.get(self.consumption_override_entity)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    day_consumption = float(state.state)
                except (ValueError, TypeError):
                    pass

        # 2. Try inverter daily energy registers (self.data is the last poll
        #    before midnight, so it still holds yesterday's total)
        if (day_consumption is None or day_consumption <= 0) and self.data:
            for key in ["daily_energy_consumed", "daily_load_energy", "total_load_energy_today",
                        "daily_consumption", "daily_energy_used",
                        "total_load_consumption_energy_day",
                        "load_consumption_energy_day",
                        "homeload_day_cost_energy",
                        "load_day_cost_energy"]:
                val = self.data.get(key)
                if val is not None and val > 0:
                    # Convert Wh to kWh if value seems to be in Wh (>100)
                    day_consumption = val / 1000.0 if val > 100 else val
                    break

        if day_consumption is None or day_consumption <= 0:
            _LOGGER.warning(
                "No daily consumption data found — weekly average will remain unknown. "
                "Configure a consumption_override_entity or check inverter registers."
            )
            return

        per_hour = round(day_consumption / 24.0, 3)
        self._store_consumption_day(day_str, {str(h): per_hour for h in range(24)}, "flat")
        await self._save_consumption_store()
        _LOGGER.info("Recorded daily consumption: %.2f kWh (7-day avg: %.2f kWh, "
                     "no hourly statistics — flat profile for this day)",
                     day_consumption, self.weekly_avg_consumption or 0)

    def _clean_day_hours(self, hours: dict) -> tuple[dict[str, float], int]:
        """One day's hours without the readings that cannot be consumption.

        Returns (kept hours, number dropped).  An hour above what the house can
        physically draw (_max_household_kwh_per_hour) or below zero is a meter
        artefact — the inverter's rollover glitch put a whole day into one hour.
        """
        cap = self._max_household_kwh_per_hour()
        kept: dict[str, float] = {}
        dropped = 0
        for h_str, kwh in (hours or {}).items():
            value = _number(kwh)
            try:
                h = int(h_str)
            except (TypeError, ValueError):
                dropped += 1
                continue
            if value is None or value < 0 or value > cap or not 0 <= h <= 23:
                dropped += 1
                continue
            kept[str(h)] = value
        return kept, dropped

    @staticmethod
    def _day_total(hours: dict[str, float]) -> float:
        """A day's total from its hours; hours that were dropped count as the
        average of the ones that are left (they were not zero)."""
        if not hours:
            return 0.0
        total = sum(hours.values())
        return round(total * 24.0 / len(hours), 2) if len(hours) < 24 else round(total, 2)

    def _sanitize_consumption_history(self) -> None:
        """Drop impossible hours from the stored days and re-derive each
        day's total from its cleaned hours, so a polluted day can neither
        shape the profile nor inflate the 7-day average."""
        cleaned = []
        totals: dict[str, float] = {}
        removed = 0
        for entry in self._hourly_consumption_history:
            if not isinstance(entry, dict) or not entry.get("date"):
                continue
            hours, dropped = self._clean_day_hours(entry.get("hours", {}))
            removed += dropped
            if not hours:
                continue
            cleaned.append({**entry, "hours": hours})
            totals[entry["date"]] = self._day_total(hours)
        self._hourly_consumption_history = cleaned
        daily = []
        for entry in self._daily_consumption_history:
            if not isinstance(entry, dict) or not entry.get("date"):
                daily.append(entry)        # not ours to judge — kept as stored
                continue
            kwh = totals.get(entry["date"], _number(entry.get("kwh")))
            # A day total no house can reach (cap x 24 h) is a meter artefact too.
            if kwh is None or kwh <= 0 or kwh > self._max_household_kwh_per_hour() * 24:
                continue
            daily.append({**entry, "kwh": kwh})
        self._daily_consumption_history = daily
        if removed:
            _LOGGER.warning(
                "Consumption history: removed %d impossible hourly reading(s) "
                "(above %.1f kWh/h — the meter's rollover glitch, issue #220)",
                removed, self._max_household_kwh_per_hour())

    def _store_consumption_day(self, day_str: str, hourly: dict[str, float],
                               source: str) -> None:
        """Put one day's hourly breakdown (and its total) into the histories."""
        hourly, _ = self._clean_day_hours(hourly)
        total = self._day_total(hourly)
        self._hourly_consumption_history = sorted(
            [e for e in self._hourly_consumption_history if e.get("date") != day_str]
            + [{"date": day_str, "hours": hourly, "source": source}],
            key=lambda e: e.get("date", ""),
        )[-CONSUMPTION_HISTORY_DAYS:]
        if total > 0:
            self._daily_consumption_history = sorted(
                [e for e in self._daily_consumption_history if e.get("date") != day_str]
                + [{"date": day_str, "kwh": total}],
                key=lambda e: e.get("date", ""),
            )[-CONSUMPTION_HISTORY_DAYS:]
        self._calculate_hourly_profile()
        self._calculate_weekly_avg()

    async def _save_consumption_store(self) -> None:
        if self._consumption_store:
            await self._consumption_store.async_save({
                "daily_history": self._daily_consumption_history,
                "hourly_history": self._hourly_consumption_history,
                "price_history": self._daily_price_history,
                "cycle_charged_kwh": round(self._cycle_charged_kwh, 3),
                "cycle_discharged_kwh": round(self._cycle_discharged_kwh, 3),
                "battery_soh_factor": round(self._battery_soh_factor, 4),
            })

    async def _backfill_consumption_history(self) -> None:
        """Rebuild the last 7 days from the recorder's hourly statistics.

        Every stored day before this fix is FLAT: the statistics parser never
        worked (see _hourly_from_statistics), so without a backfill a fixed
        parser would take a week to replace them one midnight at a time.  The
        recorder already holds the real hours, so read them now.  Days the
        recorder cannot account for (fewer than MIN_HOURS_FOR_HISTORY_DAY
        hours of statistics) keep whatever was stored.

        Retried every CONSUMPTION_BACKFILL_RETRY_S until the consumption
        entity resolves — right after a restart the inverter's own sensors
        may not have a state yet.
        """
        now_ts = time.time()
        if self._consumption_backfilled or now_ts - self._consumption_backfill_attempt_ts < CONSUMPTION_BACKFILL_RETRY_S:
            return
        self._consumption_backfill_attempt_ts = now_ts
        entity_id = self._resolve_consumption_entity()
        if not entity_id:
            return
        self._consumption_backfilled = True
        self.consumption_entity_used = entity_id
        today = datetime.now().date()
        filled = 0
        kept: list[str] = []
        self._history_query_error = None
        for back in range(CONSUMPTION_HISTORY_DAYS, 0, -1):
            day = today - timedelta(days=back)
            hourly = await self._query_hourly_from_history(entity_id, day)
            if len(hourly) >= MIN_HOURS_FOR_HISTORY_DAY:
                self._store_consumption_day(day.strftime("%Y-%m-%d"), hourly, "history")
                filled += 1
            else:
                kept.append(f"{day:%m-%d} ({len(hourly)} h)")
        if filled:
            await self._save_consumption_store()
        # One line that says what happened to every day — "partial" or a
        # strange average on the card is otherwise impossible to explain.
        _LOGGER.info(
            "Consumption profile from %s: %d day(s) rebuilt from recorder "
            "statistics; not enough statistics for %s%s; 7-day avg %.2f kWh, "
            "profile source %s",
            entity_id, filled, ", ".join(kept) or "none",
            f" (query failed: {self._history_query_error})"
            if self._history_query_error else "",
            self.weekly_avg_consumption or 0, self.consumption_profile_source,
        )

    def _record_daily_prices(self) -> bool:
        """Remember today's mean price.  True when the history changed."""
        prices = [p for p in (self.slot_prices_today or []) if _number(p) is not None]
        if not prices:
            return False
        day = datetime.now().strftime("%Y-%m-%d")
        mean = round(sum(prices) / len(prices), 5)
        if any(e.get("date") == day and e.get("mean") == mean
               for e in self._daily_price_history):
            return False
        self._daily_price_history = sorted(
            [e for e in self._daily_price_history if e.get("date") != day]
            + [{"date": day, "mean": mean}],
            key=lambda e: e.get("date", ""),
        )[-PRICE_HISTORY_DAYS:]
        return True

    @property
    def price_reference(self) -> float | None:
        """This week's typical grid price: the mean of the daily mean prices
        of the last PRICE_HISTORY_DAYS days (today included).  None until
        MIN_PRICE_HISTORY_DAYS days are known — nothing is then stored for the
        coming days beyond what the plan already buys."""
        oldest = (datetime.now().date()
                  - timedelta(days=PRICE_HISTORY_DAYS - 1)).strftime("%Y-%m-%d")
        means = [e["mean"] for e in self._daily_price_history
                 if e.get("date", "") >= oldest]
        if len(means) < MIN_PRICE_HISTORY_DAYS:
            return None
        return round(sum(means) / len(means), 5)

    async def _backfill_price_history(self) -> None:
        """Rebuild the past days' mean prices from the recorder, once.

        Without it the weekly price would take MIN_PRICE_HISTORY_DAYS days to
        appear after an update.  Uses the price entity's hourly long-term
        statistics ("mean"), which exist when the entity has a state class;
        a day counts when the recorder has MIN_HOURS_FOR_HISTORY_DAY hours of
        it.  Days already stored are kept.
        """
        if self._price_backfilled or not self.nordpool_entity:
            return
        self._price_backfilled = True
        today = datetime.now().date()
        known = {e.get("date") for e in self._daily_price_history}
        days = [today - timedelta(days=back) for back in range(PRICE_HISTORY_DAYS - 1, 0, -1)]
        days = [d for d in days if d.strftime("%Y-%m-%d") not in known]
        if not days:
            return
        error = None
        added = 0
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import (
                statistics_during_period,
            )
            start = datetime.combine(days[0], datetime.min.time()).astimezone()
            end = datetime.combine(today, datetime.min.time()).astimezone()
            stats = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period, self.hass, start, end,
                {self.nordpool_entity}, "hour", None, {"mean"},
            )
            by_day: dict[str, list[float]] = {}
            for row in stats.get(self.nordpool_entity) or []:
                mean = _number(row.get("mean"))
                ts = _number(row.get("start"))
                if mean is None or ts is None:
                    continue
                key = datetime.fromtimestamp(ts, UTC).astimezone().strftime("%Y-%m-%d")
                by_day.setdefault(key, []).append(mean)
            for day in days:
                key = day.strftime("%Y-%m-%d")
                hours = by_day.get(key, [])
                if len(hours) >= MIN_HOURS_FOR_HISTORY_DAY:
                    self._daily_price_history.append(
                        {"date": key, "mean": round(sum(hours) / len(hours), 5)})
                    added += 1
        except Exception as err:
            error = f"{type(err).__name__}: {err}"
        if added:
            self._daily_price_history = sorted(
                self._daily_price_history, key=lambda e: e.get("date", ""),
            )[-PRICE_HISTORY_DAYS:]
            await self._save_consumption_store()
        _LOGGER.info(
            "Price history from %s: %d day(s) rebuilt from recorder statistics%s; "
            "%d day(s) known, weekly price %s",
            self.nordpool_entity, added,
            f" (query failed: {error})" if error else "",
            len(self._daily_price_history),
            f"{self.price_reference:.4f}" if self.price_reference is not None
            else f"not yet (needs {MIN_PRICE_HISTORY_DAYS} days)",
        )

    @property
    def consumption_profile_source(self) -> str:
        """Where the hourly consumption profile comes from.

        "history" — every stored day has real hourly statistics; "partial" —
        some days do; "flat" — none do (daily totals spread evenly over 24 h);
        "none" — no consumption history at all.
        """
        days = self._hourly_consumption_history
        if not days:
            return "none"
        real = sum(1 for e in days if e.get("source") == "history")
        if real == len(days):
            return "history"
        return "partial" if real else "flat"

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

    async def _query_hourly_from_history(self, entity_id: str, day) -> dict[str, float]:
        """Hourly consumption (kWh) for one LOCAL calendar day, from the recorder.

        Returns {"<hour>": kWh} for each hour the recorder can account for;
        {} when it has none or the query fails.  The query starts one hour
        before the day so hour 0 has a baseline (see _hourly_from_statistics).
        """
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import (
                statistics_during_period,
            )

            # Local midnight to local midnight (aware, so a DST day spans 23
            # or 25 hours).  The day used to be cut at UTC midnight, which put
            # 22:00-24:00 of the previous evening into this day's hours 0-1.
            midnight = datetime.combine(day, datetime.min.time()).astimezone()
            start = midnight - timedelta(hours=1)
            end = datetime.combine(day + timedelta(days=1), datetime.min.time()).astimezone()

            stats = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period,
                self.hass,
                start,
                end,
                {entity_id},
                "hour",
                # Ask the recorder for kWh: it converts from the statistic's
                # own unit, so a Wh meter no longer depends on a guess.
                {"energy": "kWh"},
                {"change", "state"},
            )
            return self._hourly_from_statistics(
                stats.get(entity_id) or [], day=day,
                max_kwh_per_hour=self._max_household_kwh_per_hour())
        except Exception as err:
            _LOGGER.debug("Could not query hourly consumption history: %s", err)
            self._history_query_error = f"{type(err).__name__}: {err}"
            return {}

    def _max_household_kwh_per_hour(self) -> float:
        """The most energy the house can physically use in one hour (kWh).

        Everything the house uses arrives through the grid connection or the
        inverter, so neither can be exceeded: grid limit on three phases (the
        most generous reading — a 1-phase inverter can sit on a 3-phase
        connection) plus the inverter's rating.  Generous on purpose; it only
        has to catch readings that cannot be consumption at all.
        """
        opts = self.config_entry.options
        try:
            amps = float(opts.get("max_amperage_per_phase", 16) or 16)
        except (TypeError, ValueError):
            amps = 16.0
        inverter_kw = INVERTER_MAX_POWER_KW.get(self.inverter_model, 10.0)
        return round(amps * 0.230 * 3 + inverter_kw, 1)

    @staticmethod
    def _hourly_from_statistics(rows, day=None, max_kwh_per_hour=None) -> dict[str, float]:
        """Hourly consumption from recorder statistics rows, by local hour.

        ``start`` is a float UTC timestamp in the recorder API this integration
        runs against (HA 2023.3+).  The code used to call ``.hour`` on it, which
        raised, the broad except swallowed it, and every install's hourly
        profile silently fell back to FLAT.  A datetime is still accepted.

        **The hour's consumption is the difference between the counter's
        END-OF-HOUR values** (the row's ``state``), not HA's ``change``.  HA's
        change follows every intermediate state, and the inverter's day
        counters glitch at their rollover (~04:05 CEST, issue #220): for one
        poll they read the PREVIOUS day's total.  HA books that as consumption,
        so the 04:00 hour carried a whole day — a 52 kWh hour on a 50 kWh/day
        house, which the planner then spent at 04:00 (the SOC "dip") and
        bought expensive evening grid to cover.  The end-of-hour value is not
        touched by a one-poll glitch.  A counter that FELL during the hour was
        reset in it, and the consumption is what it counted since the reset.

        Falls back to ``change`` where a row (or the row before it) has no
        state.  Any hour above ``max_kwh_per_hour`` — what the house can
        physically draw — is not consumption and is left out.  ``day`` limits
        the result to that date; earlier rows serve only as the baseline.
        """
        parsed = []
        for entry in rows:
            if not isinstance(entry, dict):
                continue
            start = entry.get("start")
            if isinstance(start, (int, float)) and not isinstance(start, bool):
                local = datetime.fromtimestamp(start, UTC).astimezone().replace(tzinfo=None)
            elif isinstance(start, datetime):
                local = start.astimezone().replace(tzinfo=None) if start.tzinfo else start
            else:
                continue
            parsed.append((local, _number(entry.get("change")), _number(entry.get("state"))))
        parsed.sort(key=lambda r: r[0])

        hourly: dict[str, float] = {}
        prev_state = None
        for local, change, state in parsed:
            value = None
            if state is not None and prev_state is not None:
                delta = state - prev_state
                # A fall is a reset during this hour: count what it counted since.
                value = delta if delta >= -0.001 else state
                value = max(value, 0.0)
            elif change is not None and change >= 0:
                value = change
            prev_state = state
            if day is not None and local.date() != day:
                continue
            if value is None:
                continue
            if max_kwh_per_hour is not None and value > max_kwh_per_hour:
                continue
            key = str(local.hour)
            # The repeated hour of a DST fall-back day lands in one bucket.
            hourly[key] = round(hourly.get(key, 0.0) + float(value), 3)
        return hourly

    def _calculate_hourly_profile(self) -> None:
        """Build per-hour average consumption from 7-day hourly history."""
        if not self._hourly_consumption_history:
            self._hourly_consumption_profile = {}
            return

        hour_totals: dict[int, list[float]] = {h: [] for h in range(24)}
        days = self._hourly_consumption_history
        # A flat day says nothing about the SHAPE, so once any real day exists
        # only real days shape the profile.
        real_days = [e for e in days if e.get("source") == "history"]
        for entry in (real_days or days):
            hours = entry.get("hours", {})
            for h_str, kwh in hours.items():
                h = int(h_str)
                if 0 <= h <= 23 and kwh >= 0:
                    hour_totals[h].append(kwh)

        profile: dict[int, float] = {}
        means = {h: sum(v) / len(v) for h, v in hour_totals.items() if v}
        # An hour no stored day has a reading for (every one was dropped as
        # impossible) is not an hour without consumption: it gets the average
        # of the hours that are known.
        fill = sum(means.values()) / len(means) if means else 0.0
        for h in range(24):
            profile[h] = round(means.get(h, fill), 3)

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
        # A monitor-only model has no power register to lower: protection
        # through the inverter is impossible, so do not pretend to throttle.
        if safe_power_enabled == "off" or self.inverter_model in MONITOR_ONLY_MODELS:
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
        self._last_max_grid_current = max_current
        phase_currents = self.TypeSpecificHandler.determine_phase_currents(new_data)
        self._last_phase_currents = phase_currents if isinstance(phase_currents, list) else None
        # With the EMS off (safe_power_management "on") the integration does
        # not know what the inverter's own rules are doing: treat the battery
        # as running, as before.
        battery_active = (grid_mode == "off"
                          or self._current_energy_state in ("charging", "discharging"))
        # This is the key: use the freshly read register value from new_data!
        # Only while the battery runs: in idle the register may hold what the
        # idle transition itself wrote (0 on TREX-25/50/IVGM), which is not a
        # change made in the app.
        applied_kwatts = (self.TypeSpecificHandler.determine_rule_power(new_data)
                          if battery_active else None)  # works in kW
        if applied_kwatts is not None:
            detected_level = max(1, min(user_level, applied_kwatts))
            if abs(detected_level - base_level) >= 1:
                _LOGGER.info(
                    "External change detected: power limit %d → %d kW (likely via app) — syncing and re-evaluating safety",
                    base_level, detected_level
                )
                base_level = detected_level  # adopt the new higher (or lower) value

        # --- 5. Compute safe_level based on current grid draw ---
        # Maintainer rule (23 Oct 2026): the rule-1 power stays at the user's
        # Power Level and is written ONLY when a phase is over the limit; it
        # goes back to the Power Level as soon as that level fits again.
        # (The old 80 % / 70 % bands with +1 kW per poll rewrote it hundreds
        # of times a day — 518 writes on 22 Oct — and kept the battery cut
        # from 80 % of the limit.)
        safe_level = base_level
        settling = (not user_level_changed
                    and time.time() - self._safe_power_cut_ts < SAFE_POWER_SETTLE_S)
        phases = INVERTER_PHASES.get(self.inverter_model, 3)
        amps_per_kw = 1.0 / (0.230 * phases)        # one battery kW, per phase
        current = max_current or 0.0

        if not battery_active:
            # Idle: the rule-1 power limit acts only on grid charging and
            # discharging, so it is left alone — no write.  The next charge or
            # discharge transition writes the Power Level with its own writes
            # (and is cut once if a phase then goes over the limit).  Writing
            # it here undid the 0 the TREX-25/50/IVGM idle transition writes on
            # purpose, one poll later, with a false "external change" log.
            # Flexible loads can still be shed: the battery cannot help now.
            if current > max_amperage * 0.95:
                try:
                    load_shed = await self._safe_power_shed_loads(current, max_amperage)
                except Exception as err:
                    _LOGGER.error("Load shedding failed (non-fatal): %s", err)
                    load_shed = False
                if not load_shed:
                    _LOGGER.warning(
                        "High current %.1fA (max %.0fA) — battery idle and no "
                        "flexible load left to shed", current, max_amperage)
            self.safe_max_power = user_level
            return user_level

        if settling:
            # The inverter is still moving down to the last cut: the current
            # read now is from before it got there (22 Oct 2026:
            # "20.7 A — cutting to level 3", 16 s later "19.5 A — to level 1").
            _LOGGER.debug("Safe power: waiting for the inverter to follow level %d "
                          "(%.1fA now)", base_level, current)
        elif current > max_amperage and base_level > 1:
            # ORDER (maintainer decision, Oct 2026): the battery power limit
            # first, in ONE step, to the level that brings the busiest phase
            # to SAFE_POWER_TARGET of the limit; a 3-phase inverter's cut is
            # shared over its phases.
            excess_a = current - max_amperage * SAFE_POWER_TARGET
            cut_kw = max(1, math.ceil(excess_a / amps_per_kw))
            safe_level = max(1, base_level - cut_kw)
            _LOGGER.warning("High current %.1fA (max %.0fA) — cutting battery power "
                            "%d kW to level %d", current, max_amperage,
                            base_level - safe_level, safe_level)
        elif current > max_amperage * 0.95 and base_level <= 1:
            # Flexible loads only when the battery cannot help: it is at its
            # 1 kW minimum (idle is handled above).
            try:
                load_shed = await self._safe_power_shed_loads(current, max_amperage)
            except Exception as err:
                _LOGGER.error("Load shedding failed (non-fatal): %s", err)
                load_shed = False
            if not load_shed:
                _LOGGER.warning(
                    "High current %.1fA (max %.0fA) — battery power at its minimum "
                    "and no flexible load left to shed", current, max_amperage)
        elif base_level < user_level:
            # Back to the Power Level in one write once it fits: the busiest
            # phase would carry the measured current plus the restored kW.  A
            # shed load that fits returns first.
            fits = (current + (user_level - base_level) * amps_per_kw
                    <= max_amperage * SAFE_POWER_TARGET)
            if self._shed_load_waiting_to_fit(current, max_amperage):
                _LOGGER.debug("Current %.1fA — a shed load returns first; "
                              "battery stays at level %d", current, base_level)
            elif fits:
                safe_level = user_level
                _LOGGER.info("Current %.1fA — Power Level %d fits again (was %d)",
                             current, user_level, base_level)
            else:
                _LOGGER.debug("Current %.1fA — Power Level %d would not fit; "
                              "staying at %d", current, user_level, base_level)
        else:
            _LOGGER.debug("Current %.1fA — Power Level %d", current, base_level)

        # --- 6. Write if level changed OR user explicitly changed power_level ---
        if safe_level != base_level or user_level_changed:
            target_watts = round(safe_level * 1000)
            _LOGGER.info("Writing safe power limit: %dW (level %d)%s",
                         target_watts, safe_level,
                         " (user change)" if user_level_changed else "")
            try:
                await self.TypeSpecificHandler.write_type_specific_register("econ_rule_1_power", target_watts)
                if safe_level < base_level:
                    self._safe_power_cut_ts = time.time()
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
        # A monitor-only model (const.MONITOR_ONLY_MODELS) has no documented
        # control registers: never attempt a transition.  False, so the state
        # is not recorded as applied.  type_specific refuses the writes anyway;
        # this keeps the coordinator from trying every tick.
        if self.inverter_model in MONITOR_ONLY_MODELS:
            _LOGGER.debug("%s is monitoring-only — not applying state %s",
                          self.inverter_model, new_state)
            return False
        # grid_mode "off" means HANDS OFF the inverter.  The only write still
        # allowed is undoing a charge/discharge THIS session started (the user
        # switched the EMS off mid-action).  Without this guard every HA start
        # wrote the "idle" state — _current_energy_state starts as None, and
        # "idle" != None — which on TREX-5/10 forced operating_mode=0 (General)
        # and on TREX-25/50/IVGM wrote system_mode, zero-export, peak-shaving
        # and rule-1 power registers, on installs that had never enabled the EMS.
        if (opts.get("grid_mode", "off") == "off"
                and self._current_energy_state not in ("charging", "discharging")):
            _LOGGER.debug("grid_mode is off — not writing state %s to the inverter", new_state)
            return True
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
            soc_limit = round(self._reserve_target_pct)
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
                ("econ_rule_1_power", round(self.safe_max_power * 1000)),
            ]:
                ok = await self.TypeSpecificHandler.write_type_specific_register(reg, val)
                if not ok:
                    _LOGGER.warning("Failed to write %s=%s during %s transition", reg, val, new_state)
        return True

    async def _restamp_rule1_date(self) -> None:
        """Carry an action that is running at midnight into the new day.

        T-REX-5/6/10 rule 1 has a start/stop DATE, which _transition_to_state
        sets to today's date.  A charge or discharge that runs across midnight
        has no transition at 00:00, so the rule still carried yesterday's date
        and the inverter stopped obeying it while the coordinator believed it
        was still active — and the Economic-mode watchdog cannot see that.
        Found by the day-replay harness.  (No-op on models without the date
        registers: type_specific ignores them there.)
        """
        if self._current_energy_state not in ("charging", "discharging"):
            return
        now = datetime.now()
        date_16bit = (now.month << 8) | now.day
        for reg in ("econ_rule_1_start_day", "econ_rule_1_stop_day"):
            if not await self.TypeSpecificHandler.write_type_specific_register(reg, date_16bit):
                _LOGGER.warning("Failed to carry %s into the new day", reg)

    async def _apply_rule1_auto_settings(self, data: dict | None = None) -> None:
        """If rule 1 auto settings are enabled, ensure the inverter's
        time-window and weekday-mask match the auto defaults.

        Writes only when the current register value doesn't match the
        target — so this is safe to call on every state activation.
        Felicity's 24-hour convention is start=00:00, stop=23:59 (the
        firmware doesn't accept stop=00:00 or stop=24:00).
        """
        # This poll's fresh read.  self.data is the PREVIOUS poll, taken before
        # last tick's write, so checking it re-wrote every target once more.
        data = self.data if data is None else data
        if not data:
            return

        opts = self.config_entry.options
        if opts.get("grid_mode", "off") == "off":
            return  # EMS off → never write the inverter (see _transition_to_state)

        if opts.get("rule1_time_window", "manual") == "auto":
            target_start = 0                       # 00:00
            target_stop = (23 << 8) | 59           # 23:59 (Felicity 24h)
            current_start = data.get("econ_rule_1_start_time")
            current_stop = data.get("econ_rule_1_stop_time")
            if current_start != target_start:
                ok = await self.TypeSpecificHandler.write_type_specific_register(
                    "econ_rule_1_start_time", target_start
                )
                if ok:
                    data["econ_rule_1_start_time"] = target_start
                    _LOGGER.info(
                        "Rule 1 auto: wrote start_time=00:00 (was %s)",
                        current_start,
                    )
            if current_stop != target_stop:
                ok = await self.TypeSpecificHandler.write_type_specific_register(
                    "econ_rule_1_stop_time", target_stop
                )
                if ok:
                    data["econ_rule_1_stop_time"] = target_stop
                    _LOGGER.info(
                        "Rule 1 auto: wrote stop_time=23:59 (was %s)",
                        current_stop,
                    )

        if opts.get("rule1_weekday", "manual") == "auto":
            target_week = 0x7F  # all 7 days enabled (bit0=Sun..bit6=Sat)
            current_week = data.get("econ_rule_1_effective_week")
            if current_week != target_week:
                ok = await self.TypeSpecificHandler.write_type_specific_register(
                    "econ_rule_1_effective_week", target_week
                )
                if ok:
                    data["econ_rule_1_effective_week"] = target_week
                    _LOGGER.info(
                        "Rule 1 auto: wrote effective_week=all days (was 0x%02X)",
                        current_week if isinstance(current_week, int) else 0,
                    )

    async def _ensure_economic_mode_when_active(self, data: dict | None = None) -> None:
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
        # Judge the register by THIS poll's read.  self.data is the previous
        # poll — read before the transition that switched Economic mode on —
        # so every transition into charging/discharging was followed, one tick
        # later, by a false "dropped out of Economic mode" warning and a full
        # re-write of the rule (found by the day-replay harness).
        data = self.data if data is None else data
        if not data:
            return

        enable_value = {"charging": 1, "discharging": 2}[self._current_energy_state]

        # By control path, not by a hand-listed model pair: the pair lists
        # left T-REX-6K and every IVGM model with no self-heal at all.
        if self.inverter_model in ECO_TIMEOFUSE_MODELS:
            eco = data.get("eco_timeofuse")
            if eco is None or int(eco) == 1:
                return  # register not read, or Economic mode already active
            register_val = eco
        elif self.inverter_model in OPERATING_MODE_MODELS:
            mode = data.get("operating_mode")
            if mode is None or int(mode) == 2:
                return  # register not read, or Economic mode already active
            register_val = mode
        else:
            return

        rule_enable = data.get("econ_rule_1_enable")
        _LOGGER.warning(
            "Self-heal: inverter dropped out of Economic mode (register=%s, "
            "rule_1_enable=%s) while state=%s — re-applying the full Rule 1 "
            "state so charging actually resumes (battery was likely sitting "
            "inert)",
            register_val,
            "unknown" if rule_enable is None else rule_enable,
            self._current_energy_state,
        )
        # Re-apply the FULL active state, not just the operating mode.
        #
        # Whatever knocked the inverter out of Economic mode (firmware quirk,
        # Felicity app, power blip) can also have cleared `econ_rule_1_enable`
        # and the rule's SOC/voltage/power parameters.  Restoring only the mode
        # would then leave Rule 1 disabled — the battery stays inert AND the
        # watchdog goes quiet, because the mode register now reads Economic
        # again.  A silent, unrecoverable-until-next-state-change failure.
        #
        # `_transition_to_state` is the single atomic write path (mode first,
        # then enable, then the rule parameters) and every write in it is
        # idempotent, so re-applying the current state is safe.  The trigger is
        # unchanged — this only runs when the mode register actually reads
        # non-Economic — so it adds no extra write traffic in the normal case.
        ok = await self._transition_to_state(self._current_energy_state)
        if ok:
            # Reflect the re-assertion in cached data so we don't re-trigger
            # before the next read.
            if self.inverter_model in ECO_TIMEOFUSE_MODELS:
                data["eco_timeofuse"] = 1
            else:
                data["operating_mode"] = 2
            data["econ_rule_1_enable"] = enable_value
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
        

        
    def _start_read_backoff(self, reason: str) -> None:
        """Pause polling after the inverter stopped answering (doubling pause)."""
        self._read_backoff_s = min(
            READ_BACKOFF_MAX_S,
            self._read_backoff_s * 2 if self._read_backoff_s else READ_BACKOFF_INITIAL_S,
        )
        self._read_backoff_until = time.time() + self._read_backoff_s
        _LOGGER.warning(
            "Felicity inverter not responding (%s) — pausing polling for %ds, "
            "then retrying. Check the Modbus gateway/dongle and cabling.",
            reason, self._read_backoff_s,
        )
        # Drop the socket: a gateway that stopped answering often recovers only
        # on a fresh connection.  The next poll reconnects.
        self.connected = False
        try:
            self.client.close()
        except Exception as err:  # best effort
            _LOGGER.debug("Closing Modbus client failed: %s", err)

    def _split_group(self, group: dict) -> list[dict]:
        """Split a read group into two contiguous halves at a key boundary."""
        keys = group["keys"]
        halves = []
        for part in (keys[:len(keys) // 2], keys[len(keys) // 2:]):
            infos = [self.register_map[k] for k in part]
            halves.append({
                "start": infos[0]["address"],
                "count": sum(i.get("size", 1) for i in infos),
                "keys": list(part),
                "split_from": (group["start"], group["count"]),
            })
        return halves

    def _note_group_answered(self, group: dict) -> None:
        """A read answered: its registers exist, and its miss streak is over."""
        self._answered_keys.update(group["keys"])
        self._group_timeout_strikes.pop((group["start"], group["count"]), None)
        if "split_from" in group:
            self._group_timeout_strikes.pop(group["split_from"], None)

    def _adapt_read_groups(self, timed_out: list[dict]) -> None:
        """Isolate reads that time out while the rest of the poll succeeds.

        Only called when at least one read in the same poll was answered, so a
        dead link (handled by the backoff) never splits or drops anything.
        """
        replace: dict[int, list[dict]] = {}

        # Both halves of a split block silent in the same poll: the whole block
        # is unsupported (a block the model does not have), not one bad address.  Bisecting further would cost dozens of
        # timeouts, so after DEAD_REGISTER_STRIKES polls in total (once whole,
        # then twice as a silent pair) the block is dropped in one go.
        by_parent: dict[tuple, list[dict]] = {}
        for group in timed_out:
            if "split_from" in group:
                by_parent.setdefault(group["split_from"], []).append(group)
        # A block whose registers have answered before exists — then the misses
        # are the link's, and the halves are handled one by one below.
        silent_pairs = [pair for pair in by_parent.values() if len(pair) == 2
                        and not any(k in self._answered_keys
                                    for half in pair for k in half["keys"])]
        for pair in silent_pairs:
            parent = pair[0]["split_from"]
            strikes = self._group_timeout_strikes.get(parent, 1) + 1
            self._group_timeout_strikes[parent] = strikes
            if strikes >= DEAD_REGISTER_STRIKES:
                keys = pair[0]["keys"] + pair[1]["keys"]
                for half in pair:
                    replace[id(half)] = []
                self.dead_register_keys.update(keys)
                _LOGGER.warning(
                    "Registers at %d–%d (%s … %s) never respond on this inverter/gateway "
                    "— the model probably does not have them; no longer polled until "
                    "the integration is reloaded",
                    parent[0], parent[0] + parent[1] - 1, keys[0], keys[-1],
                )
        paired = {id(g) for pair in silent_pairs for g in pair}

        for group in timed_out:
            if id(group) in paired:
                continue    # handled above: kept whole for now, or dropped
            if any(k not in self.register_map for k in group["keys"]):
                continue
            answered_before = all(k in self._answered_keys for k in group["keys"])
            if answered_before and group["count"] <= FLAKY_MIN_REGISTERS:
                continue    # intermittent on a known-good block: leave it as is
            if len(group["keys"]) > 1:
                halves = self._split_group(group)
                replace[id(group)] = halves
                _LOGGER.warning(
                    "Read at %d (count %d) got no response while other reads succeeded "
                    "— splitting it into %s to isolate the cause",
                    group["start"], group["count"],
                    ", ".join(f"{h['start']}/{h['count']}" for h in halves),
                )
                continue
            if answered_before:
                continue    # a register that ever answered exists: never drop it
            sig = (group["start"], group["count"])
            strikes = self._group_timeout_strikes.get(sig, 0) + 1
            self._group_timeout_strikes[sig] = strikes
            if strikes >= DEAD_REGISTER_STRIKES:
                replace[id(group)] = []
                self.dead_register_keys.update(group["keys"])
                _LOGGER.warning(
                    "Register %s (address %d) never responds on this inverter/gateway "
                    "— no longer polled until the integration is reloaded",
                    group["keys"][0], group["start"],
                )
        if replace:
            self._address_groups = [
                g for group in self._address_groups
                for g in replace.get(id(group), [group])
            ]

    def _record_poll_stats(self, started: float, groups_ok: int,
                           timed_out: list[dict], silent_retries: int) -> None:
        """Keep the last poll's timing; warn (hourly) when polling is slow."""
        duration = time.monotonic() - started
        self.poll_stats = {
            "duration_s": round(duration, 1),
            "groups": len(self._address_groups),
            "groups_ok": groups_ok,
            "groups_timed_out": [f"{g['start']}/{g['count']}" for g in timed_out],
            "silent_retries": silent_retries,
            "dead_registers": sorted(self.dead_register_keys),
            "reply_count_mismatches": getattr(self, "_reply_count_mismatches", 0),
            "implausible_readings": dict(getattr(
                getattr(self, "_plausibility", None), "rejected", {}) or {}),
        }
        update_interval = getattr(self, "update_interval", None)
        interval = update_interval.total_seconds() if update_interval else 10
        if (duration > SLOW_POLL_FACTOR * interval
                and time.monotonic() - self._last_slow_poll_warning > 3600):
            self._last_slow_poll_warning = time.monotonic()
            _LOGGER.warning(
                "Polling the inverter took %.0fs (update interval %.0fs): %d of %d "
                "reads answered, %d without response, %d silent retries (each costs "
                "a full timeout). On a slow serial link, check the gateway's RTU "
                "timeout and raise the baud rate if the inverter allows.",
                duration, interval, groups_ok, len(self._address_groups),
                len(timed_out), silent_retries,
            )

    async def _async_update_data(self) -> dict:
        """Fetch latest data from inverter."""
        remaining = self._read_backoff_until - time.time()
        if remaining > 0:
            # Fail fast without touching the bus: UpdateFailed marks entities
            # unavailable, and during setup it becomes ConfigEntryNotReady so HA
            # retries the setup itself instead of hanging on it.
            raise UpdateFailed(
                f"Inverter not responding — next attempt in {int(remaining)}s")

        if not await self._async_connect():
            raise UpdateFailed("Cannot connect to Felicity inverter")

        new_data = {}
        any_read_ok = False
        consecutive_timeouts = 0
        poll_started = time.monotonic()
        timed_out_groups: list[dict] = []
        silent_retries = 0
        groups_ok = 0

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
                    timed_out_groups.append(group)
                    consecutive_timeouts += 1
                    if consecutive_timeouts >= MAX_CONSECUTIVE_READ_TIMEOUTS:
                        if not any_read_ok:
                            self._start_read_backoff(
                                f"{consecutive_timeouts} consecutive reads without response")
                            raise UpdateFailed(
                                "Inverter not responding — polling paused")
                        # The link answered earlier this cycle: keep what we
                        # have rather than spend ~20 s on every remaining group.
                        _LOGGER.warning(
                            "%d consecutive reads without response — skipping the "
                            "rest of this poll cycle", consecutive_timeouts)
                        break
                    continue

                # Any response — even a Modbus exception — proves the link is up.
                consecutive_timeouts = 0
                # pymodbus retries silently; each retry cost a full timeout.
                retries = getattr(result, "retries", 0)
                if isinstance(retries, int):
                    silent_retries += retries
                if result.isError():
                    _LOGGER.warning("Read error at address %d, skipping group", start_addr)
                    continue
                # A reply of the wrong length answers some OTHER request (issue
                # #220): pymodbus RTU accepts any reply from the right slave, so
                # after a late reply and a silent retry a poll can run one reply
                # behind — a 4-register lifetime total decoded into a 6-register
                # year/month/day block (0 in one, the lifetime in the next).
                # Discard it rather than decode it against the wrong keys.
                registers = getattr(result, "registers", None)
                if not isinstance(registers, list) or len(registers) != count:
                    self._reply_count_mismatches += 1
                    _LOGGER.warning(
                        "Reply for %d/%d had %s registers — not this request's "
                        "answer, discarded", start_addr, count,
                        len(registers) if isinstance(registers, list) else "no")
                    continue

                if not any_read_ok and self._read_backoff_s:
                    _LOGGER.info("Felicity inverter responding again — polling resumed")
                    self._read_backoff_s = 0.0
                    self._read_backoff_until = 0.0
                any_read_ok = True
                groups_ok += 1
                self._note_group_answered(group)

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
            self._plausibility.apply(new_data, time.time())
            self._record_poll_stats(poll_started, groups_ok, timed_out_groups, silent_retries)
            if any_read_ok and timed_out_groups:
                self._adapt_read_groups(timed_out_groups)

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
                            await self._backfill_consumption_history()
                            await self._backfill_price_history()

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
                                await self._rotate_slot_overrides(first_boot=first_boot)
                                if not first_boot:
                                    await self._restamp_rule1_date()
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
                            if self._record_daily_prices():
                                await self._save_consumption_store()
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
                            else:
                                # Manual mode: the displayed schedule must follow
                                # the PRICE THRESHOLD, not a stale optimizer plan.
                                # _calculate_schedule only runs in auto, so
                                # scheduled_slots would otherwise keep showing the
                                # last MILP/greedy plan — including charge slots
                                # ABOVE the threshold, which manual mode must
                                # never do.  Rebuild it from the threshold here.
                                self._build_manual_schedule()

                            # Always calculate available info (visible in both modes)
                            self._calculate_available_info(battery_soc)

                            # Apply rule 1 time-window / weekday auto settings
                            # if enabled.  Writes are idempotent — only happens
                            # when the register doesn't already match the target.
                            await self._apply_rule1_auto_settings(new_data)

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
                            # importing (e.g. an EV or oven pulls more than the
                            # discharge power — the battery would be better spent
                            # covering the house in self-use).  Filters spikes:
                            #   - small/moderate import (200-2000W) must persist for
                            #     ≥ANTICONFLICT_MIN_TICKS consecutive cycles
                            #   - large import (>2000W) suppresses immediately
                            #   - a suppression HOLDS discharge off for
                            #     ANTICONFLICT_HOLD_S, then allows one retry.
                            # The hold is what stops the flip-flop: once idle, the
                            # battery covers the house in self-use, the import
                            # disappears, and the guard used to let discharge
                            # straight back in — the import returned, suppress
                            # again, every minute for as long as the load ran (156
                            # rule writes in two hours in the day-replay harness).
                            # Its old 60 s "cooldown" only stopped RE-SUPPRESSION.
                            ANTICONFLICT_SOFT_THRESHOLD_W = 200
                            ANTICONFLICT_HARD_THRESHOLD_W = 2000
                            ANTICONFLICT_MIN_TICKS = 2
                            ANTICONFLICT_HOLD_S = 300
                            grid_power = None
                            if desired_state == "discharging" and time.time() < self._anticonflict_suppress_until_ts:
                                _LOGGER.debug(
                                    "Anti-conflict: discharge held off for %.0fs more",
                                    self._anticonflict_suppress_until_ts - time.time())
                                desired_state = "idle"
                            elif desired_state == "discharging":
                                if hasattr(self.TypeSpecificHandler, 'determine_grid_power'):
                                    grid_power = self.TypeSpecificHandler.determine_grid_power(new_data)
                                if grid_power is not None and grid_power > ANTICONFLICT_SOFT_THRESHOLD_W:
                                    self._anticonflict_import_ticks += 1
                                    sustained = self._anticonflict_import_ticks >= ANTICONFLICT_MIN_TICKS
                                    large = grid_power > ANTICONFLICT_HARD_THRESHOLD_W
                                    if sustained or large:
                                        _LOGGER.info(
                                            "Anti-conflict: suppressing discharge for %ds — grid "
                                            "importing %.0fW (sustained=%d ticks, large=%s) — "
                                            "would sell battery while buying from grid",
                                            ANTICONFLICT_HOLD_S, grid_power,
                                            self._anticonflict_import_ticks, large,
                                        )
                                        desired_state = "idle"
                                        self._anticonflict_import_ticks = 0
                                        self._anticonflict_suppress_until_ts = (
                                            time.time() + ANTICONFLICT_HOLD_S
                                        )
                                    else:
                                        _LOGGER.debug(
                                            "Anti-conflict: tolerating brief import %.0fW "
                                            "(tick %d/%d) — keeping discharge",
                                            grid_power,
                                            self._anticonflict_import_ticks,
                                            ANTICONFLICT_MIN_TICKS,
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

                            if self.inverter_model in MONITOR_ONLY_MODELS:
                                # Nothing to drive (const.MONITOR_ONLY_MODELS):
                                # report self-use, never attempt a transition.
                                self._current_energy_state = "idle"
                            elif desired_state != self._current_energy_state:
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
                                await self._ensure_economic_mode_when_active(new_data)
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
            except Exception as err:
                _LOGGER.error("Flex load actuation failed (non-fatal): %s", err)

            return new_data

        except UpdateFailed:
            raise
        except ConnectionException as err:
            self.connected = False
            self.client.close()  # synchronous in pymodbus 3.x
            raise UpdateFailed(f"Connection lost: {err}")
        except ModbusException as err:
            raise UpdateFailed(f"Modbus error: {err}")
        except Exception as err:
            _LOGGER.exception("Unexpected error in Felicity coordinator update")
            raise UpdateFailed(f"Unexpected update error: {err}")
