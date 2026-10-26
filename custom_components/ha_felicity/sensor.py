"""Sensor entities for the Felicity integration."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_INVERTER_MODEL, DEFAULT_INVERTER_MODEL, DOMAIN
from .coordinator import HA_FelicityCoordinator

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up Felicity entities based on the coordinator data."""
    coordinator: HA_FelicityCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []

    # Common device_info for all entities from this entry
    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title or "Felicity Inverter",
        manufacturer="Felicity Solar",
        model=entry.data.get(CONF_INVERTER_MODEL, DEFAULT_INVERTER_MODEL),
        configuration_url=f"homeassistant://config/integrations/integration/{entry.entry_id}",
    )    

    # 1. Regular sensors from selected registers 
    # Filter out keys that were already added as specific entity types above
    special_types = {"select", "time8bit", "date8bit", "select_multi", "number"}
    entities.extend([
        (HA_FelicityEnergySensor if info.get("state_class") == "total_increasing"
         else HA_FelicitySensor)(coordinator, entry, key, info)
        for key, info in coordinator.register_map.items()
        if info.get("type") not in special_types
        and info.get("index", 0) != 99
    ])

    # 2. Combined sensors (Computed values like Total PV Power)
    # Use coordinator property if available, else fallback to const defaults
    model_combined = coordinator.model_combined  # ← set in coordinator!
    if model_combined:
        entities.extend(
            HA_FelicityCombinedSensor(coordinator, entry, key, info)
            for key, info in model_combined.items()
        )
    else:
        _LOGGER.warning("No model-specific combined registers found for %s", entry.title)

    nordpool_sensors = [
        HA_FelicityNordpoolSensor(coordinator, "current_price", "Current Price", "€/kWh"),
        HA_FelicityNordpoolSensor(coordinator, "min_price", "Today Min Price", "€/kWh"),
        HA_FelicityNordpoolSensor(coordinator, "max_price", "Today Max Price", "€/kWh"),
        HA_FelicityNordpoolSensor(coordinator, "avg_price", "Today Avg Price", "€/kWh"),
        HA_FelicityNordpoolSensor(coordinator, "price_threshold", "Price Threshold", "€/kWh"),
        HA_FelicityNordpoolSensor(coordinator, "cheap_slots_remaining", "Cheap Slots Remaining", "slots"),
        HA_FelicityNordpoolSensor(coordinator, "grid_energy_planned", "Grid Energy Planned", "kWh"),
        HA_FelicityNordpoolSensor(coordinator, "available_slots_at_threshold", "Available Slots", "slots"),
        HA_FelicityNordpoolSensor(coordinator, "available_energy_capacity", "Available Energy Capacity", "kWh"),
    ]
    entities.extend(nordpool_sensors)
    simple_sensors = [
        HA_FelicitySimpleSensor(coordinator, "safe_max_power", "Safe Max. Power", "W"),
        HA_FelicitySimpleSensor(coordinator,"operational_mode","Operational Mode"),
        HA_FelicitySimpleSensor(coordinator,"highest_grid_current_now","Peak Grid Current Now", "A"),
        HA_FelicitySimpleSensor(coordinator, "weekly_avg_consumption", "Weekly Avg Consumption", "kWh")
    ]
    entities.extend(simple_sensors)
    pv_sensors = [
       HA_FelicitySimpleSensor(coordinator, "pv_forecast_today", "PV Forecast Today", "kWh"),
       HA_FelicitySimpleSensor(coordinator, "pv_forecast_remaining", "PV Forecast Remaining", "kWh"),
       HA_FelicitySimpleSensor(coordinator, "pv_forecast_tomorrow", "PV Forecast Tomorrow", "kWh"),
    ]
    entities.extend(pv_sensors)
        
    entities.append(HA_FelicityEnergyStateSensor(coordinator, entry))
    entities.append(HA_FelicityScheduleStatusSensor(coordinator, entry))
    entities.append(HA_FelicityChargeLikelihoodSensor(coordinator, entry))
    # let's make sure we tie all the sensors to the device:
    for entity in entities:
        entity._attr_device_info = device_info
    async_add_entities(entities)

class HA_FelicityScheduleStatusSensor(CoordinatorEntity, SensorEntity):
    """Sensor showing EMS schedule optimization status."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_name = f"{entry.title} Schedule Status"
        self._attr_unique_id = f"{entry.entry_id}_schedule_status"
        self._attr_icon = "mdi:calendar-clock"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._cached_attributes: dict = {}

    @property
    def native_value(self):
        return self.coordinator.schedule_status or "unknown"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Rebuild the cached attributes once per coordinator tick."""
        try:
            self._cached_attributes = self._build_attributes()
        except Exception:
            _LOGGER.debug("Error building schedule_status attributes", exc_info=True)
            self._cached_attributes = {}
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Prime the attribute cache on initial add."""
        await super().async_added_to_hass()
        try:
            self._cached_attributes = self._build_attributes()
        except Exception:
            _LOGGER.debug("Error priming schedule_status attributes", exc_info=True)
            self._cached_attributes = {}

    @property
    def extra_state_attributes(self):
        return self._cached_attributes

    def _build_attributes(self):
        slot_prices = self.coordinator.slot_prices_today
        num_slots = len(slot_prices) if slot_prices else 0
        scheduled = self.coordinator.scheduled_slots
        opts = self.coordinator.config_entry.options

        # Build per-slot data array for the EMS card
        slot_data = []
        if slot_prices:
            for i, price in enumerate(slot_prices):
                action = scheduled.get(i)  # "charge", "discharge", or None
                slot_data.append({
                    "slot": i,
                    "price": round(price, 4) if price is not None else None,
                    "action": action,
                })

        # Flex load schedule keyed by SLOT (the card draws a strip per bar):
        # {slot_index: [load indices active in that slot]}.  The coordinator
        # stores it keyed by load ({load_index: {slot: True}}).
        flex_slot_map: dict[str, list[int]] = {}
        for load_idx, slots in (self.coordinator._flex_load_scheduled or {}).items():
            for slot in slots:
                flex_slot_map.setdefault(str(slot), []).append(load_idx)
        flex_slot_map_tomorrow: dict[str, list[int]] = {}
        for load_idx, slots in (self.coordinator._flex_load_scheduled_tomorrow or {}).items():
            for slot in slots:
                flex_slot_map_tomorrow.setdefault(str(slot), []).append(load_idx)

        # Effective capacity (nominal × SOH) — what the scheduler actually
        # plans with.  The client-side simulation must use the same value
        # or its SOC/headroom math drifts as the battery ages.
        nominal_capacity = opts.get("battery_capacity_kwh", 10) or 10
        soh_factor = getattr(self.coordinator, "_battery_soh_factor", 1.0)
        effective_capacity = round(nominal_capacity * soh_factor, 2)

        # Build tomorrow's slot data with backend-computed actions
        tomorrow_slot_data = []
        tomorrow_prices = self.coordinator.slot_prices_tomorrow
        tomorrow_scheduled = self.coordinator._tomorrow_scheduled_slots or {}
        if tomorrow_prices:
            for i, price in enumerate(tomorrow_prices):
                tomorrow_slot_data.append({
                    "slot": i,
                    "price": round(price, 4) if price is not None else None,
                    "action": tomorrow_scheduled.get(i),
                })

        return {
            "schedule_reason": self.coordinator.schedule_reason,
            "scheduler_active": self.coordinator.scheduler_active,
            "milp_status": self.coordinator.milp_status,
            # What the plan was actually built from (issue #218: a flat profile
            # and a synthetic PV curve looked exactly like real ones).
            "input_sources": self._build_input_sources(),
            "cheap_slots_remaining": self.coordinator.cheap_slots_remaining,
            "grid_energy_planned_kwh": self.coordinator.grid_energy_planned,
            "scheduled_slot_count": len(scheduled),
            "scheduled_charge_slots": sum(1 for v in scheduled.values() if v == "charge"),
            "scheduled_discharge_slots": sum(1 for v in scheduled.values() if v == "discharge"),
            "tomorrow_precharge_kwh": self.coordinator.tomorrow_precharge,
            "tomorrow_planned_slots": self.coordinator.tomorrow_planned_slots,
            "tomorrow_planned_kwh": self.coordinator.tomorrow_planned_kwh,
            "pv_actual_today_kwh": self.coordinator.pv_actual_today_kwh,
            "pv_forecast_today_kwh": self.coordinator.pv_forecast_today,
            "pv_forecast_remaining_kwh": self.coordinator.pv_forecast_remaining,
            "pv_forecast_tomorrow_kwh": self.coordinator.pv_forecast_tomorrow,
            "price_slots_today": num_slots,
            "slot_granularity_min": int((24 * 60) / num_slots) if num_slots > 0 else None,
            "has_tomorrow_prices": bool(tomorrow_prices),
            "yesterday_deficit_kwh": self.coordinator._yesterday_deficit,
            "price_mode": opts.get("price_mode", "manual"),
            "self_consumption_reserve": self.coordinator.self_consumption_reserve,
            "slot_schedule": slot_data,
            "slot_schedule_tomorrow": tomorrow_slot_data,
            # Simulation parameters for client-side schedule preview
            "sim_params": {
                "battery_capacity_kwh": effective_capacity,
                "battery_soh_factor": soh_factor,
                "battery_charge_max_pct": opts.get("battery_charge_max_level", 100),
                "battery_discharge_min_pct": opts.get("battery_discharge_min_level", 20),
                "reserve_target_pct": opts.get("reserve_target_pct", 0),
                "backend_reserve_target_pct": self.coordinator._reserve_target_pct,
                "arbitrage_price_delta": opts.get("arbitrage_price_delta", 0.0),
                "efficiency": opts.get("efficiency_factor", 0.90),
                "battery_soc_pct": self.coordinator.battery_soc,
                "net_pv_kwh": getattr(self.coordinator, '_last_net_pv', 0),
                "consumption_est_kwh": self.coordinator._get_consumption_estimate(),
                "pv_hourly_kwh": self.coordinator.pv_hourly_kwh or {},
                "pv_hourly_kwh_tomorrow": self.coordinator.pv_hourly_kwh_tomorrow or {},
                "pv_confidence": getattr(self.coordinator, '_last_pv_confidence', 1.0),
                "consumption_hourly_profile": self.coordinator._hourly_consumption_profile or {},
                "backend_soc_trajectory": self.coordinator._backend_soc_trajectory,
                "backend_soc_trajectory_tomorrow": self.coordinator._backend_soc_trajectory_tomorrow,
                "inverter_max_power_kw": self.coordinator._inverter_max_power_kw,
                "sunrise_hour": getattr(self.coordinator, "sunrise_hour", None),
                "sunset_hour": getattr(self.coordinator, "sunset_hour", None),
            },
            "soc_history": self.coordinator._soc_history,
            "slot_overrides": self.coordinator.slot_overrides if self.coordinator.slot_overrides else {},
            "rule1_window_warning": self.coordinator.rule1_window_warning,
            "flex_load_schedule": flex_slot_map,
            "flex_load_schedule_tomorrow": flex_slot_map_tomorrow,
            "flex_load_states": dict(self.coordinator._flex_load_states),
            "flex_load_configs": self._build_flex_load_attr(),
            "ev_boost_active": self.coordinator.ev_boost_active,
            "ev_boost_remaining_min": self.coordinator.ev_boost_remaining_min,
            "ev_need": self._build_ev_need_attr(),
            "integration_version": self.coordinator.integration_version,
        }

    def _build_input_sources(self) -> dict:
        """Where each planning input comes from — so a guess is visible.

        ``pv_curve``: the forecast attribute the hourly PV came from, or
        "synthetic" (daily total spread over a bell curve) or "none".
        ``consumption_profile``: "history" / "partial" / "flat" / "none" (see
        coordinator.consumption_profile_source).
        """
        coord = self.coordinator
        milp = getattr(coord, "milp_status", None)
        return {
            "pv_curve": getattr(coord, "pv_curve_source", "none"),
            "pv_curve_tomorrow": getattr(coord, "pv_curve_source_tomorrow", "none"),
            "forecast_entity": getattr(coord, "forecast_entity", None),
            "sun_window": (
                [coord.sunrise_hour, coord.sunset_hour]
                if getattr(coord, "sunrise_hour", None) is not None else None
            ),
            "consumption_profile": getattr(coord, "consumption_profile_source", "none"),
            "consumption_profile_days": len(getattr(coord, "_hourly_consumption_history", []) or []),
            "consumption_entity": getattr(coord, "consumption_entity_used", None),
            "milp_solver": milp.get("solver") if isinstance(milp, dict) else None,
            # This week's typical price (None until 3 days are known) — what
            # the planner values energy kept beyond the night at.
            "price_reference": getattr(coord, "price_reference", None),
            "price_history_days": len(getattr(coord, "_daily_price_history", []) or []),
        }

    def _build_ev_need_attr(self) -> dict | None:
        """The EV's own need for the card: what is asked, what went in, and
        the plan (ems.ScheduleResult.ev_need).  None when no need is set."""
        coord = self.coordinator
        opts = coord.config_entry.options
        try:
            asked = float(opts.get("ev_need_kwh", 0) or 0)
        except (TypeError, ValueError):
            asked = 0.0
        if asked <= 0:
            return None
        plan = dict(getattr(coord, "ev_need", {}) or {})
        return {
            "asked_kwh": asked,
            "remaining_kwh": round(coord.ev_need_remaining_kwh, 2),
            "ready_by": opts.get("ev_ready_by", "07:00"),
            "hold_slots": sorted(getattr(coord, "_ev_hold_slots", set())),
            "hold_slots_tomorrow": sorted(getattr(coord, "_ev_hold_slots_tomorrow", set())),
            **plan,
        }

    def _build_flex_load_attr(self) -> list[dict]:
        """Build the per-load attribute list for the frontend card.

        Includes the *actual* power each load is drawing right now so the
        card can show "which load is active and with how much power":
          - binary loads → rated power when on, else 0
          - EV charger   → current step × voltage × phases (the real draw),
            falling back to the startup current when no step has been set
        """
        states = self.coordinator._flex_load_states
        ev_step = self.coordinator._flex_load_current_step
        configs = []
        for i, ld in enumerate(self.coordinator._build_flex_load_configs()):
            is_on = bool(states.get(i, False))
            entry = {
                "index": i,
                "name": ld.name,
                "power_kw": round(ld.rated_power_kw, 2),
                "priority": ld.priority,
                "is_ev": ld.is_ev_charger,
                "on": is_on,
            }
            if ld.is_ev_charger:
                active_a = (ev_step if (is_on and ev_step) else ld.default_current)
                max_a = max(ld.current_steps) if ld.current_steps else ld.default_current
                entry["current_a"] = active_a if is_on else None
                entry["phases"] = ld.phases
                entry["voltage"] = ld.voltage
                entry["max_power_kw"] = round(ld.power_at_current(max_a), 2)
                entry["active_power_kw"] = round(ld.power_at_current(active_a), 2) if is_on else 0.0
            else:
                entry["max_power_kw"] = round(ld.rated_power_kw, 2)
                entry["active_power_kw"] = round(ld.rated_power_kw, 2) if is_on else 0.0
            configs.append(entry)
        return configs


class HA_FelicityChargeLikelihoodSensor(CoordinatorEntity, SensorEntity):
    """Sensor showing likelihood of meeting battery charge target (always visible)."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_name = f"{entry.title} Charge Likelihood"
        self._attr_unique_id = f"{entry.entry_id}_charge_likelihood"
        self._attr_icon = "mdi:battery-check"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        return self.coordinator.charge_likelihood or "unknown"

    @property
    def extra_state_attributes(self):
        return {
            "available_slots": self.coordinator.available_slots_at_threshold,
            "available_energy_kwh": self.coordinator.available_energy_capacity,
            "weekly_avg_consumption_kwh": self.coordinator.weekly_avg_consumption,
            "yesterday_deficit_kwh": self.coordinator._yesterday_deficit,
            "price_threshold": self.coordinator.price_threshold,
        }


class HA_FelicityEnergyStateSensor(CoordinatorEntity, SensorEntity):
    """Sensor showing current energy management state."""
    
    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_name = f"{entry.title} Energy State"
        self._attr_unique_id = f"{entry.entry_id}_energy_state"
        self._attr_icon = "mdi:lightning-bolt"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
    
    @property
    def native_value(self):
        """Return the current state."""
        return self.coordinator._current_energy_state or "unknown"
    
    @property
    def extra_state_attributes(self):
        """Return additional state info."""
        return self.coordinator.get_energy_state_info()     
       
class HA_FelicitySensor(CoordinatorEntity, SensorEntity):
    """Representation of a Felicity sensor (raw register)."""

    def __init__(self, coordinator: HA_FelicityCoordinator, entry: ConfigEntry, key: str, info: dict):
        super().__init__(coordinator)
        self._key = key
        self._info = info

        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = f"{entry.title} {info['name']}"

        self._attr_native_unit_of_measurement = info.get("unit")
        self._attr_device_class = info.get("device_class")
        self._attr_state_class = info.get("state_class")

        # Read-only enum register (e.g. type "status" with an options list,
        # like working_mode @ 4353): render the option LABEL ("Line") instead
        # of the raw register value (5), as a proper HA enum sensor.
        self._enum_options: list[str] | None = None
        if info.get("options") and info.get("type") not in (
            "select", "select_multi",
        ):
            self._enum_options = list(info["options"])
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = self._enum_options
            self._attr_state_class = None  # enum sensors carry no state class

    async def async_added_to_hass(self) -> None:
        """Show the first poll's value at once (issue #220).

        The first refresh runs before the platforms are set up, and
        CoordinatorEntity does not replay it, so every sensor read "unknown"
        until the NEXT poll finished — ~15 s after a restart, longer on a slow
        link.  Templates reading it with `| float(0)` saw 0 in that window.
        """
        await super().async_added_to_hass()
        self._update_value()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_value()
        self.async_write_ha_state()

    def _update_value(self) -> None:
        value = (self.coordinator.data or {}).get(self._key)

        if value is None:
            self._attr_native_value = None
        elif self._enum_options is not None:
            try:
                self._attr_native_value = self._enum_options[int(value)]
            except (IndexError, ValueError, TypeError):
                # Unknown enum value — surface nothing rather than an invalid
                # state (ENUM sensors reject states outside their options).
                self._attr_native_value = None
        else:
            # Value is already scaled in the coordinator
            # Only apply precision rounding if it's a float
            precision = self._info.get("precision", 0)
            if isinstance(value, float):
                value = round(value, precision)
            self._attr_native_value = value

    @property
    def available(self) -> bool:
        """Return True if the entity is available."""
        return (
            self.coordinator.last_update_success
            and (self.coordinator.data or {}).get(self._key) is not None
        )
        
    @property
    def extra_state_attributes(self):
        """Add kWh attribute for Wh registers."""
        attrs = {}
        unit = self._info.get("unit")
        value = self.coordinator.data.get(self._key)
        if unit == "Wh" and value is not None:
            attrs["kWh"] = round(value / 1000.0, 3)
        return attrs

class HA_FelicityEnergySensor(HA_FelicitySensor, RestoreSensor):
    """An energy counter (``total_increasing``) that survives a restart.

    Until the inverter has answered for this register, it shows the value it
    had before the restart instead of "unknown" — a counter only grows, so the
    old value is a true lower bound, and long-term statistics see no gap and no
    dip.  The coordinator's plausibility guard (plausibility.py) keeps wrong
    readings away from it after that.
    """

    _restored_value: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._attr_native_value is not None:
            return
        last = await self.async_get_last_sensor_data()
        value = getattr(last, "native_value", None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            self._restored_value = value
            self._attr_native_value = value

    def _update_value(self) -> None:
        super()._update_value()
        if self._attr_native_value is None and self._restored_value is not None:
            self._attr_native_value = self._restored_value
        elif self._attr_native_value is not None:
            self._restored_value = None     # a real reading replaces it for good

    @property
    def available(self) -> bool:
        return super().available or (
            self.coordinator.last_update_success and self._restored_value is not None)


class HA_FelicityCombinedSensor(CoordinatorEntity, SensorEntity):
    """Representation of a combined/post-processed sensor."""

    def __init__(self, coordinator: HA_FelicityCoordinator, entry: ConfigEntry, key: str, info: dict):
        super().__init__(coordinator)
        self._key = key
        self._info = info
        self._sources = info["sources"]

        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = f"{entry.title} {info.get('name', key.replace('_', ' ').title())}"

        self._attr_native_unit_of_measurement = info.get("unit")
        self._attr_device_class = info.get("device_class")
        self._attr_state_class = info.get("state_class")

    async def async_added_to_hass(self) -> None:
        """Show the first poll's value at once (see HA_FelicitySensor)."""
        await super().async_added_to_hass()
        self._update_value()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_value()
        self.async_write_ha_state()

    def _update_value(self) -> None:
        values = [(self.coordinator.data or {}).get(src) for src in self._sources]

        if any(v is None for v in values):
            self._attr_native_value = None
            self._attr_extra_state_attributes = {}
        else:
            calc_func = self._info["calc"]
            try:
                result = calc_func(*values) if len(values) > 1 else calc_func(values[0])

                # Handle dict result (e.g., econ rules returning multiple attributes)
                if isinstance(result, dict):
                    # Main state: use "enabled" or similar summary if present
                    self._attr_native_value = result.get("enabled", "Active")
                    self._attr_extra_state_attributes = result
                else:
                    # Simple value (e.g., total energy)
                    self._attr_native_value = result
                    self._attr_extra_state_attributes = {}

                # Apply precision rounding (only for numeric states)
                precision = self._info.get("precision", 0)
                if isinstance(self._attr_native_value, (int, float)) and not isinstance(self._attr_native_value, bool):
                     self._attr_native_value = round(self._attr_native_value, precision)
            except Exception as e:
                _LOGGER.error("Error calculating combined sensor %s: %s", self._key, e)
                self._attr_native_value = None

    @property
    def available(self) -> bool:
        return all(self.coordinator.data.get(src) is not None for src in self._sources)

class HA_FelicityNordpoolSensor(CoordinatorEntity, SensorEntity):
    """Sensor for Nordpool price data from coordinator."""
    def __init__(self, coordinator, key, name, unit):
        super().__init__(coordinator)
        self._key = key
        self._attr_name = f"{coordinator.config_entry.title} {name}"
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = "monetary"
        self._attr_state_class = None
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        try:
            value = self.coordinator.data.get(self._key)
            if value is not None:
                return round(value, 3)  # or 4 – clean decimals
            return None
        except Exception:
            _LOGGER.debug("failed to get sensor value %s from coordinator", self._key)
            return None

    @property
    def extra_state_attributes(self):
        # Only expose source entity on current_price sensor (used by card as fallback)
        if self._key != "current_price":
            return None
        return {
            "price_source_entity": self.coordinator.nordpool_entity,
        }

class HA_FelicitySimpleSensor(CoordinatorEntity, SensorEntity):
    """Sensor for Nordpool price data from coordinator."""
    def __init__(self, coordinator, key: str, name: str, unit: str = "", icon: str | None = None):
        super().__init__(coordinator)
        self._key = key
        if unit:
            self._attr_native_unit_of_measurement = unit
        if icon:
            self._attr_icon = icon
        self._attr_name = f"{coordinator.config_entry.title} {name}"
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{key}"
        self._attr_state_class = None
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        try:
            value = self.coordinator.data.get(self._key)
            if value is not None:
                return value
            return None
        except Exception:
            _LOGGER.debug("failed to get sensor value %s from coordinator", self._key)
            return None 


