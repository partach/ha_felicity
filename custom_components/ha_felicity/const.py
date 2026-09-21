"""Constants for the Felicity integration."""
from .ivgm import (
    _COMBINED_REGISTERS_IVGM_EIGHT,
    _COMBINED_REGISTERS_IVGM_TWENTY,
    _REGISTERS_IVGM_EIGHT,
    _REGISTERS_IVGM_TWENTY,
    REGISTER_SETS_IVGM_EIGHT,
    REGISTER_SETS_IVGM_TWENTY,
)
from .trex_fifty import (
    _COMBINED_REGISTERS_TREX_FIFTY,
    _REGISTERS_TREX_FIFTY,
    REGISTER_SETS_TREX_FIFTY,
)
from .trex_five import (
    _COMBINED_REGISTERS_TREX_FIVE,
    _REGISTERS_TREX_FIVE,
    REGISTER_SETS_TREX_FIVE,
)
from .trex_ten import (
    _COMBINED_REGISTERS_TREX_TEN,
    _REGISTERS_TREX_TEN,
    REGISTER_SETS_TREX_TEN,
)
from .trex_twenty_five import (
    _COMBINED_REGISTERS_TREX_TWENTY_FIVE,
    _REGISTERS_TREX_TWENTY_FIVE,
    REGISTER_SETS_TREX_TWENTY_FIVE,
)

DOMAIN = "ha_felicity"

# Connection types
CONNECTION_TYPE_SERIAL = "serial"
CONNECTION_TYPE_TCP = "tcp"

# Common settings
CONF_SLAVE_ID = "slave_id"
CONF_CONNECTION_TYPE = "connection_type"
CONF_NAME = "name"
CONF_REGISTER_SET = "register_set"
CONF_INVERTER_MODEL = "inverter_model"

# Supported inverter models
INVERTER_MODEL_TREX_FIVE = "T-REX-5K-P1G01"
INVERTER_MODEL_TREX_TEN = "T-REX-10K-P3G01"
INVERTER_MODEL_TREX_FIFTY = "T-REX-50KHP3G01"
INVERTER_MODEL_TREX_TWENTY_FIVE = "T-REX-25KHP3G01"

# IVGM family (different product line — see ivgm.py and docs/IVGM_SUPPORT_GAPS.md).
# Support is PROVISIONAL: built from the 8K RS485 protocol document, not yet
# validated against hardware.  The 3-phase map in particular is inferred.
INVERTER_MODEL_IVGM_EIGHT = "IVGM-8KLP1G1"
INVERTER_MODEL_IVGM_TWENTY = "IVGM-20KLP3G1"

INVERTER_MAX_POWER_KW = {
    INVERTER_MODEL_TREX_FIVE: 5,
    INVERTER_MODEL_TREX_TEN: 10,
    INVERTER_MODEL_TREX_TWENTY_FIVE: 25,
    INVERTER_MODEL_TREX_FIFTY: 50,
    INVERTER_MODEL_IVGM_EIGHT: 8,
    INVERTER_MODEL_IVGM_TWENTY: 20,
}

SUPPORTED_MODELS = [
    INVERTER_MODEL_TREX_FIVE,
    INVERTER_MODEL_TREX_TEN,
    INVERTER_MODEL_TREX_FIFTY,
    INVERTER_MODEL_TREX_TWENTY_FIVE,
    INVERTER_MODEL_IVGM_EIGHT,
    INVERTER_MODEL_IVGM_TWENTY,
    # add new ones here
]

#: Models whose control path follows the ECO_TimeOfUse / ECOn_GridChargeEnable
#: layout (0x2207 / 0x2209...) rather than the TREX-5/10 operating_mode +
#: econ_rule_1_enable layout.  type_specific.py branches on this instead of
#: listing models one by one, so a new family member cannot silently fall
#: through to "unknown model -> return None".
ECO_TIMEOFUSE_MODELS = (
    INVERTER_MODEL_TREX_TWENTY_FIVE,
    INVERTER_MODEL_TREX_FIFTY,
    INVERTER_MODEL_IVGM_EIGHT,
    INVERTER_MODEL_IVGM_TWENTY,
)

#: Models using the TREX-5/10 operating_mode(8451) + econ_rule_1_enable path.
OPERATING_MODE_MODELS = (
    INVERTER_MODEL_TREX_FIVE,
    INVERTER_MODEL_TREX_TEN,
)

#: IVGM family members.  They share the ECO block with TREX-25/50 but NOT the
#: sell-enable registers — see IVGM_UNSUPPORTED_WRITES below.
IVGM_MODELS = (
    INVERTER_MODEL_IVGM_EIGHT,
    INVERTER_MODEL_IVGM_TWENTY,
)

#: Models whose POWER registers are expressed in WATTS.  This cuts ACROSS the
#: control-path split above and is the easiest thing to get wrong: the IVGM uses
#: the TREX-25/50 *register layout* (ECO block) but the TREX-5/10 *power unit*
#: (the protocol document gives ECO1_Power and Grid Total Power in W, not kW).
#: Reading a kW value as W under-reports grid power 1000x; writing a W value to
#: a kW register asks the inverter for 1000x the power.  Anything not listed
#: here is treated as kW.
WATT_POWER_MODELS = (
    INVERTER_MODEL_TREX_FIVE,
    INVERTER_MODEL_TREX_TEN,
    INVERTER_MODEL_IVGM_EIGHT,
    # INVERTER_MODEL_IVGM_TWENTY is deliberately NOT here — see below.
)

# ⚠️ The 20K was removed from WATT_POWER_MODELS on hardware evidence (Sept 2026).
# A customer's 20K read `bat1_power` (0x1131) as raw 156 where the true value was
# 1560 W, i.e. 0.01 kW per count — so its power registers are NOT watts, despite
# the 8K protocol document saying "W".  That matches Felicity's range-wide
# pattern (small models in W, large in 0.01 kW: T-REX-5/10 vs T-REX-25/50).
#
# The measurement is of a TELEMETRY register; ECO1_Power (0x220F) is a SETTING
# and was not measured.  Treating the 20K as kW is nonetheless the correct
# default, because the two error directions are not symmetric:
#   writing W into a kW register  -> asks for 1000x TOO MUCH power (dangerous)
#   writing kW into a W register  -> asks for 1000x too little (undercharges)
# So when the scaling is uncertain, kW is the side to be wrong on.  The 8K stays
# in W: it is what its own document says and no measurement contradicts it.

#: Registers the TREX-25/50 control path writes that the IVGM protocol document
#: does NOT define.  Writing them on an IVGM would hit an undocumented address:
#: 0x21FF-0x2204 sit inside the IVGM's GRID UNDER-FREQUENCY PROTECTION block,
#: so a stray 0/1 there is not a no-op — it could alter a grid-protection
#: threshold.  type_specific.py must never write these on an IVGM.
IVGM_UNSUPPORTED_WRITES = frozenset({
    "econ_rule_1_sell_enable", "econ_rule_2_sell_enable", "econ_rule_3_sell_enable",
    "econ_rule_4_sell_enable", "econ_rule_5_sell_enable", "econ_rule_6_sell_enable",
    "zero_export_mode_selection",
})

# Serial settings
CONF_SERIAL_PORT = "serial_port"
CONF_BAUDRATE = "baudrate"
CONF_PARITY = "parity"
CONF_STOPBITS = "stopbits"
CONF_BYTESIZE = "bytesize"

# TCP settings
CONF_HOST = "host"
CONF_PORT = "port"

REGISTER_SET_BASIC = "basic"
REGISTER_SET_BASIC_PLUS = "basic_plus"
REGISTER_SET_FULL = "full"

# Defaults
DEFAULT_SLAVE_ID = 1
DEFAULT_BAUDRATE = 2400
DEFAULT_TCP_PORT = 502
DEFAULT_REGISTER_SET = "basic"
DEFAULT_STOPBITS = 1
DEFAULT_BYTESIZE = 8
DEFAULT_PARITY = "N"
DEFAULT_INVERTER_MODEL = INVERTER_MODEL_TREX_TEN
DEFAULT_FIRST_REG = 4353

# Precision and index based on the "Rate/Magnification/Scale" column
# 0 = dont process or packed
# 1 = /10 → precision 1, index 1;
# 2 = /100 → precision 2, index 2; 
# 3 = signed index;
# 4 = /1000  → precision 2, index 4;
# 5 = faults/warnings/modes/flags index; (doesnt do anything for processing)
# 6 = time index; (obsolete)
# 7 = % index (doesnt really do anything yet)
# 8 = signed index and /10; 
# 9 = signed index and /100; 
# 99 -> dont show as sensor, it is a sub-part of a combined value, see combined registers

# Model-specific data (extend for new models)
def build_groups(registers):
    sorted_regs = sorted(registers.items(), key=lambda x: x[1]["address"])
    groups = []
    current = None
    current_size = None

    for key, info in sorted_regs:
        addr = info["address"]
        size = info.get("size", 1)

        if current is None:
            current = {"start": addr, "count": size, "keys": [key]}
            current_size = size
        else:
            expected_next = current["start"] + current["count"]
            if addr == expected_next and size == current_size and current["count"] + size <= 120:
                current["count"] += size
                current["keys"].append(key)
            else:
                groups.append(current)
                current = {"start": addr, "count": size, "keys": [key]}
                current_size = size

    if current:
        groups.append(current)
    return groups


MODEL_REGISTRY = {
    INVERTER_MODEL_IVGM_EIGHT: {
        "registers":        _REGISTERS_IVGM_EIGHT,
        "combined":         _COMBINED_REGISTERS_IVGM_EIGHT,
        "register_groups":  build_groups(_REGISTERS_IVGM_EIGHT),
        "register_sets":    REGISTER_SETS_IVGM_EIGHT,
        "default_first_reg": 4352,   # 0x1100 WorkMode — the IVGM block starts one lower
        "default_slave_id": 1,
    },
    INVERTER_MODEL_IVGM_TWENTY: {
        "registers":        _REGISTERS_IVGM_TWENTY,
        "combined":         _COMBINED_REGISTERS_IVGM_TWENTY,
        "register_groups":  build_groups(_REGISTERS_IVGM_TWENTY),
        "register_sets":    REGISTER_SETS_IVGM_TWENTY,
        "default_first_reg": 4352,
        "default_slave_id": 1,
    },
    INVERTER_MODEL_TREX_FIVE: {
        "registers":        _REGISTERS_TREX_FIVE,
        "combined":         _COMBINED_REGISTERS_TREX_FIVE,
        "register_groups":  build_groups(_REGISTERS_TREX_FIVE),
        "register_sets":    REGISTER_SETS_TREX_FIVE,
        "default_first_reg": 4353,
        "default_slave_id": 1,
    },
    INVERTER_MODEL_TREX_TEN: {
        "registers":        _REGISTERS_TREX_TEN,
        "combined":         _COMBINED_REGISTERS_TREX_TEN,
        "register_groups":  build_groups(_REGISTERS_TREX_TEN),
        "register_sets":    REGISTER_SETS_TREX_TEN,
        "default_first_reg": 4353,
        "default_slave_id": 1,
    },

    INVERTER_MODEL_TREX_FIFTY: {
        "registers":        _REGISTERS_TREX_FIFTY,
        "combined":         _COMBINED_REGISTERS_TREX_FIFTY,
        "register_groups":  build_groups(_REGISTERS_TREX_FIFTY),
        "register_sets":    REGISTER_SETS_TREX_FIFTY,
        "default_first_reg": 4357,   # ← different starting point!
        "default_slave_id": 1,
    },
    
    INVERTER_MODEL_TREX_TWENTY_FIVE: {
        "registers":        _REGISTERS_TREX_TWENTY_FIVE,
        "combined":         _COMBINED_REGISTERS_TREX_TWENTY_FIVE,
        "register_groups":  build_groups(_REGISTERS_TREX_TWENTY_FIVE),
        "register_sets":    REGISTER_SETS_TREX_TWENTY_FIVE,
        "default_first_reg": 4357,   # ← different starting point!
        "default_slave_id": 1,
    },
}

