"""Issue #220: sensors read "unknown" for a poll after every restart.

The first refresh runs before the platforms are set up, and CoordinatorEntity
does not replay it, so every sensor waited for the NEXT poll.  Templates reading
them with ``| float(0)`` saw 0 in that window (battery kWh 0.0 -> 11,678 at a
restart).  Now each sensor takes the data already in memory when it is added,
and an energy counter with no reading yet shows its value from before the
restart instead of nothing.

sensor.py imports Home Assistant, which the test environment stubs with
MagicMocks; a MagicMock cannot be a base class, so a private copy of sensor.py
is loaded here against small stand-ins for the four HA classes it builds on.
"""

from __future__ import annotations

import asyncio
import importlib.util as ilu
import pathlib
import sys
import types
from unittest.mock import MagicMock, patch

_PKG = pathlib.Path(__file__).parent.parent / "custom_components" / "ha_felicity"


class _Entity:
    def async_write_ha_state(self):
        self.writes = getattr(self, "writes", 0) + 1

    async def async_added_to_hass(self):
        pass


class _SensorEntity(_Entity):
    pass


class _RestoreSensor(_SensorEntity):
    last = None

    async def async_get_last_sensor_data(self):
        return self.last


class _CoordinatorEntity(_Entity):
    def __init__(self, coordinator):
        self.coordinator = coordinator


def _load_sensor_module():
    sensor_mod = types.ModuleType("homeassistant.components.sensor")
    sensor_mod.SensorEntity = _SensorEntity
    sensor_mod.RestoreSensor = _RestoreSensor
    sensor_mod.SensorDeviceClass = types.SimpleNamespace(ENUM="enum")
    uc_mod = types.ModuleType("homeassistant.helpers.update_coordinator")
    uc_mod.CoordinatorEntity = _CoordinatorEntity
    core_mod = types.ModuleType("homeassistant.core")
    core_mod.callback = lambda fn: fn
    core_mod.HomeAssistant = object
    coord_mod = types.ModuleType("custom_components.ha_felicity.coordinator")
    coord_mod.HA_FelicityCoordinator = object
    stubs = {
        "homeassistant.components.sensor": sensor_mod,
        "homeassistant.helpers.update_coordinator": uc_mod,
        "homeassistant.core": core_mod,
        "custom_components.ha_felicity.coordinator": coord_mod,
    }
    with patch.dict(sys.modules, stubs):
        spec = ilu.spec_from_file_location("custom_components.ha_felicity._sensor_startup",
                                           _PKG / "sensor.py")
        module = ilu.module_from_spec(spec)
        module.__package__ = "custom_components.ha_felicity"
        spec.loader.exec_module(module)
    return module


S = _load_sensor_module()

_ENTRY = MagicMock(entry_id="e1", title="Felicity Inverter")
_ENERGY = {"name": "Pv Generated Energy Inquiry Total", "unit": "Wh",
           "device_class": "energy", "state_class": "total_increasing", "index": 0}
_POWER = {"name": "Battery Power", "unit": "W", "device_class": "power",
          "state_class": "measurement", "index": 3}


def _coordinator(data):
    return types.SimpleNamespace(data=data, last_update_success=True)


def _added(entity):
    asyncio.run(entity.async_added_to_hass())
    return entity


def test_a_sensor_shows_the_first_polls_value_when_added():
    sensor = _added(S.HA_FelicitySensor(_coordinator({"battery_power": 8825}),
                                        _ENTRY, "battery_power", _POWER))
    assert sensor._attr_native_value == 8825
    assert sensor.available


def test_a_combined_sensor_shows_its_value_when_added():
    info = {"sources": ["a", "b"], "calc": lambda a, b: a + b, "name": "Total PV Power",
            "unit": "W", "precision": 0}
    sensor = _added(S.HA_FelicityCombinedSensor(_coordinator({"a": 672, "b": 580}),
                                                _ENTRY, "total_pv_power", info))
    assert sensor._attr_native_value == 1252


def test_energy_counters_get_the_restoring_class_and_others_do_not():
    assert issubclass(S.HA_FelicityEnergySensor, _RestoreSensor)
    assert not issubclass(S.HA_FelicitySensor, _RestoreSensor)


def test_an_energy_counter_without_a_reading_shows_its_value_from_before_the_restart():
    sensor = S.HA_FelicityEnergySensor(_coordinator({}), _ENTRY,
                                       "pv_generated_energy_total", _ENERGY)
    sensor.last = types.SimpleNamespace(native_value=19_366_742)
    _added(sensor)
    assert sensor._attr_native_value == 19_366_742
    assert sensor.available                 # a value, not "unknown" / unavailable


def test_a_real_reading_replaces_the_restored_value_for_good():
    coordinator = _coordinator({})
    sensor = S.HA_FelicityEnergySensor(coordinator, _ENTRY, "pv_generated_energy_total", _ENERGY)
    sensor.last = types.SimpleNamespace(native_value=19_366_742)
    _added(sensor)
    coordinator.data = {"pv_generated_energy_total": 19_366_800}
    sensor._handle_coordinator_update()
    assert sensor._attr_native_value == 19_366_800
    coordinator.data = {}                   # a later missed read is just unknown
    sensor._handle_coordinator_update()
    assert sensor._attr_native_value is None
    assert not sensor.available


def test_a_reading_present_at_startup_wins_over_the_restored_one():
    sensor = S.HA_FelicityEnergySensor(
        _coordinator({"pv_generated_energy_total": 19_366_800}), _ENTRY,
        "pv_generated_energy_total", _ENERGY)
    sensor.last = types.SimpleNamespace(native_value=1)
    _added(sensor)
    assert sensor._attr_native_value == 19_366_800
