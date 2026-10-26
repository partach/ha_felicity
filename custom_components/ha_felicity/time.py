"""Sensor entities for the Felicity integration."""
from __future__ import annotations

import logging

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
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

    entities.extend([
        HA_FelicityTime(coordinator, entry, key, info)
        for key, info in coordinator.register_map.items()
        if info.get("type") == "time8bit"
    ])
    entities.append(HA_FelicityOptionTime(
        coordinator, entry, "ev_ready_by", "EV Ready By", "07:00",
        requires_option="flexible_load_1_current_entity"))
    
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


class HA_FelicityOptionTime(CoordinatorEntity, TimeEntity):
    """A time stored in the integration's options (not an inverter register),
    e.g. when the EV must be ready.  Stored as "HH:MM"."""

    def __init__(self, coordinator, entry, option_key: str, name: str, default: str,
                 requires_option: str | None = None):
        super().__init__(coordinator)
        self._entry = entry
        self._option_key = option_key
        self._default = default
        self._requires_option = requires_option
        self._attr_unique_id = f"{entry.entry_id}_{option_key}"
        self._attr_name = f"{entry.title} {name}"
        self._attr_icon = "mdi:clock-end"

    @property
    def available(self) -> bool:
        if self._requires_option and not self._entry.options.get(self._requires_option):
            return False
        return super().available

    @property
    def native_value(self):
        from datetime import time
        raw = str(self._entry.options.get(self._option_key) or self._default)
        try:
            hh, mm = (int(x) for x in raw.split(":")[:2])
            return time(hour=hh, minute=mm)
        except ValueError:
            return None

    async def async_set_value(self, value) -> None:
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, self._option_key: f"{value.hour:02d}:{value.minute:02d}"})
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()
