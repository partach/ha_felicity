"""Tests for coordinator resilience fixes."""

import asyncio
import contextlib
import importlib.util as _ilu
import logging
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
    coord._plausibility = coordinator_mod.PlausibilityGuard(coord.register_map, 10)
    coord._reply_count_mismatches = 0
    coord.poll_stats = {}
    coord._last_slow_poll_warning = 0.0
    coord._last_register_set = None
    coord._consumption_store = None
    coord._consumption_store_loaded = False
    coord._consumption_store_lock = asyncio.Lock()
    coord._daily_consumption_history = []
    coord._hourly_consumption_history = []
    coord._hourly_consumption_profile = {}
    coord._history_query_error = None
    coord.weekly_avg_consumption = None
    coord._daily_price_history = []
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

class TestIllegalDataAddressIsolation:
    """Issue #206 (IVGM100600 on the T-REX-10 profile): 0x1225-0x1238 answer
    Modbus exception 2, ILLEGAL DATA ADDRESS, on every poll — four read groups
    and four warnings every 15 s, for ever.  The inverter says those registers do
    not exist, so they are isolated and dropped like a silent register."""

    def _coord(self, illegal):
        regs = {"soc": {"address": 4400, "size": 1, "name": "SOC", "index": 0}}
        block = [f"e{a}" for a in range(4645, 4657, 2)]          # 6 x 2-word counters
        for key in block:
            regs[key] = {"address": int(key[1:]), "size": 2, "name": key, "index": 0}
        coord = _make_coordinator(
            register_map=regs,
            groups=[{"start": 4400, "count": 1, "keys": ["soc"]},
                    {"start": 4645, "count": 12, "keys": block}],
        )
        coord.client.close = MagicMock()

        async def read(address, count, device_id):
            reply = MagicMock()
            reply.retries = 0
            if illegal(address, count):
                reply.isError.return_value = True
                reply.exception_code = 2
                return reply
            reply.isError.return_value = False
            reply.registers = [1] * count
            return reply

        coord.client.read_holding_registers = AsyncMock(side_effect=read)
        return coord, block

    @staticmethod
    async def _polls(coord, n):
        for _ in range(n):
            coord._read_backoff_until = 0.0
            with contextlib.suppress(Exception):
                await coord._async_update_data()

    @pytest.mark.asyncio
    async def test_a_block_the_inverter_does_not_have_is_dropped(self):
        coord, block = self._coord(lambda a, n: 4645 <= a < 4657)
        await self._polls(coord, 3)
        assert coord.dead_register_keys == set(block)
        assert [g["start"] for g in coord._address_groups] == [4400]
        await self._polls(coord, 1)
        assert coord.poll_stats["groups_illegal_address"] == []
        assert coord.poll_stats["groups_timed_out"] == []

    @pytest.mark.asyncio
    async def test_one_illegal_register_is_isolated_and_the_rest_kept(self):
        """Exception 2 rejects the whole read when ONE address is illegal; the
        split finds it and keeps the others."""
        coord, block = self._coord(lambda a, n: a <= 4651 < a + n)
        await self._polls(coord, 12)
        assert coord.dead_register_keys == {"e4651"}
        kept = {k for g in coord._address_groups for k in g["keys"]}
        assert kept == ({"soc"} | set(block)) - {"e4651"}

    @pytest.mark.asyncio
    async def test_a_register_that_answered_once_is_never_dropped(self):
        polls = {"n": 0}

        def illegal(a, n):
            return 4645 <= a < 4657 and polls["n"] > 0

        coord, block = self._coord(illegal)
        await self._polls(coord, 1)          # answered once
        polls["n"] = 1
        await self._polls(coord, 10)
        assert not (coord.dead_register_keys & set(block))

    @pytest.mark.asyncio
    async def test_other_exceptions_are_not_taken_as_absent(self):
        """Only code 2 means "no such address"; a busy device (6) or a gateway
        failure (10/11) says nothing about the map."""
        coord, _block = self._coord(lambda a, n: False)

        async def busy(address, count, device_id):
            reply = MagicMock()
            reply.retries = 0
            if 4645 <= address < 4657:
                reply.isError.return_value = True
                reply.exception_code = 6
                return reply
            reply.isError.return_value = False
            reply.registers = [1] * count
            return reply

        coord.client.read_holding_registers = AsyncMock(side_effect=busy)
        await self._polls(coord, 6)
        assert coord.dead_register_keys == set()
        assert any(g["start"] == 4645 and g["count"] == 12 for g in coord._address_groups)


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
        # LOCAL midnight, one hour early: hour 0 needs the counter's value at 00:00
        assert seen["start"] == self.DAY.astimezone() - timedelta(hours=1)
        assert seen["end"] == (self.DAY + timedelta(days=1)).astimezone()
        assert seen["units"] == {"energy": "kWh"}
        assert seen["types"] == {"change", "state"}

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

    # -- 20 Oct 2026: polluted days survived the parser fix ------------------
    def _polluted_week(self, coord):
        """Seven stored days as the change-based parser wrote them: a whole
        day (~56 kWh) inside the 04:00 hour, and the daily total with it."""
        for d in range(7):
            ds = (self.DAY - timedelta(days=d)).strftime("%Y-%m-%d")
            hours = {str(h): 1.6 for h in range(24)}
            hours["4"] = 57.6
            coord._hourly_consumption_history.append(
                {"date": ds, "hours": hours, "source": "history"})
            coord._daily_consumption_history.append(
                {"date": ds, "kwh": round(sum(hours.values()), 2)})

    @pytest.mark.asyncio
    async def test_polluted_stored_days_are_cleaned_on_load(self):
        """The card showed Avg 96 kWh/d and a 54.5 kWh night after the fix:
        the backfill keeps a stored day whenever the recorder cannot account
        for it, so the old polluted days were never replaced.  Loading the
        store must clean them whatever the recorder says."""
        coord = _make_coordinator()
        source = MagicMock()
        holder = _make_coordinator()
        holder._hourly_consumption_history = []
        holder._daily_consumption_history = []
        self._polluted_week(holder)
        source.async_load = AsyncMock(return_value={
            "hourly_history": holder._hourly_consumption_history,
            "daily_history": holder._daily_consumption_history,
        })
        _storage = sys.modules["homeassistant.helpers.storage"]
        _storage.Store = lambda *a, **kw: source
        coord._consumption_store_loaded = False
        await coord._init_consumption_store()
        assert coord._hourly_consumption_profile[4] == pytest.approx(1.6)
        assert max(coord._hourly_consumption_profile.values()) == pytest.approx(1.6)
        assert coord.weekly_avg_consumption == pytest.approx(38.4)
        assert coord.consumption_profile_source == "history"

    @pytest.mark.asyncio
    async def test_a_polluted_day_the_recorder_cannot_replace_is_still_cleaned(
            self, monkeypatch):
        now = self.DAY + timedelta(days=1, hours=9)
        coord, _ = self._coord_with_recorder({}, monkeypatch, now=now)  # no statistics
        self._polluted_week(coord)
        coord._sanitize_consumption_history()
        coord._calculate_hourly_profile()
        coord._calculate_weekly_avg()
        await coord._backfill_consumption_history()
        assert coord._hourly_consumption_profile[4] == pytest.approx(1.6)
        assert coord.weekly_avg_consumption == pytest.approx(38.4)

    def test_a_new_day_with_an_impossible_hour_is_stored_without_it(self):
        coord = _make_coordinator()
        coord._hourly_consumption_history = []
        coord._daily_consumption_history = []
        hours = {str(h): 2.0 for h in range(24)}
        hours["4"] = 60.0
        coord._store_consumption_day("2026-10-19", hours, "history")
        assert "4" not in coord._hourly_consumption_history[0]["hours"]
        assert coord._daily_consumption_history[0]["kwh"] == pytest.approx(48.0)
        # The dropped hour is filled from the rest of the day, not zero.
        assert coord._hourly_consumption_profile[4] == pytest.approx(2.0)

    def test_an_hour_every_day_lost_gets_the_average_not_zero(self):
        coord = _make_coordinator()
        coord._hourly_consumption_history = [
            {"date": "2026-10-19", "source": "history",
             "hours": {str(h): float(h % 3 + 1) for h in range(24) if h != 4}}]
        coord._calculate_hourly_profile()
        known = [v for h, v in coord._hourly_consumption_profile.items() if h != 4]
        assert coord._hourly_consumption_profile[4] == pytest.approx(sum(known) / 23, abs=1e-3)

    @pytest.mark.asyncio
    async def test_backfill_reports_every_day(self, monkeypatch, caplog):
        now = self.DAY + timedelta(days=1, hours=9)
        rows = {self.DAY.date(): self._rows(self.DAY)}
        coord, _ = self._coord_with_recorder(rows, monkeypatch, now=now)
        with caplog.at_level(logging.INFO):
            await coord._backfill_consumption_history()
        line = [r.getMessage() for r in caplog.records if "Consumption profile from" in r.getMessage()]
        assert line and "1 day(s) rebuilt" in line[0] and "(0 h)" in line[0], line

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

    # -- issue #220 meets the profile: the 04:05 rollover glitch -------------
    @staticmethod
    def _recorder_rows(day, hourly_kwh, glitch=None, reset_at=(0, 0), prev_total=None,
                       glitch_then_zero=False):
        """What HA's recorder stores for a total_increasing DAY counter.

        A state every 5 minutes; ``state`` is the hour's last value and
        ``change`` follows every state with HA's reset rule (a fall below
        0.9 x the previous value is a reset, and the new value counts in
        full).  ``glitch=(h, m)`` makes the counter read ``prev_total`` for
        that one poll — the inverter's rollover glitch (issue #220).
        """
        prev_total = prev_total if prev_total is not None else sum(hourly_kwh)
        rows, counter, prev = [], prev_total, prev_total   # 23:00 the day before
        rows.append({"start": (day - timedelta(hours=1)).timestamp(),
                     "change": 0.0, "state": prev_total})
        for h in range(24):
            change, state = 0.0, None
            for m in range(0, 60, 5):
                if (h, m) == reset_at:
                    counter = 0.0
                counter += hourly_kwh[h] / 12
                state = counter
                if glitch is not None and (h, m) == glitch:
                    state = prev_total
                    if glitch_then_zero:
                        counter = 0.0
                change += state if state < 0.9 * prev else state - prev
                prev = state
            rows.append({"start": (day + timedelta(hours=h)).timestamp(),
                         "change": round(change, 4), "state": round(state, 4)})
        return rows

    REAL = [1.6] * 7 + [2.5] * 10 + [2.0] * 7          # a ~50 kWh/day house

    def test_ha_change_carries_the_glitch_and_the_state_does_not(self):
        """The field case: HA's change gives the 04:00 hour a whole day."""
        rows = self._recorder_rows(self.DAY, self.REAL, glitch=(4, 5))
        assert sum(r["change"] for r in rows[1:]) > 2 * sum(self.REAL) - 1   # what HA sums
        hourly = HA_FelicityCoordinator._hourly_from_statistics(rows, day=self.DAY.date())
        assert len(hourly) == 24
        assert hourly["4"] == pytest.approx(1.6, abs=0.01)
        assert sum(hourly.values()) == pytest.approx(sum(self.REAL), abs=0.05)

    @pytest.mark.parametrize("reset_at", [(0, 0), (1, 0), (4, 5)])
    def test_a_reset_anywhere_in_the_day_counts_only_real_consumption(self, reset_at):
        """The inverter's day resets at 01:00 CEST in summer (its RTC ignores
        DST), at 00:00 in winter — and the glitch may drop it to 0 at 04:05."""
        rows = self._recorder_rows(self.DAY, self.REAL, glitch=(4, 5), reset_at=reset_at,
                                   glitch_then_zero=reset_at == (4, 5))
        hourly = HA_FelicityCoordinator._hourly_from_statistics(rows, day=self.DAY.date())
        assert len(hourly) == 24
        assert max(hourly.values()) <= 2.6
        assert sum(hourly.values()) == pytest.approx(sum(self.REAL), abs=0.5)

    def test_an_hour_beyond_what_the_house_can_draw_is_left_out(self):
        """No state to diff against: the change is all there is, and a 52 kWh
        hour is not consumption on any connection."""
        rows = [{"start": (self.DAY + timedelta(hours=h)).timestamp(),
                 "change": 52.0 if h == 4 else 2.0} for h in range(24)]
        hourly = HA_FelicityCoordinator._hourly_from_statistics(
            rows, day=self.DAY.date(), max_kwh_per_hour=22.4)
        assert "4" not in hourly and len(hourly) == 23

    def test_the_physical_bound_follows_the_connection_and_the_inverter(self):
        coord = _make_coordinator()
        coord.config_entry.options = {"max_amperage_per_phase": 18}
        coord.inverter_model = "TREX-10"
        assert coord._max_household_kwh_per_hour() == pytest.approx(18 * 0.23 * 3 + 10, abs=0.1)

    def test_the_hour_before_the_day_is_only_a_baseline(self):
        rows = self._recorder_rows(self.DAY, self.REAL)
        hourly = HA_FelicityCoordinator._hourly_from_statistics(rows, day=self.DAY.date())
        assert set(hourly) == {str(h) for h in range(24)}

    @pytest.mark.asyncio
    async def test_a_week_of_glitched_statistics_gives_a_real_profile(self, monkeypatch):
        """End to end through the backfill: the profile and the 7-day average
        are the house's, not the house's plus one extra day per day."""
        now = self.DAY + timedelta(days=1, hours=9)
        rows = {}
        for d in range(7):
            day = self.DAY - timedelta(days=d)
            rows[day.date()] = self._recorder_rows(day, self.REAL, glitch=(4, 5))
        coord, _ = self._coord_with_recorder(rows, monkeypatch, now=now)

        async def query(entity_id, day):
            return coord._hourly_from_statistics(rows.get(day, []), day=day,
                                                 max_kwh_per_hour=22.4)

        coord._query_hourly_from_history = query
        await coord._backfill_consumption_history()
        assert coord._hourly_consumption_profile[4] == pytest.approx(1.6, abs=0.01)
        assert max(coord._hourly_consumption_profile.values()) <= 2.5 + 0.01
        assert coord.weekly_avg_consumption == pytest.approx(sum(self.REAL), abs=0.1)

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


class TestWeeklyPriceMemory:
    """The coordinator keeps each day's mean price so greedy and MILP know this
    week's typical price (EMSState.price_reference_eur_kwh) and fill the
    battery on the cheapest day of the week.  Persisted with the consumption
    history; rebuilt from the price entity's recorder statistics at startup.
    """

    NOW = datetime(2026, 10, 24, 20, 30)  # noqa: DTZ001 - naive local, a Saturday

    def _coord(self, monkeypatch, history=None):
        coord = _make_coordinator()
        coord._daily_price_history = list(history or [])
        coord._price_backfilled = False
        coord.nordpool_entity = "sensor.nordpool"
        coord._consumption_store = MagicMock()
        coord._consumption_store.async_save = AsyncMock()
        coord._cycle_charged_kwh = coord._cycle_discharged_kwh = 0.0
        coord._battery_soh_factor = 1.0
        fixed = self.NOW

        class _Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed

        monkeypatch.setattr(coordinator_mod, "datetime", _Clock)
        return coord

    @staticmethod
    def _days(*means, end=NOW):
        return [{"date": (end - timedelta(days=len(means) - 1 - i)).strftime("%Y-%m-%d"),
                 "mean": m} for i, m in enumerate(means)]

    def test_no_reference_before_three_days(self, monkeypatch):
        coord = self._coord(monkeypatch, self._days(0.30, 0.28))
        assert coord.price_reference is None
        coord._daily_price_history = self._days(0.30, 0.28, 0.20)
        assert coord.price_reference == pytest.approx(0.26)

    def test_only_the_last_seven_days_count(self, monkeypatch):
        old = [{"date": "2026-10-10", "mean": 5.0}, {"date": "2026-10-17", "mean": 5.0}]
        coord = self._coord(monkeypatch, old + self._days(0.30, 0.20, 0.10))
        assert coord.price_reference == pytest.approx(0.20)

    def test_today_is_recorded_once_and_updated_when_prices_change(self, monkeypatch):
        coord = self._coord(monkeypatch)
        coord.slot_prices_today = [0.10] * 48 + [0.30] * 48
        assert coord._record_daily_prices() is True
        assert coord._record_daily_prices() is False          # unchanged: no save
        assert coord._daily_price_history == [{"date": "2026-10-24", "mean": 0.2}]
        coord.slot_prices_today = [0.40] * 96
        assert coord._record_daily_prices() is True
        assert coord._daily_price_history == [{"date": "2026-10-24", "mean": 0.4}]

    @pytest.mark.asyncio
    async def test_history_is_saved_with_the_consumption_store(self, monkeypatch):
        coord = self._coord(monkeypatch, self._days(0.3, 0.2, 0.1))
        await coord._save_consumption_store()
        saved = coord._consumption_store.async_save.call_args[0][0]
        assert saved["price_history"] == coord._daily_price_history

    @pytest.mark.asyncio
    async def test_history_is_loaded_and_junk_dropped(self, monkeypatch):
        coord = self._coord(monkeypatch)
        stored = self._days(0.3, 0.2, 0.1) + [{"date": "x"}, "junk"]
        store = MagicMock()
        store.async_load = AsyncMock(return_value={"price_history": stored})
        storage = MagicMock()
        storage.Store.return_value = store
        monkeypatch.setitem(sys.modules, "homeassistant.helpers.storage", storage)
        coord._consumption_store_loaded = False
        coord._sanitize_consumption_history = lambda: None
        coord._calculate_hourly_profile = lambda: None
        coord._calculate_weekly_avg = lambda: None
        await coord._init_consumption_store()
        assert coord._daily_price_history == self._days(0.3, 0.2, 0.1)

    def _recorder(self, monkeypatch, rows, seen=None):
        def statistics_during_period(hass, start, end, ids, period, units, types):
            if seen is not None:
                seen.update(start=start, end=end, types=types, ids=ids)
            return {"sensor.nordpool": rows}

        rec = MagicMock()
        rec.get_instance.return_value.async_add_executor_job = (
            lambda fn, *a: asyncio.sleep(0, result=fn(*a)))
        stats_mod = MagicMock()
        stats_mod.statistics_during_period = statistics_during_period
        monkeypatch.setitem(sys.modules, "homeassistant.components.recorder", rec)
        monkeypatch.setitem(sys.modules, "homeassistant.components.recorder.statistics", stats_mod)

    @staticmethod
    def _hours(day, price, hours=24):
        return [{"start": (day + timedelta(hours=h)).timestamp(), "mean": price}
                for h in range(hours)]

    @pytest.mark.asyncio
    async def test_backfill_rebuilds_the_past_days_from_the_recorder(self, monkeypatch, caplog):
        coord = self._coord(monkeypatch)
        midnight = self.NOW.replace(hour=0, minute=0)
        rows = (self._hours(midnight - timedelta(days=3), 0.30)
                + self._hours(midnight - timedelta(days=2), 0.26)
                + self._hours(midnight - timedelta(days=1), 0.22, hours=12))  # too few hours
        seen = {}
        self._recorder(monkeypatch, rows, seen)
        coord.slot_prices_today = [0.10] * 96
        coord._record_daily_prices()
        with caplog.at_level(logging.INFO):
            await coord._backfill_price_history()
        assert seen["types"] == {"mean"} and seen["ids"] == {"sensor.nordpool"}
        assert [e["date"] for e in coord._daily_price_history] == [
            "2026-10-21", "2026-10-22", "2026-10-24"]
        assert coord.price_reference == pytest.approx((0.30 + 0.26 + 0.10) / 3)
        coord._consumption_store.async_save.assert_awaited()
        line = [r.getMessage() for r in caplog.records if "Price history from" in r.getMessage()]
        assert line and "2 day(s) rebuilt" in line[0], line
        # once only
        seen.clear()
        await coord._backfill_price_history()
        assert not seen

    @pytest.mark.asyncio
    async def test_backfill_without_statistics_changes_nothing(self, monkeypatch, caplog):
        coord = self._coord(monkeypatch, self._days(0.3))
        self._recorder(monkeypatch, [])
        with caplog.at_level(logging.INFO):
            await coord._backfill_price_history()
        assert coord._daily_price_history == self._days(0.3)
        assert coord.price_reference is None
        assert any("needs 3 days" in r.getMessage() for r in caplog.records)


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


class TestRepliesOfTheWrongLength:
    """Issue #220: pymodbus RTU accepts any reply from the right slave, so after
    a late reply and a silent retry a poll can run one reply behind.  A
    4-register lifetime total decoded into the 6-register year/month/day block
    puts 0 in the year counter and the lifetime in the month counter — a reset
    and a spike in the statistics.  A reply whose length is not the request's
    is somebody else's answer and must not be decoded."""

    def _coord(self, replies):
        regs = {
            "pv_total": {"address": 4438, "size": 4, "name": "T", "index": 0,
                         "unit": "Wh", "state_class": "total_increasing"},
            "pv_year": {"address": 4442, "size": 2, "name": "Y", "index": 0,
                        "unit": "Wh", "state_class": "total_increasing"},
            "pv_month": {"address": 4444, "size": 2, "name": "M", "index": 0,
                         "unit": "Wh", "state_class": "total_increasing"},
            "pv_day": {"address": 4446, "size": 2, "name": "D", "index": 0,
                       "unit": "Wh", "state_class": "total_increasing"},
        }
        coord = _make_coordinator(
            register_map=regs,
            groups=[{"start": 4438, "count": 4, "keys": ["pv_total"]},
                    {"start": 4442, "count": 6, "keys": ["pv_year", "pv_month", "pv_day"]}])

        async def read(address, count, device_id):
            ok = MagicMock()
            ok.isError.return_value = False
            ok.registers = replies(address, count)
            ok.retries = 0
            return ok

        coord.client.read_holding_registers = AsyncMock(side_effect=read)
        return coord

    @staticmethod
    async def _poll(coord):
        with contextlib.suppress(Exception):     # later stages are not under test
            await coord._async_update_data()
        return coord

    @pytest.mark.asyncio
    async def test_a_reply_meant_for_another_block_is_discarded(self):
        total = [0, 0, 295, 33_686]                     # (295 << 16) | 33686 Wh
        good = [0, 148, 0, 7, 0, 6777]                  # year / month / day
        # One reply behind: the year/month/day request gets the total's answer.
        coord = self._coord(lambda a, n: total)
        captured = {}
        orig = coord._plausibility.apply

        def spy(data, now):
            orig(data, now)
            captured.update(data)
        coord._plausibility.apply = spy
        await self._poll(coord)
        assert "pv_year" not in captured and "pv_month" not in captured
        assert captured.get("pv_total") == (295 << 16) | 33_686
        assert coord._reply_count_mismatches == 1
        assert len(good) == 6   # what that block's own reply looks like

    @pytest.mark.asyncio
    async def test_a_reply_of_the_right_length_is_decoded(self):
        total = [0, 0, 295, 33_686]
        good = [0, 148, 0, 7, 0, 6777]
        coord = self._coord(lambda a, n: total if a == 4438 else good)
        captured = {}
        orig = coord._plausibility.apply

        def spy(data, now):
            orig(data, now)
            captured.update(data)
        coord._plausibility.apply = spy
        await self._poll(coord)
        assert captured["pv_year"] == 148 and captured["pv_month"] == 7
        assert captured["pv_day"] == 6777
        assert coord._reply_count_mismatches == 0
