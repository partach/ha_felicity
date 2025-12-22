"""Constants for the Felicity integration."""

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
INVERTER_MODEL_IVGM = "T-REX-10KLP3G01"  # our current one

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
DEFAULT_INVERTER_MODEL = INVERTER_MODEL_IVGM
DEFAULT_FIRST_REG = 4353

# Precision and index based on the "Rate/Magnification/Scale" column
# 0 = dont process or packed
# 1 = /10 → precision 1, index 1;
# 2 = /100 → precision 2, index 2; 
# 3 = signed index;
# 4 = energy high/low index; (obsolete)
# 5 = faults/warnings/modes/flags index; (doesnt do anything port procesing)
# 6 = time index; (obsolete)
# 7 = % index (doesnt really do anything yet)
# 3 = signed index and /10; 

_REGISTERS = {
    "setting_data_sn": {"address": 4352, "name": "Setting Data Sn", "precision": 0, "index": 0},
    "working_mode": {"address": 4353, "name": "Working Mode", "precision": 0, "index": 0, "type": "select", "options": ["Power On", "Standby", "Bypass", "Off-grid", "Fault", "Line", "PV Charge"]},
    "warning_state_1": {"address": 4354, "name": "Warning State 1", "precision": 0, "index": 0, "size": 2, "endian": "big"},
    "warning_state_2": {"address": 4356, "name": "Warning State 2", "precision": 0, "index": 0, "size": 2, "endian": "big"},
    "warning_state_3": {"address": 4358, "name": "Warning State 3", "precision": 0, "index": 0, "size": 2, "endian": "big"},
    "fault_code": {"address": 4360, "name": "Fault Code", "precision": 0, "index": 0},
    "ac_input_voltage": {"address": 4361, "name": "Ac Input Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_input_current": {"address": 4362, "name": "Ac Input Current", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_input_frequency": {"address": 4363, "name": "Ac Input Frequency", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 2, "index": 2},
    "ac_input_power": {"address": 4364, "name": "Ac Input Power", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
    "battery_voltage": {"address": 4365, "name": "Battery Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "battery_current": {"address": 4366, "name": "Battery Current", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 3},
    "battery_power": {"address": 4367, "name": "Battery Power", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
    "battery_capacity": {"address": 4368, "name": "Battery Capacity", "unit": "%", "device_class": "battery", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_output_voltage": {"address": 4369, "name": "Ac Output Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_output_current": {"address": 4370, "name": "Ac Output Current", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_output_frequency": {"address": 4371, "name": "Ac Output Frequency", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 2, "index": 2},
    "ac_output_active_power": {"address": 4372, "name": "Ac Output Active Power", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "ac_output_apparent_power": {"address": 4373, "name": "Ac Output Apparent Power", "unit": "VA", "device_class": "apparent_power", "state_class": "measurement", "precision": 0, "index": 0},
    "load_percentage": {"address": 4374, "name": "Load Percentage", "unit": "%", "state_class": "measurement", "precision": 0, "index": 7},
    "pv_input_voltage": {"address": 4375, "name": "Pv Input Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "pv_input_current": {"address": 4376, "name": "Pv Input Current", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 1},
    "pv_input_power": {"address": 4377, "name": "Pv Input Power", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "pv2_input_voltage": {"address": 4378, "name": "Pv2 Input Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "pv2_input_current": {"address": 4379, "name": "Pv2 Input Current", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 1},
    "pv2_input_power": {"address": 4380, "name": "Pv2 Input Power", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "pv3_input_voltage": {"address": 4381, "name": "Pv3 Input Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "pv3_input_current": {"address": 4382, "name": "Pv3 Input Current", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 1},
    "pv3_input_power": {"address": 4383, "name": "Pv3 Input Power", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "ac_input_voltage_l2": {"address": 4384, "name": "Ac Input Voltage L2", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_input_current_l2": {"address": 4385, "name": "Ac Input Current L2", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_input_frequency_l2": {"address": 4386, "name": "Ac Input Frequency L2", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 2, "index": 2},
    "ac_input_power_l2": {"address": 4387, "name": "Ac Input Power L2", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
    "ac_input_voltage_l3": {"address": 4388, "name": "Ac Input Voltage L3", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_input_current_l3": {"address": 4389, "name": "Ac Input Current L3", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_input_frequency_l3": {"address": 4390, "name": "Ac Input Frequency L3", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 2, "index": 2},
    "ac_input_power_l3": {"address": 4391, "name": "Ac Input Power L3", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
    "total_ac_input_power": {"address": 4392, "name": "Total Ac Input Power", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3, "size": 2, "endian": "big"},
    "ac_output_voltage_l2": {"address": 4394, "name": "Ac Output Voltage L2", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_output_current_l2": {"address": 4395, "name": "Ac Output Current L2", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_output_frequency_l2": {"address": 4396, "name": "Ac Output Frequency L2", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 2, "index": 2},
    "ac_output_active_power_l2": {"address": 4397, "name": "Ac Output Active Power L2", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "ac_output_apparent_power_l2": {"address": 4398, "name": "Ac Output Apparent Power L2", "unit": "VA", "device_class": "apparent_power", "state_class": "measurement", "precision": 0, "index": 0},
    "ac_output_voltage_l3": {"address": 4399, "name": "Ac Output Voltage L3", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_output_current_l3": {"address": 4400, "name": "Ac Output Current L3", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_output_frequency_l3": {"address": 4401, "name": "Ac Output Frequency L3", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 2, "index": 2},
    "ac_output_active_power_l3": {"address": 4402, "name": "Ac Output Active Power L3", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "ac_output_apparent_power_l3": {"address": 4403, "name": "Ac Output Apparent Power L3", "unit": "VA", "device_class": "apparent_power", "state_class": "measurement", "precision": 0, "index": 0},
    "total_ac_output_active_power": {"address": 4404, "name": "Total Ac Output Active Power", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0, "size": 2, "endian": "big"},
    "total_ac_output_apparent_power": {"address": 4406, "name": "Total Ac Output Apparent Power", "unit": "VA", "device_class": "apparent_power", "state_class": "measurement", "precision": 0, "index": 0, "size": 2, "endian": "big"},
    "invert_voltage": {"address": 4408, "name": "Invert Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "invert_current": {"address": 4409, "name": "Invert Current", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 3},
    "invert_active_power": {"address": 4410, "name": "Invert Active Power", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
    "invert_voltage_l2": {"address": 4411, "name": "Invert Voltage L2", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "invert_current_l2": {"address": 4412, "name": "Invert Current L2", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 3},
    "invert_active_power_l2": {"address": 4413, "name": "Invert Active Power L2", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
    "invert_voltage_l3": {"address": 4414, "name": "Invert Voltage L3", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "invert_current_l3": {"address": 4415, "name": "Invert Current L3", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 3},
    "invert_active_power_l3": {"address": 4416, "name": "Invert Active Power L3", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
    "ac_input_mid_voltage": {"address": 4417, "name": "Ac Input Mid Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_input_mid_voltage_l2": {"address": 4418, "name": "Ac Input Mid Voltage L2", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_input_mid_voltage_l3": {"address": 4419, "name": "Ac Input Mid Voltage L3", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "p_bus_voltage_master": {"address": 4420, "name": "P Bus Voltage Master", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "n_bus_voltage_master": {"address": 4421, "name": "N Bus Voltage Master", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "p_dc_converter_voltage": {"address": 4422, "name": "P Dc Converter Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "n_dc_converter_voltage": {"address": 4423, "name": "N Dc Converter Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "p_dc_dc_current": {"address": 4424, "name": "P Dc/Dc Current", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 3},
    "n_dc_dc_current": {"address": 4425, "name": "N Dc/Dc Current", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 3},
    "inner_temperature_1": {"address": 4426, "name": "Inner Temperature 1", "unit": "°C", "device_class": "temperature", "state_class": "measurement", "precision": 1, "index": 1},
    "inner_temperature_2": {"address": 4427, "name": "Inner Temperature 2", "unit": "°C", "device_class": "temperature", "state_class": "measurement", "precision": 1, "index": 1},
    "heatsink_temperature_1": {"address": 4428, "name": "Heatsink Temperature 1", "unit": "°C", "device_class": "temperature", "state_class": "measurement", "precision": 1, "index": 1},
    "heatsink_temperature_2": {"address": 4429, "name": "Heatsink Temperature 2", "unit": "°C", "device_class": "temperature", "state_class": "measurement", "precision": 1, "index": 1},
    "heatsink_temperature_3": {"address": 4430, "name": "Heatsink Temperature 3", "unit": "°C", "device_class": "temperature", "state_class": "measurement", "precision": 1, "index": 1},
    "heatsink_temperature_4": {"address": 4431, "name": "Heatsink Temperature 4", "unit": "°C", "device_class": "temperature", "state_class": "measurement", "precision": 1, "index": 1},
    "heatsink_temperature_5": {"address": 4432, "name": "Heatsink Temperature 5", "unit": "°C", "device_class": "temperature", "state_class": "measurement", "precision": 1, "index": 1},
    "heatsink_temperature_6": {"address": 4433, "name": "Heatsink Temperature 6", "unit": "°C", "device_class": "temperature", "state_class": "measurement", "precision": 1, "index": 1},
    "time_year_month": {"address": 4434, "name": "Time Year-Month", "precision": 0, "index": 99},
    "time_day_hour": {"address": 4435, "name": "Time Day-Hour", "precision": 0, "index": 99},
    "time_minute_second": {"address": 4436, "name": "Time Minute-Second", "precision": 0, "index": 99},
    "time_week": {"address": 4437, "name": "Time Week", "precision": 0, "index": 99},
    "pv_generated_energy_total": {"address": 4438, "name": "Pv Generated Energy Inquiry Total-High 32 Bit", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 0, "size": 4, "endian": "big"},
    "pv_generated_energy_year": {"address": 4442, "name": "Pv Generated Energy Inquiry Year", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4, "size": 2, "endian": "big"},
    "pv_generated_energy_month": {"address": 4444, "name": "Pv Generated Energy Inquiry Month", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4, "size": 2, "endian": "big"},
    "pv_generated_energy_day": {"address": 4446, "name": "Pv Generated Energy Inquiry Day", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4, "size": 2, "endian": "big"},
    "load_consumption_energy_total": {"address": 4448, "name": "Load Consumption Energy Inquiry Total-High 32 Bit", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 0, "size": 4, "endian": "big"},
    "load_consumption_energy_year": {"address": 4452, "name": "Load Consumption Energy Inquiry Year", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4, "size": 2, "endian": "big"},
    "load_consumption_energy_month": {"address": 4454, "name": "Load Consumption Energy Inquiry Month", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4, "size": 2, "endian": "big"},
    "load_consumption_energy_day": {"address": 4456, "name": "Load Consumption Energy Inquiry Day", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4, "size": 2, "endian": "big"},
    "ac_input_energy_total": {"address": 4458, "name": "Ac Input Energy Inquiry Total-High 32 Bit", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 0, "size": 4, "endian": "big"},
    "ac_input_energy_year": {"address": 4462, "name": "Ac Input Energy Inquiry Year", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 0, "size": 2, "endian": "big"},
    "ac_input_energy_month": {"address": 4464, "name": "Ac Input Energy Inquiry Month", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 0, "size": 2, "endian": "big"},
    "ac_input_energy_day": {"address": 4466, "name": "Ac Input Energy Inquiry Day", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 0, "size": 2, "endian": "big"},
    "ac_generated_energy_total": {"address": 4468, "name": "Ac Generated Energy Inquiry Total-High 32 Bit", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4, "size": 4, "endian": "big"},
    "ac_generated_energy_year": {"address": 4472, "name": "Ac Generated Energy Inquiry Year", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4, "size": 2, "endian": "big"},
    "ac_generated_energy_month": {"address": 4474, "name": "Ac Generated Energy Inquiry Month", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4, "size": 2, "endian": "big"},
    "ac_generated_energy_day": {"address": 4476, "name": "Ac Generated Energy Inquiry Day", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4, "size": 2, "endian": "big"},
    "battery_charged_energy_total": {"address": 4478, "name": "Battery Charged Energy Inquiry Total-High 32 Bit", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 0, "size": 4, "endian": "big"},
    "battery_charged_energy_year": {"address": 4482, "name": "Battery Charged Energy Inquiry Year", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4, "size": 2, "endian": "big"},
    "battery_charged_energy_month": {"address": 4484, "name": "Battery Charged Energy Inquiry Month", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4, "size": 2, "endian": "big"},
    "battery_charged_energy_day": {"address": 4486, "name": "Battery Charged Energy Inquiry Day", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4, "size": 2, "endian": "big"},
    "battery_discharged_energy_total": {"address": 4488, "name": "Battery Discharged Energy Inquiry Total-High 32 Bit", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 0, "size": 4, "endian": "big"},
    "battery_discharged_energy_year": {"address": 4492, "name": "Battery Discharged Energy Inquiry Year", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4, "size": 2, "endian": "big"},
    "battery_discharged_energy_month": {"address": 4494, "name": "Battery Discharged Energy Inquiry Month", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4, "size": 2, "endian": "big"},
    "battery_discharged_energy_day": {"address": 4496, "name": "Battery Discharged Energy Inquiry Day", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4, "size": 2, "endian": "big"},
    "status_bit": {"address": 4498, "name": "Status Bit", "precision": 0, "index": 5},
    "p_bus_voltage_slv": {"address": 4499, "name": "P Bus Voltage_Slv", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "n_bus_voltage_slv": {"address": 4500, "name": "N Bus Voltage_Slv", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "line_power_conversion": {"address": 4501, "name": "Linepowerconversion", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0, "size": 2, "endian": "big"},
    "load_power_conversion": {"address": 4503, "name": "Loadpowerconversion", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0, "size": 2, "endian": "big"},
    "bat_power_conversion": {"address": 4505, "name": "Batpowerconversion", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "pv_power_conversion": {"address": 4506, "name": "Pvpowerconversion", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "power_flow_msg": {"address": 4507, "name": "Powerflowmsg", "precision": 0, "index": 5},
    "parallel_system_state": {"address": 4508, "name": "Parallel system state", "precision": 0, "index": 5},
    "load_power_line_side": {"address": 4509, "name": "Loadpower_Lineside", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0, "size": 2, "endian": "big"},
    "g_uw_exist_num_parallel": {"address": 4511, "name": "G_Uwexistnum_Parallel", "precision": 0, "index": 0},
    "log_type": {"address": 4516, "name": "Log Type", "precision": 0, "index": 5},
    "log_index": {"address": 4517, "name": "Log Index", "precision": 0, "index": 0},
    "log_status": {"address": 4518, "name": "Log Status", "precision": 0, "index": 5},
    "log_id": {"address": 4519, "name": "Log Id", "precision": 0, "index": 5},
    "log_time_year_month": {"address": 4520, "name": "Log Time Year-Month", "precision": 0, "index": 99},
    "log_time_day_hour": {"address": 4521, "name": "Log Time Day-Hour", "precision": 0, "index": 99},
    "log_time_minute_second": {"address": 4522, "name": "Log Time Minute-Second", "precision": 0, "index": 99},
    "ac_input_voltage_secondary": {"address": 4523, "name": "Ac Input Voltage secondary", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_input_frequency_secondary": {"address": 4524, "name": "Ac Input Frequency secondary", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 2, "index": 2},
    "ac_input_power_secondary": {"address": 4525, "name": "Ac Input Power secondary", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
    "battery_voltage_secondary": {"address": 4526, "name": "Battery Voltage secondary", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "battery_power_secondary": {"address": 4527, "name": "Battery Power secondary", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
    "battery_capacity_secondary": {"address": 4528, "name": "Battery Capacity secondary", "unit": "%", "device_class": "battery", "state_class": "measurement", "precision": 0, "index": 1},
    "ac_output_voltage_secondary": {"address": 4529, "name": "Ac Output Voltage secondary", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_output_frequency_secondary": {"address": 4530, "name": "Ac Output Frequency secondary", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 2, "index": 2},
    "ac_output_active_power_secondary": {"address": 4531, "name": "Ac Output Active Power  secondary", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "ac_output_apparent_power_secondary": {"address": 4532, "name": "Ac Output Apparent Power secondary", "unit": "VA", "device_class": "apparent_power", "state_class": "measurement", "precision": 0, "index": 0},
    "load_percentage_secondary": {"address": 4533, "name": "Load Percentage secondary", "unit": "%", "state_class": "measurement", "precision": 0, "index": 7},
    "pv_input_voltage_secondary": {"address": 4534, "name": "Pv Input Voltage secondary", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "pv_input_power_secondary": {"address": 4535, "name": "Pv Input Power secondary", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "pv2_input_voltage_secondary": {"address": 4536, "name": "Pv2 Input Voltage  secondary", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "pv2_input_power_secondary": {"address": 4537, "name": "Pv2 Input Power secondary", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "pv3_input_voltage_secondary": {"address": 4538, "name": "Pv3 Input Voltage secondary", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "pv3_input_power_secondary": {"address": 4539, "name": "Pv3 Input Power  secondary", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "ac_input_voltage_l2_secondary": {"address": 4540, "name": "Ac Input Voltage L2 secondary", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_input_frequency_l2_secondary": {"address": 4541, "name": "Ac Input Frequency L2 secondary", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 2, "index": 2},
    "ac_input_power_l2_secondary": {"address": 4542, "name": "Ac Input Power L2 secondary", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
    "ac_input_voltage_l3_secondary": {"address": 4543, "name": "Ac Input Voltage L3 secondary", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_input_frequency_l3_secondary": {"address": 4544, "name": "Ac Input Frequency L3 secondary", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 2, "index": 2},
    "ac_input_power_l3_secondary": {"address": 4545, "name": "Ac Input Power L3 secondary", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
    "ac_output_voltage_l2_secondary": {"address": 4546, "name": "Ac Output Voltage L2 secondary", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_output_frequency_l2_secondary": {"address": 4547, "name": "Ac Output Frequency L2 secondary", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 2, "index": 2},
    "ac_output_active_power_l2_secondary": {"address": 4548, "name": "Ac Output Active Power L2 secondary", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "ac_output_apparent_power_l2_secondary": {"address": 4549, "name": "Ac Output Apparent Power L2 secondary", "unit": "VA", "device_class": "apparent_power", "state_class": "measurement", "precision": 0, "index": 0},
    "ac_output_voltage_l3_secondary": {"address": 4550, "name": "Ac Output Voltage L3 secondary", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_output_frequency_l3_secondary": {"address": 4551, "name": "Ac Output Frequency L3 secondary", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 2, "index": 2},
    "ac_output_active_power_l3_secondary": {"address": 4552, "name": "Ac Output Active Power L3 secondary", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "ac_output_apparent_power_l3_secondary": {"address": 4553, "name": "Ac Output Apparent Power L3 secondary", "unit": "VA", "device_class": "apparent_power", "state_class": "measurement", "precision": 0, "index": 0},
    "p_bus_voltage": {"address": 4554, "name": "P Bus Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "n_bus_voltage": {"address": 4555, "name": "N Bus Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "p_dc_dc_current_secondary": {"address": 4556, "name": "P Dc/Dc Current  secondary", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 8},
    "n_dc_dc_current_secondary": {"address": 4557, "name": "N Dc/Dc Current  secondary", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 8},
    "max_inner_temperature": {"address": 4558, "name": "Max. Inner Temperature", "unit": "°C", "device_class": "temperature", "state_class": "measurement", "precision": 1, "index": 8},
    "max_heat_sink_temperature": {"address": 4559, "name": "Max. Heat-Sink Temperature", "unit": "°C", "device_class": "temperature", "state_class": "measurement", "precision": 1, "index": 8},
    "charge_voltage_limit": {"address": 4608, "name": "Chargevoltagelimit", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "discharge_voltage_limit": {"address": 4609, "name": "Dischargevoltagelimit", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "charge_current_limit": {"address": 4610, "name": "Chargecurrentlimit", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 1},
    "discharge_current_limit": {"address": 4611, "name": "Dischargecurrentlimit", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 1},
    "bms_status_lo": {"address": 4612, "name": "Bmsstatuslo", "precision": 0, "index": 5},
    "bms_status_hi": {"address": 4613, "name": "Bmsstatushi", "precision": 0, "index": 5},
    "fault_flag_lo": {"address": 4614, "name": "Faultflaglo", "precision": 0, "index": 5},
    "fault_flag_hi": {"address": 4615, "name": "Faultflaghi", "precision": 0, "index": 5},
    "alarm_flag_lo": {"address": 4616, "name": "Alarmflaglo", "precision": 0, "index": 5},
    "alarm_flag_hi": {"address": 4617, "name": "Alarmflaghi", "precision": 0, "index": 5},
    "notice_flag_low": {"address": 4618, "name": "Noticeflaglow", "precision": 0, "index": 5},
    "notice_flag_high": {"address": 4619, "name": "Noticeflaghigh", "precision": 0, "index": 5},
    "total_current": {"address": 4620, "name": "Totalcurrent", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 1},
    "total_voltage": {"address": 4621, "name": "Totalvoltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 2, "index": 2},
    "total_soc": {"address": 4624, "name": "Totalsoc", "unit": "%", "device_class": "battery", "state_class": "measurement", "precision": 1, "index": 1},
    "total_soh": {"address": 4625, "name": "Totalsoh", "unit": "%", "state_class": "measurement", "precision": 1, "index": 1},
    "total_capacity_high": {"address": 4626, "name": "Totalcapacityhigh", "unit": "mAH", "precision": 0, "index": 0},
    "total_capacity_low": {"address": 4627, "name": "Totalcapacitylow", "unit": "mAH", "precision": 0, "index": 0},
    "parallel_number": {"address": 4628, "name": "Parallelnumber", "precision": 0, "index": 0},
    "parallel_status": {"address": 4629, "name": "Parallelstatus", "precision": 0, "index": 5},
    "line_load_consumption_energy_total": {"address": 4645, "name": "Lineload Consumption Energy Inquiry Total-High 32 Bit", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 0, "size": 4, "endian": "big"},
    "line_load_consumption_energy_year": {"address": 4649, "name": "Lineload Consumption Energy Inquiry Year", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4, "size": 2, "endian": "big"},
    "line_load_consumption_energy_month": {"address": 4651, "name": "Lineload Consumption Energy Inquiry Month", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4, "size": 2, "endian": "big"},
    "line_load_consumption_energy_day": {"address": 4653, "name": "Lineload Consumption Energy Inquiry Day", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4, "size": 2, "endian": "big"},
    "total_load_consumption_energy_total": {"address": 4655, "name": "Totalload Consumption Energy Inquiry Total-High 32 Bit", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4, "size": 4, "endian": "big"},
    "total_load_consumption_energy_year": {"address": 4659, "name": "Totalload Consumption Energy Inquiry Year", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4, "size": 2, "endian": "big"},
    "total_load_consumption_energy_month": {"address": 4661, "name": "Totalload Consumption Energy Inquiry Month", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4, "size": 2, "endian": "big"},
    "total_load_consumption_energy_day": {"address": 4663, "name": "Totalload Consumption Energy Inquiry Day", "unit": "Wh", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4, "size": 2, "endian": "big"},

    "operating_mode": {"address": 8451,"name": "Operating Mode","precision": 0,"index": 0,"type": "select","options": ["General mode (self-use, load priority)","Backup mode (grid-tied, no battery discharge)","Economic mode (scheduled charge-discharge)"]},

    # Time setting (writeable)
    "time_set_year_month": {"address": 8560, "name": "Time Setting Year-Month", "precision": 0, "index": 99},
    "time_set_day_hour": {"address": 8561, "name": "Time Setting Day-Hour", "precision": 0, "index": 99},
    "time_set_minute_second": {"address": 8562, "name": "Time Setting Minute-Second", "precision": 0, "index": 99},
    "time_set_week": {"address": 8563, "name": "Time Setting Week", "precision": 0, "index": 99},
    
    # Economic Mode Rules (4 rules, each 9 registers)
    "econ_rule_1_enable": {"address": 8568, "name": "Economic Mode Rule 1 Enable", "precision": 0, "index": 0, "type": "select", "options": ["Disabled", "Charge", "Discharge"]},
    "econ_rule_1_start_time": {"address": 8569, "name": "Rule 1 Start Time", "precision": 0, "index": 0, "type": "time"},
    "econ_rule_1_stop_time": {"address": 8570, "name": "Rule 1 Stop Time", "precision": 0, "index": 0, "type": "time"},
    "econ_rule_1_start_day": {"address": 8571, "name": "Rule 1 Start Date", "precision": 0, "index": 0, "type": "date"},
    "econ_rule_1_stop_day": {"address": 8572, "name": "Rule 1 Stop Date", "precision": 0, "index": 0, "type": "date"},
    "econ_rule_1_effective_week": {"address": 8573, "name": "Rule 1 Effective Days", "precision": 0, "index": 0, "type": "select_multi", "options": ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]},
    "econ_rule_1_voltage": {"address": 8574, "name": "Rule 1 Voltage", "unit": "V", "device_class": "voltage", "precision": 1, "index": 1, "type": "number", "min": 50.0, "max": 60.0, "step": 1},
    "econ_rule_1_soc": {"address": 8575, "name": "Rule 1 SOC", "unit": "%", "device_class": "battery", "precision": 0, "index": 0, "type": "number", "min": 0, "max": 100, "step": 1},
    "econ_rule_1_power": {"address": 8576, "name": "Rule 1 Power", "unit": "W", "device_class": "power", "precision": 0, "index": 0, "type": "number", "min": 0, "max": 10000, "step": 100},
    
    "econ_rule_2_enable": {"address": 8577, "name": "Economic Mode Rule 2 Enable", "precision": 0, "index": 0,"type": "select", "options": ["Disabled", "Charge", "Discharge"]},
    "econ_rule_2_start_time": {"address": 8578, "name": "Rule 2 Start Time", "precision": 0, "index": 0, "type": "time"},
    "econ_rule_2_stop_time": {"address": 8579, "name": "Rule 2 Stop Time", "precision": 0, "index": 0, "type": "time"},
    "econ_rule_2_start_day": {"address": 8580, "name": "Rule 2 Start Day", "precision": 0, "index": 0, "type": "date"},
    "econ_rule_2_stop_day": {"address": 8581, "name": "Rule 2 Stop Day", "precision": 0, "index": 0, "type": "date"},
    "econ_rule_2_effective_week": {"address": 8582, "name": "Rule 2 Effective Week", "precision": 0, "index": 0, "type": "select_multi", "options": ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]},
    "econ_rule_2_voltage": {"address": 8583, "name": "Rule 2 Voltage", "unit": "V", "device_class": "voltage", "precision": 1, "index": 1, "type": "number", "min": 50.0, "max": 60.0, "step": 1},
    "econ_rule_2_soc": {"address": 8584, "name": "Rule 2 SOC", "unit": "%", "device_class": "battery", "precision": 0, "index": 0, "type": "number", "min": 0, "max": 100, "step": 1},
    "econ_rule_2_power": {"address": 8585, "name": "Rule 2 Power", "unit": "W", "device_class": "power", "precision": 0, "index": 0, "type": "number", "min": 0, "max": 10000, "step": 100},
    
    "econ_rule_3_enable": {"address": 8586, "name": "Economic Mode Rule 3 Enable", "precision": 0, "index": 0,"type": "select", "options": ["Disabled", "Charge", "Discharge"]},
    "econ_rule_3_start_time": {"address": 8587, "name": "Rule 3 Start Time", "precision": 0, "index": 0, "type": "time"},
    "econ_rule_3_stop_time": {"address": 8588, "name": "Rule 3 Stop Time", "precision": 0, "index": 0, "type": "time"},
    "econ_rule_3_start_day": {"address": 8589, "name": "Rule 3 Start Day", "precision": 0, "index": 0, "type": "date"},
    "econ_rule_3_stop_day": {"address": 8590, "name": "Rule 3 Stop Day", "precision": 0, "index": 0, "type": "date"},
    "econ_rule_3_effective_week": {"address": 8591, "name": "Rule 3 Effective Week", "precision": 0, "index": 0, "type": "select_multi", "options": ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]},
    "econ_rule_3_voltage": {"address": 8592, "name": "Rule 3 Voltage", "unit": "V", "device_class": "voltage", "precision": 1, "index": 1, "type": "number", "min": 50.0, "max": 60.0, "step": 1},
    "econ_rule_3_soc": {"address": 8593, "name": "Rule 3 SOC", "unit": "%", "device_class": "battery", "precision": 0, "index": 0, "type": "number", "min": 0, "max": 100, "step": 1},
    "econ_rule_3_power": {"address": 8594, "name": "Rule 3 Power", "unit": "W", "device_class": "power", "precision": 0, "index": 0, "type": "number", "min": 0, "max": 10000, "step": 100},
    
    "econ_rule_4_enable": {"address": 8595, "name": "Economic Mode Rule 4 Enable", "precision": 0, "index": 0,"type": "select", "options": ["Disabled", "Charge", "Discharge"]},
    "econ_rule_4_start_time": {"address": 8596, "name": "Rule 4 Start Time", "precision": 0, "index": 0, "type": "time"},
    "econ_rule_4_stop_time": {"address": 8597, "name": "Rule 4 Stop Time", "precision": 0, "index": 0, "type": "time"},
    "econ_rule_4_start_day": {"address": 8598, "name": "Rule 4 Start Day", "precision": 0, "index": 0, "type": "date"},
    "econ_rule_4_stop_day": {"address": 8599, "name": "Rule 4 Stop Day", "precision": 0, "index": 0, "type": "date"},
    "econ_rule_4_effective_week": {"address": 8600, "name": "Rule 4 Effective Week", "precision": 0, "index": 0, "type": "select_multi", "options": ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]},
    "econ_rule_4_voltage": {"address": 8601, "name": "Rule 4 Voltage", "unit": "V", "device_class": "voltage", "precision": 1, "index": 1, "type": "number", "min": 50.0, "max": 60.0, "step": 1},
    "econ_rule_4_soc": {"address": 8602, "name": "Rule 4 SOC", "unit": "%", "device_class": "battery", "precision": 0, "index": 0, "type": "number", "min": 0, "max": 100, "step": 1},
    "econ_rule_4_power": {"address": 8603, "name": "Rule 4 Power", "unit": "W", "device_class": "power", "precision": 0, "index": 0, "type": "number", "min": 0, "max": 10000, "step": 100},
 
    # Battery configuration registers (8483–8494)
    "battery_type": {"address": 8483, "name": "Battery Type", "precision": 0, "index": 5},  # 0: User, 1: Lithium (default), 2: LPBF, 3: LPBA, 4: No battery
    "battery_pack_series_count": {"address": 8484, "name": "Battery Pack Number in Series", "precision": 0, "index": 0},  # 1~10
    "battery_charged_voltage": {"address": 8485, "name": "Battery Charged Voltage", "unit": "V", "device_class": "voltage", "precision": 1, "index": 1},
    "battery_floating_charged_voltage": {"address": 8486, "name": "Battery Floating Charged Voltage", "unit": "V", "device_class": "voltage", "precision": 1, "index": 1},
    "battery_cutoff_voltage_ongrid_no_bms": {"address": 8487, "name": "Battery Cut-off Voltage (On-grid, no BMS)", "unit": "V", "device_class": "voltage", "precision": 1, "index": 1},
    "battery_cutoff_voltage_offgrid_no_bms": {"address": 8488, "name": "Battery Cut-off Voltage (Off-grid, no BMS)", "unit": "V", "device_class": "voltage", "precision": 1, "index": 1},
    "battery_restart_voltage_offgrid_no_bms": {"address": 8489, "name": "Battery Restart Voltage (Off-grid, no BMS)", "unit": "V", "device_class": "voltage", "precision": 1, "index": 1},
    "battery_discharge_depth_ongrid_bms": {"address": 8490, "name": "Battery Discharge Depth (On-grid, BMS)", "unit": "%", "device_class": "battery", "precision": 0, "index": 0},
    "battery_discharge_depth_offgrid_bms": {"address": 8491, "name": "Battery Discharge Depth (Off-grid, BMS)", "unit": "%", "device_class": "battery", "precision": 0, "index": 0},
    "battery_restart_depth_offgrid_bms": {"address": 8492, "name": "Battery Restart Depth (Off-grid, BMS)", "unit": "%", "device_class": "battery", "precision": 0, "index": 0},
    "battery_max_charge_current": {"address": 8493, "name": "Battery Max Charged Current", "unit": "A", "device_class": "current", "precision": 1, "index": 1},
    "battery_max_discharge_current": {"address": 8494, "name": "Battery Max Discharged Current", "unit": "A", "device_class": "current", "precision": 1, "index": 1},
}

# 2. Combined entities (post-process after reading)
_COMBINED_REGISTERS = {
    "pv_total_power": {
        "sources": ["pv_input_power", "pv2_input_power", "pv3_input_power"],
        "calc": lambda p1, p2, p3: (p1 or 0) + (p2 or 0) + (p3 or 0),
        "unit": "W",
        "device_class": "power",
        "state_class": "measurement",
        "name": "Total PV Power",
        "precision": 0,
    },
    "econ_rule_1": {
        "sources": [
            "econ_rule_1_enable",
            "econ_rule_1_start_time",
            "econ_rule_1_stop_time",
            "econ_rule_1_start_day",
            "econ_rule_1_stop_day",
            "econ_rule_1_effective_week",
            "econ_rule_1_voltage",
            "econ_rule_1_soc",
            "econ_rule_1_power",
        ],
        "calc": lambda enable, start_t, stop_t, start_d, stop_d, week, volt, soc, power: {
            "enabled": ["Disabled", "Charge", "Discharge"][enable] if 0 <= enable <= 2 else f"Unknown({enable})",
            "start_time": f"{start_t >> 8:02d}:{start_t & 0xFF:02d}",
            "stop_time": f"{stop_t >> 8:02d}:{stop_t & 0xFF:02d}",
            "start_date": f"{start_d >> 8:02d}-{start_d & 0xFF:02d}",
            "stop_date": f"{stop_d >> 8:02d}-{stop_d & 0xFF:02d}",
            "days": [day for i, day in enumerate(["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]) if (week & (1 << i))],
            "voltage_v": volt if volt else None,
            "soc_value": soc,
            "power_w": power,
        },
        "name": "Economic Mode Rule 1",
    },
    "econ_rule_2": {
        "sources": [
            "econ_rule_2_enable",
            "econ_rule_2_start_time",
            "econ_rule_2_stop_time",
            "econ_rule_2_start_day",
            "econ_rule_2_stop_day",
            "econ_rule_2_effective_week",
            "econ_rule_2_voltage",
            "econ_rule_2_soc",
            "econ_rule_2_power",
        ],
        "calc": lambda enable, start_t, stop_t, start_d, stop_d, week, volt, soc, power: {
            "enabled": ["Disabled", "Charge", "Discharge"][enable] if 0 <= enable <= 2 else f"Unknown({enable})",
            "start_time": f"{start_t >> 8:02d}:{start_t & 0xFF:02d}",
            "stop_time": f"{stop_t >> 8:02d}:{stop_t & 0xFF:02d}",
            "start_date": f"{start_d >> 8:02d}-{start_d & 0xFF:02d}",
            "stop_date": f"{stop_d >> 8:02d}-{stop_d & 0xFF:02d}",
            "days": [day for i, day in enumerate(["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]) if (week & (1 << i))],
            "voltage_v": volt if volt else None,
            "soc_value": soc,
            "power_w": power,
        },
        "name": "Economic Mode Rule 2",
    },
    "econ_rule_3": {
        "sources": [
            "econ_rule_3_enable",
            "econ_rule_3_start_time",
            "econ_rule_3_stop_time",
            "econ_rule_3_start_day",
            "econ_rule_3_stop_day",
            "econ_rule_3_effective_week",
            "econ_rule_3_voltage",
            "econ_rule_3_soc",
            "econ_rule_3_power",
        ],
        "calc": lambda enable, start_t, stop_t, start_d, stop_d, week, volt, soc, power: {
            "enabled": ["Disabled", "Charge", "Discharge"][enable] if 0 <= enable <= 2 else f"Unknown({enable})",
            "start_time": f"{start_t >> 8:02d}:{start_t & 0xFF:02d}",
            "stop_time": f"{stop_t >> 8:02d}:{stop_t & 0xFF:02d}",
            "start_date": f"{start_d >> 8:02d}-{start_d & 0xFF:02d}",
            "stop_date": f"{stop_d >> 8:02d}-{stop_d & 0xFF:02d}",
            "days": [day for i, day in enumerate(["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]) if (week & (1 << i))],
            "voltage_v": volt if volt else None,
            "soc_value": soc,
            "power_w": power,
        },
        "name": "Economic Mode Rule 3",
    },
    "econ_rule_4": {
        "sources": [
            "econ_rule_4_enable",
            "econ_rule_4_start_time",
            "econ_rule_4_stop_time",
            "econ_rule_4_start_day",
            "econ_rule_4_stop_day",
            "econ_rule_4_effective_week",
            "econ_rule_4_voltage",
            "econ_rule_4_soc",
            "econ_rule_4_power",
        ],
        "calc": lambda enable, start_t, stop_t, start_d, stop_d, week, volt, soc, power: {
            "enabled": ["Disabled", "Charge", "Discharge"][enable] if 0 <= enable <= 2 else f"Unknown({enable})",
            "start_time": f"{start_t >> 8:02d}:{start_t & 0xFF:02d}",
            "stop_time": f"{stop_t >> 8:02d}:{stop_t & 0xFF:02d}",
            "start_date": f"{start_d >> 8:02d}-{start_d & 0xFF:02d}",
            "stop_date": f"{stop_d >> 8:02d}-{stop_d & 0xFF:02d}",
            "days": [day for i, day in enumerate(["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]) if (week & (1 << i))],
            "voltage_v": volt if volt else None,
            "soc_value": soc,
            "power_w": power,
        },
        "name": "Economic Mode Rule 4",
    },

    "inverter_time": {
        "sources": [
            "time_year_month",   # 4434
            "time_day_hour",     # 4435
            "time_minute_second",# 4436
            "time_week"          # 4437
        ],
        "calc": lambda year_month, day_hour, min_sec, week: {
            "year": 2000 + (year_month >> 8),  # Assuming 2000-2099
            "month": year_month & 0xFF,
            "day": day_hour >> 8,
            "hour": day_hour & 0xFF,
            "minute": min_sec >> 8,
            "second": min_sec & 0xFF,
            "weekday": ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"][week] if 0 <= week <= 6 else f"Unknown({week})",
            "iso_datetime": f"{2000 + (year_month >> 8):04d}-{year_month & 0xFF:02d}-{day_hour >> 8:02d} "
                            f"{day_hour & 0xFF:02d}:{min_sec >> 8:02d}:{min_sec & 0xFF:02d}",
        },
        "name": "Inverter Time",
    },
    "log_time": {
        "sources": [
            "log_time_year_month",
            "log_time_day_hour",
            "log_time_minute_second",
        ],
        "calc": lambda year_month, day_hour, min_sec: {
            "year": 2000 + (year_month >> 8),
            "month": year_month & 0xFF,
            "day": day_hour >> 8,
            "hour": day_hour & 0xFF,
            "minute": min_sec >> 8,
            "second": min_sec & 0xFF,
            "iso_datetime": f"{2000 + (year_month >> 8):04d}-{year_month & 0xFF:02d}-{day_hour >> 8:02d} "
                            f"{day_hour & 0xFF:02d}:{min_sec >> 8:02d}:{min_sec & 0xFF:02d}",
        },
        "name": "Log Entry Time",
    },
}

# In const.py – at the end
REGISTER_SETS = {
    "basic": {
        key: info
        for key, info in _REGISTERS.items()
        if key in {
            "working_mode",
            "sn",
            "warning_state_1",
            "warning_state_2",
            "warning_state_3",
            "operating_mode",
            "fault_code",
            "ac_input_voltage",
            "ac_input_voltage_l2",
            "ac_input_voltage_l3",
            "ac_input_current",
            "ac_input_frequency",
            "ac_input_power",
            "battery_voltage",
            "battery_capacity",
            "battery_power",
            "ac_output_voltage",
            "ac_output_voltage_l2",
            "ac_output_voltage_l3",
            "ac_output_current",
            "ac_output_frequency",
            "ac_output_active_power",
            "ac_input_mid_voltage",
            "ac_input_mid_voltage_l2",
            "ac_input_mid_voltage_l3",
            "load_percentage",
            "pv_input_voltage",
            "pv_input_current",
            "pv_input_power",
            "pv2_input_voltage",
            "pv2_input_current",
            "pv2_input_power",
            "pv3_input_voltage",
            "pv3_input_current",
            "pv3_input_power",
            "total_ac_output_active_power",
            "total_ac_output_apparent_power",
            # Add combined keys if you want them always visible
            "pv_generated_energy_total",
            "load_consumption_energy_total",
            "line_load_consumption_energy_total",
            "battery_charged_energy_total",
            "battery_discharged_energy_total",
            "inverter_time",
            "econ_rule_1",
            "econ_rule_2",
            "econ_rule_3",
            "econ_rule_4",
            "pv_generated_energy_day",
            "pv_generated_energy_month",
            "pv_generated_energy_year",
            "battery_charged_energy_day",
            "battery_discharged_energy_day",
        }
        and "_secondary" not in key  # ← Excludes duplicates!
    },
    "basic_plus": {
        key: info
        for key, info in _REGISTERS.items()
        if (
             key.startswith((
               "ac_input_", 
               "ac_output_", 
               "pv_input_", 
               "battery_", 
               "invert_", 
               "total_ac_"
             ))

           )
        and "_secondary" not in key
        and "_alt" not in key
    },
    "full": _REGISTERS,  # All – including secondary if any (or filter here too if you want)
}

# Optional: Strict full without duplicates
# "full": {k: v for k, v in _REGISTERS.items() if "_secondary" not in k and "_alt" not in k},

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

_REGISTER_GROUPS = build_groups(_REGISTERS)  # auto-generated

MODEL_DATA = {
    INVERTER_MODEL_IVGM: {
        "registers": _REGISTERS,
        "groups": _REGISTER_GROUPS,
        "combined": _COMBINED_REGISTERS,
        "sets": REGISTER_SETS,
    },
}
