"""Number entities for the Felicity integration."""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo, EntityCategory

from .const import DOMAIN, CONF_INVERTER_MODEL
from .coordinator import HA_FelicityCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
):
    """Set up Felicity number entities based on the coordinator data."""
    coordinator: HA_FelicityCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []

    # Common device_info for all entities from this entry
    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title or "Felicity Inverter",
        manufacturer="Felicity Solar",
        model=entry.data.get(CONF_INVERTER_MODEL, "T-REX-10KLP3G01"),
        configuration_url=f"homeassistant://config/integrations/integration/{entry.entry_id}",
    )

    # Register-based number entities (from coordinator register_map)
    entities.extend([
        HA_FelicityNumber(coordinator, entry, key, info)
        for key, info in coordinator.register_map.items()
        if info.get("type") == "number"
    ])

    # Internal configuration number entities (stored in entry.options)
    entities.extend([
        HA_FelicityInternalNumber(
            coordinator,
            entry,
            option_key="price_threshold_level",
            name="Price Threshold Level",
            min_val=1,
            max_val=10,
            step=1,
            icon="mdi:currency-eur"
        ),
        HA_FelicityInternalNumber(
            coordinator,
            entry,
            option_key="power_level",
            name="Power Level",
            min_val=1,
            max_val=10,
            step=0.5,
            icon="mdi:battery-plus-variant"
        ),
        HA_FelicityInternalNumber(
            coordinator,
            entry,
            option_key="voltage_level",
            name="Voltage Level",
            min_val=50,
            max_val=60,
            step=1,
            icon="mdi:gauge"
        ),
        HA_FelicityInternalNumber(
            coordinator,
            entry,
            option_key="battery_charge_max_level",
            name="Battery Charge Max Level",
            min_val=30,
            max_val=100,
            step=1,
            unit="%",
            icon="mdi:battery-charging-100",
            device_class="battery"
        ),
        HA_FelicityInternalNumber(
            coordinator,
            entry,
            option_key="battery_discharge_min_level",
            name="Battery Discharge Min Level",
            min_val=10,
            max_val=70,
            step=1,
            unit="%",
            icon="mdi:battery-charging-20",
            device_class="battery"
        ),
    ])

    # Tie all entities to the device
    for entity in entities:
        entity._attr_device_info = device_info

    async_add_entities(entities)


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
        self._attr_mode = NumberMode.SLIDER

    @property
    def native_value(self):
        return self.coordinator.data.get(self._key)

    async def async_set_native_value(self, value: float) -> None:
        """Write the new value to the inverter register."""
        index = self._info.get("index", 0)
        if index == 0:
            packed = int(value)
        elif index == 1:
            packed = int(value * 10)
        elif index == 2:
            packed = int(value * 100)
        else:
            return # we do not know what to write and dont want to kill the device with weird register values
        await self.coordinator.async_write_register(self._key, packed)
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()

class HA_FelicityInternalNumber(CoordinatorEntity, NumberEntity):
    """Generic internal number entity for user-configurable options (sliders)."""

    def __init__(
        self,
        coordinator,
        entry: ConfigEntry,
        option_key: str,
        name: str,
        min_val: float,
        max_val: float,
        step: float = 1,
        unit: str | None = None,
        icon: str | None = None,
        device_class: str | None = None,
    ):
        super().__init__(coordinator)
        self._entry = entry
        self._option_key = option_key

        self._attr_name = f"{entry.title} {name}"
        self._attr_unique_id = f"{entry.entry_id}_{option_key}"
        self._attr_native_min_value = min_val
        self._attr_native_max_value = max_val
        self._attr_native_step = step
        if unit:
            self._attr_native_unit_of_measurement = unit
        self._attr_mode = NumberMode.SLIDER
        self._attr_entity_category = EntityCategory.CONFIG

        if icon:
            self._attr_icon = icon
        if device_class:
            self._attr_device_class = device_class

    @property
    def native_value(self) -> float | None:
        """Return current value from persisted options."""
        return self._entry.options.get(self._option_key)

    async def async_set_native_value(self, value: float) -> None:
        """Update the option in config_entry and trigger refresh."""
        # Clamp and round to step
        value = max(self.native_min_value, min(self.native_max_value, value))
        if self.native_step >= 1:
            value = round(value) if self.native_step == 1 else round(value / self.native_step) * self.native_step
        else:
            value = round(value, 2)  # reasonable for sub-1 steps

        _LOGGER.info("Setting %s to %.3f", self._attr_name, value)

        # Update the actual persisted options
        updated_options = dict(self._entry.options)
        updated_options[self._option_key] = value

        self.hass.config_entries.async_update_entry(
            self._entry,
            options=updated_options,
        )

        # Force immediate coordinator refresh so logic uses new value
        await self.coordinator.async_request_refresh()

        # Update entity state in UI
        self.async_write_ha_state()

    def _update_range_from_system(self):
        """Dynamically adjust min/max based on battery voltage system."""
        if not self.coordinator.data:
            return
        battery_voltage = self.coordinator.data.get("battery_nominal_voltage") 

        if battery_voltage is None:
            return

        # Example logic: 48V system vs 400V high-voltage
        if battery_voltage >= 400:  # High-voltage system (e.g., Felicity HV packs)
            new_min = 416
            new_max = 448
        else:  # Low-voltage (48V typical)
            new_min = 48
            new_max = 60

        # Only update if changed (avoids unnecessary state writes)
        if (self.native_min_value != new_min or self.native_max_value != new_max):
            self._attr_native_min_value = new_min
            self._attr_native_max_value = new_max
            # Trigger state update so UI reflects new range
            self.async_write_ha_state()
