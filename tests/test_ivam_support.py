"""IVAM6048P1G1 — a MONITOR-ONLY model.

Its protocol document (v01, 2026-07-06) has one register table: read-only
telemetry.  No operating/economic mode, no time-of-use rule, no power limit —
so the integration reads it and must never write to it.  These tests pin both
halves: the map is the document's (addresses, names, scales), and nothing the
integration does can put a write on the bus.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import load_component

const = sys.modules["custom_components.ha_felicity.const"]
MODEL = const.INVERTER_MODEL_IVAM_SIX
_DOC = json.loads((pathlib.Path(__file__).parent.parent / "custom_components" /
                   "ha_felicity" / "ivam_documented_registers.json").read_text())["registers"]
REGISTERS = const.MODEL_REGISTRY[MODEL]["registers"]


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


# --- the map is the document's ---------------------------------------------

def test_every_address_is_in_the_document_with_its_size():
    """No address may be borrowed — not even from the IVGM, whose telemetry
    happens to sit at the same addresses: its SETTING addresses are exactly
    what the IVAM document does not define."""
    for key, info in REGISTERS.items():
        doc = _DOC.get(f"0x{info['address']:04X}")
        assert doc, f"{key} @0x{info['address']:04X} is not in the IVAM document"
        assert info.get("size", 1) == doc["size"], f"{key}: size differs from the document"


def test_every_name_is_the_documents():
    for key, info in REGISTERS.items():
        doc = _DOC[f"0x{info['address']:04X}"]["name"]
        assert _norm(info["name"]) in _norm(doc), f"{key}: {info['name']!r} vs document {doc!r}"


def test_scaling_follows_the_documents_type_and_rate_columns():
    """"int" is signed, "Rate -1" is /10, "-2" is /100, "0" raw."""
    signed_ok = {"-1": {8}, "0": {3}}
    unsigned_ok = {"-1": {1}, "-2": {2}, "0": {0}}
    for key, info in REGISTERS.items():
        doc = _DOC[f"0x{info['address']:04X}"]
        table = signed_ok if doc["type"] == "int" else unsigned_ok
        assert info["index"] in table[doc["rate"]], (
            f"{key}: index {info['index']} does not match {doc['type']} rate {doc['rate']}")


def test_power_is_in_the_documents_watts_and_declared_so():
    """W per the document — and declared in POWER_UNIT_BY_MODEL as such.  The
    claim is UNMEASURED (the IVGM document also said W and was 10x off), which
    is why the dump's physics checks cover these registers."""
    assert const.POWER_UNIT_BY_MODEL[MODEL] == "W"
    for key, info in REGISTERS.items():
        if _DOC[f"0x{info['address']:04X}"]["unit"] == "W":
            assert info.get("unit") == "W", key


def test_the_transcript_has_no_overlapping_registers():
    taken = {}
    for addr, info in _DOC.items():
        for i in range(info["size"]):
            word = int(addr, 16) + i
            assert word not in taken, f"{addr} overlaps {taken[word]}"
            taken[word] = addr


# --- it is monitor-only, and nothing writes ----------------------------------

def test_it_is_monitor_only_and_in_no_control_path():
    assert MODEL in const.MONITOR_ONLY_MODELS
    assert MODEL not in const.ECO_TIMEOFUSE_MODELS
    assert MODEL not in const.OPERATING_MODE_MODELS


def _type_specific():
    """A private copy of the REAL type_specific.

    test_coordinator.py replaces sys.modules["…type_specific"] with a MagicMock
    at import time, so load_component would hand back that stub whenever the
    suite runs in full — and every assertion here would be about a mock.
    """
    import importlib.util as ilu
    path = pathlib.Path(__file__).parent.parent / "custom_components" / "ha_felicity" / "type_specific.py"
    spec = ilu.spec_from_file_location("custom_components.ha_felicity._ivam_type_specific", path)
    module = ilu.module_from_spec(spec)
    module.__package__ = "custom_components.ha_felicity"
    spec.loader.exec_module(module)
    assert not isinstance(module.TypeSpecificHandler, MagicMock)
    return module


def _handler():
    ts = _type_specific()
    client = MagicMock()
    client.write_registers = AsyncMock()
    return ts.TypeSpecificHandler(client=client, slave_id=1, inverter_model=MODEL,
                                  register_map=REGISTERS), client


@pytest.mark.parametrize("register, value", [
    ("econ_rule_1_enable", 1), ("operating_mode", 2), ("econ_rule_1_power", 5000),
    ("bat1_soc", 50),      # even a register that exists: it is read-only
])
def test_no_write_reaches_the_bus(register, value):
    handler, client = _handler()
    assert asyncio.run(handler.write_type_specific_register(register, value)) is False
    assert asyncio.run(handler.async_write_registers(0x2100, [1])) is False
    client.write_registers.assert_not_awaited()


def test_the_reads_use_the_ivam_registers():
    handler, _ = _handler()
    data = {"bat1_soc": 64.5, "bat1_voltage": 52.1, "grid_a_phase_power": -812,
            "grid_a_current": -3.6}
    assert handler.determine_battery_soc(data) == 64.5
    assert handler.determine_battery_voltage(data) == 52.1
    assert handler.determine_grid_power(data) == -812.0
    assert handler.determine_phase_currents(data) == [3.6]
    assert handler.determine_max_amperage(data) == 3.6
    assert handler.determine_operational_mode(data) == "Monitoring only"


def test_the_ems_is_kept_off_and_not_offered():
    """The setup migration forces grid_mode off, and the select entities offer
    nothing else — a plan the inverter cannot carry out is not shown."""
    root = pathlib.Path(__file__).parent.parent / "custom_components" / "ha_felicity"
    init = (root / "__init__.py").read_text()
    assert re.search(r"inverter_model in MONITOR_ONLY_MODELS and updated_options\.get\(\"grid_mode\"\) != \"off\"", init)
    select = (root / "select.py").read_text()
    assert 'select_options=["off"] if monitor_only' in select
    assert re.search(r"if not monitor_only:\s*\n\s*entities\.append\(\s*\n\s*HA_FelicityStrategySelect", select)


def test_the_dump_reads_the_whole_document_including_low_words():
    rd = load_component("register_dump")
    addresses = rd.load_ivam_documented_addresses()
    assert 0x1260 in addresses                       # unmapped "inquiry" block too
    assert addresses[0x1196].endswith("(low word)")  # second word of PV1 total
    assert len(addresses) == sum(i["size"] for i in _DOC.values())
