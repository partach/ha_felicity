"""Select entities for the Felicity integration."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
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
    """Set up Felicity select entities based on the coordinator data."""
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

    # Register-based select entities (from coordinator register_map)
    entities.extend([
        HA_FelicitySelect(coordinator, entry, key, info)
        for key, info in coordinator.register_map.items()
        if info.get("type") == "select"
    ])

    # Multi-Select entities (custom bitmask handling)
    entities.extend([
        HA_FelicitySelectMulti(coordinator, entry, key, info)
        for key, info in coordinator.register_map.items()
        if info.get("type") == "select_multi"
    ])

    # Internal configuration select entities (stored in entry.options)
    entities.append(
        HA_FelicitySpecialModeSelect(
            coordinator=coordinator,
            entry=entry,
            option_key="grid_mode",
            select_options=["off", "from_grid", "to_grid"],
            name="Grid Mode",
            icon="mdi:transmission-tower",
            entity_category=EntityCategory.CONFIG,
        )
    )

    entities.append(
        HA_FelicitySpecialModeSelect(
            coordinator=coordinator,
            entry=entry,
            option_key="price_mode",
            select_options=["manual", "auto"],
            name="Price Mode",
            icon="mdi:chart-timeline-variant-shimmer",
            entity_category=EntityCategory.CONFIG,
        )
    )

    # Tie all entities to the device
    for entity in entities:
        entity._attr_device_info = device_info

    async_add_entities(entities)


class HA_FelicitySelect(CoordinatorEntity, SelectEntity):
    """Representation of a writable select (enum) register."""

    def __init__(
        self, 
        coordinator: HA_FelicityCoordinator, 
        entry: ConfigEntry, 
        key: str, 
        info: dict
    ):
        super().__init__(coordinator)
        self._key = key
        self._info = info
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = f"{entry.title} {info['name']}"
        self._attr_options = info["options"]  # Required: list of strings

    @property
    def current_option(self) -> str | None:
        """Return the current selected option."""
        value = self.coordinator.data.get(self._key)
        if value is None:
            return None
        try:
            return self._attr_options[value]
        except IndexError:
            return None

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        if option not in self._attr_options:
            return
        value = self._attr_options.index(option)
        success = await self.coordinator.TypeSpecificHandler.write_type_specific_register(self._key, value)
        if success:
            # Optimistic update
            self.coordinator.data[self._key] = value
            self.async_write_ha_state()
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
        await self.coordinator.TypeSpecificHandler.write_type_specific_register(self._key, value)
        await self.coordinator.async_request_refresh()


class HA_FelicitySpecialModeSelect(CoordinatorEntity, SelectEntity):
    """Live selector for Special self made selector"""

    _attr_icon = "mdi:transmission-tower"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator,
        entry: ConfigEntry,
        option_key: str,
        select_options: list[str],
        name: str,
        icon: str | None = None,
        entity_category: EntityCategory | None = EntityCategory.CONFIG,
    ):
        super().__init__(coordinator)
        self._entry = entry
        self._option_key = option_key
        self._select_options = select_options
        self._attr_unique_id = f"{entry.entry_id}_{option_key}"
        self._attr_name = f"{entry.title} {name}"
        self._attr_options = select_options
        if icon:
            self._attr_icon = icon
        if entity_category:
            self._attr_entity_category = entity_category        

    @property
    def current_option(self) -> str:
        """Return the current selected option from persisted options."""
        return self._entry.options.get(self._option_key, self._select_options[0])

    async def async_select_option(self, option: str) -> None:
        """Change the selected option and persist it."""
        if option not in self._select_options:
            _LOGGER.warning("Invalid option '%s' for %s", option, self._option_key)
            return

        _LOGGER.info("%s set to %s via selector", self._option_key, option)

        # Update persisted options
        updated_options = dict(self._entry.options)
        updated_options[self._option_key] = option

        self.hass.config_entries.async_update_entry(
            self._entry,
            options=updated_options,
        )

        # Force immediate refresh so economic logic sees the change
        await self.coordinator.async_request_refresh()

        # Update UI
        self.async_write_ha_state()
