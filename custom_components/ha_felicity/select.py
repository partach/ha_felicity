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

    # Strategy preset — the primary user-facing control
    entities.append(
        HA_FelicityStrategySelect(
            coordinator=coordinator,
            entry=entry,
        )
    )

    # Internal configuration select entities (stored in entry.options)
    entities.append(
        HA_FelicitySpecialModeSelect(
            coordinator=coordinator,
            entry=entry,
            option_key="grid_mode",
            select_options=["off", "from_grid", "to_grid", "both"],
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

    entities.append(
        HA_FelicitySpecialModeSelect(
            coordinator=coordinator,
            entry=entry,
            option_key="safe_power_management",
            select_options=["auto", "on", "off"],
            name="Safe Power Management",
            icon="mdi:shield-lightning-outline",
            entity_category=EntityCategory.CONFIG,
        )
    )

    entities.append(
        HA_FelicitySpecialModeSelect(
            coordinator=coordinator,
            entry=entry,
            option_key="optimization_priority",
            select_options=["cost", "longevity", "self_consumption"],
            name="Optimization Priority",
            icon="mdi:scale-balance",
            entity_category=EntityCategory.CONFIG,
        )
    )

    entities.append(
        HA_FelicitySpecialModeSelect(
            coordinator=coordinator,
            entry=entry,
            option_key="block_export_on_negative_price",
            select_options=["on", "off"],
            name="Block Export On Negative Price",
            icon="mdi:transmission-tower-export",
            entity_category=EntityCategory.CONFIG,
        )
    )

    entities.append(
        HA_FelicitySpecialModeSelect(
            coordinator=coordinator,
            entry=entry,
            option_key="scheduler_engine",
            select_options=["greedy", "milp"],
            name="Scheduler Engine",
            icon="mdi:function-variant",
            entity_category=EntityCategory.CONFIG,
        )
    )

    entities.append(
        HA_FelicitySpecialModeSelect(
            coordinator=coordinator,
            entry=entry,
            option_key="charge_to_full_on_negative_price",
            select_options=["off", "on"],
            name="Charge to Full on Negative Price",
            icon="mdi:battery-arrow-up",
            entity_category=EntityCategory.CONFIG,
        )
    )

    entities.append(
        HA_FelicitySpecialModeSelect(
            coordinator=coordinator,
            entry=entry,
            option_key="discharge_to_make_room_for_negative_price",
            select_options=["off", "on"],
            name="Discharge to Make Room for Negative Price",
            icon="mdi:battery-arrow-down",
            entity_category=EntityCategory.CONFIG,
        )
    )

    entities.append(
        HA_FelicitySpecialModeSelect(
            coordinator=coordinator,
            entry=entry,
            option_key="rule1_time_window",
            select_options=["manual", "auto"],
            name="Rule 1 Time Window",
            icon="mdi:clock-outline",
            entity_category=EntityCategory.CONFIG,
        )
    )

    entities.append(
        HA_FelicitySpecialModeSelect(
            coordinator=coordinator,
            entry=entry,
            option_key="rule1_weekday",
            select_options=["manual", "auto"],
            name="Rule 1 Weekday",
            icon="mdi:calendar-week",
            entity_category=EntityCategory.CONFIG,
        )
    )

    # EV charge strategy — applies to the EV charger (load 1).  Gated on a
    # switch entity being assigned to load 1.
    entities.append(
        HA_FelicitySpecialModeSelect(
            coordinator=coordinator,
            entry=entry,
            option_key="ev_charge_strategy",
            select_options=["smart", "solar_only", "cheap_only", "always_on"],
            name="EV Charge Strategy",
            icon="mdi:ev-station",
            entity_category=EntityCategory.CONFIG,
            requires_option="flexible_load_1_switch_entity",
        )
    )

    # Flexible load enable selects. Gated on a switch entity being assigned
    # to the load (assigned via the options flow).
    for i in range(1, 4):
        label = f"Flexible Load {i}"
        if i == 1:
            label = "EV Charger / Load 1"
        entities.append(
            HA_FelicitySpecialModeSelect(
                coordinator=coordinator,
                entry=entry,
                option_key=f"flexible_load_{i}_enabled",
                select_options=["off", "on"],
                name=f"{label} Enabled",
                icon="mdi:ev-station" if i == 1 else "mdi:power-plug-outline",
                entity_category=EntityCategory.CONFIG,
                requires_option=f"flexible_load_{i}_switch_entity",
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
        if success is True:
            # Optimistic update only on explicit True (not None / truthy)
            self.coordinator.data[self._key] = value
            self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


class HA_FelicitySelectMulti(CoordinatorEntity, SelectEntity):
    """
    Representation of a multi-select bitmask register.

    Each option in the list maps to one bit in the register value (LSB-first):
    options[0] → bit 0, options[1] → bit 1, etc.

    The dropdown shows checkmarks (✓/✗) so the user can see which
    options are currently active before toggling.
    """

    _CHECK = "✓ "
    _CROSS = "✗ "

    def __init__(self, coordinator, entry, key, info):
        super().__init__(coordinator)
        self._key = key
        self._info = info
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = f"{entry.title} {info['name']}"
        self._base_options = list(info["options"])

    @property
    def options(self) -> list[str]:
        """Build option list with checkmark prefixes reflecting current state."""
        raw = self.coordinator.data.get(self._key, 0)
        result = []
        for i, opt in enumerate(self._base_options):
            prefix = self._CHECK if raw & (1 << i) else self._CROSS
            result.append(f"{prefix}{opt}")
        return result

    @property
    def current_option(self):
        """Return None because multiple options might be active."""
        return None

    @property
    def state(self):
        """Return a comma-separated string of the selected options."""
        raw = self.coordinator.data.get(self._key, 0)
        selected = [opt for i, opt in enumerate(self._base_options) if raw & (1 << i)]
        if not selected:
            return "None"
        return ", ".join(selected)

    def _strip_prefix(self, option: str) -> str:
        """Remove the checkmark/cross prefix from a dropdown option."""
        if option.startswith(self._CHECK):
            return option[len(self._CHECK):]
        if option.startswith(self._CROSS):
            return option[len(self._CROSS):]
        return option

    async def async_select_option(self, option: str) -> None:
        """Toggle the selected option in the bitmask."""
        bare = self._strip_prefix(option)
        if bare not in self._base_options:
            _LOGGER.warning("Unknown multi-select option '%s' for %s", bare, self._key)
            return

        raw = self.coordinator.data.get(self._key, 0)
        bit_index = self._base_options.index(bare)
        # Toggle the bit
        new_raw = raw ^ (1 << bit_index)

        success = await self.coordinator.TypeSpecificHandler.write_type_specific_register(self._key, new_raw)
        if success is True:
            # Optimistic update only on explicit True (not None / truthy)
            self.coordinator.data[self._key] = new_raw
            self.async_write_ha_state()
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
        requires_option: str | None = None,
    ):
        super().__init__(coordinator)
        self._entry = entry
        self._option_key = option_key
        self._select_options = select_options
        self._requires_option = requires_option
        self._attr_unique_id = f"{entry.entry_id}_{option_key}"
        self._attr_name = f"{entry.title} {name}"
        self._attr_options = select_options
        if icon:
            self._attr_icon = icon
        if entity_category:
            self._attr_entity_category = entity_category

    @property
    def available(self) -> bool:
        """Disabled until the load's prerequisite entity is assigned."""
        if self._requires_option and not self._entry.options.get(self._requires_option):
            return False
        return super().available

    @property
    def current_option(self) -> str:
        """Return the current selected option from persisted options."""
        value = self._entry.options.get(self._option_key, self._select_options[0])
        # Normalize legacy bool values (early installs stored True/False
        # for on/off selects) so the entity doesn't render an invalid state.
        if isinstance(value, bool) and set(self._select_options) >= {"on", "off"}:
            return "on" if value else "off"
        if value not in self._select_options:
            return self._select_options[0]
        return value

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


# Strategy presets: each maps to a set of underlying configuration knobs.
# Users pick a strategy; the individual knobs still exist for advanced tuning.
#
# IMPORTANT: presets set ONLY the two knobs that DEFINE the strategy axis —
# `grid_mode` and `optimization_priority`.  They deliberately do NOT touch any
# user-owned preference: `reserve_target_pct`, `arbitrage_price_delta`,
# `battery_cycle_cost_eur_kwh`, or the negative-price flags
# (`block_export_on_negative_price`, `charge_to_full_on_negative_price`,
# `discharge_to_make_room_for_negative_price`).  Putting those in the presets
# meant that re-selecting a strategy (or the card re-applying one) silently
# reset the user's tuning every time — the reported "arbitrage_price_delta /
# negative-price settings aren't remembered on re-install" symptom (the user
# re-picks their strategy after re-adding the integration, which clobbered
# them).  They now persist independently; their defaults are written once at
# install (config_flow / __init__ migration), never re-clobbered here.
# Each preset sets ONLY the two knobs that DEFINE the strategy axis:
# grid_mode and optimization_priority.  Everything else — reserve_target_pct,
# arbitrage_price_delta, battery_cycle_cost_eur_kwh, and the negative-price
# flags — is a user-owned preference and is NEVER written here, so re-selecting
# (or the card re-applying) a strategy can't wipe the user's tuning.
#   • battery_care needs NO explicit cycle cost: the "longevity" priority itself
#     enforces a 0.05 EUR/kWh cycle-cost floor in both engines
#     (ems.py `max(cycle_cost, 0.05)`, milp.py likewise).
#   • self_sufficiency needs NO reserve_target_pct: the "self_consumption"
#     priority applies the 1.25x reserve boost on its own.
STRATEGY_PRESETS = {
    "save_money": {
        "grid_mode": "from_grid",
        "optimization_priority": "cost",
    },
    "self_sufficiency": {
        "grid_mode": "from_grid",
        "optimization_priority": "self_consumption",
    },
    "battery_care": {
        "grid_mode": "from_grid",
        "optimization_priority": "longevity",
    },
    "trader": {
        "grid_mode": "both",
        "optimization_priority": "cost",
    },
}


class HA_FelicityStrategySelect(CoordinatorEntity, SelectEntity):
    """Strategy preset — the primary user-facing EMS control.

    Selecting a strategy auto-configures multiple underlying knobs.
    'custom' means the user has manually tuned the knobs.
    """

    _attr_icon = "mdi:strategy"
    _attr_entity_category = EntityCategory.CONFIG
    _STRATEGY_OPTIONS = ["save_money", "self_sufficiency", "battery_care", "trader", "custom"]

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_ems_strategy"
        self._attr_name = f"{entry.title} EMS Strategy"
        self._attr_options = list(self._STRATEGY_OPTIONS)

    @property
    def current_option(self) -> str:
        stored = self._entry.options.get("ems_strategy", "custom")
        if stored not in self._STRATEGY_OPTIONS:
            return "custom"
        return stored

    async def async_select_option(self, option: str) -> None:
        if option not in self._STRATEGY_OPTIONS:
            _LOGGER.warning("Invalid strategy '%s'", option)
            return

        updated_options = dict(self._entry.options)
        updated_options["ems_strategy"] = option

        # Apply preset knobs (skip for "custom" — keep whatever the user set)
        preset = STRATEGY_PRESETS.get(option)
        if preset:
            updated_options.update(preset)

        _LOGGER.info("EMS strategy set to '%s'", option)

        self.hass.config_entries.async_update_entry(
            self._entry,
            options=updated_options,
        )

        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()
