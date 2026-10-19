"""The `ha_felicity.write_register` service.

It called `coordinator.async_write_register`, a method the coordinator lost on
2026-01-18 when the write logic moved to TypeSpecificHandler (853c446).  The
service and that removal first shipped in the same release (v.0.8.1), so in
every release that had the service it raised AttributeError.  Nothing caught it
because nothing ran the handler: these tests drive the real one from
__init__.py, against the REAL TypeSpecificHandler and a fake Modbus client, with
a coordinator that is a plain object — a MagicMock would answer any method name
and hide exactly this bug.
"""

from __future__ import annotations

import asyncio
import importlib.util as ilu
import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

const = sys.modules["custom_components.ha_felicity.const"]
_PKG = pathlib.Path(__file__).parent.parent / "custom_components" / "ha_felicity"


def _private(name: str, filename: str):
    """A private copy of a component module (test_coordinator.py replaces some
    of them in sys.modules with MagicMocks at import time)."""
    for missing in ("homeassistant.components.frontend", "homeassistant.components.http"):
        sys.modules.setdefault(missing, MagicMock())
    spec = ilu.spec_from_file_location(f"custom_components.ha_felicity.{name}", _PKG / filename)
    module = ilu.module_from_spec(spec)
    module.__package__ = "custom_components.ha_felicity"
    spec.loader.exec_module(module)
    return module


_TS = _private("_svc_type_specific", "type_specific.py")
_INIT = _private("_svc_init", "__init__.py")


class _Coordinator:
    """Only what the service may touch — no async_write_register on purpose."""

    def __init__(self, model):
        self.client = MagicMock()
        self.client.write_registers = AsyncMock(
            return_value=MagicMock(isError=MagicMock(return_value=False)))
        registers = const.MODEL_REGISTRY[model]["registers"]
        self.TypeSpecificHandler = _TS.TypeSpecificHandler(
            client=self.client, slave_id=1, inverter_model=model, register_map=registers)
        self.async_request_refresh = AsyncMock()


def _service(coordinator, entry_id="entry1"):
    """Register the services on a fake hass and return the write handler."""
    hass = MagicMock()
    hass.data = {const.DOMAIN: {entry_id: coordinator}}
    handlers = {}
    hass.services.async_register = lambda domain, name, fn, *a, **kw: handlers.setdefault(name, fn)
    registry = MagicMock()
    registry.async_get = lambda eid: (MagicMock(config_entry_id=entry_id)
                                      if eid.startswith("sensor.felicity") else None)
    _INIT.er = MagicMock(async_get=lambda _hass: registry)
    asyncio.run(_INIT.async_setup_services(hass))
    return handlers["write_register"]


def _call(handler, **data):
    asyncio.run(handler(MagicMock(data={"entity_id": "sensor.felicity_soc", **data})))


def test_the_service_writes_through_the_register_path():
    coord = _Coordinator(const.INVERTER_MODEL_TREX_TEN)
    _call(_service(coord), key="battery_charged_voltage", value=56)
    info = const.MODEL_REGISTRY[const.INVERTER_MODEL_TREX_TEN]["registers"]["battery_charged_voltage"]
    coord.client.write_registers.assert_awaited_once()
    kwargs = coord.client.write_registers.await_args.kwargs
    assert kwargs["address"] == info["address"]
    assert kwargs["values"] == [560]          # 56 V, scaled like the entity's write
    coord.async_request_refresh.assert_awaited_once()


def test_a_value_is_written_as_a_whole_number_like_the_entities():
    coord = _Coordinator(const.INVERTER_MODEL_TREX_TEN)
    _call(_service(coord), key="battery_charged_voltage", value=56.7)
    assert coord.client.write_registers.await_args.kwargs["values"] == [560]


def test_an_unknown_key_writes_nothing():
    coord = _Coordinator(const.INVERTER_MODEL_TREX_TEN)
    _call(_service(coord), key="no_such_register", value=1)
    coord.client.write_registers.assert_not_awaited()
    coord.async_request_refresh.assert_not_awaited()


def test_a_monitor_only_model_refuses_the_service_too():
    coord = _Coordinator(const.INVERTER_MODEL_IVAM_SIX)
    _call(_service(coord), key="bat1_soc", value=50)
    coord.client.write_registers.assert_not_awaited()


def test_an_entity_of_another_integration_is_ignored():
    coord = _Coordinator(const.INVERTER_MODEL_TREX_TEN)
    handler = _service(coord)
    asyncio.run(handler(MagicMock(data={"entity_id": "sensor.other", "key": "battery_charged_voltage",
                                        "value": 56})))
    coord.client.write_registers.assert_not_awaited()


@pytest.mark.parametrize("model", [const.INVERTER_MODEL_TREX_TEN, const.INVERTER_MODEL_TREX_TWENTY_FIVE])
def test_the_service_does_not_need_a_coordinator_write_method(model):
    """The regression itself: a coordinator without async_write_register."""
    coord = _Coordinator(model)
    assert not hasattr(coord, "async_write_register")
    _call(_service(coord), key="econ_rule_1_soc", value=80)
    coord.client.write_registers.assert_awaited()
