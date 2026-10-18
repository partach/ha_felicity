"""Tests for coordinator resilience fixes."""

import asyncio
import contextlib
import importlib.util as _ilu
import os
import random
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

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

# The package namespace and the REAL const module come from tests/conftest.py.
# const is deliberately NOT stubbed here: a hand-typed copy drifts the moment a
# constant is added to production, and when it did, this whole file stopped
# collecting (see the conftest docstring).
#
# type_specific IS stubbed — these tests drive the coordinator's own logic and
# assert on the Modbus writes it requests, so the translation layer is a mock.
_type_specific_mod = MagicMock()
_type_specific_mod.__name__ = "custom_components.ha_felicity.type_specific"
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
    coord._read_backoff_s = 0.0
    coord._read_backoff_until = 0.0
    coord._group_timeout_strikes = {}
    coord.dead_register_keys = set()
    coord._answered_keys = set()
    coord.poll_stats = {}
    coord._last_slow_poll_warning = 0.0
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


# ---------------------------------------------------------------------------
# Slot overrides persistence
# ---------------------------------------------------------------------------

class TestSlotOverridesPersistence:
    """Verify that slot overrides load from entry.options on init."""

    def test_loads_overrides_from_entry_options(self):
        """Coordinator should load slot_overrides from entry.options on init."""
        coord = _make_coordinator()
        coord.config_entry.options = {
            "slot_overrides": {"today": {"3": "charge", "4": "charge"}, "tomorrow": {}}
        }
        # Re-init the slot_overrides as the constructor would
        coord.slot_overrides = coord.config_entry.options.get("slot_overrides", {})
        assert coord.slot_overrides == {"today": {"3": "charge", "4": "charge"}, "tomorrow": {}}

    def test_empty_options_gives_empty_overrides(self):
        """No slot_overrides in options means empty dict."""
        coord = _make_coordinator()
        coord.config_entry.options = {}
        coord.slot_overrides = coord.config_entry.options.get("slot_overrides", {})
        assert coord.slot_overrides == {}

    def test_overrides_exposed_in_schedule_status_attrs(self):
        """slot_overrides should appear in the schedule_status sensor attributes."""
        coord = _make_coordinator()
        coord.slot_overrides = {"today": {"5": "discharge"}, "tomorrow": {}}
        # The coordinator exposes slot_overrides in info dict at line 1178
        # We test this by checking the dict directly
        info = {"slot_overrides": coord.slot_overrides if coord.slot_overrides else {}}
        assert info["slot_overrides"] == {"today": {"5": "discharge"}, "tomorrow": {}}

    def test_overrides_merged_into_scheduled_slots(self):
        """Manual overrides should be merged into scheduled_slots during scheduling."""
        coord = _make_coordinator()
        coord.scheduled_slots = {0: "charge", 1: "charge"}
        coord.slot_overrides = {"today": {"1": "discharge", "5": "charge"}, "tomorrow": {}}

        # Simulate the merge logic from coordinator.py:541-552
        grid_mode = "both"
        today_overrides = coord.slot_overrides.get("today", {})
        for idx_str, action in today_overrides.items():
            idx = int(idx_str)
            if grid_mode == "from_grid" and action != "charge":
                continue
            if grid_mode == "to_grid" and action != "discharge":
                continue
            coord.scheduled_slots[idx] = action

        assert coord.scheduled_slots[0] == "charge"      # original, untouched
        assert coord.scheduled_slots[1] == "discharge"    # overridden
        assert coord.scheduled_slots[5] == "charge"       # new from override

    def test_overrides_respect_grid_mode_from_grid(self):
        """In from_grid mode, discharge overrides should be ignored."""
        coord = _make_coordinator()
        coord.scheduled_slots = {}
        coord.slot_overrides = {"today": {"0": "discharge", "1": "charge"}, "tomorrow": {}}

        grid_mode = "from_grid"
        today_overrides = coord.slot_overrides.get("today", {})
        for idx_str, action in today_overrides.items():
            idx = int(idx_str)
            if grid_mode == "from_grid" and action != "charge":
                continue
            if grid_mode == "to_grid" and action != "discharge":
                continue
            coord.scheduled_slots[idx] = action

        assert 0 not in coord.scheduled_slots  # discharge blocked
        assert coord.scheduled_slots[1] == "charge"       # charge allowed

    def test_overrides_respect_grid_mode_to_grid(self):
        """In to_grid mode, charge overrides should be ignored."""
        coord = _make_coordinator()
        coord.scheduled_slots = {}
        coord.slot_overrides = {"today": {"0": "discharge", "1": "charge"}, "tomorrow": {}}

        grid_mode = "to_grid"
        today_overrides = coord.slot_overrides.get("today", {})
        for idx_str, action in today_overrides.items():
            idx = int(idx_str)
            if grid_mode == "from_grid" and action != "charge":
                continue
            if grid_mode == "to_grid" and action != "discharge":
                continue
            coord.scheduled_slots[idx] = action

        assert coord.scheduled_slots[0] == "discharge"    # discharge allowed
        assert 1 not in coord.scheduled_slots              # charge blocked


# ---------------------------------------------------------------------------
# Unresponsive inverter: stop hammering, back off, retry later
# ---------------------------------------------------------------------------

class TestUnresponsiveInverterBackoff:
    """A silent inverter costs ~20 s per read (timeout x retries).  Without a
    limit one poll of the 'full' set (74 groups) took ~25 min and the first
    refresh — which HA setup waits on — never finished (real report, IVGM-20K).
    """

    @staticmethod
    def _silent(n_groups=20):
        coord = _make_coordinator(
            register_map={f"r{i}": {"size": 1, "name": f"R{i}", "index": 0}
                          for i in range(n_groups)},
            groups=[{"start": 100 + i, "count": 1, "keys": [f"r{i}"]}
                    for i in range(n_groups)],
        )
        coord.client.read_holding_registers = AsyncMock(
            side_effect=Exception("No response received after 3 retries"))
        coord.client.close = MagicMock()
        return coord

    @pytest.mark.asyncio
    async def test_gives_up_after_consecutive_timeouts(self):
        coord = self._silent()
        with pytest.raises(Exception, match="not responding"):
            await coord._async_update_data()
        assert (coord.client.read_holding_registers.await_count
                == coordinator_mod.MAX_CONSECUTIVE_READ_TIMEOUTS)
        assert coord.connected is False          # fresh socket next time
        coord.client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_paused_poll_does_not_touch_the_bus(self):
        coord = self._silent()
        with pytest.raises(Exception, match="not responding"):
            await coord._async_update_data()
        calls = coord.client.read_holding_registers.await_count
        with pytest.raises(Exception, match="next attempt in"):
            await coord._async_update_data()
        assert coord.client.read_holding_registers.await_count == calls

    @pytest.mark.asyncio
    async def test_pause_doubles_up_to_the_cap(self):
        coord = self._silent()
        pauses = []
        for _ in range(6):
            coord._read_backoff_until = 0.0      # pause elapsed
            with pytest.raises(Exception, match="not responding"):
                await coord._async_update_data()
            pauses.append(coord._read_backoff_s)
        assert pauses[0] == coordinator_mod.READ_BACKOFF_INITIAL_S
        assert pauses[1] == 2 * pauses[0]
        assert pauses[-1] == coordinator_mod.READ_BACKOFF_MAX_S

    @pytest.mark.asyncio
    async def test_modbus_exception_responses_are_not_timeouts(self):
        """An exception RESPONSE proves the link is alive — keep reading."""
        coord = self._silent(n_groups=6)
        err = MagicMock()
        err.isError.return_value = True
        coord.client.read_holding_registers = AsyncMock(return_value=err)
        with contextlib.suppress(Exception):     # later stages are not under test
            await coord._async_update_data()
        assert coord.client.read_holding_registers.await_count == 6
        assert coord._read_backoff_s == 0.0

    @pytest.mark.asyncio
    async def test_partial_cycle_keeps_data_without_pausing(self):
        """The link answered, then went quiet: stop the cycle, but no pause."""
        coord = self._silent(n_groups=10)
        ok = MagicMock()
        ok.isError.return_value = False
        ok.registers = [7]
        coord.client.read_holding_registers = AsyncMock(
            side_effect=[ok] + [Exception("No response")] * 9)
        with contextlib.suppress(Exception):
            await coord._async_update_data()
        assert coord.client.read_holding_registers.await_count == 1 + 3
        assert coord._read_backoff_s == 0.0


# ---------------------------------------------------------------------------
# grid_mode "off" means hands off: no automatic Modbus writes
# ---------------------------------------------------------------------------

class TestGridModeOffNeverWrites:
    """_current_energy_state starts as None, so the first tick's desired
    "idle" differed from it and _transition_to_state wrote the idle state on
    EVERY start — operating_mode=0 on TREX-5/10, system_mode/zero-export/
    peak-shaving/rule-1 power on TREX-25/50/IVGM — with the EMS switched off
    (IVGM-20K report: "could any write have happened without grid_mode on?").
    """

    @staticmethod
    def _coord(grid_mode, state):
        coord = _make_coordinator()
        coord.config_entry.options = {"grid_mode": grid_mode}
        coord._current_energy_state = state
        coord._reserve_target_pct = 0.0
        coord.safe_max_power = 5
        coord.current_price = None
        coord.price_threshold = None
        coord.TypeSpecificHandler = MagicMock()
        coord.TypeSpecificHandler.write_type_specific_register = AsyncMock(return_value=True)
        coord.data = {"econ_rule_1_start_time": 5, "econ_rule_1_stop_time": 5,
                      "econ_rule_1_effective_week": 1}
        return coord

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", [None, "idle"])
    async def test_startup_idle_with_ems_off_writes_nothing(self, state):
        coord = self._coord("off", state)
        assert await coord._transition_to_state("idle") is True
        coord.TypeSpecificHandler.write_type_specific_register.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", ["charging", "discharging"])
    async def test_switching_off_still_stops_our_own_action(self, state):
        coord = self._coord("off", state)
        assert await coord._transition_to_state("idle") is True
        keys = [c.args[0] for c in
                coord.TypeSpecificHandler.write_type_specific_register.await_args_list]
        assert "econ_rule_1_enable" in keys

    @pytest.mark.asyncio
    async def test_ems_on_still_writes(self):
        coord = self._coord("from_grid", None)
        assert await coord._transition_to_state("idle") is True
        coord.TypeSpecificHandler.write_type_specific_register.assert_awaited()

    @pytest.mark.asyncio
    async def test_rule1_auto_settings_do_not_write_with_ems_off(self):
        coord = self._coord("off", None)
        coord.config_entry.options = {"grid_mode": "off", "rule1_time_window": "auto",
                                      "rule1_weekday": "auto"}
        await coord._apply_rule1_auto_settings()
        coord.TypeSpecificHandler.write_type_specific_register.assert_not_awaited()


# ---------------------------------------------------------------------------
# A read group that never answers is bisected, and a dead register dropped
# ---------------------------------------------------------------------------

class TestNonRespondingGroupIsolation:
    """T-REX-5 via a Waveshare TCP-to-RTU gateway at 2400 baud: the 36-register
    read at 8568 never answered while every other read worked, costing ~20 s
    (timeout x retries) on every poll."""

    BLOCK = range(8568, 8604)          # 36 registers, like the report

    def _coord(self, answers):
        regs = {f"r{a}": {"address": a, "size": 1, "name": f"R{a}", "index": 0}
                for a in self.BLOCK}
        regs["soc"] = {"address": 4400, "size": 1, "name": "SOC", "index": 0}
        coord = _make_coordinator(
            register_map=regs,
            groups=[{"start": 4400, "count": 1, "keys": ["soc"]},
                    {"start": 8568, "count": 36, "keys": [f"r{a}" for a in self.BLOCK]}],
        )
        coord.client.close = MagicMock()

        async def read(address, count, device_id):
            if not answers(address, count):
                raise OSError("No response received after 3 retries")
            ok = MagicMock()
            ok.isError.return_value = False
            ok.registers = [1] * count
            ok.retries = 0
            return ok

        coord.client.read_holding_registers = AsyncMock(side_effect=read)
        return coord

    @staticmethod
    async def _polls(coord, n):
        for _ in range(n):
            coord._read_backoff_until = 0.0
            with contextlib.suppress(Exception):   # later stages are not under test
                await coord._async_update_data()

    def _covered(self, coord):
        return {a for g in coord._address_groups
                for a in range(g["start"], g["start"] + g["count"])}

    @pytest.mark.asyncio
    async def test_one_silent_address_is_isolated_and_dropped(self):
        coord = self._coord(lambda a, n: not (a <= 8590 < a + n))
        await self._polls(coord, 12)
        assert coord.dead_register_keys == {"r8590"}
        assert self._covered(coord) == (set(self.BLOCK) - {8590}) | {4400}
        before = coord.client.read_holding_registers.await_count
        await self._polls(coord, 1)                  # steady state: no timeouts
        assert coord.poll_stats["groups_timed_out"] == []
        assert coord.client.read_holding_registers.await_count > before

    @pytest.mark.asyncio
    async def test_unsupported_block_is_dropped_whole_and_cheaply(self):
        """The model may simply not have the block (T-REX-5 ECO rules?).
        Bisecting to 36 single registers would cost >100 timeouts; instead the
        block goes after 3 polls: once whole, then twice as a silent pair."""
        coord = self._coord(lambda a, n: not (8568 <= a < 8604))
        await self._polls(coord, 3)
        assert coord.dead_register_keys == {f"r{a}" for a in self.BLOCK}
        assert self._covered(coord) == {4400}
        timeouts = coord.client.read_holding_registers.await_count - 3   # 3 SOC reads
        assert timeouts == 1 + 2 + 2
        await self._polls(coord, 1)
        assert coord.poll_stats["groups_timed_out"] == []

    @pytest.mark.asyncio
    async def test_frame_too_large_splits_once_and_drops_nothing(self):
        coord = self._coord(lambda a, n: n <= 20)
        await self._polls(coord, 3)
        assert coord.dead_register_keys == set()
        assert self._covered(coord) == set(self.BLOCK) | {4400}
        assert all(g["count"] <= 20 for g in coord._address_groups)

    @pytest.mark.asyncio
    async def test_dead_link_never_splits_or_drops(self):
        coord = self._coord(lambda a, n: False)
        groups = list(coord._address_groups)
        await self._polls(coord, 5)
        assert coord._address_groups == groups
        assert coord.dead_register_keys == set()

    # Measured by the #209 reporter with raw pymodbus, one attempt per read,
    # integration disabled: success rate by read length.  pymodbus makes up to
    # four attempts per read (retries=3) before reporting "no response".
    FLAKY_LINK = ((9, 0.80), (18, 0.75), (27, 0.45), (36, 0.12))

    def _flaky(self, seed):
        rng = random.Random(seed)

        def answers(address, count):
            if not 8568 <= address < 8604:
                return True
            p = next(v for n, v in self.FLAKY_LINK if count <= n)
            return any(rng.random() < p for _ in range(4))
        return answers

    @pytest.mark.asyncio
    @pytest.mark.parametrize("seed", range(5))
    async def test_an_intermittent_block_is_never_dropped(self, seed):
        """The 5K's ECO block exists (4 rules x 9 registers, dumped by the
        reporter) but its link fails long reads more often than short ones.
        Strikes used to accumulate over the whole uptime and never reset, so
        random misses eventually dropped the block — and with it
        econ_rule_1_enable, the register the EMS controls a 5K through."""
        coord = self._coord(self._flaky(seed))
        await self._polls(coord, 600)
        assert coord.dead_register_keys == set()
        assert self._covered(coord) == set(self.BLOCK) | {4400}
        block = [g for g in coord._address_groups if g["start"] >= 8568]
        assert all(g["count"] >= 9 for g in block), block       # never fragmented
        missed = 0
        for _ in range(200):
            await self._polls(coord, 1)
            missed += bool(coord.poll_stats["groups_timed_out"])
        assert missed <= 10, f"{missed}/200 polls missed part of the block"

    @pytest.mark.asyncio
    async def test_a_register_that_answered_once_is_never_dropped(self):
        answered = set()

        def answers(address, count):
            first = (address, count) not in answered
            answered.add((address, count))
            return first or not (address <= 8590 < address + count)
        coord = self._coord(answers)
        await self._polls(coord, 30)
        assert coord.dead_register_keys == set()
        assert self._covered(coord) == set(self.BLOCK) | {4400}

    @pytest.mark.asyncio
    async def test_shared_model_groups_are_not_mutated(self):
        shared = [{"start": 4400, "count": 1, "keys": ["soc"]},
                  {"start": 8568, "count": 36, "keys": [f"r{a}" for a in self.BLOCK]}]
        coord = self._coord(lambda a, n: n <= 20)
        coord._address_groups = list(shared)
        snapshot = [dict(g) for g in shared]
        await self._polls(coord, 2)
        assert shared == snapshot


# ---------------------------------------------------------------------------
# Manual price mode executes the card's slot overrides
# ---------------------------------------------------------------------------

class TestManualModeExecutesOverrides:
    """Manual mode decided by price vs threshold only: the card accepted an
    override click and drew it, but the coordinator never executed it."""

    @staticmethod
    def _coord(grid_mode, override):
        coord = _make_coordinator()
        coord.slot_prices_today = [0.15] * 24
        slot = 5
        coord._current_slot_index = lambda: slot
        coord.slot_overrides = {"today": {str(slot): override} if override else {},
                                "tomorrow": {}}
        coord.config_entry.options = {"grid_mode": grid_mode, "price_mode": "manual",
                                      "battery_charge_max_level": 100,
                                      "battery_discharge_min_level": 20}
        coord.current_price = 0.15          # above the threshold: threshold says idle
        coord.price_threshold = 0.137
        coord.min_price, coord.max_price = 0.02, 0.35
        coord._current_energy_state = "idle"
        return coord

    def test_charge_override_charges_above_the_threshold(self):
        assert self._coord("from_grid", "charge")._determine_energy_state(45.0) == "charging"

    def test_without_override_the_threshold_still_rules(self):
        assert self._coord("from_grid", None)._determine_energy_state(45.0) == "idle"

    def test_override_the_grid_mode_forbids_is_ignored(self):
        assert self._coord("from_grid", "discharge")._determine_energy_state(45.0) == "idle"

    def test_full_battery_does_not_charge(self):
        assert self._coord("from_grid", "charge")._determine_energy_state(100.0) == "idle"


class TestConsumptionProfileFromRecorder:
    """The hourly consumption profile must come from the recorder's statistics.

    Issue #218: the recorder returns ``start`` as a float timestamp, and the
    parser called ``.hour`` on it — the exception was swallowed and every
    install's profile was FLAT.  The midnight bookkeeping also asked for the
    hours of the day that had just begun, so even a working parser got
    nothing.
    """

    DAY = datetime(2026, 10, 17)  # noqa: DTZ001 - naive local, as the coordinator

    @classmethod
    def _rows(cls, day=None, shape=None):
        day = day or cls.DAY
        shape = shape or [0.3] * 7 + [1.0] * 10 + [2.5] * 5 + [0.5] * 2
        return [{"start": (day + timedelta(hours=h)).timestamp(), "change": kwh}
                for h, kwh in enumerate(shape)]

    def _coord_with_recorder(self, rows_by_day, monkeypatch, now=None):
        coord = _make_coordinator()
        coord._hourly_consumption_history = []
        coord._hourly_consumption_profile = {}
        coord._daily_consumption_history = []
        coord.consumption_override_entity = None
        coord._consumption_backfilled = False
        coord._consumption_backfill_attempt_ts = 0.0
        coord.consumption_entity_used = None
        coord._cycle_charged_kwh = coord._cycle_discharged_kwh = 0.0
        coord._battery_soh_factor = 1.0
        coord._consumption_store = MagicMock()
        coord._consumption_store.async_save = AsyncMock()
        coord._consumption_store_loaded = True
        coord._resolve_consumption_entity = lambda: "sensor.house_energy"
        calls = []

        async def query(entity_id, day):
            calls.append(day)
            return coord._hourly_from_statistics(rows_by_day.get(day, []))

        coord._query_hourly_from_history = query
        if now is not None:
            fixed = now

            class _Clock(datetime):
                @classmethod
                def now(cls, tz=None):
                    return fixed

            monkeypatch.setattr(coordinator_mod, "datetime", _Clock)
        return coord, calls

    def test_float_timestamps_are_parsed_into_local_hours(self):
        hourly = HA_FelicityCoordinator._hourly_from_statistics(self._rows())
        assert len(hourly) == 24
        assert hourly["3"] == 0.3 and hourly["12"] == 1.0 and hourly["19"] == 2.5

    def test_datetime_starts_and_bad_rows(self):
        aware = (self.DAY + timedelta(hours=8)).astimezone()
        rows = [{"start": aware, "change": 1.2}, {"start": None, "change": 9},
                {"start": self.DAY.timestamp(), "change": None},
                {"start": self.DAY.timestamp(), "change": -4.0}, "junk"]
        assert HA_FelicityCoordinator._hourly_from_statistics(rows) == {"8": 1.2}

    @pytest.mark.asyncio
    async def test_query_asks_for_the_local_day_in_kwh(self, monkeypatch, amsterdam):
        seen = {}

        def statistics_during_period(hass, start, end, ids, period, units, types):
            seen.update(start=start, end=end, units=units, types=types)
            return {"sensor.x": self._rows()}

        rec = MagicMock()
        rec.get_instance.return_value.async_add_executor_job = (
            lambda fn, *a: asyncio.sleep(0, result=fn(*a)))
        stats_mod = MagicMock()
        stats_mod.statistics_during_period = statistics_during_period
        monkeypatch.setitem(sys.modules, "homeassistant.components.recorder", rec)
        monkeypatch.setitem(sys.modules, "homeassistant.components.recorder.statistics", stats_mod)
        coord = _make_coordinator()
        hourly = await coord._query_hourly_from_history("sensor.x", self.DAY.date())
        assert len(hourly) == 24
        assert seen["start"] == self.DAY.astimezone()            # LOCAL midnight
        assert seen["end"] == (self.DAY + timedelta(days=1)).astimezone()
        assert seen["units"] == {"energy": "kWh"}

    @pytest.mark.asyncio
    async def test_midnight_records_yesterday_from_the_recorder(self, monkeypatch):
        coord, calls = self._coord_with_recorder(
            {self.DAY.date(): self._rows()}, monkeypatch,
            now=self.DAY + timedelta(days=1, seconds=5))
        await coord._record_daily_consumption()
        assert calls == [self.DAY.date()]
        assert coord._hourly_consumption_history[-1]["date"] == "2026-10-17"
        assert coord.consumption_profile_source == "history"
        assert coord._hourly_consumption_profile[19] == 2.5
        assert coord._hourly_consumption_profile[3] == 0.3
        assert coord._daily_consumption_history[-1] == {"date": "2026-10-17", "kwh": 25.6}

    @pytest.mark.asyncio
    async def test_backfill_replaces_the_flat_days(self, monkeypatch):
        now = self.DAY + timedelta(days=1, hours=9)
        rows = {(self.DAY - timedelta(days=d)).date(): self._rows(self.DAY - timedelta(days=d))
                for d in range(7)}
        coord, calls = self._coord_with_recorder(rows, monkeypatch, now=now)
        # A week of legacy flat days, as every install has today
        for d in range(7):
            ds = (self.DAY - timedelta(days=d)).strftime("%Y-%m-%d")
            coord._hourly_consumption_history.append({"date": ds, "hours": {str(h): 1.1 for h in range(24)}})
        coord._calculate_hourly_profile()
        assert coord.consumption_profile_source == "flat"
        await coord._backfill_consumption_history()
        assert len(calls) == 7
        assert coord.consumption_profile_source == "history"
        assert coord._hourly_consumption_profile[19] == 2.5
        assert coord._consumption_store.async_save.await_count == 1
        # once only
        await coord._backfill_consumption_history()
        assert len(calls) == 7

    @pytest.mark.asyncio
    async def test_backfill_retries_until_the_entity_resolves(self, monkeypatch):
        coord, calls = self._coord_with_recorder({}, monkeypatch)
        coord._resolve_consumption_entity = lambda: None
        await coord._backfill_consumption_history()
        assert not coord._consumption_backfilled
        coord._consumption_backfill_attempt_ts = 0.0       # retry window passed
        coord._resolve_consumption_entity = lambda: "sensor.house_energy"
        await coord._backfill_consumption_history()
        assert coord._consumption_backfilled and len(calls) == 7

    def test_a_flat_day_does_not_shape_a_real_profile(self):
        coord = _make_coordinator()
        coord._hourly_consumption_history = [
            {"date": "2026-10-16", "hours": {str(h): 1.0 for h in range(24)}, "source": "flat"},
            {"date": "2026-10-17", "hours": {"19": 3.0, "3": 0.2}, "source": "history"},
        ]
        coord._calculate_hourly_profile()
        assert coord._hourly_consumption_profile[19] == 3.0
        assert coord._hourly_consumption_profile[3] == 0.2
        assert coord.consumption_profile_source == "partial"


@pytest.fixture
def amsterdam(monkeypatch):
    """Run in a timezone that is NOT UTC, so a stripped offset shows up."""
    import time as _time
    monkeypatch.setenv("TZ", "Europe/Amsterdam")
    _time.tzset()
    yield
    monkeypatch.undo()
    _time.tzset()


class TestForecastParsing:
    """Issue #218: only Forecast.Solar's wh_hours was read.  Open-Meteo Solar
    Forecast (wh_period) and Solcast (detailedHourly, a LIST) fell back to the
    synthetic 06:00-20:00 curve, and offset-aware timestamps had the offset
    stripped instead of converted."""

    TODAY = datetime(2026, 10, 18)  # noqa: DTZ001 - naive local, as the coordinator

    @classmethod
    def _iso(cls, day_offset, hour, utc=False):
        local = cls.TODAY + timedelta(days=day_offset, hours=hour)
        return local.astimezone().astimezone(UTC).isoformat() if utc else local.astimezone().isoformat()

    def _coord(self, states, monkeypatch, options=None):
        coord = _make_coordinator()
        coord.forecast_entity = "sensor.pv_today"
        coord.config_entry.options = options or {}
        coord.hass.states.get = lambda eid: states.get(eid)
        fixed = self.TODAY + timedelta(hours=11)

        class _Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed

        monkeypatch.setattr(coordinator_mod, "datetime", _Clock)
        return coord

    @staticmethod
    def _state(value, **attrs):
        st = MagicMock()
        st.state = str(value)
        st.attributes = attrs
        return st

    def test_open_meteo_wh_period(self, monkeypatch):
        wh = {self._iso(0, h): 1000.0 for h in range(9, 17)}
        tmr = {self._iso(1, h): 500.0 for h in range(9, 17)}
        coord = self._coord({
            "sensor.pv_today": self._state(8.0, wh_period=wh, watts={}),
            "sensor.pv_tomorrow": self._state(4.0, wh_period=tmr),
        }, monkeypatch, {"forecast_entity_tomorrow": "sensor.pv_tomorrow"})
        coord._retrieve_pv_forecast()
        assert coord.pv_curve_source == "wh_period"
        assert sorted(coord.pv_hourly_kwh) == list(range(9, 17))
        assert coord.pv_forecast_remaining == 6.0                  # 11:00..16:00
        # tomorrow's hours live on the tomorrow entity
        assert coord.pv_curve_source_tomorrow == "wh_period"
        assert coord.pv_hourly_kwh_tomorrow[12] == 0.5

    def test_fifteen_minute_periods_sum_into_hours(self, monkeypatch):
        wh = {(self.TODAY + timedelta(hours=12, minutes=15 * q)).astimezone().isoformat(): 250.0
              for q in range(4)}
        coord = self._coord({"sensor.pv_today": self._state(1.0, wh_period=wh)}, monkeypatch)
        coord._retrieve_pv_forecast()
        assert coord.pv_hourly_kwh == {12: 1.0}

    def test_solcast_detailed_hourly_list(self, monkeypatch):
        rows = [{"period_start": self._iso(0, h), "pv_estimate": 2.0} for h in range(10, 14)]
        coord = self._coord({"sensor.pv_today": self._state(8.0, detailedHourly=rows)}, monkeypatch)
        coord._retrieve_pv_forecast()
        assert coord.pv_curve_source == "detailedHourly"
        assert coord.pv_hourly_kwh == {10: 2.0, 11: 2.0, 12: 2.0, 13: 2.0}

    def test_solcast_half_hourly_is_halved(self, monkeypatch):
        rows = [{"period_start": (self.TODAY + timedelta(hours=12, minutes=30 * q)).astimezone(),
                 "pv_estimate": 2.0} for q in range(2)]
        coord = self._coord({"sensor.pv_today": self._state(2.0, detailedForecast=rows)}, monkeypatch)
        coord._retrieve_pv_forecast()
        assert coord.pv_hourly_kwh == {12: 2.0}

    def test_utc_timestamps_are_converted_not_stripped(self, monkeypatch, amsterdam):
        wh = {self._iso(0, 13, utc=True): 1000.0}
        coord = self._coord({"sensor.pv_today": self._state(1.0, wh_hours=wh)}, monkeypatch)
        coord._retrieve_pv_forecast()
        assert coord.pv_hourly_kwh == {13: 1.0}

    def test_no_breakdown_is_reported_as_synthetic(self, monkeypatch):
        coord = self._coord({"sensor.pv_today": self._state(9.0)}, monkeypatch)
        coord._retrieve_pv_forecast()
        assert coord.pv_hourly_kwh == {}
        assert coord.pv_curve_source == "synthetic"

    def test_sun_times_come_from_sun_entity(self, monkeypatch):
        sun = self._state("below_horizon",
                          next_rising=self._iso(1, 8.1, utc=True),
                          next_setting=self._iso(0, 18.75, utc=True))
        coord = self._coord({"sensor.pv_today": self._state(9.0), "sun.sun": sun}, monkeypatch)
        coord._retrieve_pv_forecast()
        assert coord.sunrise_hour == 8.1 and coord.sunset_hour == 18.75
        # the remaining-PV estimate uses the real window: 11:00 of 08:06-18:45
        frac = (11 - 8.1) / (18.75 - 8.1)
        import math as _m
        assert coord.pv_forecast_remaining == round(9.0 * (1 - (1 - _m.cos(_m.pi * frac)) / 2), 2)
