"""Sensor entities for the Felicity integration."""
from __future__ import annotations

import logging

from homeassistant.components.time import TimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo

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

    entities.extend([
        HA_FelicityTime(coordinator, entry, key, info)
        for key, info in coordinator.register_map.items()
        if info.get("type") == "time8bit"
    ])
    
    # let's make sure we tie all the sensors to the device:
    for entity in entities:
        entity._attr_device_info = device_info
    async_add_entities(entities)

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
