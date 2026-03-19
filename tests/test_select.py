"""Tests for select entity resilience fixes.

import sys
import os
import types
from unittest.mock import AsyncMock, MagicMock
import importlib.util as _ilu
import pytest

# ---------------------------------------------------------------------------
# Mock HA + package modules so select.py can be imported
# ---------------------------------------------------------------------------
for mod in [
    "homeassistant",
    "homeassistant.core",
    "homeassistant.config_entries",
    "homeassistant.helpers",
    "homeassistant.helpers.update_coordinator",
    "homeassistant.helpers.entity",
    "homeassistant.components",
    "homeassistant.components.select",
    "pymodbus",
    "pymodbus.exceptions",
]:
    sys.modules.setdefault(mod, MagicMock())

# Provide real base classes
_mock_select = sys.modules["homeassistant.components.select"]
_mock_select.SelectEntity = type("SelectEntity", (), {})

_mock_duc = sys.modules["homeassistant.helpers.update_coordinator"]
_mock_duc.CoordinatorEntity = type(
    "CoordinatorEntity",
    (),
    {"__init__": lambda self, coordinator: setattr(self, "coordinator", coordinator)},
)
_mock_duc.DataUpdateCoordinator = type("DataUpdateCoordinator", (), {})

_mock_entity = sys.modules["homeassistant.helpers.entity"]
_mock_entity.DeviceInfo = dict
_mock_entity.EntityCategory = MagicMock()
_mock_entity.EntityCategory.CONFIG = "config"

# Create ha_felicity package namespace
_pkg_root = os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "ha_felicity"
)
_pkg = types.ModuleType("custom_components.ha_felicity")
_pkg.__path__ = [_pkg_root]
_pkg.__package__ = "custom_components.ha_felicity"

_const_mod = types.ModuleType("custom_components.ha_felicity.const")
_const_mod.DOMAIN = "ha_felicity"
_const_mod.CONF_INVERTER_MODEL = "inverter_model"
_const_mod.DEFAULT_INVERTER_MODEL = "TREX-10"

_coord_mock = MagicMock()
_coord_mock.__name__ = "custom_components.ha_felicity.coordinator"

sys.modules.setdefault("custom_components", types.ModuleType("custom_components"))
sys.modules["custom_components.ha_felicity"] = _pkg
sys.modules["custom_components.ha_felicity.const"] = _const_mod
sys.modules["custom_components.ha_felicity.coordinator"] = _coord_mock


_select_path = os.path.join(_pkg_root, "select.py")
_spec = _ilu.spec_from_file_location(
    "custom_components.ha_felicity.select", _select_path,
    submodule_search_locations=[],
)
select_mod = _ilu.module_from_spec(_spec)
select_mod.__package__ = "custom_components.ha_felicity"
sys.modules["custom_components.ha_felicity.select"] = select_mod
_spec.loader.exec_module(select_mod)

HA_FelicitySelect = select_mod.HA_FelicitySelect
HA_FelicitySelectMulti = select_mod.HA_FelicitySelectMulti


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coordinator(data=None):
    coord = MagicMock()
    coord.data = data if data is not None else {}
    coord.TypeSpecificHandler = MagicMock()
    coord.async_request_refresh = AsyncMock()
    return coord


def _make_entry(entry_id="test_entry", title="Felicity"):
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.title = title
    return entry


# ---------------------------------------------------------------------------
# Bug #5: HA_FelicitySelect — optimistic update guard
# ---------------------------------------------------------------------------

class TestSelectOptimisticUpdate:
    """Write success must be explicitly True to update coordinator.data."""

    @pytest.mark.asyncio
    async def test_write_true_updates_data(self):
        """Explicit True → optimistic update applied."""
        coord = _make_coordinator(data={"mode": 0})
        coord.TypeSpecificHandler.write_type_specific_register = AsyncMock(
            return_value=True
        )
        entry = _make_entry()
        info = {"name": "Mode", "options": ["off", "on", "auto"]}

        sel = HA_FelicitySelect(coord, entry, "mode", info)
        sel.async_write_ha_state = MagicMock()

        await sel.async_select_option("on")

        assert coord.data["mode"] == 1
        sel.async_write_ha_state.assert_called_once()
        coord.async_request_refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_write_none_no_update(self):
        """None from write (silent failure) → data NOT mutated."""
        coord = _make_coordinator(data={"mode": 0})
        coord.TypeSpecificHandler.write_type_specific_register = AsyncMock(
            return_value=None
        )
        entry = _make_entry()
        info = {"name": "Mode", "options": ["off", "on", "auto"]}

        sel = HA_FelicitySelect(coord, entry, "mode", info)
        sel.async_write_ha_state = MagicMock()

        await sel.async_select_option("on")

        assert coord.data["mode"] == 0
        sel.async_write_ha_state.assert_not_called()
        # Refresh is always called to reconcile state
        coord.async_request_refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_write_false_no_update(self):
        """Explicit False → data NOT mutated."""
        coord = _make_coordinator(data={"mode": 0})
        coord.TypeSpecificHandler.write_type_specific_register = AsyncMock(
            return_value=False
        )
        entry = _make_entry()
        info = {"name": "Mode", "options": ["off", "on", "auto"]}

        sel = HA_FelicitySelect(coord, entry, "mode", info)
        sel.async_write_ha_state = MagicMock()

        await sel.async_select_option("on")

        assert coord.data["mode"] == 0
        sel.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_option_ignored(self):
        """Selecting an option not in the list does nothing."""
        coord = _make_coordinator(data={"mode": 0})
        coord.TypeSpecificHandler.write_type_specific_register = AsyncMock()
        entry = _make_entry()
        info = {"name": "Mode", "options": ["off", "on"]}

        sel = HA_FelicitySelect(coord, entry, "mode", info)

        await sel.async_select_option("bogus")

        coord.TypeSpecificHandler.write_type_specific_register.assert_not_awaited()
        assert coord.data["mode"] == 0

    def test_current_option_returns_label(self):
        """current_option maps register int to option string."""
        coord = _make_coordinator(data={"mode": 2})
        entry = _make_entry()
        info = {"name": "Mode", "options": ["off", "on", "auto"]}

        sel = HA_FelicitySelect(coord, entry, "mode", info)
        assert sel.current_option == "auto"

    def test_current_option_none_for_missing(self):
        """current_option returns None when value not in data."""
        coord = _make_coordinator(data={})
        entry = _make_entry()
        info = {"name": "Mode", "options": ["off", "on"]}

        sel = HA_FelicitySelect(coord, entry, "mode", info)
        assert sel.current_option is None

    def test_current_option_none_for_out_of_range(self):
        """current_option returns None when index exceeds options."""
        coord = _make_coordinator(data={"mode": 99})
        entry = _make_entry()
        info = {"name": "Mode", "options": ["off", "on"]}

        sel = HA_FelicitySelect(coord, entry, "mode", info)
        assert sel.current_option is None


# ---------------------------------------------------------------------------
# Bug #5: HA_FelicitySelectMulti — write guard + no silent mutation
# ---------------------------------------------------------------------------

class TestSelectMultiOptimisticUpdate:
    """HA_FelicitySelectMulti must guard optimistic update on write success."""

    @pytest.mark.asyncio
    async def test_write_true_toggles_bit(self):
        """Explicit True → bit toggled in coordinator.data."""
        coord = _make_coordinator(data={"sources": 1})  # bit 0 = Solar on
        coord.TypeSpecificHandler.write_type_specific_register = AsyncMock(
            return_value=True
        )
        entry = _make_entry()
        info = {"name": "Sources", "options": ["Solar", "Grid", "Generator"]}

        sel = HA_FelicitySelectMulti(coord, entry, "sources", info)
        sel.async_write_ha_state = MagicMock()

        # Toggle Grid (bit 1) → 1 | 2 = 3
        await sel.async_select_option("✗ Grid")

        assert coord.data["sources"] == 3
        sel.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_write_none_no_mutation(self):
        """None from write → bitmask NOT updated."""
        coord = _make_coordinator(data={"sources": 1})
        coord.TypeSpecificHandler.write_type_specific_register = AsyncMock(
            return_value=None
        )
        entry = _make_entry()
        info = {"name": "Sources", "options": ["Solar", "Grid"]}

        sel = HA_FelicitySelectMulti(coord, entry, "sources", info)
        sel.async_write_ha_state = MagicMock()

        await sel.async_select_option("✗ Grid")

        assert coord.data["sources"] == 1  # unchanged
        sel.async_write_ha_state.assert_not_called()
        coord.async_request_refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_write_false_no_mutation(self):
        """False from write → bitmask NOT updated."""
        coord = _make_coordinator(data={"sources": 3})
        coord.TypeSpecificHandler.write_type_specific_register = AsyncMock(
            return_value=False
        )
        entry = _make_entry()
        info = {"name": "Sources", "options": ["Solar", "Grid"]}

        sel = HA_FelicitySelectMulti(coord, entry, "sources", info)
        sel.async_write_ha_state = MagicMock()

        await sel.async_select_option("✓ Solar")

        assert coord.data["sources"] == 3  # unchanged

    @pytest.mark.asyncio
    async def test_unknown_option_ignored(self):
        """Unknown bare option logs warning and does nothing."""
        coord = _make_coordinator(data={"sources": 0})
        coord.TypeSpecificHandler.write_type_specific_register = AsyncMock()
        entry = _make_entry()
        info = {"name": "Sources", "options": ["Solar", "Grid"]}

        sel = HA_FelicitySelectMulti(coord, entry, "sources", info)

        await sel.async_select_option("✗ Nuclear")

        coord.TypeSpecificHandler.write_type_specific_register.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_toggle_off_with_success(self):
        """Toggle an active bit off when write succeeds."""
        coord = _make_coordinator(data={"sources": 3})  # Solar + Grid
        coord.TypeSpecificHandler.write_type_specific_register = AsyncMock(
            return_value=True
        )
        entry = _make_entry()
        info = {"name": "Sources", "options": ["Solar", "Grid"]}

        sel = HA_FelicitySelectMulti(coord, entry, "sources", info)
        sel.async_write_ha_state = MagicMock()

        # Toggle Solar off → 3 ^ 1 = 2
        await sel.async_select_option("✓ Solar")

        assert coord.data["sources"] == 2

    def test_state_shows_selected_options(self):
        """state returns comma-separated active options."""
        coord = _make_coordinator(data={"sources": 5})  # bits 0 and 2
        entry = _make_entry()
        info = {"name": "Sources", "options": ["Solar", "Grid", "Generator"]}

        sel = HA_FelicitySelectMulti(coord, entry, "sources", info)
        assert sel.state == "Solar, Generator"

    def test_state_none_when_empty(self):
        """state returns 'None' when no bits set."""
        coord = _make_coordinator(data={"sources": 0})
        entry = _make_entry()
        info = {"name": "Sources", "options": ["Solar", "Grid"]}

        sel = HA_FelicitySelectMulti(coord, entry, "sources", info)
        assert sel.state == "None"

    def test_options_shows_checkmarks(self):
        """options property prefixes check/cross based on current bitmask."""
        coord = _make_coordinator(data={"sources": 2})  # only Grid
        entry = _make_entry()
        info = {"name": "Sources", "options": ["Solar", "Grid"]}

        sel = HA_FelicitySelectMulti(coord, entry, "sources", info)
        opts = sel.options
        assert opts[0] == "✗ Solar"
        assert opts[1] == "✓ Grid"
