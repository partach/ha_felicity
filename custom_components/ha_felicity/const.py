"""Constants for the Felicity integration."""
from .trex_fifty import _REGISTERS_TREX_FIFTY, _COMBINED_REGISTERS_TREX_FIFTY, REGISTER_SETS_TREX_FIFTY
from .trex_ten import _REGISTERS_TREX_TEN, _COMBINED_REGISTERS_TREX_TEN, REGISTER_SETS_TREX_TEN 
from .trex_five import _REGISTERS_TREX_FIVE, _COMBINED_REGISTERS_TREX_FIVE, REGISTER_SETS_TREX_FIVE 


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

SUPPORTED_MODELS = [
    INVERTER_MODEL_TREX_FIVE,
    INVERTER_MODEL_TREX_TEN,
    INVERTER_MODEL_TREX_FIFTY,
    # add new ones here
]

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
        "register_groups":  build_groups(_REGISTERS_TWENTY_FIVE),
        "register_sets":    REGISTER_SETS_TWENTY_FIVE,
        "default_first_reg": 4357,   # ← different starting point!
        "default_slave_id": 1,
    },
}

