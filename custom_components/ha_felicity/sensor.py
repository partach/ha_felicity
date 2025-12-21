"""Sensor entities for the Felicity integration."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.time import TimeEntity
from homeassistant.components.date import DateEntity
from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, _COMBINED_REGISTERS
from .coordinator import HA_FelicityCoordinator

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up Felicity entities based on the coordinator data."""
    coordinator: HA_FelicityCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []

    # Common device_info for all entities from this entry
    device_info = DeviceInfo{
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": entry.title or "Felicity Inverter",
        "manufacturer": "Felicity Solar",
        "model": entry.data.get(CONF_INVERTER_MODEL, "T-REX-10KLP3G01"),
        "configuration_url": f"homeassistant://config/integrations/integration/{entry.entry_id}",
    }
    # 1. Select entities for writable enums
    entities.extend([
        HA_FelicitySelect(coordinator, entry, key, info)
        for key, info in coordinator.register_map.items()
        if info.get("type") == "select"
    ])

    # 2. Time entities
    entities.extend([
        HA_FelicityTime(coordinator, entry, key, info)
        for key, info in coordinator.register_map.items()
        if info.get("type") == "time"
    ])
    
    # 3. Date entities
    entities.extend([
        HA_FelicityDate(coordinator, entry, key, info)
        for key, info in coordinator.register_map.items()
        if info.get("type") == "date"
    ])
    
    # 4. Multi-Select entities (custom bitmask handling)
    entities.extend([
        HA_FelicitySelectMulti(coordinator, entry, key, info)
        for key, info in coordinator.register_map.items()
        if info.get("type") == "select_multi"
    ])
    
    # 5. Number entities (sliders/boxes)
    entities.extend([
        HA_FelicityNumber(coordinator, entry, key, info)
        for key, info in coordinator.register_map.items()
        if info.get("type") == "number"
    ])

    # 6. Regular sensors from selected registers 
    # Filter out keys that were already added as specific entity types above
    special_types = {"select", "time", "date", "select_multi", "number"}
    entities.extend([
        HA_FelicitySensor(coordinator, entry, key, info)
        for key, info in coordinator.register_map.items()
        if info.get("type") not in special_types
        and info.get("index", 0) != 99
    ])

    # 7. Combined sensors (Computed values like Total PV Power)
    # Use coordinator property if available, else fallback to const defaults
    model_combined = getattr(coordinator, "model_combined", _COMBINED_REGISTERS)
    entities.extend([
        HA_FelicityCombinedSensor(coordinator, entry, key, info)
        for key, info in model_combined.items()
    ])
    for entity in entities:
        entity._attr_device_info = device_info
    async_add_entities(entities)


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

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        value = self.coordinator.data.get(self._key)

        if value is None:
            self._attr_native_value = None
        else:
            # Value is already scaled in the coordinator
            # Only apply precision rounding if it's a float
            precision = self._info.get("precision", 0)
            if isinstance(value, float):
                value = round(value, precision)
            self._attr_native_value = value

        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return True if the entity is available."""
        return (
            self.coordinator.last_update_success
            and self.coordinator.data.get(self._key) is not None
        )


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

    @callback
    def _handle_coordinator_update(self) -> None:
        values = [self.coordinator.data.get(src) for src in self._sources]

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
                
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return all(self.coordinator.data.get(src) is not None for src in self._sources)


class HA_FelicitySelect(CoordinatorEntity, SelectEntity):
    """Representation of a writable select (enum) register."""

    def __init__(self, coordinator: HA_FelicityCoordinator, entry: ConfigEntry, key: str, info: dict):
        super().__init__(coordinator)
        self._key = key
        self._info = info
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = f"{entry.title} {info['name']}"
        self._attr_options = info["options"]  # Required: list of strings

    @property
    def current_option(self) -> str | None:
        value = self.coordinator.data.get(self._key)
        if value is None:
            return None
        try:
            return self._attr_options[value]
        except IndexError:
            return None

    async def async_select_option(self, option: str) -> None:
        if option not in self._attr_options:
            return
        value = self._attr_options.index(option)
        success = await self.coordinator.async_write_register(self._key, value)
        if success:
            # Optimistic update
            self.coordinator.data[self._key] = value
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()


class HA_FelicityTime(CoordinatorEntity, TimeEntity):
    """Representation of a writable time register."""

    def __init__(self, coordinator, entry, key, info):
        super().__init__(coordinator)
        self._key = key
        self._info = info
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = f"{entry.title} {info['name']}"

    @property
    def native_value(self):
        raw = self.coordinator.data.get(self._key)
        if raw is None:
            return None
        hours = raw >> 8
        minutes = raw & 0xFF
        from datetime import time
        return time(hour=hours, minute=minutes)

    async def async_set_value(self, value) -> None:
        # value is datetime.time
        packed = (value.hour << 8) | value.minute
        await self.coordinator.async_write_register(self._key, packed)
        await self.coordinator.async_request_refresh()


class HA_FelicityDate(CoordinatorEntity, DateEntity):
    """Representation of a writable date (month/day) register."""

    def __init__(self, coordinator, entry, key, info):
        super().__init__(coordinator)
        self._key = key
        self._info = info
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = f"{entry.title} {info['name']}"

    @property
    def native_value(self):
        raw = self.coordinator.data.get(self._key)
        if raw is None:
            return None
        month = raw >> 8
        day = raw & 0xFF
        from datetime import date
        # Year is arbitrary as the register only holds month/day
        return date(year=2025, month=month, day=day)

    async def async_set_value(self, value) -> None:
        # value is datetime.date
        packed = (value.month << 8) | value.day
        await self.coordinator.async_write_register(self._key, packed)
        await self.coordinator.async_request_refresh()


class HA_FelicitySelectMulti(CoordinatorEntity, SelectEntity):
    """
    Representation of a multi-select bitmask register.
    
    Note: SelectEntity is typically single-selection in HA. 
    This implementation mimics toggling logic but UI support 
    for multi-select via SelectEntity is limited.
    """
    def __init__(self, coordinator, entry, key, info):
        super().__init__(coordinator)
        self._key = key
        self._info = info
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = f"{entry.title} {info['name']}"
        self._attr_options = info["options"]

    @property
    def current_option(self):
        """Return None because multiple options might be active."""
        return None 

    @property
    def state(self):
        """Return a string representation of selected options for display."""
        days = self.current_options_list
        if not days:
            return "None"
        return ", ".join(days)

    @property
    def current_options_list(self):
        """Helper to get list of active options."""
        raw = self.coordinator.data.get(self._key, 0)
        return [day for i, day in enumerate(self._attr_options) if raw & (1 << i)]

    async def async_select_option(self, option: str) -> None:
        """Toggle the selected option in the bitmask."""
        days = self.current_options_list
        if option in days:
            days.remove(option)
        else:
            days.append(option)
        
        value = sum(1 << i for i, day in enumerate(self._attr_options) if day in days)
        await self.coordinator.async_write_register(self._key, value)
        await self.coordinator.async_request_refresh()


class HA_FelicityNumber(CoordinatorEntity, NumberEntity):
    """Representation of a writable number register."""

    def __init__(self, coordinator, entry, key, info):
        super().__init__(coordinator)
        self._key = key
        self._info = info
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = f"{entry.title} {info['name']}"
        self._attr_native_unit_of_measurement = info.get("unit")
        self._attr_device_class = info.get("device_class")
        self._attr_native_min_value = info.get("min", 0)
        self._attr_native_max_value = info.get("max", 100)
        self._attr_native_step = info.get("step", 1)

    @property
    def native_value(self):
        return self.coordinator.data.get(self._key)

    async def async_set_native_value(self, value: float) -> None:
        index = self._info.get("index", 0)
        if index == 1:
            packed = int(value * 10)
        else:
            packed = int(value)
        await self.coordinator.async_write_register(self._key, packed)
        await self.coordinator.async_request_refresh()
