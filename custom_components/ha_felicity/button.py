"""Button entities for the Felicity integration."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo, EntityCategory

from .const import DOMAIN, CONF_INVERTER_MODEL, DEFAULT_INVERTER_MODEL
from .coordinator import HA_FelicityCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
):
    coordinator: HA_FelicityCoordinator = hass.data[DOMAIN][entry.entry_id]

    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title or "Felicity Inverter",
        manufacturer="Felicity Solar",
        model=entry.data.get(CONF_INVERTER_MODEL, DEFAULT_INVERTER_MODEL),
    )

    entities = [
        HA_FelicityEVBoostButton(coordinator, entry),
        HA_FelicityEVBoostCancelButton(coordinator, entry),
    ]

    for entity in entities:
        entity._attr_device_info = device_info

    async_add_entities(entities)


class HA_FelicityEVBoostButton(CoordinatorEntity, ButtonEntity):
    """Button that adds +1 hour to the EV Boost override timer."""

    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator: HA_FelicityCoordinator, entry: ConfigEntry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_ev_boost"
        self._attr_name = f"{entry.title} EV Boost +1h"

    async def async_press(self) -> None:
        self.coordinator.ev_boost_add_hour()
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


class HA_FelicityEVBoostCancelButton(CoordinatorEntity, ButtonEntity):
    """Button that cancels the EV Boost override."""

    _attr_icon = "mdi:ev-station-off"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: HA_FelicityCoordinator, entry: ConfigEntry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_ev_boost_cancel"
        self._attr_name = f"{entry.title} EV Boost Cancel"

    async def async_press(self) -> None:
        self.coordinator.ev_boost_cancel()
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
