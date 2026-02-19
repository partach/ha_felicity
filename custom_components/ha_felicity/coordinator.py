"""Data update coordinator for Felicity with proper async handling."""

import logging
import math
from datetime import timedelta, datetime
from typing import Dict, Any
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pymodbus.exceptions import ModbusException, ConnectionException
from .const import DOMAIN, INVERTER_MODEL_TREX_TEN # only for determining default
from .type_specific import TypeSpecificHandler

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
        config_entry=ConfigEntry,
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
        self.TypeSpecificHandler = TypeSpecificHandler(client=self.client, slave_id=self.slave_id, inverter_model=self.inverter_model, register_map=self.register_map)
        
        # Nordpool: override wins over entity
        self.nordpool_entity = nordpool_override or nordpool_entity
        self.original_nordpool_entity = nordpool_entity
        self.override_nordpool_entity = nordpool_override
        
        # Runtime state
        self.connected = False
        self._current_energy_state: str | None = None
        self._last_state_change: datetime | None = None
        self._current_day: int | None = None

        # Price tracking
        self.current_price: float | None = None
        self.max_price: float | None = None
        self.min_price: float | None = None
        self.avg_price: float | None = None
        self.price_threshold: float | None = None
        self.safe_max_power = 0 # used in setting rule 1 power checks toward max amperage
        self._last_known_max_amperage: float | None = None

        # Forecast & schedule
        self.forecast_entity = forecast_entity
        self.slot_prices_today: list | None = None
        self.slot_prices_tomorrow: list | None = None
        self.pv_forecast_today: float | None = None
        self.pv_forecast_remaining: float | None = None
        self.pv_forecast_tomorrow: float | None = None
        self.scheduled_slots: set = set()
        self.cheap_slots_remaining: int = 0
        self.grid_energy_planned: float = 0.0
        self.schedule_status: str = "unknown"

        # Consumption tracking & persistent storage
        self.consumption_override_entity = consumption_override_entity
        self._daily_consumption_history: list = []
        self._consumption_store = None
        self._consumption_store_loaded = False
        self.weekly_avg_consumption: float | None = None
        self._yesterday_deficit: float = 0.0

        # Always-visible slot info (regardless of price_mode)
        self.available_slots_at_threshold: int = 0
        self.available_energy_capacity: float = 0.0
        self.charge_likelihood: str = "unknown"

        
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
                    if grid_mode == "from_grid" and battery_soc <= charge_max:
                        return "charging"
                    if grid_mode == "to_grid" and battery_soc >= discharge_min:
                        return "discharging"
                return "idle"
            # Auto mode fallback when no slot data yet
            _LOGGER.debug("Auto mode: no slot data available, returning idle")
            return "idle"

        # Manual mode: simple price threshold comparison (original behavior)
        if self.current_price is None or self.price_threshold is None:
            _LOGGER.info("current price or price threshold is unknown, returning idle")
            return "idle"

        if grid_mode == "from_grid" and self.current_price < self.price_threshold and battery_soc <= charge_max:
            return "charging"
        if grid_mode == "to_grid" and self.current_price > self.price_threshold and battery_soc >= discharge_min:
            return "discharging"

        return "idle"

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
            return

        state = self.hass.states.get(self.forecast_entity)
        if not state or state.state in ("unknown", "unavailable"):
            self.pv_forecast_today = None
            self.pv_forecast_remaining = None
            return

        try:
            self.pv_forecast_today = float(state.state)
        except (ValueError, TypeError):
            self.pv_forecast_today = None
            self.pv_forecast_remaining = None
            return

        now = datetime.now()
        attrs = state.attributes or {}
        remaining = None

        # Try Forecast.Solar (wh_hours) or Solcast (detailedHourly) hourly breakdown
        wh_data = attrs.get("wh_hours") or attrs.get("detailedHourly")
        if isinstance(wh_data, dict):
            try:
                remaining_wh = 0.0
                for ts_str, value in wh_data.items():
                    ts = self._parse_forecast_time(ts_str)
                    if ts and ts >= now:
                        remaining_wh += float(value)
                remaining = remaining_wh / 1000.0
            except Exception as err:
                _LOGGER.debug("Could not parse forecast hourly data: %s", err)

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

    def _calculate_schedule(self, battery_soc: float | None) -> None:
        """Calculate optimal charge/discharge schedule based on prices, forecast, and battery.

        Handles any slot granularity (15-min, 30-min, hourly).
        Negative prices: always charge (from_grid), never sell (to_grid).
        Includes yesterday's deficit carryover in energy target.
        Uses safe_max_power for realistic energy-per-slot calculation.
        """
        opts = self.config_entry.options
        grid_mode = opts.get("grid_mode", "off")

        if grid_mode == "off" or not self.slot_prices_today:
            self.scheduled_slots = set()
            self.cheap_slots_remaining = 0
            self.grid_energy_planned = 0.0
            self.schedule_status = "off" if grid_mode == "off" else "no_price_data"
            return

        now = datetime.now()
        prices = self.slot_prices_today
        num_slots = len(prices)
        minutes_per_slot = (24 * 60) / num_slots
        current_slot = int((now.hour * 60 + now.minute) / minutes_per_slot)
        current_slot = min(current_slot, num_slots - 1)
        slot_duration_hours = minutes_per_slot / 60.0

        battery_capacity = opts.get("battery_capacity_kwh", 10)
        efficiency = opts.get("efficiency_factor", 0.90)
        charge_max = opts.get("battery_charge_max_level", 100)
        discharge_min = opts.get("battery_discharge_min_level", 20)
        consumption_est = self._get_consumption_estimate()

        # Use safe_max_power (watts) for realistic slot energy, fallback to power_level (kW scale)
        safe_power_kw = max(1, self.safe_max_power / 1000) if self.safe_max_power > 0 else opts.get("power_level", 5)

        remaining = [(i, prices[i]) for i in range(current_slot, num_slots) if prices[i] is not None]

        if not remaining:
            self.scheduled_slots = set()
            self.cheap_slots_remaining = 0
            self.grid_energy_planned = 0.0
            self.schedule_status = "day_complete"
            return

        current_kwh = (battery_soc / 100.0) * battery_capacity if battery_soc is not None else 0
        hours_left = len(remaining) * slot_duration_hours
        pv_remaining = self.pv_forecast_remaining or 0.0
        consumption_remaining = (consumption_est / 24.0) * hours_left
        net_pv = max(0.0, pv_remaining - consumption_remaining)
        energy_per_slot = safe_power_kw * slot_duration_hours

        energy_target = 0.0  # for logging

        if grid_mode == "from_grid":
            target_kwh = (charge_max / 100.0) * battery_capacity
            energy_deficit = max(0.0, target_kwh - current_kwh - net_pv)

            # Add yesterday's deficit carryover
            if self._yesterday_deficit > 0:
                energy_deficit += self._yesterday_deficit
                _LOGGER.debug("Adding yesterday deficit: +%.2f kWh → total deficit: %.2f kWh",
                              self._yesterday_deficit, energy_deficit)

            energy_target = energy_deficit

            if energy_deficit <= 0:
                self.scheduled_slots = set()
                self.cheap_slots_remaining = 0
                self.grid_energy_planned = 0.0
                self.schedule_status = "no_action_needed"
                return

            effective_per_slot = energy_per_slot * efficiency

            # Negative prices: always charge (you get paid to take energy!)
            negative_slots = [(i, p) for i, p in remaining if p < 0]
            non_negative_slots = [(i, p) for i, p in remaining if p >= 0]

            # Energy provided by forced negative-price slots
            negative_energy = len(negative_slots) * effective_per_slot
            remaining_deficit = max(0.0, energy_deficit - negative_energy)

            # Fill remaining deficit with cheapest non-negative slots
            sorted_non_negative = sorted(non_negative_slots, key=lambda x: x[1])
            additional_needed = math.ceil(remaining_deficit / effective_per_slot) if effective_per_slot > 0 else 0
            selected_positive = sorted_non_negative[:additional_needed]

            selected = negative_slots + selected_positive

            self.scheduled_slots = {s[0] for s in selected}
            self.cheap_slots_remaining = len(self.scheduled_slots)
            self.grid_energy_planned = round(min(energy_deficit, len(selected) * effective_per_slot), 2)

            if selected:
                # Threshold is the highest price in our selected set (boundary price)
                self.price_threshold = max(s[1] for s in selected)

        elif grid_mode == "to_grid":
            min_kwh = (discharge_min / 100.0) * battery_capacity
            sellable = max(0.0, current_kwh - min_kwh) * efficiency
            energy_target = sellable

            if sellable <= 0:
                self.scheduled_slots = set()
                self.cheap_slots_remaining = 0
                self.grid_energy_planned = 0.0
                self.schedule_status = "no_action_needed"
                return

            # Never sell at negative prices
            positive_slots = [(i, p) for i, p in remaining if p > 0]
            slots_needed = math.ceil(sellable / energy_per_slot) if energy_per_slot > 0 else 0
            sorted_slots = sorted(positive_slots, key=lambda x: -x[1])
            selected = sorted_slots[:slots_needed]

            self.scheduled_slots = {s[0] for s in selected}
            self.cheap_slots_remaining = len(self.scheduled_slots)
            self.grid_energy_planned = round(min(sellable, len(selected) * energy_per_slot), 2)

            if selected:
                self.price_threshold = min(s[1] for s in selected)

        else:
            self.scheduled_slots = set()
            self.cheap_slots_remaining = 0
            self.grid_energy_planned = 0.0

        # Update schedule status
        if not self.scheduled_slots:
            self.schedule_status = "no_action_needed"
        elif current_slot in self.scheduled_slots:
            self.schedule_status = "active"
        else:
            self.schedule_status = "waiting"

        _LOGGER.debug(
            "Schedule: mode=%s, target=%.1fkWh, net_pv=%.1fkWh, slots=%d/%d-min, status=%s, threshold=%.4f, deficit_carryover=%.2f",
            grid_mode, energy_target, net_pv,
            len(self.scheduled_slots), int(minutes_per_slot),
            self.schedule_status, self.price_threshold or 0,
            self._yesterday_deficit,
        )

    def _get_consumption_estimate(self) -> float:
        """Get best available daily consumption estimate.

        Priority: 7-day rolling average > user-set estimate > default.
        """
        if self.weekly_avg_consumption is not None and self.weekly_avg_consumption > 0:
            return self.weekly_avg_consumption
        return self.config_entry.options.get("daily_consumption_estimate", 10)

    def _calculate_available_info(self, battery_soc: float | None) -> None:
        """Calculate available slots and charge likelihood (always visible, regardless of price_mode).

        This gives the user insight into how many slots remain below/above their
        current threshold and whether the battery target is likely to be met.
        """
        opts = self.config_entry.options
        grid_mode = opts.get("grid_mode", "off")

        if not self.slot_prices_today or self.price_threshold is None:
            self.available_slots_at_threshold = 0
            self.available_energy_capacity = 0.0
            self.charge_likelihood = "no_data"
            return

        now = datetime.now()
        prices = self.slot_prices_today
        num_slots = len(prices)
        minutes_per_slot = (24 * 60) / num_slots
        current_slot = int((now.hour * 60 + now.minute) / minutes_per_slot)
        current_slot = min(current_slot, num_slots - 1)
        slot_duration_hours = minutes_per_slot / 60.0

        remaining = [(i, prices[i]) for i in range(current_slot, num_slots) if prices[i] is not None]

        # Use safe_max_power for realistic calculation
        safe_power_kw = max(1, self.safe_max_power / 1000) if self.safe_max_power > 0 else opts.get("power_level", 5)
        efficiency = opts.get("efficiency_factor", 0.90)
        energy_per_slot = safe_power_kw * slot_duration_hours * efficiency

        # Count slots that match the current threshold
        if grid_mode == "from_grid":
            available = [s for s in remaining if s[1] <= self.price_threshold]
        elif grid_mode == "to_grid":
            available = [s for s in remaining if s[1] >= self.price_threshold]
        else:
            available = []

        self.available_slots_at_threshold = len(available)
        self.available_energy_capacity = round(len(available) * energy_per_slot, 2)

        # Determine charge likelihood
        if battery_soc is None or grid_mode == "off":
            self.charge_likelihood = "unknown" if battery_soc is None else "idle"
            return

        battery_capacity = opts.get("battery_capacity_kwh", 10)
        charge_max = opts.get("battery_charge_max_level", 100)
        discharge_min = opts.get("battery_discharge_min_level", 20)
        current_kwh = (battery_soc / 100.0) * battery_capacity

        pv_remaining = self.pv_forecast_remaining or 0.0
        hours_left = len(remaining) * slot_duration_hours
        consumption_remaining = (self._get_consumption_estimate() / 24.0) * hours_left
        net_pv = max(0.0, pv_remaining - consumption_remaining)

        if grid_mode == "from_grid":
            target_kwh = (charge_max / 100.0) * battery_capacity
            energy_deficit = max(0.0, target_kwh - current_kwh - net_pv)

            if energy_deficit <= 0:
                self.charge_likelihood = "on_track"
            elif self.available_energy_capacity >= energy_deficit * 1.2:
                self.charge_likelihood = "on_track"
            elif self.available_energy_capacity >= energy_deficit:
                self.charge_likelihood = "tight"
            elif self.available_energy_capacity >= energy_deficit * 0.5:
                self.charge_likelihood = "at_risk"
            else:
                self.charge_likelihood = "insufficient"
        elif grid_mode == "to_grid":
            min_kwh = (discharge_min / 100.0) * battery_capacity
            sellable = max(0.0, current_kwh - min_kwh)
            if sellable <= 0:
                self.charge_likelihood = "nothing_to_sell"
            elif self.available_slots_at_threshold > 0:
                self.charge_likelihood = "selling"
            else:
                self.charge_likelihood = "no_profitable_slots"

    async def _init_consumption_store(self) -> None:
        """Initialize persistent storage for 7-day consumption history."""
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
                        "daily_consumption", "daily_energy_used"]:
                val = self.data.get(key)
                if val is not None and val > 0:
                    # Convert Wh to kWh if value seems to be in Wh (>100)
                    today_consumption = val / 1000.0 if val > 100 else val
                    break

        if today_consumption is not None and today_consumption > 0:
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

            # Persist
            if self._consumption_store:
                await self._consumption_store.async_save({
                    "daily_history": self._daily_consumption_history
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

    def _calculate_yesterday_deficit(self, battery_soc: float | None) -> None:
        """At midnight, calculate how much energy target was missed yesterday."""
        if battery_soc is None:
            self._yesterday_deficit = 0.0
            return

        opts = self.config_entry.options
        grid_mode = opts.get("grid_mode", "off")

        if grid_mode != "from_grid":
            self._yesterday_deficit = 0.0
            return

        battery_capacity = opts.get("battery_capacity_kwh", 10)
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
        No extra reads needed — uses already-fetched new_data."""
        
        opts = self.config_entry.options
        user_level = opts.get("power_level", 5)
        max_amperage = opts.get("max_amperage_per_phase", 16)
    
        # --- 1. Safe base_level init ---
        base_level = getattr(self, "safe_max_power", 0)
        if base_level == 0:
            base_level = user_level
    
        # --- 2. Detect config changes (max_amperage or user_level) ---
        previous_max = getattr(self, "_last_known_max_amperage", None)
        if previous_max is not None and previous_max != max_amperage:
            _LOGGER.info(
                "Max amperage changed: %.1fA → %.1fA — resetting state to user level",
                previous_max, max_amperage
            )
            base_level = user_level
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
    
        if max_current == 0:
            _LOGGER.debug("No grid current — keeping current level %d", base_level)
        elif max_current > max_amperage * 0.95:
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
    
        # --- 6. Write only if safety requires change ---
        if safe_level != base_level:
            target_watts = int(round(safe_level * 1000))
            _LOGGER.info("Writing safe power limit: %dW (level %d)", target_watts, safe_level)
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
    
    async def _transition_to_state(self, new_state: str) -> None:
        """Apply state change via economic rule 1."""
        opts = self.config_entry.options
        now = datetime.now()
        date_16bit = (now.month << 8) | now.day
        voltage_level = opts.get("voltage_level", 58) # safe but how will it go with high voltage systems?
        soc_limit = (
            opts.get("battery_charge_max_level", 100)
            if new_state == "charging"
            else opts.get("battery_discharge_min_level", 20)
        )

        enable_value = {"charging": 1, "discharging": 2, "idle": 0}[new_state]

        _LOGGER.info(
            "Energy state → %s | Price: %.4f | Threshold: %.4f | SOC limit: %d%%",
            new_state.upper(),
            self.current_price or 0,
            self.price_threshold or 0,
            soc_limit,
        )
        await self.TypeSpecificHandler.write_type_specific_register("econ_rule_1_enable", enable_value)
        # This one should be set by user (because we can start stop via rule enable setting?):
 #       await self.TypeSpecificHandler.write_type_specific_register("operating_mode", enable_value)
        if new_state != "idle":
            await self.TypeSpecificHandler.write_type_specific_register("econ_rule_1_soc", int(soc_limit))
            await self.TypeSpecificHandler.write_type_specific_register("econ_rule_1_start_day", date_16bit)
            await self.TypeSpecificHandler.write_type_specific_register("econ_rule_1_stop_day", date_16bit)
            await self.TypeSpecificHandler.write_type_specific_register("econ_rule_1_voltage", int(voltage_level)) # the index is known and used when at writing
            # next one is moved to checking safe power levels, should not be done here
            # await self.TypeSpecificHandler.write_type_specific_register("econ_rule_1_power", int(round(power_level * 1000,0)))
    
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
            "price_slots_today": len(self.slot_prices_today) if self.slot_prices_today else 0,
            "slot_granularity_min": int((24 * 60) / len(self.slot_prices_today)) if self.slot_prices_today else None,
            "available_slots_at_threshold": self.available_slots_at_threshold,
            "available_energy_capacity": self.available_energy_capacity,
            "charge_likelihood": self.charge_likelihood,
            "weekly_avg_consumption": self.weekly_avg_consumption,
            "yesterday_deficit": self._yesterday_deficit,
        }

        # Add kWh for all Wh registers
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

                if result.isError():
                    _LOGGER.warning("Read error at address %d, skipping group", start_addr)
                    continue

                registers = result.registers
                pos = 0
                for key in group["keys"]:
                    info = self.register_map[key]
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

                            # In manual mode, threshold comes from user level. In auto, schedule may override.
                            if price_mode == "manual":
                                self.price_threshold = manual_threshold
                            else:
                                # Auto mode: start with manual as base, schedule will override
                                self.price_threshold = manual_threshold

                            new_data["price_threshold"] = self.price_threshold

                            # Initialize consumption store on first run
                            await self._init_consumption_store()

                            # Midnight reset
                            now = datetime.now()
                            if self._current_day != now.day:
                                _LOGGER.info("New day detected — resetting energy state")
                                battery_soc = self.TypeSpecificHandler.determine_battery_soc(new_data)
                                # Record deficit before resetting (for next-day compensation)
                                self._calculate_yesterday_deficit(battery_soc)
                                # Record daily consumption for rolling average
                                await self._record_daily_consumption()
                                await self._transition_to_state("idle")
                                self._current_energy_state = None
                                self._current_day = now.day
                            else:
                                # Normal cycle: retrieve data, calculate, determine state
                                self._retrieve_slot_prices(price_state)
                                self._retrieve_pv_forecast()

                                battery_soc = self.TypeSpecificHandler.determine_battery_soc(new_data)

                                # In auto mode, run the schedule optimizer
                                if price_mode == "auto":
                                    self._calculate_schedule(battery_soc)
                                    # Schedule may have updated self.price_threshold
                                    new_data["price_threshold"] = self.price_threshold

                                # Always calculate available info (visible in both modes)
                                self._calculate_available_info(battery_soc)

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

                                if desired_state != self._current_energy_state:
                                    await self._transition_to_state(desired_state)
                                    self._current_energy_state = desired_state
                                    self._last_state_change = now
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
