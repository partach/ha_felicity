"""Text entities for the Felicity integration — entity ID configuration."""
from __future__ import annotations

import logging

from homeassistant.components.text import TextEntity
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

    entities = []

    # Flexible load text inputs. Entity assignments (switch / current entity)
    # are configured via the integration's options flow (gear icon), like the
    # price and solar-forecast entities — not here. These remaining text
    # fields are gated on a switch entity being assigned to the load.
    load_defs = [
        (1, "EV Charger / Load 1", [
            ("name", "Name", "mdi:label-outline", "flexible_load_1_switch_entity"),
            ("current_steps", "Current Steps (e.g. 6,10,13,16,20,25)",
             "mdi:format-list-numbered", "flexible_load_1_current_entity"),
        ]),
        (2, "Flexible Load 2", [
            ("name", "Name", "mdi:label-outline", "flexible_load_2_switch_entity"),
        ]),
        (3, "Flexible Load 3", [
            ("name", "Name", "mdi:label-outline", "flexible_load_3_switch_entity"),
        ]),
    ]

    for load_num, load_label, fields in load_defs:
        for field_key, field_name, icon, requires_option in fields:
            entities.append(
                HA_FelicityConfigText(
                    coordinator=coordinator,
                    entry=entry,
                    option_key=f"flexible_load_{load_num}_{field_key}",
                    name=f"{load_label} {field_name}",
                    icon=icon,
                    requires_option=requires_option,
                )
            )

    for entity in entities:
        entity._attr_device_info = device_info

    async_add_entities(entities)


class HA_FelicityConfigText(CoordinatorEntity, TextEntity):
    """Text entity for configuring entity IDs and string settings."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: HA_FelicityCoordinator,
        entry: ConfigEntry,
        option_key: str,
        name: str,
        icon: str | None = None,
        requires_option: str | None = None,
    ):
        super().__init__(coordinator)
        self._entry = entry
        self._option_key = option_key
        self._requires_option = requires_option
        self._attr_unique_id = f"{entry.entry_id}_{option_key}"
        self._attr_name = f"{entry.title} {name}"
        self._attr_native_max = 255
        if icon:
            self._attr_icon = icon

    @property
    def available(self) -> bool:
        """Disabled until the load's prerequisite entity is assigned."""
        if self._requires_option and not self._entry.options.get(self._requires_option):
            return False
        return super().available

    @property
    def native_value(self) -> str | None:
        return self._entry.options.get(self._option_key, "")

    async def async_set_value(self, value: str) -> None:
        value = value.strip()
        _LOGGER.info("Setting %s to '%s'", self._option_key, value)
        updated_options = dict(self._entry.options)
        updated_options[self._option_key] = value
        self.hass.config_entries.async_update_entry(
            self._entry, options=updated_options,
        )
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()
