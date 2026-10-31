"""IVGM100600 — 10 kW three-phase, HIGH-VOLTAGE battery (issue #206).

Felicity said it is "the same as the T-REX-10K", and it does answer the T-REX-10
telemetry layout — but a register dump (1.3.13, 2026-10-31) showed three
differences, so it has its own entry, derived from the T-REX-10 map, and the
T-REX-10 map itself is left alone.  Monitor-only: every EMS voltage and battery
setting is for a 48 V pack.  These tests hold both halves.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import load_component

const = sys.modules["custom_components.ha_felicity.const"]
rd = load_component("register_dump")
MODEL = const.INVERTER_MODEL_IVGM_HUNDRED
REGISTERS = const.MODEL_REGISTRY[MODEL]["registers"]
TREX10 = const.MODEL_REGISTRY[const.INVERTER_MODEL_TREX_TEN]["registers"]

#: Raw words from the issue #206 dump (2026-10-31 17:53, battery discharging).
_DUMP = {
    0x1110: 980,     # battery capacity, 98.0 %
    0x110D: 2125,    # battery voltage, 212.5 V
    0x110F: 65388,   # battery power, -148 W
    0x120C: 65530,   # TotalCurrent
    0x120D: 2130,    # TotalVoltage
}


def test_its_own_entry_monitor_only_and_in_no_control_path():
    assert MODEL in const.SUPPORTED_MODELS
    assert MODEL in const.MONITOR_ONLY_MODELS
    assert MODEL not in const.OPERATING_MODE_MODELS
    assert MODEL not in const.ECO_TIMEOFUSE_MODELS
    assert MODEL in const.TREX_TELEMETRY_MODELS          # reads like a T-REX-10
    assert const.MODEL_REGISTRY[MODEL] is not const.MODEL_REGISTRY[const.INVERTER_MODEL_TREX_TEN]
    assert const.INVERTER_MAX_POWER_KW[MODEL] == 10
    assert const.INVERTER_PHASES[MODEL] == 3


def test_the_registers_that_answered_illegal_address_are_not_read():
    addresses = rd.map_addresses(REGISTERS)
    assert not addresses & set(range(0x1225, 0x1239)), sorted(addresses & set(range(0x1225, 0x1239)))
    # ... and they stay on the T-REX-10, where they answer.
    assert set(range(0x1225, 0x1239)) <= rd.map_addresses(TREX10)


def test_only_telemetry_versions_and_the_bms_depth_are_mapped():
    """Nothing in the settings block but 0x212A (read-only here): no rule, no
    voltage, no battery setting a 48 V map would get wrong on 212 V."""
    settings = {k for k, v in REGISTERS.items() if 0x2000 <= v["address"] < 0xF000}
    assert settings == {"battery_discharge_depth_ongrid_bms"}
    assert "type" not in REGISTERS["battery_discharge_depth_ongrid_bms"]


def test_total_current_is_signed_and_matches_the_battery():
    """raw 65530 = -6 -> -0.6 A; battery -148 W / 212.5 V = -0.70 A."""
    decoded = rd.decode(REGISTERS, _DUMP)
    current = decoded["total_current"]["value"]
    assert current == -0.6
    power, volts = decoded["battery_power"]["value"], decoded["battery_voltage"]["value"]
    assert (power, volts) == (-148, 212.5)
    assert abs(current - power / volts) < 0.15


def test_total_voltage_is_tenths_and_matches_the_battery():
    """raw 2130 -> 213.0 V beside a 212.5 V battery (the document's 0.01 V gives 21.3)."""
    decoded = rd.decode(REGISTERS, _DUMP)
    assert decoded["total_voltage"]["value"] == 213.0
    assert abs(decoded["total_voltage"]["value"] - decoded["battery_voltage"]["value"]) < 2


def test_the_t_rex_ten_map_is_not_changed_by_this_measurement():
    """A measurement on one model says nothing about another (CLAUDE.md)."""
    assert TREX10["total_current"]["index"] == 1
    assert TREX10["total_voltage"]["index"] == 2


def _type_specific():
    """A private copy of the REAL type_specific (test_coordinator.py mocks the
    shared one at import time)."""
    import importlib.util as ilu
    path = pathlib.Path(__file__).parent.parent / "custom_components" / "ha_felicity" / "type_specific.py"
    spec = ilu.spec_from_file_location("custom_components.ha_felicity._ivgm_hv_type_specific", path)
    module = ilu.module_from_spec(spec)
    module.__package__ = "custom_components.ha_felicity"
    spec.loader.exec_module(module)
    assert not isinstance(module.TypeSpecificHandler, MagicMock)
    return module


def _handler():
    client = MagicMock()
    client.write_registers = AsyncMock()
    handler = _type_specific().TypeSpecificHandler(
        client=client, slave_id=1, inverter_model=MODEL, register_map=REGISTERS)
    return handler, client


@pytest.mark.parametrize("register, value", [
    ("econ_rule_1_enable", 1), ("operating_mode", 2), ("econ_rule_1_voltage", 58),
    ("battery_discharge_depth_ongrid_bms", 20),   # mapped, but read-only here
])
def test_no_write_reaches_the_bus(register, value):
    handler, client = _handler()
    assert asyncio.run(handler.write_type_specific_register(register, value)) is False
    assert asyncio.run(handler.async_write_registers(0x2178, [1])) is False
    client.write_registers.assert_not_awaited()


def test_the_reads_use_the_t_rex_ten_names():
    handler, _ = _handler()
    data = {"battery_capacity": 98.0, "battery_voltage": 212.5,
            "total_ac_input_power": 112, "ac_input_current": 0.5,
            "ac_input_current_l2": -1.2, "ac_input_current_l3": 0.3}
    assert handler.determine_battery_soc(data) == 98.0
    assert handler.determine_battery_voltage(data) == 212.5
    assert handler.determine_grid_power(data) == 112.0
    assert handler.determine_max_amperage(data) == 1.2
    assert handler.determine_operational_mode(data) == "Monitoring only"


def test_the_setup_label_says_monitoring_only_and_own_risk():
    flow = (pathlib.Path(__file__).parent.parent / "custom_components" / "ha_felicity"
            / "config_flow.py").read_text()
    assert "monitoring only" in flow and "own risk" in flow
    assert "INVERTER_MODEL_IVGM_HUNDRED" in flow
