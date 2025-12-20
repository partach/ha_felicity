"""Constants for the Felicity integration."""
from typing import Dict

DOMAIN = "ha_felicity"

# Connection types
CONF_CONNECTION_TYPE = "connection_type"
CONNECTION_TYPE_SERIAL = "serial"
CONNECTION_TYPE_TCP = "tcp"

# Common settings
CONF_SLAVE_ID = "slave_id"

CONF_NAME = "name"
CONF_REGISTER_SET = "register_set"
CONF_INVERTER_MODEL = "inverter_model"

#supported inverter models
INVERTER_MODEL_IVGM = "ivgm"  # our current one


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
DEFAULT_BAUDRATE = 9600
DEFAULT_TCP_PORT = 502
DEFAULT_REGISTER_SET = "basic"
DEFAULT_STOPBITS = 1
DEFAULT_BYTESIZE = 8
DEFAULT_PARITY = "N"
DEFAULT_INVERTER_MODEL = INVERTER_MODEL_IVGM

_REGISTERS = {
    "setting_data_sn": {"address": 4352, "name": "Setting Data Sn", "precision": 0, "index": 0},
    "working_mode": {"address": 4353, "name": "Working Mode", "precision": 0, "index": 5},
    "warning_state_1": {"address": 4354, "name": "Warning State 1", "precision": 0, "index": 5},
    "warning_state_2": {"address": 4356, "name": "Warning State 2", "precision": 0, "index": 5},
    "warning_state_3": {"address": 4358, "name": "Warning State 3", "precision": 0, "index": 5},
    "fault_code": {"address": 4360, "name": "Fault Code", "precision": 0, "index": 5},
    "ac_input_voltage": {"address": 4361, "name": "Ac Input Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_input_current": {"address": 4362, "name": "Ac Input Current", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_input_frequency": {"address": 4363, "name": "Ac Input Frequency", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 2, "index": 2},
    "ac_input_power": {"address": 4364, "name": "Ac Input Power", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
    "battery_voltage": {"address": 4365, "name": "Battery Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "battery_current": {"address": 4366, "name": "Battery Current", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 3},
    "battery_power": {"address": 4367, "name": "Battery Power", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
    "battery_capacity": {"address": 4368, "name": "Battery Capacity", "unit": "%", "device_class": "battery", "state_class": "measurement", "precision": 1, "index": 7},
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
    "total_ac_input_power": {"address": 4392, "name": "Total Ac Input Power", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
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
    "total_ac_output_active_power": {"address": 4404, "name": "Total Ac Output Active Power", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "total_ac_output_apparent_power": {"address": 4406, "name": "Total Ac Output Apparent Power", "unit": "VA", "device_class": "apparent_power", "state_class": "measurement", "precision": 0, "index": 0},
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
    "time_year_month": {"address": 4434, "name": "Time Year-Month", "precision": 0, "index": 6},
    "time_day_hour": {"address": 4435, "name": "Time Day-Hour", "precision": 0, "index": 6},
    "time_minute_second": {"address": 4436, "name": "Time Minute-Second", "precision": 0, "index": 6},
    "time_week": {"address": 4437, "name": "Time Week", "precision": 0, "index": 6},
    "pv_generated_energy_total_high": {"address": 4438, "name": "Pv Generated Energy Inquiry Total-High 32 Bit", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "pv_generated_energy_total_low": {"address": 4440, "name": "Pv Generated Energy Inquiry Total-Low 32 Bit", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "pv_generated_energy_year": {"address": 4442, "name": "Pv Generated Energy Inquiry Year", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "pv_generated_energy_month": {"address": 4444, "name": "Pv Generated Energy Inquiry Month", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "pv_generated_energy_day": {"address": 4446, "name": "Pv Generated Energy Inquiry Day", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "load_consumption_energy_total_high": {"address": 4448, "name": "Load Consumption Energy Inquiry Total-High 32 Bit", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "load_consumption_energy_total_low": {"address": 4450, "name": "Load Consumption Energy Inquiry Total-Low 32 Bit", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "load_consumption_energy_year": {"address": 4452, "name": "Load Consumption Energy Inquiry Year", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "load_consumption_energy_month": {"address": 4454, "name": "Load Consumption Energy Inquiry Month", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "load_consumption_energy_day": {"address": 4456, "name": "Load Consumption Energy Inquiry Day", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "ac_input_energy_total_high": {"address": 4458, "name": "Ac Input Energy Inquiry Total-High 32 Bit", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "ac_input_energy_total_low": {"address": 4460, "name": "Ac Input Energy Inquiry Total-Low 32 Bit", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "ac_input_energy_year": {"address": 4462, "name": "Ac Input Energy Inquiry Year", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "ac_input_energy_month": {"address": 4464, "name": "Ac Input Energy Inquiry Month", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "ac_input_energy_day": {"address": 4466, "name": "Ac Input Energy Inquiry Day", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "ac_generated_energy_total_high": {"address": 4468, "name": "Ac Generated Energy Inquiry Total-High 32 Bit", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "ac_generated_energy_total_low": {"address": 4470, "name": "Ac Generated Energy Inquiry Total-Low 32 Bit", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "ac_generated_energy_year": {"address": 4472, "name": "Ac Generated Energy Inquiry Year", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "ac_generated_energy_month": {"address": 4474, "name": "Ac Generated Energy Inquiry Month", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "ac_generated_energy_day": {"address": 4476, "name": "Ac Generated Energy Inquiry Day", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "battery_charged_energy_total_high": {"address": 4478, "name": "Battery Charged Energy Inquiry Total-High 32 Bit", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "battery_charged_energy_total_low": {"address": 4480, "name": "Battery Charged Energy Inquiry Total-Low 32 Bit", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "battery_charged_energy_year": {"address": 4482, "name": "Battery Charged Energy Inquiry Year", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "battery_charged_energy_month": {"address": 4484, "name": "Battery Charged Energy Inquiry Month", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "battery_charged_energy_day": {"address": 4486, "name": "Battery Charged Energy Inquiry Day", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "battery_discharged_energy_total_high": {"address": 4488, "name": "Battery Discharged Energy Inquiry Total-High 32 Bit", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "battery_discharged_energy_total_low": {"address": 4490, "name": "Battery Discharged Energy Inquiry Total-Low 32 Bit", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "battery_discharged_energy_year": {"address": 4492, "name": "Battery Discharged Energy Inquiry Year", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "battery_discharged_energy_month": {"address": 4494, "name": "Battery Discharged Energy Inquiry Month", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "battery_discharged_energy_day": {"address": 4496, "name": "Battery Discharged Energy Inquiry Day", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "status_bit": {"address": 4498, "name": "Status Bit", "precision": 0, "index": 5},
    "p_bus_voltage_slv": {"address": 4499, "name": "P Bus Voltage_Slv", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "n_bus_voltage_slv": {"address": 4500, "name": "N Bus Voltage_Slv", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "line_power_conversion": {"address": 4501, "name": "Linepowerconversion", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
    "load_power_conversion": {"address": 4503, "name": "Loadpowerconversion", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
    "bat_power_conversion": {"address": 4505, "name": "Batpowerconversion", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
    "pv_power_conversion": {"address": 4506, "name": "Pvpowerconversion", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "power_flow_msg": {"address": 4507, "name": "Powerflowmsg", "precision": 0, "index": 5},
    "parallel_system_state": {"address": 4508, "name": "Parallelsystemstate", "precision": 0, "index": 5},
    "load_power_line_side": {"address": 4509, "name": "Loadpower_Lineside", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "g_uw_exsit_num_parallel": {"address": 4511, "name": "G_Uwexsitnum_Parallel", "precision": 0, "index": 0},
    "log_type": {"address": 4516, "name": "Log Type", "precision": 0, "index": 5},
    "log_index": {"address": 4517, "name": "Log Index", "precision": 0, "index": 0},
    "log_status": {"address": 4518, "name": "Log Status", "precision": 0, "index": 5},
    "log_id": {"address": 4519, "name": "Log Id", "precision": 0, "index": 5},
    "log_time_year_month": {"address": 4520, "name": "Log Time Year-Month", "precision": 0, "index": 6},
    "log_time_day_hour": {"address": 4521, "name": "Log Time Day-Hour", "precision": 0, "index": 6},
    "log_time_minute_second": {"address": 4522, "name": "Log Time Minute-Second", "precision": 0, "index": 6},
    "ac_input_voltage_secondary": {"address": 4523, "name": "Ac Input Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_input_frequency_secondary": {"address": 4524, "name": "Ac Input Frequency", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 2, "index": 2},
    "ac_input_power_secondary": {"address": 4525, "name": "Ac Input Power", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
    "battery_voltage_secondary": {"address": 4526, "name": "Battery Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "battery_power_secondary": {"address": 4527, "name": "Battery Power", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
    "battery_capacity_secondary": {"address": 4528, "name": "Battery Capacity", "unit": "%", "device_class": "battery", "state_class": "measurement", "precision": 0, "index": 7},
    "ac_output_voltage_secondary": {"address": 4529, "name": "Ac Output Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_output_frequency_secondary": {"address": 4530, "name": "Ac Output Frequency", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 2, "index": 2},
    "ac_output_active_power_secondary": {"address": 4531, "name": "Ac Output Active Power", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "ac_output_apparent_power_secondary": {"address": 4532, "name": "Ac Output Apparent Power", "unit": "VA", "device_class": "apparent_power", "state_class": "measurement", "precision": 0, "index": 0},
    "load_percentage_secondary": {"address": 4533, "name": "Load Percentage", "unit": "%", "state_class": "measurement", "precision": 0, "index": 7},
    "pv_input_voltage_secondary": {"address": 4534, "name": "Pv Input Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "pv_input_power_secondary": {"address": 4535, "name": "Pv Input Power", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "pv2_input_voltage_secondary": {"address": 4536, "name": "Pv2 Input Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "pv2_input_power_secondary": {"address": 4537, "name": "Pv2 Input Power", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "pv3_input_voltage_secondary": {"address": 4538, "name": "Pv3 Input Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "pv3_input_power_secondary": {"address": 4539, "name": "Pv3 Input Power", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "ac_input_voltage_l2_secondary": {"address": 4540, "name": "Ac Input Voltage L2", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_input_frequency_l2_secondary": {"address": 4541, "name": "Ac Input Frequency L2", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 2, "index": 2},
    "ac_input_power_l2_secondary": {"address": 4542, "name": "Ac Input Power L2", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
    "ac_input_voltage_l3_secondary": {"address": 4543, "name": "Ac Input Voltage L3", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_input_frequency_l3_secondary": {"address": 4544, "name": "Ac Input Frequency L3", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 2, "index": 2},
    "ac_input_power_l3_secondary": {"address": 4545, "name": "Ac Input Power L3", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
    "ac_output_voltage_l2_secondary": {"address": 4546, "name": "Ac Output Voltage L2", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_output_frequency_l2_secondary": {"address": 4547, "name": "Ac Output Frequency L2", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 2, "index": 2},
    "ac_output_active_power_l2_secondary": {"address": 4548, "name": "Ac Output Active Power L2", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "ac_output_apparent_power_l2_secondary": {"address": 4549, "name": "Ac Output Apparent Power L2", "unit": "VA", "device_class": "apparent_power", "state_class": "measurement", "precision": 0, "index": 0},
    "ac_output_voltage_l3_secondary": {"address": 4550, "name": "Ac Output Voltage L3", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "ac_output_frequency_l3_secondary": {"address": 4551, "name": "Ac Output Frequency L3", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 2, "index": 2},
    "ac_output_active_power_l3_secondary": {"address": 4552, "name": "Ac Output Active Power L3", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "ac_output_apparent_power_l3_secondary": {"address": 4553, "name": "Ac Output Apparent Power L3", "unit": "VA", "device_class": "apparent_power", "state_class": "measurement", "precision": 0, "index": 0},
    "p_bus_voltage": {"address": 4554, "name": "P Bus Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "n_bus_voltage": {"address": 4555, "name": "N Bus Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "p_dc_dc_current_secondary": {"address": 4556, "name": "P Dc/Dc Current", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 3},
    "n_dc_dc_current_secondary": {"address": 4557, "name": "N Dc/Dc Current", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 1, "index": 3},
    "max_inner_temperature": {"address": 4558, "name": "Max. Inner Temperature", "unit": "°C", "device_class": "temperature", "state_class": "measurement", "precision": 1, "index": 1},
    "max_heat_sink_temperature": {"address": 4559, "name": "Max. Heat-Sink Temperature", "unit": "°C", "device_class": "temperature", "state_class": "measurement", "precision": 1, "index": 1},
    "log_time_year_month_secondary": {"address": 4567, "name": "Log Time Year-Month", "precision": 0, "index": 6},
    "log_time_day_hour_secondary": {"address": 4568, "name": "Log Time Day-Hour", "precision": 0, "index": 6},
    "log_time_minute_second_secondary": {"address": 4569, "name": "Log Time Minute-Second", "precision": 0, "index": 6},
    "auto_test_result": {"address": 4570, "name": "Autotestresult", "precision": 0, "index": 5},
    "secondly_grid_over_voltage": {"address": 4571, "name": "Secondly Grid Over Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "secondly_grid_over_voltage_triptime": {"address": 4572, "name": "Secondly Grid Over Voltage Triptime", "unit": "ms", "precision": 0, "index": 0},
    "secondly_grid_over_adjvoltage": {"address": 4573, "name": "Secondly Grid Over Adjvoltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "grid_voltage": {"address": 4574, "name": "Gridvoltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "voltage_high2_loss_time": {"address": 4575, "name": "Voltagehigh2losstime", "unit": "ms", "precision": 1, "index": 0},
    "voltage_low2_seting": {"address": 4576, "name": "Voltagelow2seting", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "voltage_low2_time_seting": {"address": 4577, "name": "Voltagelow2timeseting", "unit": "ms", "precision": 0, "index": 0},
    "voltage_low2_adj": {"address": 4578, "name": "Voltagelow2adj", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "grid_voltage2": {"address": 4579, "name": "Gridvoltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "voltage_low2_loss_time": {"address": 4580, "name": "Voltagelow2losstime", "unit": "ms", "precision": 1, "index": 0},
    "10minute_voltage_high_loss_seting": {"address": 4581, "name": "10Minute Voltagehighlossseting", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "10minute_voltage_high_loss_time_set": {"address": 4582, "name": "10Minute Voltagehighlosstimeset", "unit": "s", "precision": 0, "index": 0},
    "10minute_voltage_high_loss_adj": {"address": 4583, "name": "10Minute Voltagehighlossadj", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "grid_voltage3": {"address": 4584, "name": "Gridvoltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "10minute_voltage_high_loss_time": {"address": 4585, "name": "10Minute Voltagehighlosstime", "unit": "s", "precision": 1, "index": 0},
    "voltage_low_seting": {"address": 4586, "name": "Voltagelowseting", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "voltage_low_loss_time_seting": {"address": 4587, "name": "Voltagelowlosstimeseting", "unit": "ms", "precision": 0, "index": 0},
    "voltage_low_adj": {"address": 4588, "name": "Voltagelowadj", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "grid_voltage4": {"address": 4589, "name": "Gridvoltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 1, "index": 1},
    "voltage_low_loss_time": {"address": 4590, "name": "Voltagelowlosstime", "unit": "ms", "precision": 1, "index": 0},
    "frequency_high2_seting": {"address": 4591, "name": "Frequencyhigh2seting", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 2, "index": 2},
    "frequency_high2_time_seting": {"address": 4592, "name": "Frequencyhigh2timeseting", "unit": "ms", "precision": 0, "index": 0},
    "frequency_high2_adj": {"address": 4593, "name": "Frequencyhigh2adj", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 1, "index": 1},
    "grid_frequency": {"address": 4594, "name": "Gridfrequency", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 1, "index": 1},
    "frequency_high_time": {"address": 4595, "name": "Frequencyhightime", "unit": "ms", "precision": 1, "index": 0},
    "frequency_low2_seting": {"address": 4596, "name": "Frequencylow2seting", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 2, "index": 2},
    "frequency_low2_time_seting": {"address": 4597, "name": "Frequencylow2timeseting", "unit": "ms", "precision": 0, "index": 0},
    "frequency_low2_adj": {"address": 4598, "name": "Frequencylow2adj", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 1, "index": 1},
    "grid_frequency2": {"address": 4599, "name": "Gridfrequency", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "precision": 1, "index": 1},
    "frequency_low_time": {"address": 4600, "name": "Frequencylowtime", "unit": "ms", "precision": 1, "index": 0},
    "curr_bms_addr": {"address": 4606, "name": "Currbmsaddr", "precision": 0, "index": 0},
    "bms_flg": {"address": 4607, "name": "Bms_Flg", "precision": 0, "index": 5},
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
    "total_soc": {"address": 4624, "name": "Totalsoc", "unit": "%", "device_class": "battery", "state_class": "measurement", "precision": 1, "index": 7},
    "total_soh": {"address": 4625, "name": "Totalsoh", "unit": "%", "state_class": "measurement", "precision": 1, "index": 7},
    "total_capacity_high": {"address": 4626, "name": "Totalcapacityhigh", "unit": "mAH", "precision": 0, "index": 0},
    "total_capacity_low": {"address": 4627, "name": "Totalcapacitylow", "unit": "mAH", "precision": 0, "index": 0},
    "parallel_number": {"address": 4628, "name": "Parallelnumber", "precision": 0, "index": 0},
    "parallel_status": {"address": 4629, "name": "Parallelstatus", "precision": 0, "index": 5},
    "maximum_cell_voltage_no": {"address": 4632, "name": "Maximumcellvoltageno.", "precision": 0, "index": 0},
    "maximum_cell_voltage": {"address": 4633, "name": "Maximumcellvoltage", "unit": "mV", "device_class": "voltage", "state_class": "measurement", "precision": 0, "index": 0},
    "minimum_cell_voltage_no": {"address": 4634, "name": "Minimumcellvoltageno.", "precision": 0, "index": 0},
    "minimum_cell_voltage": {"address": 4635, "name": "Minimumcellvoltage", "unit": "mV", "device_class": "voltage", "state_class": "measurement", "precision": 0, "index": 0},
    "maximum_cell_temperature_no": {"address": 4636, "name": "Maximumcelltemperatureno.", "precision": 0, "index": 0},
    "maximum_cell_temperature": {"address": 4637, "name": "Maximumcelltemperature", "unit": "℃", "device_class": "temperature", "state_class": "measurement", "precision": 1, "index": 1},
    "minimum_cell_temperature_no": {"address": 4638, "name": "Minimumcelltemperatureno.", "precision": 0, "index": 0},
    "minimum_cell_temperature_no2": {"address": 4639, "name": "Minimumcelltemperatureno2.", "unit": "℃", "device_class": "temperature", "state_class": "measurement", "precision": 1, "index": 1},
    "line_load_consumption_energy_total_high": {"address": 4645, "name": "Lineload Consumption Energy Inquiry Total-High 32 Bit", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "line_load_consumption_energy_total_low": {"address": 4647, "name": "Lineload Consumption Energy Inquiry Total-Low 32 Bit", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "line_load_consumption_energy_year": {"address": 4649, "name": "Lineload Consumption Energy Inquiry Year", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "line_load_consumption_energy_month": {"address": 4651, "name": "Lineload Consumption Energy Inquiry Month", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "line_load_consumption_energy_day": {"address": 4653, "name": "Lineload Consumption Energy Inquiry Day", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "total_load_consumption_energy_total_high": {"address": 4655, "name": "Totalload Consumption Energy Inquiry Total-High 32 Bit", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "total_load_consumption_energy_total_low": {"address": 4657, "name": "Totalload Consumption Energy Inquiry Total-Low 32 Bit", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "total_load_consumption_energy_year": {"address": 4659, "name": "Totalload Consumption Energy Inquiry Year", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "total_load_consumption_energy_month": {"address": 4661, "name": "Totalload Consumption Energy Inquiry Month", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "total_load_consumption_energy_day": {"address": 4663, "name": "Totalload Consumption Energy Inquiry Day", "unit": "WH", "device_class": "energy", "state_class": "total_increasing", "precision": 0, "index": 4},
    "electricity_meter_power_l1": {"address": 4864, "name": "Electricity Meter Power L1", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
    "electricity_meter_power_l2": {"address": 4866, "name": "Electricity Meter Power L2", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
    "electricity_meter_power_l3": {"address": 4868, "name": "Electricity Meter Power L3", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 3},
    "charging_gun_state": {"address": 4870, "name": "Charginggunstate", "precision": 0, "index": 5},
    "charging_pile_working_state": {"address": 4871, "name": "Chargingpileworkingstate", "precision": 0, "index": 5},
    "charging_pile_ac_input_voltage_l1_hi": {"address": 4872, "name": "Chargingpile Ac Input Voltage L1 Hi", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 2, "index": 2},
    "charging_pile_ac_input_voltage_l1_lo": {"address": 4873, "name": "Chargingpile Ac Input Voltage L1 Lo", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 2, "index": 2},
    "charging_pile_ac_input_voltage_l2_hi": {"address": 4874, "name": "Chargingpile Ac Input Voltage L2 Hi", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 2, "index": 2},
    "charging_pile_ac_input_voltage_l2_lo": {"address": 4875, "name": "Chargingpile Ac Input Voltage L2 Lo", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 2, "index": 2},
    "charging_pile_ac_input_voltage_l3_hi": {"address": 4876, "name": "Chargingpile Ac Input Voltage L3 Hi", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 2, "index": 2},
    "charging_pile_ac_input_voltage_l3_lo": {"address": 4877, "name": "Chargingpile Ac Input Voltage L3 Lo", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 2, "index": 2},
    "charging_pile_ac_input_current_l1_hi": {"address": 4878, "name": "Chargingpile Ac Input Current L1 Hi", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 2, "index": 2},
    "charging_pile_ac_input_current_l1_lo": {"address": 4879, "name": "Chargingpile Ac Input Current L1 Lo", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 2, "index": 2},
    "charging_pile_ac_input_current_l2_hi": {"address": 4880, "name": "Chargingpile Ac Input Current L2 Hi", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 2, "index": 2},
    "charging_pile_ac_input_current_l2_lo": {"address": 4881, "name": "Chargingpile Ac Input Current L2 Lo", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 2, "index": 2},
    "charging_pile_ac_input_current_l3_hi": {"address": 4882, "name": "Chargingpile Ac Input Current L3 Hi", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 2, "index": 2},
    "charging_pile_ac_input_current_l3_lo": {"address": 4883, "name": "Chargingpile Ac Input Current L3 Lo", "unit": "A", "device_class": "current", "state_class": "measurement", "precision": 2, "index": 2},
    "charging_pile_ac_input_power_hi": {"address": 4884, "name": "Chargingpile Ac Input Powerhi", "unit": "kW", "device_class": "power", "state_class": "measurement", "precision": 2, "index": 2},
    "charging_pile_ac_input_power_lo": {"address": 4885, "name": "Chargingpile Ac Input Powerlo", "unit": "kW", "device_class": "power", "state_class": "measurement", "precision": 2, "index": 2},
    "charging_pile_steering_voltage_hi": {"address": 4886, "name": "Chargingpile Steering Voltagehi", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 2, "index": 2},
    "charging_pile_steering_voltage_lo": {"address": 4887, "name": "Chargingpile Steering Voltagelo", "unit": "V", "device_class": "voltage", "state_class": "measurement", "precision": 2, "index": 2},
    "charging_pile_setting_power_hi": {"address": 4888, "name": "Chargingpile Settingpowerhi", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "charging_pile_setting_power_lo": {"address": 4889, "name": "Chargingpile Settingpowerlo", "unit": "W", "device_class": "power", "state_class": "measurement", "precision": 0, "index": 0},
    "charging_pile_fault_date_year_mon": {"address": 4890, "name": "Chargingpilefaultdate_Year_Mon", "precision": 0, "index": 6},
    "charging_pile_fault_date_day_hour": {"address": 4891, "name": "Chargingpilefaultdate_Day_Hour", "precision": 0, "index": 6},
    "charging_pile_fault_date_min_sec": {"address": 4892, "name": "Chargingpilefaultdate_Min_Sec", "precision": 0, "index": 6},
    "charging_pile_fault_code_hi": {"address": 4893, "name": "Chargingpilefaultcodehi", "precision": 0, "index": 5},
    "charging_pile_fault_code_lo": {"address": 4894, "name": "Chargingpilefaultcodelo", "precision": 0, "index": 5},
    "charging_pile_fault_state": {"address": 4895, "name": "Chargingpilefaultstate", "precision": 0, "index": 5},
    "charging_pile_fault_soure": {"address": 4896, "name": "Chargingpilefaultsoure", "precision": 0, "index": 5},
    "charging_pile_fault_sn": {"address": 4897, "name": "Chargingpilefaultsn", "precision": 0, "index": 0},
    "charging_pile_fault_level": {"address": 4899, "name": "Chargingpilefaultlevel", "precision": 0, "index": 5},
    # Extra configurable registers (starting at 8555)
    "lcd_backlight_function": {"address": 8555, "name": "LCD Backlight Function", "precision": 0, "index": 5},  # 0: disable, 1: enable
    "buzzer_beeping_function": {"address": 8556, "name": "Buzzer Beeping Function", "precision": 0, "index": 5},  # 0: disable, 1: enable
    "overload_protection_reset": {"address": 8557, "name": "Overload Protection Reset", "precision": 0, "index": 5},  # 0: disable, 1: enable
    "remote_off": {"address": 8558, "name": "Remote Off", "precision": 0, "index": 5},  # 0: disable, 1: enable
    "remote_ac_output_control": {"address": 8559, "name": "Remote Turn On/Off AC Output", "precision": 0, "index": 5},  # 0: off, 1: on
    
    # Time setting (writeable)
    "time_set_year_month": {"address": 8560, "name": "Time Setting Year-Month", "precision": 0, "index": 6},
    "time_set_day_hour": {"address": 8561, "name": "Time Setting Day-Hour", "precision": 0, "index": 6},
    "time_set_minute_second": {"address": 8562, "name": "Time Setting Minute-Second", "precision": 0, "index": 6},
    "time_set_week": {"address": 8563, "name": "Time Setting Week", "precision": 0, "index": 6},
    
    # Generated energy inquiry period
    "energy_inquiry_year_month": {"address": 8564, "name": "Generated Energy Inquiry Year-Month", "precision": 0, "index": 6},
    "energy_inquiry_day": {"address": 8565, "name": "Generated Energy Inquiry Day", "precision": 0, "index": 0},
    
    # Zero export / anti-reflux
    "zero_export_mode": {"address": 8566, "name": "Zero Export Mode", "precision": 0, "index": 5},  # 1: To load, 2: To CT
    "zero_export_power_adjust": {"address": 8567, "name": "Zero Export Power Adjust", "unit": "W", "device_class": "power", "precision": 0, "index": 3},  # signed, -500~500
    
    # Economic Mode Rules (4 rules, each 9 registers)
    "econ_rule_1_enable": {"address": 8568, "name": "Economic Mode Rule 1 Enable", "precision": 0, "index": 5},  # 0:disable, 1:charge, 2:discharge
    "econ_rule_1_start_time": {"address": 8569, "name": "Rule 1 Start Time", "precision": 0, "index": 6},
    "econ_rule_1_stop_time": {"address": 8570, "name": "Rule 1 Stop Time", "precision": 0, "index": 6},
    "econ_rule_1_start_day": {"address": 8571, "name": "Rule 1 Start Day", "precision": 0, "index": 6},
    "econ_rule_1_stop_day": {"address": 8572, "name": "Rule 1 Stop Day", "precision": 0, "index": 6},
    "econ_rule_1_effective_week": {"address": 8573, "name": "Rule 1 Effective Week", "precision": 0, "index": 5},
    "econ_rule_1_voltage": {"address": 8574, "name": "Rule 1 Voltage", "unit": "V", "device_class": "voltage", "precision": 1, "index": 1},
    "econ_rule_1_soc": {"address": 8575, "name": "Rule 1 SOC", "unit": "%", "device_class": "battery", "precision": 0, "index": 0},
    "econ_rule_1_power": {"address": 8576, "name": "Rule 1 Power", "unit": "W", "device_class": "power", "precision": 0, "index": 0},
    
    "econ_rule_2_enable": {"address": 8577, "name": "Economic Mode Rule 2 Enable", "precision": 0, "index": 5},
    "econ_rule_2_start_time": {"address": 8578, "name": "Rule 2 Start Time", "precision": 0, "index": 6},
    "econ_rule_2_stop_time": {"address": 8579, "name": "Rule 2 Stop Time", "precision": 0, "index": 6},
    "econ_rule_2_start_day": {"address": 8580, "name": "Rule 2 Start Day", "precision": 0, "index": 6},
    "econ_rule_2_stop_day": {"address": 8581, "name": "Rule 2 Stop Day", "precision": 0, "index": 6},
    "econ_rule_2_effective_week": {"address": 8582, "name": "Rule 2 Effective Week", "precision": 0, "index": 5},
    "econ_rule_2_voltage": {"address": 8583, "name": "Rule 2 Voltage", "unit": "V", "device_class": "voltage", "precision": 1, "index": 1},
    "econ_rule_2_soc": {"address": 8584, "name": "Rule 2 SOC", "unit": "%", "device_class": "battery", "precision": 0, "index": 0},
    "econ_rule_2_power": {"address": 8585, "name": "Rule 2 Power", "unit": "W", "device_class": "power", "precision": 0, "index": 0},
    
    "econ_rule_3_enable": {"address": 8586, "name": "Economic Mode Rule 3 Enable", "precision": 0, "index": 5},
    "econ_rule_3_start_time": {"address": 8587, "name": "Rule 3 Start Time", "precision": 0, "index": 6},
    "econ_rule_3_stop_time": {"address": 8588, "name": "Rule 3 Stop Time", "precision": 0, "index": 6},
    "econ_rule_3_start_day": {"address": 8589, "name": "Rule 3 Start Day", "precision": 0, "index": 6},
    "econ_rule_3_stop_day": {"address": 8590, "name": "Rule 3 Stop Day", "precision": 0, "index": 6},
    "econ_rule_3_effective_week": {"address": 8591, "name": "Rule 3 Effective Week", "precision": 0, "index": 5},
    "econ_rule_3_voltage": {"address": 8592, "name": "Rule 3 Voltage", "unit": "V", "device_class": "voltage", "precision": 1, "index": 1},
    "econ_rule_3_soc": {"address": 8593, "name": "Rule 3 SOC", "unit": "%", "device_class": "battery", "precision": 0, "index": 0},
    "econ_rule_3_power": {"address": 8594, "name": "Rule 3 Power", "unit": "W", "device_class": "power", "precision": 0, "index": 0},
    
    "econ_rule_4_enable": {"address": 8595, "name": "Economic Mode Rule 4 Enable", "precision": 0, "index": 5},
    "econ_rule_4_start_time": {"address": 8596, "name": "Rule 4 Start Time", "precision": 0, "index": 6},
    "econ_rule_4_stop_time": {"address": 8597, "name": "Rule 4 Stop Time", "precision": 0, "index": 6},
    "econ_rule_4_start_day": {"address": 8598, "name": "Rule 4 Start Day", "precision": 0, "index": 6},
    "econ_rule_4_stop_day": {"address": 8599, "name": "Rule 4 Stop Day", "precision": 0, "index": 6},
    "econ_rule_4_effective_week": {"address": 8600, "name": "Rule 4 Effective Week", "precision": 0, "index": 5},
    "econ_rule_4_voltage": {"address": 8601, "name": "Rule 4 Voltage", "unit": "V", "device_class": "voltage", "precision": 1, "index": 1},
    "econ_rule_4_soc": {"address": 8602, "name": "Rule 4 SOC", "unit": "%", "device_class": "battery", "precision": 0, "index": 0},
    "econ_rule_4_power": {"address": 8603, "name": "Rule 4 Power", "unit": "W", "device_class": "power", "precision": 0, "index": 0},
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



# 1. Batch read groups
_REGISTER_GROUPS = [

    # Warning states (6 words for 3 x uint32)
    {"start": 4354, "count": 6, "keys": ["warning_state_1", "warning_state_2", "warning_state_3"]},
    
    # 32-bit powers
    {"start": 4392, "count": 2, "keys": ["total_ac_input_power"]},
    {"start": 4404, "count": 2, "keys": ["total_ac_output_active_power"]},
    {"start": 4406, "count": 2, "keys": ["total_ac_output_apparent_power"]},
    {"start": 4501, "count": 4, "keys": ["line_power_conversion", "load_power_conversion"]},  # 4501 + 4503
    
    # Energy high/low pairs (all 32-bit energies)
    {"start": 4438, "count": 2, "keys": ["pv_generated_energy_total_high", "pv_generated_energy_total_low"]},
    {"start": 4448, "count": 2, "keys": ["load_consumption_energy_total_high", "load_consumption_energy_total_low"]},
    {"start": 4458, "count": 2, "keys": ["ac_input_energy_total_high", "ac_input_energy_total_low"]},
    {"start": 4468, "count": 2, "keys": ["ac_generated_energy_total_high", "ac_generated_energy_total_low"]},
    {"start": 4478, "count": 2, "keys": ["battery_charged_energy_total_high", "battery_charged_energy_total_low"]},
    {"start": 4488, "count": 2, "keys": ["battery_discharged_energy_total_high", "battery_discharged_energy_total_low"]},
    {"start": 4645, "count": 2, "keys": ["line_load_consumption_energy_total_high", "line_load_consumption_energy_total_low"]},
    {"start": 4655, "count": 2, "keys": ["total_load_consumption_energy_total_high", "total_load_consumption_energy_total_low"]},
    
    # SN string (10 words)
    {"start": 4640, "count": 10, "keys": ["sn"]},
    
    # Charging pile fault SN (4897–4898: 2 words)
    {"start": 4897, "count": 2, "keys": ["charging_pile_fault_sn"]},

    # charge pile
    {"start": 4884, "count": 2, "keys": ["charging_pile_ac_input_power_hi", "charging_pile_ac_input_power_lo"]},
    {"start": 4886, "count": 2, "keys": ["charging_pile_steering_voltage_hi", "charging_pile_steering_voltage_lo"]},
    {"start": 4888, "count": 2, "keys": ["charging_pile_setting_power_hi", "charging_pile_setting_power_lo"]},
    {"start": 4897, "count": 2, "keys": ["charging_pile_fault_sn"]},
    
    #time and date
    {"start": 4434, "count": 4, "keys": ["time_year_month", "time_day_hour", "time_minute_second", "time_week"]},
    {"start": 4520, "count": 3, "keys": ["log_time_year_month", "log_time_day_hour", "log_time_minute_second"]},
    {"start": 4567, "count": 3, "keys": ["log_time_year_month", "log_time_day_hour", "log_time_minute_second"]},
    {"start": 4890, "count": 3, "keys": ["charging_pile_fault_date_year_mon", "charging_pile_fault_date_day_hour", "charging_pile_fault_date_min_sec"]},
    # LCD/Buzzer/Remote controls
    {"start": 8555, "count": 5, "keys": ["lcd_backlight_function", "buzzer_beeping_function", "overload_protection_reset", "remote_off", "remote_ac_output_control"]},

    # Time setting (writeable version of time)
    {"start": 8560, "count": 4, "keys": ["time_set_year_month", "time_set_day_hour", "time_set_minute_second", "time_set_week"]},

    # Energy inquiry period
    {"start": 8564, "count": 2, "keys": ["energy_inquiry_year_month", "energy_inquiry_day"]},

    # Zero export
    {"start": 8566, "count": 2, "keys": ["zero_export_mode", "zero_export_power_adjust"]},

    # Full Economic Mode Rules block (4 rules × 9 registers = 36 consecutive!)
    {"start": 8568, "count": 36, "keys": [
        "econ_rule_1_enable", "econ_rule_1_start_time", "econ_rule_1_stop_time", "econ_rule_1_start_day", "econ_rule_1_stop_day",
        "econ_rule_1_effective_week", "econ_rule_1_voltage", "econ_rule_1_soc", "econ_rule_1_power",
        "econ_rule_2_enable", "econ_rule_2_start_time", "econ_rule_2_stop_time", "econ_rule_2_start_day", "econ_rule_2_stop_day",
        "econ_rule_2_effective_week", "econ_rule_2_voltage", "econ_rule_2_soc", "econ_rule_2_power",
        "econ_rule_3_enable", "econ_rule_3_start_time", "econ_rule_3_stop_time", "econ_rule_3_start_day", "econ_rule_3_stop_day",
        "econ_rule_3_effective_week", "econ_rule_3_voltage", "econ_rule_3_soc", "econ_rule_3_power",
        "econ_rule_4_enable", "econ_rule_4_start_time", "econ_rule_4_stop_time", "econ_rule_4_start_day", "econ_rule_4_stop_day",
        "econ_rule_4_effective_week", "econ_rule_4_voltage", "econ_rule_4_soc", "econ_rule_4_power",
    ]},
    # Battery configuration block (12 consecutive registers)
    {"start": 8483, "count": 12, "keys": [
        "battery_type",
        "battery_pack_series_count",
        "battery_charged_voltage",
        "battery_floating_charged_voltage",
        "battery_cutoff_voltage_ongrid_no_bms",
        "battery_cutoff_voltage_offgrid_no_bms",
        "battery_restart_voltage_offgrid_no_bms",
        "battery_discharge_depth_ongrid_bms",
        "battery_discharge_depth_offgrid_bms",
        "battery_restart_depth_offgrid_bms",
        "battery_max_charge_current",
        "battery_max_discharge_current",
    ]},
]

# 2. Combined entities (post-process after reading)
_COMBINED_REGISTERS = {
    "pv_generated_energy_total": {
        "sources": ["pv_generated_energy_total_high", "pv_generated_energy_total_low"],
        "calc": lambda high, low: (high << 32) | low,
        "unit": "Wh",
        "device_class": "energy",
        "state_class": "total_increasing",
        "name": "PV Generated Energy Total",
        "precision": 0,
    },
    "load_consumption_energy_total": {
        "sources": ["load_consumption_energy_total_high", "load_consumption_energy_total_low"],
        "calc": lambda high, low: (high << 32) | low,
        "unit": "Wh",
        "device_class": "energy",
        "state_class": "total_increasing",
        "name": "Load Consumption Energy Total",
        "precision": 0,
    },
    "ac_input_energy_total": {
        "sources": ["ac_input_energy_total_high", "ac_input_energy_total_low"],
        "calc": lambda high, low: (high << 32) | low,
        "unit": "Wh",
        "device_class": "energy",
        "state_class": "total_increasing",
        "name": "AC Input Energy Total",
        "precision": 0,
    },
    "ac_generated_energy_total": {
        "sources": ["ac_generated_energy_total_high", "ac_generated_energy_total_low"],
        "calc": lambda high, low: (high << 32) | low,
        "unit": "Wh",
        "device_class": "energy",
        "state_class": "total_increasing",
        "name": "AC Generated Energy Total",
        "precision": 0,
    },
    "battery_charged_energy_total": {
        "sources": ["battery_charged_energy_total_high", "battery_charged_energy_total_low"],
        "calc": lambda high, low: (high << 32) | low,
        "unit": "Wh",
        "device_class": "energy",
        "state_class": "total_increasing",
        "name": "Battery Charged Energy Total",
        "precision": 0,
    },
    "battery_discharged_energy_total": {
        "sources": ["battery_discharged_energy_total_high", "battery_discharged_energy_total_low"],
        "calc": lambda high, low: (high << 32) | low,
        "unit": "Wh",
        "device_class": "energy",
        "state_class": "total_increasing",
        "name": "Battery Discharged Energy Total",
        "precision": 0,
    },
    "line_load_consumption_energy_total": {
        "sources": ["line_load_consumption_energy_total_high", "line_load_consumption_energy_total_low"],
        "calc": lambda high, low: (high << 32) | low,
        "unit": "Wh",
        "device_class": "energy",
        "state_class": "total_increasing",
        "name": "Line Load Consumption Energy Total",
        "precision": 0,
    },
    "total_load_consumption_energy_total": {
        "sources": ["total_load_consumption_energy_total_high", "total_load_consumption_energy_total_low"],
        "calc": lambda high, low: (high << 32) | low,
        "unit": "Wh",
        "device_class": "energy",
        "state_class": "total_increasing",
        "name": "Total Load Consumption Energy Total",
        "precision": 0,
    },
    "device_sn": {
        "sources": ["sn"],  # 5 words
        "calc": lambda words: ''.join(f"{w:04d}" for w in words).lstrip('0') or '0',
        "name": "Device Serial Number",
        "precision": 0,
    },
    "max_cell_voltage_info": {
        "sources": ["maximum_cell_voltage_no", "maximum_cell_voltage"],
        "calc": lambda no, volt: {"cell_no": no, "voltage_mv": volt},
        "name": "Maximum Cell Voltage Info",
        "precision": 0,
    },
    "min_cell_voltage_info": {
        "sources": ["minimum_cell_voltage_no", "minimum_cell_voltage"],
        "calc": lambda no, volt: {"cell_no": no, "voltage_mv": volt},
        "name": "Minimum Cell Voltage Info",
        "precision": 0,
    },
    "max_cell_temp_info": {
        "sources": ["maximum_cell_temperature_no", "maximum_cell_temperature"],
        "calc": lambda no, temp: {"cell_no": no, "temp_c": temp},
        "name": "Maximum Cell Temperature Info",
        "precision": 0,
    },
}

# 3. Optional: User-selectable sets (for options flow)
_REGISTER_SETS = {
    "basic": [
        "ac_input_voltage", "ac_input_current", "ac_input_frequency", "ac_input_power",
        "battery_voltage", "battery_capacity", "battery_power",
        "ac_output_voltage", "ac_output_current", "ac_output_frequency", "ac_output_active_power",
        "load_percentage", "pv_input_power", "working_mode", "fault_code"
    ],
    "pv_detailed": ["pv_input_voltage", "pv_input_current", "pv2_input_voltage", "pv2_input_current", "pv3_input_voltage", "pv3_input_current"],
    "ac_phases": ["ac_input_voltage_l2", "ac_input_voltage_l3", "ac_output_voltage_l2", "ac_output_voltage_l3"],
    "energy": ["pv_generated_energy_total", "load_consumption_energy_total", "ac_input_energy_total", "battery_charged_energy_total", "battery_discharged_energy_total"],
    "bms": ["total_soc", "total_soh", "maximum_cell_voltage", "minimum_cell_voltage", "maximum_cell_temperature"],
    "temperatures": ["inner_temperature_1", "inner_temperature_2", "heatsink_temperature_1", "max_inner_temperature", "max_heat_sink_temperature"],
    "faults": ["fault_code", "warning_state_1", "warning_state_2", "warning_state_3"],
}

REGISTER_SETS = {
    "basic": {
        key: info
        for key, info in _REGISTERS.items()
        if key in [
            # Core essentials – fast, low overhead
            "working_mode",
            "fault_code",
            "ac_input_voltage",
            "ac_input_current",
            "ac_input_frequency",
            "ac_input_power",
            "battery_voltage",
            "battery_capacity",
            "battery_power",
            "ac_output_voltage",
            "ac_output_current",
            "ac_output_frequency",
            "ac_output_active_power",
            "load_percentage",
            "pv_input_power",
            "pv_generated_energy_total",  # combined
            "load_consumption_energy_total",  # combined
            "battery_charged_energy_total",  # combined
            "battery_discharged_energy_total",  # combined
        ]
    },
    "basic_plus": {
        key: info
        for key, info in _REGISTERS.items()
        if key.startswith(("ac_input_", "ac_output_", "pv_input_", "battery_", "invert_", "total_ac_"))
        or key in ["load_percentage", "working_mode", "fault_code"]
    },
    "full": _REGISTERS,  # All registers
}

# Model-specific data (extend for new models)
MODEL_DATA = {
    INVERTER_MODEL_IVGM: {
        "registers": _REGISTERS,
        "groups": _REGISTER_GROUPS,
        "combined": _COMBINED_REGISTERS,
        "sets": REGISTER_SETS,
    },
    # Future model example (add when ready)
    # "other_model": {
    #     "registers": _REGISTERS_OTHER,
    #     "groups": _REGISTER_GROUPS_OTHER,
    #     "combined": _COMBINED_REGISTERS_OTHER,
    #     "sets": REGISTER_SETS_OTHER,
    # },
}

