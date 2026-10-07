"""The read-only register dump (register_dump.py, diagnostics.py, tools/ivgm_dump.py).

The dump exists to check a register map against real hardware, so it must
(a) decode exactly like the coordinator — otherwise a scale bug in one is
hidden by a matching bug in the other — and (b) never touch anything but the
addresses it was told to read, with function 3 only.
"""

import asyncio
import datetime as dt
import importlib.util as _ilu
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

from tests.conftest import load_component

rd = load_component("register_dump")
const = sys.modules["custom_components.ha_felicity.const"]
ivgm = sys.modules["custom_components.ha_felicity.ivgm"]


# ---------------------------------------------------------------------------
# Decoding parity with the coordinator
# ---------------------------------------------------------------------------

def test_scaling_matches_the_coordinator():
    from tests.test_coordinator import HA_FelicityCoordinator
    coordinator_scale = HA_FelicityCoordinator._apply_scaling
    raws = {1: (0, 1, 156, 410, 0x7FFF, 0x8000, 0xFFFF),
            2: (0, 1, 70000, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF)}
    for index in (0, 1, 2, 3, 4, 8, 9, 99):
        for size, values in raws.items():
            for raw in values:
                assert rd.apply_scaling(raw, index, size) == \
                    coordinator_scale(None, raw, index, size), (index, size, raw)


def test_decode_uses_the_measured_ivgm_scales():
    """Raw words from the Sept 2026 15K dump decode to the physical values."""
    regs = ivgm._REGISTERS_IVGM_TWENTY
    raw = {regs["bat1_voltage"]["address"]: 536,        # 53.6 V
           regs["bat1_current"]["address"]: 151,        # 15.1 A
           regs["bat1_power"]["address"]: 80,           # 0.80 kW
           regs["environment_temperature"]["address"]: 410,
           regs["bms_total_voltage"]["address"]: 5360}
    decoded = rd.decode(regs, raw)
    assert decoded["bat1_power"]["value"] == 0.8
    assert decoded["environment_temperature"]["value"] == 41.0
    assert decoded["bms_total_voltage"]["value"] == 53.6
    check = next(c for c in rd.physics_checks(decoded) if c["check"] == "Bat1")
    assert check["result"] == "ok"


def test_physics_check_flags_a_tenfold_scale_error():
    regs = {"bat1_voltage": {"address": 1, "index": 1, "unit": "V", "name": "V"},
            "bat1_current": {"address": 2, "index": 1, "unit": "A", "name": "I"},
            "bat1_power": {"address": 3, "index": 1, "unit": "kW", "name": "P"}}
    decoded = rd.decode(regs, {1: 536, 2: 151, 3: 80})        # 8.0 kW: 10x off
    assert rd.physics_checks(decoded)[0]["result"] == "SCALE SUSPECT"


def test_32bit_registers_combine_big_endian():
    regs = {"e": {"address": 10, "size": 2, "index": 1, "unit": "kWh", "name": "E",
                  "precision": 1}}
    assert rd.decode(regs, {10: 1, 11: 0})["e"]["value"] == 6553.6
    assert rd.decode(regs, {10: 1})["e"]["value"] is None     # half a register


# ---------------------------------------------------------------------------
# What gets read
# ---------------------------------------------------------------------------

def test_reads_only_documented_or_mapped_addresses():
    documented = rd.load_ivgm_documented_addresses()
    wanted = set(documented) | rd.map_addresses(ivgm._REGISTERS_IVGM_TWENTY)
    read = set()
    for start, count in rd.plan_reads(wanted):
        assert count <= rd.MAX_CHUNK
        read |= set(range(start, start + count))
    assert read == wanted                       # no gap address, nothing missed
    assert 0xAAAA not in read                   # prose example, not a register


def test_ivgm_map_is_covered_by_the_document():
    """Reading the document's addresses is enough to decode the whole map."""
    documented = set(rd.load_ivgm_documented_addresses())
    assert rd.map_addresses(ivgm._REGISTERS_IVGM_TWENTY) <= documented


def test_failed_batch_is_isolated_register_by_register():
    def read(addr, count):
        if count > 1 or addr == 102:
            raise OSError("exception 2")
        return [addr]

    raw, failed = rd.read_all(read, [(100, 5)])
    assert raw[101] == 101 and raw[104] == 104
    assert list(failed) == ["0x0066"]


def test_dead_link_gives_up_instead_of_timing_out_every_address():
    calls = []

    def read(addr, count):
        calls.append(addr)
        raise OSError("timeout")

    _, failed = rd.read_all(read, [(0, 60)])
    assert len(calls) == 1 + 3                  # batch + three singles
    assert len(failed) == 60


# ---------------------------------------------------------------------------
# Diagnostics platform
# ---------------------------------------------------------------------------

class _FakeInverter:
    """Answers function 3 from a dict and records every call."""

    def __init__(self, registers):
        self.registers, self.calls, self.connected = registers, [], True

    async def read_holding_registers(self, address, count, device_id):
        self.calls.append((address, count))
        result = MagicMock()
        result.isError.return_value = False
        result.registers = [self.registers.get(a, 0) for a in range(address, address + count)]
        return result


def _load_diagnostics():
    diag_stub = MagicMock()
    diag_stub.async_redact_data = lambda data, keys: {
        k: ("**REDACTED**" if k in keys else v) for k, v in data.items()}
    util_stub, dt_stub = MagicMock(), MagicMock()
    dt_stub.now = lambda: dt.datetime(2026, 10, 7, 12, 0, tzinfo=dt.UTC)
    util_stub.dt = dt_stub
    sys.modules["homeassistant.components.diagnostics"] = diag_stub
    sys.modules["homeassistant.util"] = util_stub
    sys.modules["homeassistant.util.dt"] = dt_stub
    return load_component("diagnostics")


def test_diagnostics_dumps_the_full_ivgm_map_read_only():
    diagnostics = _load_diagnostics()
    model = const.INVERTER_MODEL_IVGM_TWENTY
    regs = ivgm._REGISTERS_IVGM_TWENTY
    inverter = _FakeInverter({regs["bat1_soc"]["address"]: 870})

    coordinator = MagicMock()
    coordinator.client, coordinator.slave_id = inverter, 1
    coordinator.inverter_model = model
    coordinator.register_map = {}                      # selected set: irrelevant
    coordinator.data = {"bat1_soc": 87.0}
    coordinator.integration_version = "test"
    coordinator.milp_status = None
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.data = {"host": "192.168.1.50", "inverter_model": model}
    entry.options = {"grid_mode": "off"}
    hass = MagicMock()
    hass.data = {const.DOMAIN: {"e1": coordinator}}

    async def executor(fn, *args):
        return fn(*args)
    hass.async_add_executor_job = executor

    out = asyncio.run(diagnostics.async_get_config_entry_diagnostics(hass, entry))

    assert out["entry_data"]["host"] == "**REDACTED**"
    dump = out["register_dump"]
    assert dump["model"] == model
    assert dump["decoded"]["bat1_soc"]["value"] == 87.0
    assert len(dump["decoded"]) == len(regs)          # full map, not the selected set
    assert "unmapped_documented" in dump
    documented = set(rd.load_ivgm_documented_addresses())
    touched = {a for start, n in inverter.calls for a in range(start, start + n)}
    assert touched <= documented                      # never an undocumented address
    json.dumps(dump)                                  # serialisable as-is


# ---------------------------------------------------------------------------
# Standalone tool
# ---------------------------------------------------------------------------

def test_standalone_tool_writes_a_dump(tmp_path, monkeypatch):
    path = os.path.join(os.path.dirname(__file__), "..", "tools", "ivgm_dump.py")
    spec = _ilu.spec_from_file_location("ivgm_dump_tool", path)
    tool = _ilu.module_from_spec(spec)
    spec.loader.exec_module(tool)

    class Client:
        def connect(self):
            return True

        def close(self):
            pass

        def read_holding_registers(self, address, count, device_id):
            result = MagicMock()
            result.isError.return_value = False
            result.registers = [0] * count
            return result

    monkeypatch.setattr(tool, "_make_client", lambda args: Client())
    out = tmp_path / "dump.json"
    assert tool.main(["--host", "x", "--out", str(out)]) == 0
    dump = json.loads(out.read_text(encoding="utf-8"))
    assert dump["model"] == "IVGM-20KLP3G1"
    assert dump["addresses_failed"] == {}


@pytest.mark.parametrize("model", ["IVGM-8KLP1G1", "IVGM-15KLP3G1", "IVGM-20KLP3G1"])
def test_tool_knows_every_ivgm_model(model):
    path = os.path.join(os.path.dirname(__file__), "..", "tools", "ivgm_dump.py")
    spec = _ilu.spec_from_file_location("ivgm_dump_tool2", path)
    tool = _ilu.module_from_spec(spec)
    spec.loader.exec_module(tool)
    assert model in tool._MAPS and model in const.IVGM_MODELS
