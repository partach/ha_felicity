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

    entities.extend([
        HA_FelicityDate(coordinator, entry, key, info)
        for key, info in coordinator.register_map.items()
        if info.get("type") == "date8bit"
    ])
    # let's make sure we tie all the sensors to the device:
    for entity in entities:
        entity._attr_device_info = device_info
    async_add_entities(entities)

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
