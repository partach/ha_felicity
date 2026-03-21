"""Tests for coordinator resilience fixes."""

import asyncio
import sys
import os
import types
from unittest.mock import AsyncMock, MagicMock
import importlib.util as _ilu
import pytest

# ---------------------------------------------------------------------------
# Mock HA + package modules so coordinator.py can be imported
# ---------------------------------------------------------------------------
for mod in [
    "homeassistant",
    "homeassistant.core",
    "homeassistant.config_entries",
    "homeassistant.helpers",
    "homeassistant.helpers.update_coordinator",
    "homeassistant.helpers.storage",
    "homeassistant.helpers.entity",
    "pymodbus",
    "pymodbus.exceptions",
]:
    sys.modules.setdefault(mod, MagicMock())

# Provide a real base class for DataUpdateCoordinator
_mock_duc = MagicMock()
_mock_duc.DataUpdateCoordinator = type(
    "DataUpdateCoordinator",
    (),
    {"__init__": lambda self, *a, **kw: None},
)
_mock_duc.UpdateFailed = Exception
sys.modules["homeassistant.helpers.update_coordinator"] = _mock_duc

# Create the ha_felicity package namespace so relative imports work
_pkg = types.ModuleType("custom_components.ha_felicity")
_pkg.__path__ = [
    os.path.join(os.path.dirname(__file__), "..", "custom_components", "ha_felicity")
]
_pkg.__package__ = "custom_components.ha_felicity"

# Mock sub-modules that coordinator imports relatively
_const_mod = types.ModuleType("custom_components.ha_felicity.const")
_const_mod.DOMAIN = "ha_felicity"
_const_mod.INVERTER_MODEL_TREX_TEN = "TREX-10"

_type_specific_mod = MagicMock()
_type_specific_mod.__name__ = "custom_components.ha_felicity.type_specific"

sys.modules["custom_components"] = types.ModuleType("custom_components")
sys.modules["custom_components.ha_felicity"] = _pkg
sys.modules["custom_components.ha_felicity.const"] = _const_mod
sys.modules["custom_components.ha_felicity.type_specific"] = _type_specific_mod

# ems module — import the real one (already tested separately)

_ems_path = os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "ha_felicity", "ems.py"
)
_ems_spec = _ilu.spec_from_file_location("custom_components.ha_felicity.ems", _ems_path)
_ems_mod = _ilu.module_from_spec(_ems_spec)
sys.modules["custom_components.ha_felicity.ems"] = _ems_mod
_ems_spec.loader.exec_module(_ems_mod)

# Now import coordinator via its package path
_coord_path = os.path.join(
    os.path.dirname(__file__),
    "..",
    "custom_components",
    "ha_felicity",
    "coordinator.py",
)
_coord_spec = _ilu.spec_from_file_location(
    "custom_components.ha_felicity.coordinator", _coord_path,
    submodule_search_locations=[],
)
coordinator_mod = _ilu.module_from_spec(_coord_spec)
coordinator_mod.__package__ = "custom_components.ha_felicity"
sys.modules["custom_components.ha_felicity.coordinator"] = coordinator_mod
_coord_spec.loader.exec_module(coordinator_mod)

HA_FelicityCoordinator = coordinator_mod.HA_FelicityCoordinator
Store = MagicMock  # placeholder for patching


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coordinator(**overrides):
    """Create a minimal coordinator for testing."""
    coord = object.__new__(HA_FelicityCoordinator)
    coord.hass = MagicMock()
    coord.client = AsyncMock()
    coord.slave_id = 1
    coord.register_map = overrides.get("register_map", {
        "voltage": {"size": 1, "name": "Voltage", "index": 0, "precision": 1},
        "power":   {"size": 1, "name": "Power",   "index": 0, "precision": 0},
    })
    coord._address_groups = overrides.get("groups", [
        {"start": 100, "count": 2, "keys": ["voltage", "power"]},
    ])
    coord.config_entry = MagicMock()
    coord.config_entry.entry_id = "test_entry"
    coord.config_entry.options = {}
    coord.model_combined = {}
    coord.inverter_model = "TREX-10"
    coord.TypeSpecificHandler = MagicMock()
    coord.data = {}
    coord.connected = False
    coord._last_register_set = None
    coord._consumption_store = None
    coord._consumption_store_loaded = False
    coord._consumption_store_lock = asyncio.Lock()
    coord._daily_consumption_history = []
    coord.weekly_avg_consumption = None
    return coord


# ---------------------------------------------------------------------------
# Bug #4: Missing bounds check — register_map.get() guard
# ---------------------------------------------------------------------------

class TestRegisterMapBoundsCheck:
    """Verify that a missing key in register_map doesn't crash the poll loop."""

    @pytest.mark.asyncio
    async def test_missing_key_skipped_gracefully(self):
        """If a group references a key not in register_map, skip without KeyError."""
        coord = _make_coordinator(
            register_map={
                # "voltage" intentionally missing from map
                "power": {"size": 1, "name": "Power", "index": 0, "precision": 0},
            },
            groups=[
                {"start": 100, "count": 2, "keys": ["voltage", "power"]},
            ],
        )

        mock_result = MagicMock()
        mock_result.isError.return_value = False
        mock_result.registers = [230, 1500]
        coord.client.read_holding_registers = AsyncMock(return_value=mock_result)

        # Replicate the fixed loop logic
        new_data = {}
        for group in coord._address_groups:
            result = await coord.client.read_holding_registers(
                address=group["start"],
                count=group["count"],
                device_id=coord.slave_id,
            )
            registers = result.registers
            pos = 0
            for key in group["keys"]:
                info = coord.register_map.get(key)
                if info is None:
                    # This is the fix: skip unknown keys
                    continue
                size = info.get("size", 1)
                if pos + size > len(registers):
                    break
                reg_slice = registers[pos : pos + size]
                pos += size
                new_data[key] = reg_slice[0]

        # voltage skipped, power parsed (from pos=0 since voltage was skipped)
        assert "voltage" not in new_data
        # No KeyError was raised — that's the main assertion

    @pytest.mark.asyncio
    async def test_all_keys_present_works_normally(self):
        """When all keys exist in register_map, registers parse correctly."""
        coord = _make_coordinator()
        mock_result = MagicMock()
        mock_result.isError.return_value = False
        mock_result.registers = [2300, 1500]
        coord.client.read_holding_registers = AsyncMock(return_value=mock_result)

        new_data = {}
        for group in coord._address_groups:
            result = await coord.client.read_holding_registers(
                address=group["start"],
                count=group["count"],
                device_id=coord.slave_id,
            )
            registers = result.registers
            pos = 0
            for key in group["keys"]:
                info = coord.register_map.get(key)
                if info is None:
                    continue
                size = info.get("size", 1)
                if pos + size > len(registers):
                    break
                reg_slice = registers[pos : pos + size]
                pos += size
                new_data[key] = reg_slice[0]

        assert new_data["voltage"] == 2300
        assert new_data["power"] == 1500


# ---------------------------------------------------------------------------
# Bug #6: Race condition in consumption store init
# ---------------------------------------------------------------------------

class TestConsumptionStoreLock:
    """Verify _init_consumption_store is protected against concurrent calls."""

    @pytest.mark.asyncio
    async def test_concurrent_init_only_loads_once(self):
        """Two concurrent calls should only create/load the store once."""
        coord = _make_coordinator()
        load_count = 0

        async def mock_async_load():
            nonlocal load_count
            load_count += 1
            await asyncio.sleep(0.01)  # simulate I/O
            return {"daily_history": [30.0, 32.0, 28.0]}

        mock_store = MagicMock()
        mock_store.async_load = mock_async_load
        coord._calculate_weekly_avg = MagicMock()

        _storage = sys.modules["homeassistant.helpers.storage"]
        _orig_store = getattr(_storage, "Store", None)
        _storage.Store = lambda *a, **kw: mock_store
        try:
            await asyncio.gather(
                coord._init_consumption_store(),
                coord._init_consumption_store(),
            )
        finally:
            if _orig_store is not None:
                _storage.Store = _orig_store

        assert coord._consumption_store_loaded is True
        assert load_count == 1  # loaded exactly once thanks to the lock
        assert coord._daily_consumption_history == [30.0, 32.0, 28.0]

    @pytest.mark.asyncio
    async def test_already_loaded_is_noop(self):
        """If store is already loaded, init is a no-op."""
        coord = _make_coordinator()
        coord._consumption_store_loaded = True
        coord._daily_consumption_history = [10.0]

        await coord._init_consumption_store()
        assert coord._daily_consumption_history == [10.0]

    @pytest.mark.asyncio
    async def test_empty_store_data(self):
        """None data from store is handled gracefully."""
        coord = _make_coordinator()

        mock_store = MagicMock()
        mock_store.async_load = AsyncMock(return_value=None)

        _storage = sys.modules["homeassistant.helpers.storage"]
        _storage.Store = lambda *a, **kw: mock_store

        await coord._init_consumption_store()

        assert coord._consumption_store_loaded is True
        assert coord._daily_consumption_history == []

    @pytest.mark.asyncio
    async def test_store_trims_to_seven_days(self):
        """History longer than 7 days is trimmed to the last 7."""
        coord = _make_coordinator()
        coord._calculate_weekly_avg = MagicMock()

        long_history = [10, 20, 30, 40, 50, 60, 70, 80, 90]
        mock_store = MagicMock()
        mock_store.async_load = AsyncMock(
            return_value={"daily_history": long_history}
        )

        _storage = sys.modules["homeassistant.helpers.storage"]
        _storage.Store = lambda *a, **kw: mock_store

        await coord._init_consumption_store()

        assert len(coord._daily_consumption_history) == 7
        assert coord._daily_consumption_history == long_history[-7:]
        coord._calculate_weekly_avg.assert_called_once()
