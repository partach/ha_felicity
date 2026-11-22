"""Changing the inverter's address in place (Settings → … → Reconfigure).

A router that hands the Modbus gateway a new IP address used to mean deleting
the integration and adding it again.  The reconfigure step changes only the
link (host/port/slave id, or the serial line), tests it the way setup does, and
stores it only when the inverter answers.

The real config_flow.py is driven against a minimal stand-in for Home
Assistant's flow API, so what is asserted is what the step hands Home Assistant:
the form it shows and the data it stores.
"""
from __future__ import annotations

import asyncio
import importlib.util as ilu
import pathlib
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("voluptuous")

from tests.conftest import const

_PKG = pathlib.Path(__file__).parent.parent / "custom_components" / "ha_felicity"


class _ModbusException(Exception):
    pass


class _ConfigFlow:
    """What config_flow.py uses of Home Assistant's ConfigFlow."""

    def __init_subclass__(cls, **kwargs):          # swallows domain=...
        super().__init_subclass__()

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_update_reload_and_abort(self, entry, **kwargs):
        return {"type": "abort", "reason": "reconfigure_successful", "entry": entry, **kwargs}

    def _get_reconfigure_entry(self):
        return self._test_entry


def _module(name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def _load_config_flow():
    selector = _module(
        "homeassistant.helpers.selector",
        SelectOptionDict=dict, SelectSelector=MagicMock(), SelectSelectorConfig=MagicMock(),
        SelectSelectorMode=MagicMock(), EntitySelector=MagicMock(),
        EntitySelectorConfig=MagicMock(), NumberSelector=MagicMock(),
        NumberSelectorConfig=MagicMock(), NumberSelectorMode=MagicMock(),
        TextSelector=MagicMock(), TextSelectorConfig=MagicMock(),
    )
    config_entries = _module(
        "homeassistant.config_entries",
        ConfigFlow=_ConfigFlow, OptionsFlow=object, ConfigEntry=object)
    list_ports = _module("serial.tools.list_ports", comports=list)
    tools = _module("serial.tools", list_ports=list_ports)
    stubs = {
        "homeassistant": _module("homeassistant", config_entries=config_entries),
        "homeassistant.config_entries": config_entries,
        "homeassistant.components": _module("homeassistant.components"),
        "homeassistant.components.sensor": _module(
            "homeassistant.components.sensor", SensorDeviceClass=MagicMock()),
        "homeassistant.const": _module("homeassistant.const", CONF_NAME="name"),
        "homeassistant.core": _module("homeassistant.core", callback=lambda f: f),
        "homeassistant.data_entry_flow": _module("homeassistant.data_entry_flow", FlowResult=dict),
        "homeassistant.helpers": _module("homeassistant.helpers", selector=selector),
        "homeassistant.helpers.selector": selector,
        "serial": _module("serial", tools=tools),
        "serial.tools": tools,
        "serial.tools.list_ports": list_ports,
        "pymodbus.client": _module(
            "pymodbus.client", AsyncModbusSerialClient=MagicMock(), AsyncModbusTcpClient=MagicMock()),
        "pymodbus.exceptions": _module("pymodbus.exceptions", ModbusException=_ModbusException),
    }
    with patch.dict(sys.modules, stubs):
        spec = ilu.spec_from_file_location(
            "custom_components.ha_felicity._reconfigure_config_flow", _PKG / "config_flow.py")
        module = ilu.module_from_spec(spec)
        module.__package__ = "custom_components.ha_felicity"
        spec.loader.exec_module(module)
    return module


CF = _load_config_flow()

TCP_ENTRY = {
    const.CONF_CONNECTION_TYPE: const.CONNECTION_TYPE_TCP,
    "name": "Felicity Inverter",
    const.CONF_HOST: "192.168.1.50",
    const.CONF_PORT: 502,
    const.CONF_SLAVE_ID: 1,
    const.CONF_INVERTER_MODEL: const.INVERTER_MODEL_TREX_TEN,
}
SERIAL_ENTRY = {
    const.CONF_CONNECTION_TYPE: const.CONNECTION_TYPE_SERIAL,
    "name": "Felicity Inverter",
    const.CONF_SERIAL_PORT: "/dev/serial/by-id/usb-FTDI-if00",
    const.CONF_SLAVE_ID: 1,
    const.CONF_BAUDRATE: 2400,
    const.CONF_PARITY: "N",
    const.CONF_STOPBITS: 1,
    const.CONF_BYTESIZE: 8,
    const.CONF_INVERTER_MODEL: const.INVERTER_MODEL_TREX_TEN,
}


def _flow(data):
    flow = CF.HA_FelicityConfigFlow()
    flow._test_entry = SimpleNamespace(data=dict(data), title=data["name"])

    async def run_in_executor(fn, *args):
        return fn(*args)

    flow.hass = SimpleNamespace(async_add_executor_job=run_in_executor)
    flow._async_test_tcp_connection = AsyncMock()
    flow._async_test_serial_connection = AsyncMock()
    return flow


def _defaults(form):
    return {str(key): key.default() for key in form["data_schema"].schema}


def test_the_form_starts_from_the_current_address():
    form = asyncio.run(_flow(TCP_ENTRY).async_step_reconfigure())
    assert form["type"] == "form" and form["step_id"] == "reconfigure"
    assert _defaults(form) == {const.CONF_HOST: "192.168.1.50", const.CONF_PORT: 502,
                               const.CONF_SLAVE_ID: 1}
    assert "192.168.1.50:502" in form["description_placeholders"]["current"]


def test_a_new_ip_that_answers_is_stored_and_the_entry_reloads():
    flow = _flow(TCP_ENTRY)
    new = {const.CONF_HOST: "192.168.1.77", const.CONF_PORT: 502, const.CONF_SLAVE_ID: 1}
    result = asyncio.run(flow.async_step_reconfigure(new))
    assert result["type"] == "abort" and result["reason"] == "reconfigure_successful"
    assert result["data_updates"] == new
    tested = flow._async_test_tcp_connection.await_args.args[0]
    assert tested[const.CONF_HOST] == "192.168.1.77"
    # Nothing but the link changes: model, type and name stay.
    assert tested[const.CONF_INVERTER_MODEL] == const.INVERTER_MODEL_TREX_TEN
    assert tested[const.CONF_CONNECTION_TYPE] == const.CONNECTION_TYPE_TCP


@pytest.mark.parametrize(("raised", "error"), [
    (ConnectionError("no route"), "cannot_connect"),
    (_ModbusException("exception 2"), "read_error"),
    (ValueError("short reply"), "read_error"),
    (RuntimeError("boom"), "unknown"),
])
def test_an_address_that_does_not_answer_is_not_stored(raised, error):
    flow = _flow(TCP_ENTRY)
    flow._async_test_tcp_connection.side_effect = raised
    new = {const.CONF_HOST: "192.168.1.99", const.CONF_PORT: 502, const.CONF_SLAVE_ID: 1}
    form = asyncio.run(flow.async_step_reconfigure(new))
    assert form["type"] == "form" and form["errors"] == {"base": error}
    # The form comes back holding what was typed, not the old address.
    assert _defaults(form)[const.CONF_HOST] == "192.168.1.99"


def test_a_serial_link_keeps_a_port_the_scan_does_not_list():
    form = asyncio.run(_flow(SERIAL_ENTRY).async_step_reconfigure())
    defaults = _defaults(form)
    assert defaults[const.CONF_SERIAL_PORT] == "/dev/serial/by-id/usb-FTDI-if00"
    assert defaults[const.CONF_BAUDRATE] == 2400
    assert const.CONF_HOST not in defaults


def test_a_serial_link_is_tested_as_serial():
    flow = _flow(SERIAL_ENTRY)
    new = {const.CONF_SERIAL_PORT: "/dev/ttyUSB1", const.CONF_SLAVE_ID: 2,
           const.CONF_BAUDRATE: 9600, const.CONF_PARITY: "N",
           const.CONF_STOPBITS: 1, const.CONF_BYTESIZE: 8}
    result = asyncio.run(flow.async_step_reconfigure(new))
    assert result["type"] == "abort" and result["data_updates"] == new
    flow._async_test_serial_connection.assert_awaited_once()
    flow._async_test_tcp_connection.assert_not_awaited()


def test_an_entry_without_a_model_is_tested_with_the_default_one():
    data = {k: v for k, v in TCP_ENTRY.items() if k != const.CONF_INVERTER_MODEL}
    flow = _flow(data)
    new = {const.CONF_HOST: "10.0.0.5", const.CONF_PORT: 502, const.CONF_SLAVE_ID: 1}
    result = asyncio.run(flow.async_step_reconfigure(new))
    tested = flow._async_test_tcp_connection.await_args.args[0]
    assert tested[const.CONF_INVERTER_MODEL] == const.DEFAULT_INVERTER_MODEL
    # ... but the default is not written into the stored entry.
    assert const.CONF_INVERTER_MODEL not in result["data_updates"]
