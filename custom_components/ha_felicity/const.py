"""Constants for the Eastron SDM630 integration."""
from typing import Dict

DOMAIN = "ha_sdm630"

# Connection types
CONF_CONNECTION_TYPE = "connection_type"
CONNECTION_TYPE_SERIAL = "serial"
CONNECTION_TYPE_TCP = "tcp"

# Common settings
CONF_SLAVE_ID = "slave_id"
CONF_NAME = "name"
CONF_REGISTER_SET = "register_set"

# Serial settings
CONF_SERIAL_PORT = "serial_port"
CONF_BAUDRATE = "baudrate"
CONF_PARITY = "parity"
CONF_STOPBITS = "stopbits"
CONF_BYTESIZE = "bytesize"

# TCP settings
CONF_HOST = "host"
CONF_PORT = "port"

# Defaults
DEFAULT_SLAVE_ID = 1
DEFAULT_BAUDRATE = 9600
DEFAULT_TCP_PORT = 502
DEFAULT_REGISTER_SET = "basic"
DEFAULT_STOPBITS = 1
DEFAULT_BYTESIZE = 8
DEFAULT_PARITY = "N"

# Register set options
REGISTER_SET_BASIC = "basic"
REGISTER_SET_BASIC_PLUS = "basic_plus"
REGISTER_SET_FULL = "full"

# Define the three register sets
_BASIC_REGISTERS = {
    # Essential sensors — fast polling
    "phase_1_l_n_volts": {"address": 0, "name": "Phase 1 L/N Volts", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 2},
    "phase_2_l_n_volts": {"address": 2, "name": "Phase 2 L/N Volts", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 2},
    "phase_3_l_n_volts": {"address": 4, "name": "Phase 3 L/N Volts", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 2},
    "phase_1_current": {"address": 6, "name": "Phase 1 Current", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 2},
    "phase_2_current": {"address": 8, "name": "Phase 2 Current", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 2},
    "phase_3_current": {"address": 10, "name": "Phase 3 Current", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 2},
    "phase_1_power": {"address": 12, "name": "Phase 1 Power", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 2},
    "phase_2_power": {"address": 14, "name": "Phase 2 Power", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 2},
    "phase_3_power": {"address": 16, "name": "Phase 3 Power", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 2},
    "total_system_power": {"address": 52, "name": "Total Power", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 2},
    "frequency": {"address": 70, "name": "Frequency", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 2},
    "import_energy": {"address": 72, "name": "Import Energy", "unit": "kWh", "device_class": "energy", "state_class": "total_increasing", "precision": 2},
    "export_energy": {"address": 74, "name": "Export Energy", "unit": "kWh", "device_class": "energy", "state_class": "total_increasing", "precision": 2},
    "total_kwh": {"address": 342, "name": "Total kWh", "unit": "kWh", "device_class": "energy", "state_class": "total", "precision": 2},
}

_BASIC_PLUS_REGISTERS = {
    **_BASIC_REGISTERS,
    "phase_1_volt_amps": {"address": 18, "name": "Phase 1 VA", "unit": "VA", "device_class": "apparent_power", "state_class": "measurement", "precision": 2},
    "phase_2_volt_amps": {"address": 20, "name": "Phase 2 VA", "unit": "VA", "device_class": "apparent_power", "state_class": "measurement", "precision": 2},
    "phase_3_volt_amps": {"address": 22, "name": "Phase 3 VA", "unit": "VA", "device_class": "apparent_power", "state_class": "measurement", "precision": 2},
    "phase_1_power_factor": {"address": 30, "name": "Phase 1 Power Factor", "unit": None, "device_class": "power_factor", "state_class": "measurement", "precision": 3},
    "phase_2_power_factor": {"address": 32, "name": "Phase 2 Power Factor", "unit": None, "device_class": "power_factor", "state_class": "measurement", "precision": 3},
    "phase_3_power_factor": {"address": 34, "name": "Phase 3 Power Factor", "unit": None, "device_class": "power_factor", "state_class": "measurement", "precision": 3},
    "neutral_current": {"address": 224, "name": "Neutral Current", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 2},
    "line_1_to_line_2_volts": {"address": 200, "name": "Line 1 to Line 2 Volts", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 2},
    "line_2_to_line_3_volts": {"address": 202, "name": "Line 2 to Line 3 Volts", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 2},
    "line_3_to_line_1_volts": {"address": 204, "name": "Line 3 to Line 1 Volts", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 2},
}

_FULL_REGISTERS = {
    **_BASIC_PLUS_REGISTERS,
    "phase_1_volt_amps_reactive": {"address": 24, "name": "Phase 1 Volt Amps Reactive", "unit": "VAr", "device_class": "reactive_power", "state_class": "measurement", "precision": 2},
    "phase_2_volt_amps_reactive": {"address": 26, "name": "Phase 2 Volt Amps Reactive", "unit": "VAr", "device_class": "reactive_power", "state_class": "measurement", "precision": 2},
    "phase_3_volt_amps_reactive": {"address": 28, "name": "Phase 3 Volt Amps Reactive", "unit": "VAr", "device_class": "reactive_power", "state_class": "measurement", "precision": 2},
    "phase_1_phase_angle": {"address": 36, "name": "Phase 1 Phase Angle", "unit": "deg", "state_class": "measurement", "precision": 2},
    "phase_2_phase_angle": {"address": 38, "name": "Phase 2 Phase Angle", "unit": "deg", "state_class": "measurement", "precision": 2},
    "phase_3_phase_angle": {"address": 40, "name": "Phase 3 Phase Angle", "unit": "deg", "state_class": "measurement", "precision": 2},
    "average_line_to_neutral_volts": {"address": 42, "name": "Average Line to Neutral Volts", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 2},
    "average_line_current": {"address": 46, "name": "Average Line Current", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 2},
    "sum_of_line_currents": {"address": 48, "name": "Sum of Line Currents", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 2},
    "total_system_volt_amps": {"address": 56, "name": "Total System Volt Amps", "unit": "VA", "device_class": "apparent_power", "state_class": "measurement", "precision": 2},
    "total_system_var": {"address": 60, "name": "Total System VAr", "unit": "VAr", "device_class": "reactive_power", "state_class": "measurement", "precision": 2},
    "total_system_power_factor": {"address": 62, "name": "Total System Power Factor", "unit": None, "device_class": "power_factor", "state_class": "measurement", "precision": 3},
    "total_system_phase_angle": {"address": 66, "name": "Total System Phase Angle", "unit": "deg", "state_class": "measurement", "precision": 2},
    "import_varh_since_last_reset": {"address": 76, "name": "Import VArh Since Last Reset", "unit": "kVArh", "device_class": "energy", "state_class": "total_increasing", "precision": 2},
    "export_varh_since_last_reset": {"address": 78, "name": "Export VArh Since Last Reset", "unit": "kVArh", "device_class": "energy", "state_class": "total_increasing", "precision": 2},
    "vah_since_last_reset": {"address": 80, "name": "VAh Since Last Reset", "unit": "kVAh", "device_class": "energy", "state_class": "total_increasing", "precision": 2},
    "ah_since_last_reset": {"address": 82, "name": "Ah Since Last Reset", "unit": "Ah", "state_class": "total_increasing", "precision": 2},
    "total_system_power_demand": {"address": 84, "name": "Total System Power Demand", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 2},
    "maximum_total_system_power_demand": {"address": 86, "name": "Maximum Total System Power Demand", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 2},
    "total_system_va_demand": {"address": 100, "name": "Total System VA Demand", "unit": "VA", "device_class": "apparent_power", "state_class": "measurement", "precision": 2},
    "maximum_total_system_va_demand": {"address": 102, "name": "Maximum Total System VA Demand", "unit": "VA", "device_class": "apparent_power", "state_class": "measurement", "precision": 2},
    "neutral_current_demand": {"address": 104, "name": "Neutral Current Demand", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 2},
    "maximum_neutral_current_demand": {"address": 106, "name": "Maximum Neutral Current Demand", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 2},
    "phase_1_l_n_volts_thd": {"address": 234, "name": "Phase 1 L/N Volts THD", "unit": "%", "state_class": "measurement", "precision": 2},
    "phase_2_l_n_volts_thd": {"address": 236, "name": "Phase 2 L/N Volts THD", "unit": "%", "state_class": "measurement", "precision": 2},
    "phase_3_l_n_volts_thd": {"address": 238, "name": "Phase 3 L/N Volts THD", "unit": "%", "state_class": "measurement", "precision": 2},
    "phase_1_current_thd": {"address": 240, "name": "Phase 1 Current THD", "unit": "%", "state_class": "measurement", "precision": 2},
    "phase_2_current_thd": {"address": 242, "name": "Phase 2 Current THD", "unit": "%", "state_class": "measurement", "precision": 2},
    "phase_3_current_thd": {"address": 244, "name": "Phase 3 Current THD", "unit": "%", "state_class": "measurement", "precision": 2},
    "average_line_to_neutral_volts_thd": {"address": 248, "name": "Average Line to Neutral Volts THD", "unit": "%", "state_class": "measurement", "precision": 2},
    "average_line_current_thd": {"address": 250, "name": "Average Line Current THD", "unit": "%", "state_class": "measurement", "precision": 2},
    "total_system_power_factor_5": {"address": 254, "name": "Total System Power Factor (5)", "unit": "deg", "state_class": "measurement", "precision": 2},
    "phase_1_current_demand": {"address": 258, "name": "Phase 1 Current Demand", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 2},
    "phase_2_current_demand": {"address": 260, "name": "Phase 2 Current Demand", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 2},
    "phase_3_current_demand": {"address": 262, "name": "Phase 3 Current Demand", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 2},
    "maximum_phase_1_current_demand": {"address": 264, "name": "Maximum Phase 1 Current Demand", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 2},
    "maximum_phase_2_current_demand": {"address": 266, "name": "Maximum Phase 2 Current Demand", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 2},
    "maximum_phase_3_current_demand": {"address": 268, "name": "Maximum Phase 3 Current Demand", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 2},
    "line_1_to_line_2_volts_thd": {"address": 334, "name": "Line 1 to Line 2 Volts THD", "unit": "%", "state_class": "measurement", "precision": 2},
    "line_2_to_line_3_volts_thd": {"address": 336, "name": "Line 2 to Line 3 Volts THD", "unit": "%", "state_class": "measurement", "precision": 2},
    "line_3_to_line_1_volts_thd": {"address": 338, "name": "Line 3 to Line 1 Volts THD", "unit": "%", "state_class": "measurement", "precision": 2},
    "average_line_to_line_volts_thd": {"address": 340, "name": "Average Line to Line Volts THD", "unit": "%", "state_class": "measurement", "precision": 2},
    "total_kvarh": {"address": 344, "name": "Total kVArh", "unit": "kVArh", "device_class": "energy", "state_class": "total", "precision": 2},
    "l1_import_active_energy": {"address": 346, "name": "L1 Import Active Energy", "unit": "kWh", "device_class": "energy", "state_class": "total_increasing", "precision": 2},
    "l2_import_active_energy": {"address": 348, "name": "L2 Import Active Energy", "unit": "kWh", "device_class": "energy", "state_class": "total_increasing", "precision": 2},
    "l3_import_active_energy": {"address": 350, "name": "L3 Import Active Energy", "unit": "kWh", "device_class": "energy", "state_class": "total_increasing", "precision": 2},
    "l1_export_active_energy": {"address": 352, "name": "L1 Export Active Energy", "unit": "kWh", "device_class": "energy", "state_class": "total_increasing", "precision": 2},
    "l2_export_active_energy": {"address": 354, "name": "L2 Export Active Energy", "unit": "kWh", "device_class": "energy", "state_class": "total_increasing", "precision": 2},
    "l3_export_active_energy": {"address": 356, "name": "L3 Export Active Energy", "unit": "kWh", "device_class": "energy", "state_class": "total_increasing", "precision": 2},
    "l1_total_active_energy": {"address": 358, "name": "L1 Total Active Energy", "unit": "kWh", "device_class": "energy", "state_class": "total", "precision": 2},
    "l2_total_active_energy": {"address": 360, "name": "L2 Total Active Energy", "unit": "kWh", "device_class": "energy", "state_class": "total", "precision": 2},
    "l3_total_active_energy": {"address": 362, "name": "L3 Total Active Energy", "unit": "kWh", "device_class": "energy", "state_class": "total", "precision": 2},
    "l1_import_reactive_energy": {"address": 364, "name": "L1 Import Reactive Energy", "unit": "kVArh", "device_class": "energy", "state_class": "total_increasing", "precision": 2},
    "l2_import_reactive_energy": {"address": 366, "name": "L2 Import Reactive Energy", "unit": "kVArh", "device_class": "energy", "state_class": "total_increasing", "precision": 2},
    "l3_import_reactive_energy": {"address": 368, "name": "L3 Import Reactive Energy", "unit": "kVArh", "device_class": "energy", "state_class": "total_increasing", "precision": 2},
    "l1_export_reactive_energy": {"address": 370, "name": "L1 Export Reactive Energy", "unit": "kVArh", "device_class": "energy", "state_class": "total_increasing", "precision": 2},
    "l2_export_reactive_energy": {"address": 372, "name": "L2 Export Reactive Energy", "unit": "kVArh", "device_class": "energy", "state_class": "total_increasing", "precision": 2},
    "l3_export_reactive_energy": {"address": 374, "name": "L3 Export Reactive Energy", "unit": "kVArh", "device_class": "energy", "state_class": "total_increasing", "precision": 2},
    "l1_total_reactive_energy": {"address": 376, "name": "L1 Total Reactive Energy", "unit": "kVArh", "device_class": "energy", "state_class": "total", "precision": 2},
    "l2_total_reactive_energy": {"address": 378, "name": "L2 Total Reactive Energy", "unit": "kVArh", "device_class": "energy", "state_class": "total", "precision": 2},
    "l3_total_reactive_energy": {"address": 380, "name": "L3 Total Reactive Energy", "unit": "kVArh", "device_class": "energy", "state_class": "total", "precision": 2},
}

REGISTER_SETS = {
    REGISTER_SET_BASIC: _BASIC_REGISTERS,
    REGISTER_SET_BASIC_PLUS: _BASIC_PLUS_REGISTERS,
    REGISTER_SET_FULL: _FULL_REGISTERS,
}
