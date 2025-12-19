from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN
from .coordinator import HA_FelicityCoordinator

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    coordinator: HA_FelicityCoordinator = hass.data[DOMAIN][entry.entry_id]

    # The coordinator already knows which registers to create
    entities = [
        HA_FelicitySensor(coordinator, entry, key, info)
        for key, info in coordinator.register_map.items()
    ]

    async_add_entities(entities)


class HA_FelicitySensor(CoordinatorEntity, SensorEntity):  # ← Inherit from CoordinatorEntity
    """Representation of an Felicity sensor."""

    def __init__(self, coordinator: HA_FelicityCoordinator, entry: ConfigEntry, key: str, info: dict):
        """Initialize the sensor."""
        super().__init__(coordinator)  # This handles update listening
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = f"{entry.title} {info['name']}"
        self._attr_native_unit_of_measurement = info.get("unit")
        self._attr_device_class = info.get("device_class")
        self._attr_state_class = info.get("state_class")

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self.coordinator.data.get(self._key)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success and self.coordinator.data.get(self._key) is not None
