"""Sensor entities for the Felicity integration."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.components.time import TimeEntity
from homeassistant.components.date import DateEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo, EntityCategory

from .const import DOMAIN, CONF_INVERTER_MODEL, DEFAULT_INVERTER_MODEL 
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

    # 2. Time entities
    entities.extend([
        HA_FelicityTime(coordinator, entry, key, info)
        for key, info in coordinator.register_map.items()
        if info.get("type") == "time8bit"
    ])
    
    # 3. Date entities
    entities.extend([
        HA_FelicityDate(coordinator, entry, key, info)
        for key, info in coordinator.register_map.items()
        if info.get("type") == "date8bit"
    ])
    

    # 6. Regular sensors from selected registers 
    # Filter out keys that were already added as specific entity types above
    special_types = {"select", "time8bit", "date8bit", "select_multi", "number"}
    entities.extend([
        HA_FelicitySensor(coordinator, entry, key, info)
        for key, info in coordinator.register_map.items()
        if info.get("type") not in special_types
        and info.get("index", 0) != 99
    ])

    # 7. Combined sensors (Computed values like Total PV Power)
    # Use coordinator property if available, else fallback to const defaults
    model_combined = coordinator.model_combined  # ← set in coordinator!
    if model_combined:
        entities.extend(
            HA_FelicityCombinedSensor(coordinator, entry, key, info)
            for key, info in model_combined.items()
        )
    else:
        _LOGGER.warning("No model-specific combined registers found for %s", entry.title)

    nordpool_sensors = []
    if coordinator.nordpool_entity:
        nordpool_sensors = [
            HA_FelicityNordpoolSensor(coordinator, "current_price", "Current Price", "€/kWh"),
            HA_FelicityNordpoolSensor(coordinator, "min_price", "Today Min Price", "€/kWh"),
            HA_FelicityNordpoolSensor(coordinator, "max_price", "Today Max Price", "€/kWh"),
            HA_FelicityNordpoolSensor(coordinator, "avg_price", "Today Avg Price", "€/kWh"),
            HA_FelicityNordpoolSensor(coordinator, "price_threshold", "Price Threshold", "€/kWh"),
        ]    
    entities.extend(nordpool_sensors)
    simple_sensors = [
        HA_FelicitySimpleSensor(coordinator, "safe_max_power", "Safe Max. Power", "W"),
        HA_FelicitySimpleSensor(coordinator,"operational_mode","Operational Mode")
    ]
    entities.extend(simple_sensors)
    entities.append(
        HA_FelicityEnergyStateSensor(coordinator, entry)
    )
    # let's make sure we tie all the sensors to the device:
    for entity in entities:
        entity._attr_device_info = device_info
    async_add_entities(entities)

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
        
    @property
    def extra_state_attributes(self):
        """Add kWh attribute for Wh registers."""
        attrs = {}
        unit = self._info.get("unit")
        value = self.coordinator.data.get(self._key)
        if unit == "Wh" and value is not None:
            attrs["kWh"] = round(value / 1000.0, 3)
        return attrs

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

class HA_FelicityTime(CoordinatorEntity, TimeEntity):
    """Representation of a writable time register."""

    def __init__(self, coordinator, entry, key, info):
        super().__init__(coordinator)
        self._key = key
        self._info = info
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = f"{entry.title} {info['name']}"
        self._time_error = False 

    @property
    def native_value(self):
        raw = self.coordinator.data.get(self._key)
        if raw is None:
            return None
        # currently only time8bit exist so we assume that setup
        hours = raw >> 8
        minutes = raw & 0xFF
        theTime = raw
        from datetime import time
        try:
           theTime = time(hour=hours, minute=minutes)
           self._time_error = False
        except Exception as err:
           self._time_error = True
           _LOGGER.debug("Failed to interpret time of register with error:%s, returning raw value: %s", err, theTime)  
        return theTime

    async def async_set_value(self, value) -> None:
        # value is datetime.time
        try:
            if not self._time_error: # only write if the value makes sense
                #   we let the write_type_specific_register take care of special treatment? Not yet as we only have on type atm
               packed = (value.hour << 8) | value.minute
               await self.coordinator.TypeSpecificHandler.write_type_specific_register(self._key, packed)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to set time for %s: %s", self._key, err)        

class HA_FelicityDate(CoordinatorEntity, DateEntity):
    """Representation of a writable date (month/day) register."""

    def __init__(self, coordinator, entry, key, info):
        super().__init__(coordinator)
        self._key = key
        self._info = info
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = f"{entry.title} {info['name']}"
        self._date_error = False 

    @property
    def native_value(self):
        raw = self.coordinator.data.get(self._key)
        if raw is None:
            return None
        # currently only date8bit exist so we assume that setup
        month = raw >> 8
        day = raw & 0xFF
        theDate = raw
        from datetime import date
        try:
           current_year = date.today().year 
           theDate = date(year=current_year, month=month, day=day)
           self._date_error = False
        except Exception as err:
           self._date_error = True
           _LOGGER.debug("Failed to interpret date of register with error:%s, returning raw value: %s", err, theDate)  
        return theDate

    async def async_set_value(self, value) -> None:
        try:
            # value is datetime.date
            if not self._date_error:
                #   we let the write_type_specific_register take care of special treatment? Not yet as we only have on type atm
                packed = (value.month << 8) | value.day
                await self.coordinator.TypeSpecificHandler.write_type_specific_register(self._key, packed)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to set date for %s: %s", self._key, err)        


