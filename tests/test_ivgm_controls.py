"""IVGM economic-rule controls: exactly the settings whose encoding is known.

The IVGM protocol document defines addresses but no enum values, so its map
shipped read-only.  The ECO rule settings that need no enum table — times
(HH<<8|MM, confirmed on hardware), voltage/SOC/power numbers and 0/1 enable
flags — are now writable.  Everything whose meaning is still a guess must stay
a sensor.
"""

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import load_component

const = load_component("const")
ivgm = sys.modules["custom_components.ha_felicity.ivgm"]

WRITABLE = {"select", "number", "time8bit", "date8bit", "select_multi"}
ECO_BLOCK = range(0x2207, 0x2232 + 1)


def _writable(registers):
    return {k: v for k, v in registers.items() if v.get("type") in WRITABLE}


@pytest.mark.parametrize("model", const.IVGM_MODELS)
def test_only_eco_rule_settings_are_writable(model):
    controls = _writable(const.MODEL_REGISTRY[model]["registers"])
    expected = {"eco_timeofuse"} | {
        f"econ_rule_{n}_{field}" for n in range(1, 7)
        for field in ("grid_charge_enable", "gen_charge_enable", "start_time",
                      "stop_time", "voltage", "soc", "power")
    }
    assert set(controls) == expected
    assert all(v["address"] in ECO_BLOCK for v in controls.values())


@pytest.mark.parametrize("model", const.IVGM_MODELS)
def test_guessed_settings_stay_read_only(model):
    registers = const.MODEL_REGISTRY[model]["registers"]
    # Work Mode values are undocumented; the weekday bit order is unobserved.
    for key in ("system_mode", "eco_effectiveweek", "grid_peak_shaving_enable",
                "zero_export_to_ct_sell_enable"):
        assert registers[key].get("type") not in WRITABLE, key


@pytest.mark.parametrize("model,cap", [("IVGM-8KLP1G1", 8000),
                                       ("IVGM-15KLP3G1", 20000),
                                       ("IVGM-20KLP3G1", 20000)])
def test_power_controls_are_watts_capped_at_the_nameplate(model, cap):
    registers = const.MODEL_REGISTRY[model]["registers"]
    for n in range(1, 7):
        info = registers[f"econ_rule_{n}_power"]
        assert info["unit"] == "W" and info["index"] == 3   # raw watts, 8xxx block
        assert info["max"] == cap


def test_controls_do_not_leak_into_the_shared_family_map():
    """_with_eco_controls copies entries; the scaled family map stays sensors."""
    assert "type" not in ivgm._REGISTERS_IVGM_SCALED["econ_rule_1_power"]
    assert ivgm._REGISTERS_IVGM_FIFTEEN is ivgm._REGISTERS_IVGM_TWENTY


# ---------------------------------------------------------------------------
# Number entity: scaled registers must not be truncated before scaling
# ---------------------------------------------------------------------------

def _load_number():
    number_stub = sys.modules.setdefault("homeassistant.components.number", MagicMock())
    number_stub.NumberEntity = type("NumberEntity", (), {})
    duc = sys.modules["homeassistant.helpers.update_coordinator"]
    if not isinstance(getattr(duc, "CoordinatorEntity", None), type):
        duc.CoordinatorEntity = type("CoordinatorEntity", (), {})
    sys.modules.setdefault("custom_components.ha_felicity.coordinator", MagicMock())
    return load_component("number")


@pytest.mark.parametrize("index,value,expected", [
    (1, 56.4, 56.4),   # 0.1 V register: the write path does x10 then rounds
    (0, 49.6, 50),     # unscaled register: round, don't truncate
    (3, 7500.0, 7500), # IVGM rule power, raw watts
])
def test_number_write_keeps_precision_for_scaled_registers(index, value, expected):
    number = _load_number()
    coord = MagicMock()
    coord.TypeSpecificHandler.write_type_specific_register = AsyncMock(return_value=True)
    coord.async_request_refresh = AsyncMock()
    fake = types.SimpleNamespace(_key="econ_rule_2_voltage", _info={"index": index},
                                 coordinator=coord, async_write_ha_state=lambda: None)
    asyncio.run(number.HA_FelicityNumber.async_set_native_value(fake, value))
    written = coord.TypeSpecificHandler.write_type_specific_register.await_args.args[1]
    assert written == expected
