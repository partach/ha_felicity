# sensor.py
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.components.select import SelectEntity

from .const import DOMAIN, _REGISTERS, _REGISTER_GROUPS, _COMBINED_REGISTERS
from .coordinator import HA_FelicityCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    coordinator: HA_FelicityCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []

    # 1. Regular sensors – from selected registers (model + set filtered)
    entities.extend([
        HA_FelicitySensor(coordinator, entry, key, info)
        for key, info in coordinator.register_map.items()  # ← This is correct
    ])

    # 2. Select entities – for writable enums
    entities.extend([
        HA_FelicitySelect(coordinator, entry, key, info)
        for key, info in coordinator.register_map.items()
        if info.get("type") == "select"
    ])

    # 3. Combined sensors – from model's combined (not filtered – always show)
    model_combined = getattr(coordinator, "model_combined", _COMBINED_REGISTERS)  # fallback
    entities.extend([
        HA_FelicityCombinedSensor(coordinator, entry, key, info)
        for key, info in model_combined.items()
    ])

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
        raw_value = self.coordinator.data.get(self._key)
        if raw_value is None:
            self._attr_native_value = None
        else:
            # Apply scaling based on index
            scaled = self._scale_value(raw_value, self._info.get("index", 0))
            # Apply precision rounding
            precision = self._info.get("precision", 0)
            if isinstance(scaled, float):
                scaled = round(scaled, precision)
            self._attr_native_value = scaled
        self.async_write_ha_state()

    @staticmethod
    def _scale_value(value: int | float, index: int) -> int | float:
        """Apply scaling based on index."""
        if index == 1:  # /10
            return value / 10.0
        if index == 2:  # /100
            return value / 100.0
        if index == 3:  # signed 16-bit (or 32-bit handled elsewhere)
            if value >= 0x8000:
                return value - 0x10000
            return value
        if index == 4:  # high/low word – handled in combined
            return value
        if index == 7:  # percentage – raw
            return value
        return value  # 0, 5, 6 – raw

    @property
    def available(self) -> bool:
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
            result = calc_func(*values) if len(values) > 1 else calc_func(values[0])

            # Handle dict result (e.g., econ rules)
            if isinstance(result, dict):
                # Main state: use "enabled" if present, else fallback
                self._attr_native_value = result.get("enabled", "Active")
                self._attr_extra_state_attributes = result
            else:
                # Simple value (e.g., total energy)
                self._attr_native_value = result
                self._attr_extra_state_attributes = {}

            # Apply precision rounding (only for numbers)
            precision = self._info.get("precision", 0)
            if isinstance(self._attr_native_value, (int, float)):
                self._attr_native_value = round(self._attr_native_value, precision)
                
class HA_FelicitySelect(CoordinatorEntity, SelectEntity):
    """Representation of a writable select (enum) register."""

    def __init__(self, coordinator: HA_FelicityCoordinator, entry: ConfigEntry, key: str, info: dict):
        super().__init__(coordinator)
        self._key = key
        self._info = info

        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = f"{entry.title} {info['name']}"

        # Get options from const.py
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
        value = self._attr_options.index(option)
        success = await self.coordinator.async_write_register(self._key, value)
        if success:
            # Optimistic update
            self.coordinator.data[self._key] = value
            self.async_write_ha_state()
            # Trigger refresh in case other registers changed
            await self.coordinator.async_request_refresh()


        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return all(self.coordinator.data.get(src) is not None for src in self._sources)
