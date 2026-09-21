"""Register maps for the Felicity **IVGM** hybrid inverter family.

Source: "Inverter Communication Protocol — Series RS485", Guangzhou Felicity
Solar Technology Co., Ltd., v01 2025-03-13 (author: Moliting).  That document
is scoped to the **8K** model: its tables are headed "8K Analog Quantity
Information" / "8K Setting Quantity Information", and the registers a larger
unit would use (phase C, PV3/PV4, battery 2) are present but annotated
"(8K donot support)".

ONE FAMILY MAP, TWO MODELS
--------------------------
Because the 8K map is a strict subset of the family map, this module defines
the family ONCE and derives both models from it.  Hand-copying it into two
files is precisely the duplication that has bitten this project before (see
CLAUDE.md, "Test harness: never hand-type a copy of production code") — a
register added to one copy and not the other drifts silently.

    _REGISTERS_IVGM_TWENTY  = the full family map
    _REGISTERS_IVGM_EIGHT   = the family map minus _IVGM_EIGHT_UNSUPPORTED

⚠️ THE 3-PHASE MAP IS INFERRED, NOT DOCUMENTED.  The protocol document never
mentions a 20K.  Deriving the 3-phase map from the "(8K donot support)"
annotations is a well-founded inference — those registers exist precisely
because a larger model uses them — but it is an inference.  Before trusting it
against hardware, read docs/IVGM_SUPPORT_GAPS.md, which lists every open
question, the dangerous ones first.
"""

# Registers the 8K does not implement — the document marks each
# "(8K donot support)".  They are what separates the 3-phase family
# member (phase C, PV3/PV4, battery 2) from the 8K.
_IVGM_EIGHT_UNSUPPORTED = {
    'pv3_voltage', 'pv4_voltage', 'pv3_current', 'pv4_current',
    'phase_c_home_load_power', 'pv3_power', 'pv4_power', 'bat2_voltage',
    'bat2_current', 'bat2_power', 'bat2_soc', 'gen_ccurr', 'gen_cvolt',
    'grid_cvolt', 'inv_cvolt', 'load_cvolt', 'phase_c_ct_current',
    'grid_ccurr', 'inv_ccurr', 'load_c_curr', 'gen_cphase_p',
    'grid_cphase_p', 'inv_cphase_p', 'load_cphase_p', 'load_cpersent',
    'phase_c_ct_active_power', 'pv3_total_gen_energy',
    'pv4_total_gen_energy', 'battery_2_total_charge_low',
    'battery_2_total_dis_charge', 'pv3_day_gen_energy',
    'pv3_month_gen_energy', 'pv3_year_gen_energy', 'pv4_month_gen_energy',
    'pv4_year_gen_energy', 'bat_2_day_charge', 'bat2_month_charge',
    'bat2_year_charge', 'bat2_day_dis_charge', 'bat2_month_dis_charge',
    'bat2_year_dis_charge',
}


# The complete family map, as documented.
_REGISTERS_IVGM_FAMILY = {
    "work_mode_status":                            {'address': 4352, 'name': 'WorkMode', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "state1":                                      {'address': 4353, 'name': 'State1', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "state2":                                      {'address': 4354, 'name': 'State2', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "state3":                                      {'address': 4355, 'name': 'State3', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "state4":                                      {'address': 4356, 'name': 'State4', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "time_year_month":                             {'address': 4357, 'name': 'Time: Year-Month', 'precision': 0, 'index': 99, 'state_class': 'measurement'},
    "time_day_time":                               {'address': 4358, 'name': 'Time: Day-Time', 'precision': 0, 'index': 99, 'state_class': 'measurement'},
    "time_minutes_seconds":                        {'address': 4359, 'name': 'Time: minutes-seconds', 'precision': 0, 'index': 99, 'state_class': 'measurement'},
    "time_week":                                   {'address': 4360, 'name': 'Time: Week', 'precision': 0, 'index': 99, 'state_class': 'measurement'},
    "alarm_codes":                                 {'address': 4361, 'name': 'Warn Code', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "fault_codes":                                 {'address': 4362, 'name': 'Fault Code', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "warn_state1":                                 {'address': 4363, 'name': 'WarnState1', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "warn_state2":                                 {'address': 4364, 'name': 'WarnState2', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "warn_state3":                                 {'address': 4365, 'name': 'WarnState3', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "warn_state4":                                 {'address': 4366, 'name': 'WarnState4', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "warn_state5":                                 {'address': 4367, 'name': 'WarnState5', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "warn_state6":                                 {'address': 4368, 'name': 'WarnState6', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "fault_code1":                                 {'address': 4369, 'name': 'FaultCode1', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "fault_code2":                                 {'address': 4370, 'name': 'FaultCode2', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "fault_code3":                                 {'address': 4371, 'name': 'FaultCode3', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "fault_code4":                                 {'address': 4372, 'name': 'FaultCode4', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "fault_code5":                                 {'address': 4373, 'name': 'FaultCode5', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "fault_code6":                                 {'address': 4374, 'name': 'FaultCode6', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "fault_code7":                                 {'address': 4375, 'name': 'FaultCode7', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "pv1_voltage":                                 {'address': 4376, 'name': 'PV1 voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "pv2_voltage":                                 {'address': 4377, 'name': 'PV2 voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "pv3_voltage":                                 {'address': 4378, 'name': 'PV3 voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "pv4_voltage":                                 {'address': 4379, 'name': 'PV4 voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "pv1_current":                                 {'address': 4380, 'name': 'PV1 Current', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "pv2_current":                                 {'address': 4381, 'name': 'PV2 Current', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "pv3_current":                                 {'address': 4382, 'name': 'PV3 Current', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "pv4_current":                                 {'address': 4383, 'name': 'PV4 Current', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "phase_a_home_load_power":                     {'address': 4384, 'name': 'Home Load A', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "phase_b_home_load_power":                     {'address': 4385, 'name': 'Home Load B', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "phase_c_home_load_power":                     {'address': 4386, 'name': 'Home Load C', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "pv1_power":                                   {'address': 4392, 'name': 'PV1 Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "pv2_power":                                   {'address': 4393, 'name': 'PV2 Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "pv3_power":                                   {'address': 4394, 'name': 'PV3 Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "pv4_power":                                   {'address': 4395, 'name': 'PV4 Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "bat1_voltage":                                {'address': 4396, 'name': 'Bat1 Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "bat2_voltage":                                {'address': 4397, 'name': 'Bat2 Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "bat1_current":                                {'address': 4400, 'name': 'Bat1 Current', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "bat1_power":                                  {'address': 4401, 'name': 'Bat1Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "bat2_current":                                {'address': 4402, 'name': 'Bat2 Current', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "bat2_power":                                  {'address': 4403, 'name': 'Bat2 Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "bat1_soc":                                    {'address': 4408, 'name': 'Bat1 SOC', 'precision': 1, 'index': 1, 'unit': '%', 'device_class': 'battery', 'state_class': 'measurement'},
    "bat2_soc":                                    {'address': 4411, 'name': 'Bat2 SOC', 'precision': 1, 'index': 1, 'unit': '%', 'device_class': 'battery', 'state_class': 'measurement'},
    "boost_temperature":                           {'address': 4420, 'name': 'Boost temperature', 'precision': 0, 'index': 3, 'unit': '°C', 'device_class': 'temperature', 'state_class': 'measurement'},
    "inverter_temperature":                        {'address': 4421, 'name': 'Inverter temperature', 'precision': 0, 'index': 3, 'unit': '°C', 'device_class': 'temperature', 'state_class': 'measurement'},
    "environment_temperature":                     {'address': 4422, 'name': 'Environment temperature', 'precision': 0, 'index': 3, 'unit': '°C', 'device_class': 'temperature', 'state_class': 'measurement'},
    "lead_acid_tempe":                             {'address': 4423, 'name': 'Lead Acid Tempe', 'precision': 1, 'index': 8, 'unit': '°C', 'device_class': 'temperature', 'state_class': 'measurement'},
    "generator_a_current":                         {'address': 4426, 'name': 'Generator A Current', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "generator_b_current":                         {'address': 4427, 'name': 'Generator B Current', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "gen_ccurr":                                   {'address': 4428, 'name': 'Gen_CCurr', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "generator_a_voltage":                         {'address': 4429, 'name': 'Generator A Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "generator_b_voltage":                         {'address': 4430, 'name': 'Generator B Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "gen_cvolt":                                   {'address': 4431, 'name': 'GEN_CVolt', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "grid_a_voltage":                              {'address': 4432, 'name': 'Grid A Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "grid_b_voltage":                              {'address': 4433, 'name': 'Grid B Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "grid_cvolt":                                  {'address': 4434, 'name': 'Grid_CVolt', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "inverter_a_voltage":                          {'address': 4435, 'name': 'Inverter A Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "inverter_b_voltage":                          {'address': 4436, 'name': 'Inverter B Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "inv_cvolt":                                   {'address': 4437, 'name': 'INV_CVolt', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "load_a_voltage":                              {'address': 4438, 'name': 'Load A Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "load_b_voltage":                              {'address': 4439, 'name': 'Load B Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "load_cvolt":                                  {'address': 4440, 'name': 'Load_CVolt', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "phase_a_ct_current":                          {'address': 4441, 'name': 'Outside CT A Current', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "phase_b_ct_current":                          {'address': 4442, 'name': 'Outside CT B Current', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "phase_c_ct_current":                          {'address': 4443, 'name': 'OutCT_CCurr', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "grid_a_current":                              {'address': 4444, 'name': 'Grid A Current', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "grid_b_current":                              {'address': 4445, 'name': 'Grid B Current', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "grid_ccurr":                                  {'address': 4446, 'name': 'Grid_CCurr', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "inverter_a_current":                          {'address': 4447, 'name': 'Inverter A Current', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "inverter_b_current":                          {'address': 4448, 'name': 'Inverter B Current', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "inv_ccurr":                                   {'address': 4449, 'name': 'INV_CCurr', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "load_a_current":                              {'address': 4450, 'name': 'Load A Current', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "load_b_current":                              {'address': 4451, 'name': 'Load B Current', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "load_c_curr":                                 {'address': 4452, 'name': 'Load C Curr', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "generator_frequency":                         {'address': 4460, 'name': 'Generator frequency', 'precision': 2, 'index': 2, 'unit': 'Hz', 'device_class': 'frequency', 'state_class': 'measurement'},
    "grid_frequency":                              {'address': 4461, 'name': 'Grid frequency', 'precision': 2, 'index': 2, 'unit': 'Hz', 'device_class': 'frequency', 'state_class': 'measurement'},
    "inverter_frequency":                          {'address': 4462, 'name': 'Inverter frequency', 'precision': 2, 'index': 2, 'unit': 'Hz', 'device_class': 'frequency', 'state_class': 'measurement'},
    "load_frequency":                              {'address': 4463, 'name': 'Load frequency', 'precision': 2, 'index': 2, 'unit': 'Hz', 'device_class': 'frequency', 'state_class': 'measurement'},
    "generator_a_phase_power":                     {'address': 4464, 'name': 'Generator A Phase Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "generator_b_phase_power":                     {'address': 4465, 'name': 'Generator B Phase Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "gen_cphase_p":                                {'address': 4466, 'name': 'GEN_CPhaseP', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "grid_a_phase_power":                          {'address': 4467, 'name': 'Grid A Phase Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "grid_b_phase_power":                          {'address': 4468, 'name': 'Grid B Phase Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "grid_cphase_p":                               {'address': 4469, 'name': 'Grid_CPhaseP', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "inverter_aphase_power":                       {'address': 4470, 'name': 'Inverter APhase Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "inverter_bphase_power":                       {'address': 4471, 'name': 'Inverter BPhase Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "inv_cphase_p":                                {'address': 4472, 'name': 'INV_CPhaseP', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "load_aphase_power":                           {'address': 4473, 'name': 'Load APhase Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "load_bphase_power":                           {'address': 4474, 'name': 'Load BPhase Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "load_cphase_p":                               {'address': 4475, 'name': 'Load_CPhaseP', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "load_a_persent":                              {'address': 4479, 'name': 'Load A Persent', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "load_b_persent":                              {'address': 4480, 'name': 'Load B Persent', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "load_cpersent":                               {'address': 4481, 'name': 'Load_CPersent', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "phase_a_ct_active_power":                     {'address': 4482, 'name': 'Grid CT APhase Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "phase_b_ct_active_power":                     {'address': 4483, 'name': 'Grid CT BPhase Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "phase_c_ct_active_power":                     {'address': 4484, 'name': 'GridCT_CPhaseP', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "mirco_inverter_loaction":                     {'address': 4491, 'name': 'Mirco Inverter Loaction', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "mirco_inverter_total_gen_energy":             {'address': 4492, 'name': 'Mirco Inverter Total Gen Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "power_flow_msg2":                             {'address': 4495, 'name': 'PowerFlowMsg2', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "power_flow_msg":                              {'address': 4496, 'name': 'PowerFlowMsg', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "genstart_signal":                             {'address': 4497, 'name': 'Genstart signal', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "total_generator_power":                       {'address': 4498, 'name': 'Gen Total Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "total_load_power":                            {'address': 4499, 'name': 'Load Total Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "total_grid_power":                            {'address': 4500, 'name': 'Grid Total Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "pv1_total_gen_energy":                        {'address': 4501, 'name': 'PV1 Total Gen Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "pv2_total_gen_energy":                        {'address': 4503, 'name': 'PV2 Total Gen Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "pv3_total_gen_energy":                        {'address': 4505, 'name': 'PV3 Total Gen Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "pv4_total_gen_energy":                        {'address': 4507, 'name': 'PV4TotalGenEnergy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "battery_1_total_charge":                      {'address': 4509, 'name': 'Battery 1 Total Charge', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "battery_1_total_dis_charge":                  {'address': 4511, 'name': 'Battery 1 Total DisCharge', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "battery_2_total_charge_high_8_k_donot_0_1_kwh_support": {'address': 4513, 'name': 'Battery 2 Total Charge High(8K donot 0.1KWh support)', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "battery_2_total_charge_low":                  {'address': 4514, 'name': 'Battery 2 Total Charge Low', 'precision': 1, 'index': 1, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "battery_2_total_dis_charge":                  {'address': 4515, 'name': 'Battery 2 Total DisCharge', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "grid_total_cost_energy":                      {'address': 4517, 'name': 'Grid Total Cost Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "grid_total_gen_energy":                       {'address': 4519, 'name': 'Grid Total Gen Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "load_total_cost_energy":                      {'address': 4521, 'name': 'Load Total Cost Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "gen_total_cost_energy":                       {'address': 4523, 'name': 'Gen Total Cost Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "smart_load_total_cost_energy":                {'address': 4525, 'name': 'SmartLoad Total Cost Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "home_load_total_cost_energy":                 {'address': 4527, 'name': 'HomeLoad Total Cost Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "pv1_day_gen_energy":                          {'address': 4529, 'name': 'PV1 Day Gen Energy', 'precision': 1, 'index': 1, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "pv1_month_gen_energy":                        {'address': 4530, 'name': 'PV1 Month Gen Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "pv1_year_gen_energy":                         {'address': 4532, 'name': 'PV1 Year Gen Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "pv2_day_gen_energy":                          {'address': 4534, 'name': 'PV2 Day Gen Energy', 'precision': 1, 'index': 1, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "pv2_month_gen_energy":                        {'address': 4535, 'name': 'PV2 Month Gen Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "pv2_year_gen_energy":                         {'address': 4537, 'name': 'PV2 Year Gen Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "pv3_day_gen_energy":                          {'address': 4539, 'name': 'PV3 Day Gen Energy', 'precision': 1, 'index': 1, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "pv3_month_gen_energy":                        {'address': 4540, 'name': 'PV3 Month Gen Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "pv3_year_gen_energy":                         {'address': 4542, 'name': 'PV3 Year Gen Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "pv4_day_gen_energy_8_k_donot_0_1_kwh_support": {'address': 4544, 'name': 'PV4 Day Gen Energy(8K donot 0.1KWh support)', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "pv4_month_gen_energy":                        {'address': 4545, 'name': 'PV4 Month Gen Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "pv4_year_gen_energy":                         {'address': 4547, 'name': 'PV4 Year Gen Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "pv_total_gen_energy":                         {'address': 4549, 'name': 'PV Total Gen Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "battery_1_day_charge":                        {'address': 4551, 'name': 'Battery 1 Day Charge', 'precision': 1, 'index': 1, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "battery_1_month_charge":                      {'address': 4552, 'name': 'Battery 1 Month Charge', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "battery_1_year_charge":                       {'address': 4554, 'name': 'Battery 1 Year Charge', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "battery_1_day_dis_charge":                    {'address': 4556, 'name': 'Battery 1 Day DisCharge', 'precision': 1, 'index': 1, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "battery_1_month_dis_charge":                  {'address': 4557, 'name': 'Battery 1 Month DisCharge', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "battery_1_year_dis_charge":                   {'address': 4559, 'name': 'Battery 1 Year DisCharge', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "bat_2_day_charge":                            {'address': 4561, 'name': 'Bat 2 Day Charge', 'precision': 1, 'index': 1, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "bat2_month_charge":                           {'address': 4562, 'name': 'Bat2MonthCharge', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "bat2_year_charge":                            {'address': 4564, 'name': 'Bat2YearCharge', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "bat2_day_dis_charge":                         {'address': 4566, 'name': 'Bat2DayDisCharge', 'precision': 1, 'index': 1, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "bat2_month_dis_charge":                       {'address': 4567, 'name': 'Bat2MonthDisCharge', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "bat2_year_dis_charge":                        {'address': 4569, 'name': 'Bat2YearDisCharge', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "grid_day_cost_energy":                        {'address': 4571, 'name': 'Grid Day Cost Energy', 'precision': 1, 'index': 1, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "grid_month_cost_energy":                      {'address': 4572, 'name': 'Grid Month Cost Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "grid_year_cost_energy":                       {'address': 4574, 'name': 'Grid Year Cost Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "grid_day_gen_energy":                         {'address': 4576, 'name': 'Grid Day Gen Energy', 'precision': 1, 'index': 1, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "grid_month_gen_energy":                       {'address': 4577, 'name': 'Grid Month Gen Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "grid_year_gen_energy":                        {'address': 4579, 'name': 'Grid Year Gen Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "load_day_cost_energy":                        {'address': 4581, 'name': 'Load Day Cost Energy', 'precision': 1, 'index': 1, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "load_month_cost_energy":                      {'address': 4582, 'name': 'Load Month Cost Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "load_year_cost_energy":                       {'address': 4584, 'name': 'Load Year Cost Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "generator_day_cost_energy":                   {'address': 4586, 'name': 'Gen Day Cost Energy', 'precision': 1, 'index': 1, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "gen_month_cost_energy":                       {'address': 4587, 'name': 'Gen Month Cost Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "gen_year_cost_energy":                        {'address': 4589, 'name': 'Gen Year Cost Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "microinverter_day_cost_energy":               {'address': 4591, 'name': 'Micro Inverter Day Gen Energy', 'precision': 1, 'index': 1, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "micro_inverter_month_gen_energy":             {'address': 4592, 'name': 'Micro Inverter Month Gen Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "micro_inverter_year_gen_energy":              {'address': 4594, 'name': 'Micro Inverter Year Gen Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "homeload_day_cost_energy":                    {'address': 4596, 'name': 'HomeLoad Day Cost Energy', 'precision': 1, 'index': 1, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "home_load_month_cost_energy":                 {'address': 4597, 'name': 'HomeLoad Month Cost Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "home_load_year_cost_energy":                  {'address': 4599, 'name': 'HomeLoad Year Cost Energy', 'precision': 1, 'index': 1, 'size': 2, 'unit': 'kWh', 'device_class': 'energy', 'state_class': 'total_increasing'},
    "bms_wifi_bat_meter_status":                   {'address': 4605, 'name': 'BMS/WIFI/Bat/Meter/Status', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "the_current_address_of_bms_data":             {'address': 4606, 'name': 'The current address of BMS data', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "bms_current_effective_communication_bit":     {'address': 4607, 'name': 'BMS current effective communication bit', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "bms_charge_voltage_limit":                    {'address': 4608, 'name': 'BMS Charge Voltage Limit', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "bms_discharge_voltage_limit":                 {'address': 4609, 'name': 'BMS Discharge Voltage Limit', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "bms_charge_current_limit":                    {'address': 4610, 'name': 'BMS Charge Current Limit', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "bms_discharge_current_limit":                 {'address': 4611, 'name': 'BMS Discharge Current Limit', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "bms_status":                                  {'address': 4612, 'name': 'BMS Status', 'precision': 0, 'index': 0, 'size': 2, 'state_class': 'measurement'},
    "bms_fault_flag_high":                         {'address': 4614, 'name': 'BMS Fault Flag High', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "fault_flag_low":                              {'address': 4615, 'name': 'Fault Flag Low', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "alarm_flag":                                  {'address': 4616, 'name': 'Alarm Flag', 'precision': 0, 'index': 0, 'size': 2, 'state_class': 'measurement'},
    "bms_total_current":                           {'address': 4620, 'name': 'BMS Total Current', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "bms_total_voltage":                           {'address': 4621, 'name': 'BMS Total Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "bms_total_soc":                               {'address': 4624, 'name': 'BMS Total SOC', 'precision': 1, 'index': 1, 'unit': '%', 'device_class': 'battery', 'state_class': 'measurement'},
    "bms_parallel_number":                         {'address': 4628, 'name': 'BMS Parallel Number', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "bms_parallel_status":                         {'address': 4629, 'name': 'BMS Parallel Status', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "bms_maximum_cell_voltage_no":                 {'address': 4632, 'name': 'BMS Maximum Cell Voltage No', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "bms_maximum_cell_voltage":                    {'address': 4633, 'name': 'BMS Maximum Cell Voltage', 'precision': 0, 'index': 0, 'unit': 'mV', 'state_class': 'measurement'},
    "bms_minimum_cell_voltage_no":                 {'address': 4634, 'name': 'BMS Minimum Cell Voltage No', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "bms_minimum_cell_voltage":                    {'address': 4635, 'name': 'BMS Minimum Cell Voltage', 'precision': 0, 'index': 0, 'unit': 'mV', 'state_class': 'measurement'},
    "bms_maximum_cell_temperature_no":             {'address': 4636, 'name': 'BMS Maximum Cell temperature No', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "bms_maximum_cell_temperature":                {'address': 4637, 'name': 'BMS Maximum Cell temperature', 'precision': 0, 'index': 3, 'unit': '°C', 'device_class': 'temperature', 'state_class': 'measurement'},
    "bms_minimum_cell_temperature_no":             {'address': 4638, 'name': 'BMS Minimum Cell temperature No', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "bms_minmum_cell_temperature":                 {'address': 4639, 'name': 'BMS Minmum Cell temperature', 'precision': 0, 'index': 3, 'unit': '°C', 'device_class': 'temperature', 'state_class': 'measurement'},
    "sn1":                                         {'address': 4640, 'name': 'SN1', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "sn2":                                         {'address': 4641, 'name': 'SN2', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "sn3":                                         {'address': 4642, 'name': 'SN3', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "sn4":                                         {'address': 4643, 'name': 'SN4', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "sn5":                                         {'address': 4644, 'name': 'SN5', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "ats_start_signal":                            {'address': 4654, 'name': 'ATS Start Signal', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "4_communication_frame_format_4_1_8_k_setting_quantity_information_data_address_byte_size_paramet_er_parameter_unit": {'address': 4655, 'name': '4. Communication frame format 4.1 8K Setting Quantity Information Data Address Byte Size Paramet er Parameter Unit', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "max_pv_input_power":                          {'address': 8456, 'name': 'Max PV Input Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "bat1_max_charge_current_value":               {'address': 8471, 'name': 'Bat1 Max Charge Current Value', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "bat1_max_dis_charge_current_value":           {'address': 8472, 'name': 'Bat1 Max DisCharge Current Value', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "grid_start_signal":                           {'address': 8477, 'name': 'Grid Start Signal', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "gen_force_run":                               {'address': 8479, 'name': 'Gen Force Run', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "on_grid_battery_auto_start_charge_soc":       {'address': 8480, 'name': 'On Grid Battery Auto Start Charge SOC', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "on_grid_battery_exit_auto_charge_soc":        {'address': 8481, 'name': 'On Grid Battery Exit Auto Charge SOC', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "on_gen_battery_auto_start_charge_soc":        {'address': 8482, 'name': 'On Gen Battery Auto Start Charge SOC', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "on_gen_battery_exit_auto_charge_soc":         {'address': 8483, 'name': 'On Gen Battery Exit Auto Charge SOC', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "grid_charge_battery_current":                 {'address': 8484, 'name': 'Grid Charge Battery Current', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "gen_charge_battery_current":                  {'address': 8485, 'name': 'Gen Charge Battery Current', 'precision': 1, 'index': 8, 'unit': 'A', 'device_class': 'current', 'state_class': 'measurement'},
    "gen_start_signal":                            {'address': 8486, 'name': 'Gen Start Signal', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "grid_charge_battery_total_enable":            {'address': 8487, 'name': 'Grid Charge Battery Total Enable', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "gen_charge_battery_total_enable":             {'address': 8488, 'name': 'Gen Charge Battery Total Enable', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "on_grid_battery_1_auto_start_charge_voltage": {'address': 8490, 'name': 'On Grid Battery 1 Auto Start Charge Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "on_grid_battery_1_exit_auto_charge_voltage":  {'address': 8491, 'name': 'On Grid Battery 1 Exit Auto Charge Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "on_gen_battery_1_auto_start_charge_voltage":  {'address': 8492, 'name': 'On Gen Battery 1 Auto Start Charge Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "on_gen_battery_1_exit_auto_charge_voltage":   {'address': 8493, 'name': 'On Gen Battery 1 Exit Auto Charge Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "battery_equalization_voltage":                {'address': 8495, 'name': 'Battery Equalization Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "battery_equalization_days":                   {'address': 8496, 'name': 'Battery Equalization Days', 'precision': 0, 'index': 0, 'unit': 'd', 'state_class': 'measurement'},
    "battery_equalization_hours":                  {'address': 8497, 'name': 'Battery Equalization Hours', 'precision': 1, 'index': 1, 'unit': 'h', 'state_class': 'measurement'},
    "battery_absorption_voltage":                  {'address': 8499, 'name': 'Battery Absorption Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "battery_1_float_voltage":                     {'address': 8500, 'name': 'Battery 1 Float Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "temperature_coefficient_of_battery":          {'address': 8501, 'name': 'Temperature coefficient of battery', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "battery_resistance":                          {'address': 8502, 'name': 'Battery Resistance', 'precision': 0, 'index': 0, 'unit': 'mΩ', 'state_class': 'measurement'},
    "battery_1_ac_restart_voltage":                {'address': 8503, 'name': 'Battery 1 AC Restart Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "battery_1_low_alarm_voltage":                 {'address': 8504, 'name': 'Battery 1 Low Alarm Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "battery_1_low_shut_down_voltage":             {'address': 8505, 'name': 'Battery 1 Low ShutDown Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "battery_ac_restart_soc":                      {'address': 8506, 'name': 'Battery AC Restart SOC', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "battery_low_alarm_soc":                       {'address': 8507, 'name': 'Battery Low Alarm SOC', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "battery_low_shut_down_soc":                   {'address': 8508, 'name': 'Battery Low Shut Down SOC', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "system_mode":                                 {'address': 8516, 'name': 'Work Mode', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "zero_export_to_load_sell_enable":             {'address': 8517, 'name': 'Zero Export To Load Sell Enable', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "zero_export_to_ct_sell_enable":               {'address': 8518, 'name': 'Zero Export To CT Sell Enable', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "max_sell_power":                              {'address': 8519, 'name': 'Max Sell Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "grid_peak_shaving_enable":                    {'address': 8520, 'name': 'Grid Peak Shaving Enable', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "grid_peak_shaving_power":                     {'address': 8521, 'name': 'Grid Peak Shaving Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "zero_export_power_hysteresis":                {'address': 8522, 'name': 'Zero Export Power Hysteresis', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "dispatch_mode_active_power_given":            {'address': 8524, 'name': 'Dispatch mode active power given', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "energy_pattern":                              {'address': 8525, 'name': 'Energy Pattern', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "reactive_power_control_mode":                 {'address': 8526, 'name': 'Reactive power control mode', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "reactive_power_given_va":                     {'address': 8527, 'name': 'Reactive power given VA', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "given_power_factor":                          {'address': 8528, 'name': 'Given power factor', 'precision': 2, 'index': 2, 'state_class': 'measurement'},
    "active_power_change_rate_0_01_s":             {'address': 8529, 'name': 'Active power change rate 0.01(%/S)', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "reactive_power_change_rate_0_01_s":           {'address': 8530, 'name': 'Reactive power change rate 0.01(%/S)', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "turn_on_ramp_rate_0_01_s":                    {'address': 8531, 'name': 'Turn On Ramp Rate 0.01(%/S)', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "recconnect_ramp_rate_0_01_s":                 {'address': 8532, 'name': 'Recconnect Ramp Rate 0.01(%/S)', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "recconect_wait_time_s":                       {'address': 8533, 'name': 'Recconect Wait Time s', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "grid_normal_connect_high_voltage":            {'address': 8624, 'name': 'Grid Normal Connect High Voltage', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "grid_normal_connect_low_voltage":             {'address': 8625, 'name': 'Grid Normal Connect Low Voltage', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "grid_normal_connect_high_frequency":          {'address': 8626, 'name': 'Grid Normal Connect High Frequency', 'precision': 2, 'index': 2, 'unit': 'Hz', 'device_class': 'frequency', 'state_class': 'measurement'},
    "grid_normal_connect_low_frequency":           {'address': 8627, 'name': 'Grid Normal Connect Low Frequency', 'precision': 2, 'index': 2, 'unit': 'Hz', 'device_class': 'frequency', 'state_class': 'measurement'},
    "grid_rec_connect_high_voltage":               {'address': 8628, 'name': 'Grid RecConnect High Voltage', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "grid_rec_connect_low_voltage":                {'address': 8629, 'name': 'Grid RecConnect Low Voltage', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "grid_rec_connect_high_frequency":             {'address': 8630, 'name': 'Grid RecConnect High Frequency', 'precision': 2, 'index': 2, 'unit': 'Hz', 'device_class': 'frequency', 'state_class': 'measurement'},
    "grid_rec_connect_low_frequency":              {'address': 8631, 'name': 'Grid RecConnect Low Frequency', 'precision': 2, 'index': 2, 'unit': 'Hz', 'device_class': 'frequency', 'state_class': 'measurement'},
    "grid1_over_voltage_value":                    {'address': 8666, 'name': 'Grid1 Over Voltage Value', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "grid1_over_voltage_time":                     {'address': 8667, 'name': 'Grid1 Over Voltage Time', 'precision': 2, 'index': 2, 'unit': 's', 'state_class': 'measurement'},
    "grid2_over_voltage_value":                    {'address': 8668, 'name': 'Grid2 Over Voltage Value', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "grid2_over_voltage_time":                     {'address': 8669, 'name': 'Grid2 Over Voltage Time', 'precision': 2, 'index': 2, 'unit': 's', 'state_class': 'measurement'},
    "grid3_over_voltage_value":                    {'address': 8670, 'name': 'Grid3 Over Voltage Value', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "grid1_under_voltage_value":                   {'address': 8676, 'name': 'Grid1 under Voltage Value', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "grid1_under_voltage_time":                    {'address': 8677, 'name': 'Grid1 under Voltage Time', 'precision': 2, 'index': 2, 'unit': 's', 'state_class': 'measurement'},
    "grid2_under_voltage_value":                   {'address': 8678, 'name': 'Grid2 under Voltage Value', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "grid2_under_voltage_time":                    {'address': 8679, 'name': 'Grid2 under Voltage Time', 'precision': 2, 'index': 2, 'unit': 's', 'state_class': 'measurement'},
    "grid3_under_voltage_value":                   {'address': 8680, 'name': 'Grid3 under Voltage Value', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "grid1_over_frequency_value":                  {'address': 8688, 'name': 'Grid1 Over frequency Value', 'precision': 2, 'index': 2, 'unit': 'Hz', 'device_class': 'frequency', 'state_class': 'measurement'},
    "grid1_over_frequency_time":                   {'address': 8689, 'name': 'Grid1 Over frequency Time', 'precision': 2, 'index': 2, 'unit': 's', 'state_class': 'measurement'},
    "grid2_over_frequency_value":                  {'address': 8690, 'name': 'Grid2 Over frequency Value', 'precision': 2, 'index': 2, 'unit': 'Hz', 'device_class': 'frequency', 'state_class': 'measurement'},
    "grid2_over_frequency_time":                   {'address': 8691, 'name': 'Grid2 Over frequency Time', 'precision': 2, 'index': 2, 'unit': 's', 'state_class': 'measurement'},
    "grid3_over_frequency_value":                  {'address': 8692, 'name': 'Grid3 Over frequency Value', 'precision': 2, 'index': 2, 'unit': 'Hz', 'device_class': 'frequency', 'state_class': 'measurement'},
    "grid1_under_frequency_value":                 {'address': 8698, 'name': 'Grid1 under frequency Value', 'precision': 2, 'index': 2, 'unit': 'Hz', 'device_class': 'frequency', 'state_class': 'measurement'},
    "grid1_under_frequency_time":                  {'address': 8699, 'name': 'Grid1 under frequency Time', 'precision': 2, 'index': 2, 'unit': 's', 'state_class': 'measurement'},
    "grid2_under_frequency_value":                 {'address': 8700, 'name': 'Grid2 under frequency Value', 'precision': 2, 'index': 2, 'unit': 'Hz', 'device_class': 'frequency', 'state_class': 'measurement'},
    "grid2_under_frequency_time":                  {'address': 8701, 'name': 'Grid2 under frequency Time', 'precision': 2, 'index': 2, 'unit': 's', 'state_class': 'measurement'},
    "grid3_under_frequency_value":                 {'address': 8702, 'name': 'Grid3 under frequency Value', 'precision': 2, 'index': 2, 'unit': 'Hz', 'device_class': 'frequency', 'state_class': 'measurement'},
    "10_minute_overvoltage_threshold_of_the_grid": {'address': 8709, 'name': '10 minute overvoltage threshold of the grid', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "eco_timeofuse":                               {'address': 8711, 'name': 'ECO_TimeOfUse', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "eco_effectiveweek":                           {'address': 8712, 'name': 'ECO_EffectiveWeek', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_1_grid_charge_enable":              {'address': 8713, 'name': 'ECO1_GridChargeEnable', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_1_gen_charge_enable":               {'address': 8714, 'name': 'ECO1_GenChargeEnable', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_1_start_time":                      {'address': 8715, 'name': 'ECO1_StartTime', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_1_stop_time":                       {'address': 8716, 'name': 'ECO1_StopTime', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_1_voltage":                         {'address': 8717, 'name': 'ECO1_Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "econ_rule_1_soc":                             {'address': 8718, 'name': 'ECO1_SOC', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "econ_rule_1_power":                           {'address': 8719, 'name': 'ECO1_Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "econ_rule_2_grid_charge_enable":              {'address': 8720, 'name': 'ECO2_GridChargeEnable', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_2_gen_charge_enable":               {'address': 8721, 'name': 'ECO2_GenChargeEnable', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_2_start_time":                      {'address': 8722, 'name': 'ECO2_StartTime', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_2_stop_time":                       {'address': 8723, 'name': 'ECO2_StopTime', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_2_voltage":                         {'address': 8724, 'name': 'ECO2_Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "econ_rule_2_soc":                             {'address': 8725, 'name': 'ECO2_SOC', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "econ_rule_2_power":                           {'address': 8726, 'name': 'ECO2_Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "econ_rule_3_grid_charge_enable":              {'address': 8727, 'name': 'ECO3_GridChargeEnable', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_3_gen_charge_enable":               {'address': 8728, 'name': 'ECO3_GenChargeEnable', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_3_start_time":                      {'address': 8729, 'name': 'ECO3_StartTime', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_3_stop_time":                       {'address': 8730, 'name': 'ECO3_StopTime', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_3_voltage":                         {'address': 8731, 'name': 'ECO3_Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "econ_rule_3_soc":                             {'address': 8732, 'name': 'ECO3_SOC', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "econ_rule_3_power":                           {'address': 8733, 'name': 'ECO3_Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "econ_rule_4_grid_charge_enable":              {'address': 8734, 'name': 'ECO4_GridChargeEnable', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_4_gen_charge_enable":               {'address': 8735, 'name': 'ECO4_GenChargeEnable', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_4_start_time":                      {'address': 8736, 'name': 'ECO4_StartTime', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_4_stop_time":                       {'address': 8737, 'name': 'ECO4_StopTime', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_4_voltage":                         {'address': 8738, 'name': 'ECO4_Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "econ_rule_4_soc":                             {'address': 8739, 'name': 'ECO4_SOC', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "econ_rule_4_power":                           {'address': 8740, 'name': 'ECO4_Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "econ_rule_5_grid_charge_enable":              {'address': 8741, 'name': 'ECO5_GridChargeEnable', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_5_gen_charge_enable":               {'address': 8742, 'name': 'ECO5_GenChargeEnable', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_5_start_time":                      {'address': 8743, 'name': 'ECO5_StartTime', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_5_stop_time":                       {'address': 8744, 'name': 'ECO5_StopTime', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_5_voltage":                         {'address': 8745, 'name': 'ECO5_Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "econ_rule_5_soc":                             {'address': 8746, 'name': 'ECO5_SOC', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "econ_rule_5_power":                           {'address': 8747, 'name': 'ECO5_Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "econ_rule_6_grid_charge_enable":              {'address': 8748, 'name': 'ECO6_GridChargeEnable', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_6_gen_charge_enable":               {'address': 8749, 'name': 'ECO6_GenChargeEnable', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_6_start_time":                      {'address': 8750, 'name': 'ECO6_StartTime', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_6_stop_time":                       {'address': 8751, 'name': 'ECO6_StopTime', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "econ_rule_6_voltage":                         {'address': 8752, 'name': 'ECO6_Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "econ_rule_6_soc":                             {'address': 8753, 'name': 'ECO6_SOC', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "econ_rule_6_power":                           {'address': 8754, 'name': 'ECO6_Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "gen_mode":                                    {'address': 8759, 'name': 'Gen Mode', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "gen_input_rate_power":                        {'address': 8760, 'name': 'Gen Input Rate Power', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "gen_power_peak_shaving":                      {'address': 8761, 'name': 'Gen Power Peak Shaving', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "gen_connect_to_grid_port_enable":             {'address': 8762, 'name': 'Gen Connect To Grid Port Enable', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "smart_load_close_enable":                     {'address': 8763, 'name': 'SmartLoad Close Enable', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "smart_load_close_bat_volt":                   {'address': 8764, 'name': 'SmartLoad_CloseBatVolt', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "smart_load_open_battery_voltage":             {'address': 8765, 'name': 'SmartLoad Open Battery Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "smart_load_close_soc":                        {'address': 8766, 'name': 'SmartLoad_CloseSOC', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "smart_load_open_soc":                         {'address': 8767, 'name': 'SmartLoad_OpenSOC', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "ac_couple_frz_high":                          {'address': 8768, 'name': 'AC_Couple_Frz_High', 'precision': 2, 'index': 2, 'unit': 'Hz', 'device_class': 'frequency', 'state_class': 'measurement'},
    "ac_couple_on_side":                           {'address': 8770, 'name': 'AC_Couple_OnSide', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "micro_close_battage_voltage":                 {'address': 8771, 'name': 'Micro_Close Battage Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "micro_open_battage_voltage":                  {'address': 8772, 'name': 'Micro_Open Battage Voltage', 'precision': 1, 'index': 1, 'unit': 'V', 'device_class': 'voltage', 'state_class': 'measurement'},
    "micro_close_soc":                             {'address': 8773, 'name': 'Micro_CloseSOC', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "micro_open_soc":                              {'address': 8774, 'name': 'Micro_OpenSOC', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "smart_load_close_min_pv_power":               {'address': 8775, 'name': 'SmartLoad_CloseMinPvPower', 'precision': 0, 'index': 3, 'unit': 'W', 'device_class': 'power', 'state_class': 'measurement'},
    "smart_load_off_gridimmediately_off":          {'address': 8776, 'name': 'SmartLoad_offGridimmediatelyOff', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "gen1_over_voltage_value":                     {'address': 8778, 'name': 'Gen1 Over Voltage Value', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "gen1_over_voltage_time":                      {'address': 8779, 'name': 'Gen1 Over Voltage Time', 'precision': 2, 'index': 2, 'unit': 's', 'state_class': 'measurement'},
    "gen2_over_voltage_value":                     {'address': 8780, 'name': 'Gen2 Over Voltage Value', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "gen2_over_voltage_time":                      {'address': 8781, 'name': 'Gen2 Over Voltage Time', 'precision': 2, 'index': 2, 'unit': 's', 'state_class': 'measurement'},
    "gen1_under_voltage_value":                    {'address': 8782, 'name': 'Gen1 Under Voltage Value', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "gen1_under_voltage_time":                     {'address': 8783, 'name': 'Gen1 Under Voltage Time', 'precision': 2, 'index': 2, 'unit': 's', 'state_class': 'measurement'},
    "gen2_under_voltage_value":                    {'address': 8784, 'name': 'Gen2 Under Voltage Value', 'precision': 0, 'index': 0, 'unit': '%', 'state_class': 'measurement'},
    "gen2_under_voltage_time":                     {'address': 8785, 'name': 'Gen2 Under Voltage Time', 'precision': 2, 'index': 2, 'unit': 's', 'state_class': 'measurement'},
    "gen1_over_frequency_value":                   {'address': 8786, 'name': 'Gen1 Over frequency Value', 'precision': 2, 'index': 2, 'unit': 'Hz', 'device_class': 'frequency', 'state_class': 'measurement'},
    "gen1_over_frequency_time":                    {'address': 8787, 'name': 'Gen1 Over frequency Time', 'precision': 2, 'index': 2, 'unit': 's', 'state_class': 'measurement'},
    "gen2_over_frequency_value":                   {'address': 8788, 'name': 'Gen2 Over frequency Value', 'precision': 2, 'index': 2, 'unit': 'Hz', 'device_class': 'frequency', 'state_class': 'measurement'},
    "gen2_over_frequency_time":                    {'address': 8789, 'name': 'Gen2 Over frequency Time', 'precision': 2, 'index': 2, 'unit': 's', 'state_class': 'measurement'},
    "gen1_under_frequency_value":                  {'address': 8790, 'name': 'Gen1 Under frequency Value', 'precision': 2, 'index': 2, 'unit': 'Hz', 'device_class': 'frequency', 'state_class': 'measurement'},
    "gen1_under_frequency_time":                   {'address': 8791, 'name': 'Gen1 Under frequency Time', 'precision': 2, 'index': 2, 'unit': 's', 'state_class': 'measurement'},
    "gen2_under_frequency_value":                  {'address': 8792, 'name': 'Gen2 Under frequency Value', 'precision': 2, 'index': 2, 'unit': 'Hz', 'device_class': 'frequency', 'state_class': 'measurement'},
    "gen2_under_frequency_time":                   {'address': 8793, 'name': 'Gen2 Under frequency Time', 'precision': 2, 'index': 2, 'unit': 's', 'state_class': 'measurement'},
    "value_ask_hex_response_hex_slave_address_01_slave_address_01_command_06_command_06_register_start_address_high_00_register_start_address_high_00_register_start_address_low_08_register_start_address_low_08_register_value_high_bit_aa_register_value_high_bit_aa_register_value_low_bit_aa_register_value_low_bit_aa_crc_low_bit_crc_low_bit_crc_high_bit_crc_high_bit_2_3_3_0x10_writes_multiple_registers_the_function_code_command_is_used_to_write_a_number_of_consecutive_addresses_to_the_register_the_values_required_to_be_written_are_the_requirements_specified_in_the_data_field_the_data_is_a_two_byte_register_the_normal_response_returns_the_function_code_the_start_address_and_the_number_of_register_writes_such_as_writing_register_number_0_x0001_address_is_according_to_0_x1194_writes_data_to_0_0_x0002_address_register_x01_cc_ask_hex_response_hex_slave_address_01_slave_address_01_command_10_command_10_register_start_address_high_00_register_start_address_high_00_register_start_address_low_01_register_start_address_low_01_register_number_high_bit_00_register_number_high_bit_00_register_number_low_bit_02_register_number_low_bit_02_byte_numbers_04_crc_low_bit_register_value_high_bit_01_11_crc_high_bit_register_value_low_bit_01_94_register_value_high_bit_02_11_register_value_low_bit_02_cc_crc_low_bit_crc_high_bit_3_command_lists_3_1_information_data_data_address_byte_size_parameter_parameter_unit": {'address': 43690, 'name': 'value: Ask （Hex） Response （Hex） Slave Address 01 Slave Address 01 Command 06 Command 06 Register Start Address High 00 Register Start Address High 00 Register Start Address Low 08 Register Start Address Low 08 Register Value High bit AA Register Value High bit AA Register Value Low bit AA Register Value Low bit AA CRC Low bit —— CRC Low bit —— CRC High bit —— CRC High bit —— 2.3.3. 0x10 Writes Multiple Registers The function code (command) is used to write a number of consecutive addresses to the register. The values required to be written are the requirements specified in the data field. The data is a two-byte register. The normal response returns the function code, the start address, and the number of register writes. Such as writing register number 0 x0001 address is according to 0 x1194, writes data to 0 0 x0002 address register x01CC. Ask （Hex） Response （Hex） Slave Address 01 Slave Address 01 Command 10 Command 10 Register Start Address High 00 Register Start Address High 00 Register Start Address Low 01 Register Start Address Low 01 Register Number High bit 00 Register Number High bit 00 Register Number Low bit 02 Register Number Low bit 02 Byte Numbers 04 CRC Low bit —— Register Value High bit（01） 11 CRC High bit —— Register Value Low bit（01） 94 Register Value High bit（02） 11 Register Value Low bit（02） CC CRC Low bit —— CRC High bit —— 3. Command lists 3.1 Information Data Data Address Byte Size Parameter Parameter Unit', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "device_type_id":                              {'address': 63488, 'name': 'Device TypeID', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "device_sub_type_id":                          {'address': 63489, 'name': 'Device SubTypeID', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "communication_protocol_information_version_id": {'address': 63490, 'name': 'Communication Protocol Information VersionID', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "communication_protocol_information_id":       {'address': 63491, 'name': 'Communication Protocol InformationID', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "machine_serial_number_snid":                  {'address': 63492, 'name': 'Machine Serial NumberSNID', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "machine_serial_number_snlen_id":              {'address': 63497, 'name': 'Machine Serial NumberSNLenID', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "display_board_software_number_version_id":    {'address': 63498, 'name': 'Display Board Software Number VersionID', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "mcu1_software_version_id":                    {'address': 63499, 'name': 'MCU1 Software VersionID', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "mcu2_software_version_id":                    {'address': 63500, 'name': 'MCU2 Software VersionID', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "display_board_hardware_version_id":           {'address': 63501, 'name': 'Display Board Hardware VersionID', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "control_board_hardware_version_id":           {'address': 63502, 'name': 'Control Board Hardware VersionID', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "power_board_hardware_version_id":             {'address': 63503, 'name': 'Power Board Hardware VersionID', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
    "flash_data_version_id":                       {'address': 63508, 'name': 'Flash Data VersionID', 'precision': 0, 'index': 0, 'state_class': 'measurement'},
}


# --- The two supported family members -----------------------------------------
# Derived, never hand-copied: a register added to the family map above reaches
# both models automatically, and can only be absent from the 8K by being listed
# in _IVGM_EIGHT_UNSUPPORTED above.

def _as_centi_kilowatt_power(registers):
    """Re-express the W-valued power registers as 0.01 kW, for the WHOLE family.

    WHY (hardware report, Sept 2026 — the first real IVGM feedback):
    on a 20K, `bat1_power` (0x1131) read **156** raw.  The true value was
    **1560 W**, so each count is 10 W = 0.01 kW — the register is NOT in watts,
    even though the protocol document says "W" for it.

    ⚠️ **Applied to the family map, so BOTH models get it.**  This looks like
    the cross-model inference this project refuses to make (see the T-REX-25
    freeze), but it is the opposite.  There, two models diverged because the
    25's firmware was *altered* and its scaling was *field-proven*; borrowing
    the 50's number would have overwritten measured truth with a guess.  Here
    neither IVGM has ever been in the field, and both maps are generated from
    ONE document — the same document, the same "W" column, the same generated
    entry.  The measurement did not reveal a 20K quirk; it revealed that the
    document's unit column is wrong.  A source error does not stop at the model
    that happened to be plugged in first.

    So the 8K stays consistent with its sibling until an 8K is actually
    measured.  If one is, and it really does report watts, split the family
    then — with that measurement in the commit message.

    Direction of risk matters too: if this is wrong for the 8K, its power
    readings are 1000x LOW (harmlessly wrong, obvious on sight) and its
    setpoints ask for 1000x too LITTLE power.  Leaving it in W when it is
    really 0.01 kW asks the inverter for 1000x too MUCH.

    The coordinator's scalers only ever DIVIDE (`_apply_scaling`: /10, /100,
    /1000), so a raw 156 can never be shown as 1560 W.  The only faithful
    representation is kW with index 9 (signed, /100) — exactly what the
    T-REX-25/50 maps use at these addresses.

    `precision` is 2, not the 1 the T-REX-25 map uses: precision is applied in
    the COORDINATOR (`_async_update_data`), so it rounds the value the EMS
    schedules on, not merely the display.  0.01 kW is the register's real
    resolution — rounding 1.56 to 1.6 would hand the EMS 1600 W for a
    1560 W reading.
    """
    converted = {}
    for key, info in registers.items():
        if info.get("unit") == "W" and info.get("device_class") == "power":
            info = {**info, "unit": "kW", "index": 9, "precision": 2}
        converted[key] = info
    return converted


#: The family map with the measured power scale applied — the basis for BOTH
#: models.  The register SET still differs per model; the SCALE does not.
_REGISTERS_IVGM_SCALED = _as_centi_kilowatt_power(_REGISTERS_IVGM_FAMILY)

#: 3-phase family member (phase C + PV3/PV4 + battery 2).  The register SET is
#: inferred from the "(8K donot support)" annotations; the power SCALING is
#: measured on this model — see _as_centi_kilowatt_power.
_REGISTERS_IVGM_TWENTY = _REGISTERS_IVGM_SCALED

#: The 8K: the documented register set, on the family's corrected power scale.
_REGISTERS_IVGM_EIGHT = {
    key: info for key, info in _REGISTERS_IVGM_SCALED.items()
    if key not in _IVGM_EIGHT_UNSUPPORTED
}


def _combined(registers):
    """Build the computed/aggregate sensors for one IVGM model.

    Only aggregates over registers the model actually has, so the 8K does not
    advertise a phase-C or battery-2 total it can never populate.
    """
    have = registers.__contains__

    def _sum(*keys):
        present = [k for k in keys if have(k)]
        return {
            "sources": present,
            "calc": lambda *vals: round(sum(v for v in vals if v is not None), 2),
        }

    combined = {
        "inverter_time": {
            "sources": ["time_year_month", "time_day_time",
                        "time_minutes_seconds", "time_week"],
            "calc": lambda ym, dh, ms, w: {
                "year": 2000 + (ym >> 8), "month": ym & 0xFF,
                "day": dh >> 8, "hour": dh & 0xFF,
                "minute": ms >> 8, "second": ms & 0xFF,
                "weekday": ["Sunday", "Monday", "Tuesday", "Wednesday",
                            "Thursday", "Friday", "Saturday"][w]
                           if 0 <= w <= 6 else f"Unknown({w})",
                "iso_datetime": f"{2000 + (ym >> 8):04d}-{ym & 0xFF:02d}-{dh >> 8:02d} "
                                f"{dh & 0xFF:02d}:{ms >> 8:02d}:{ms & 0xFF:02d}",
            },
            "name": "Inverter Time",
        },
    }
    combined["total_pv_power"] = dict(
        _sum("pv1_power", "pv2_power", "pv3_power", "pv4_power"),
        name="Total PV Power", unit="W", device_class="power",
        state_class="measurement", precision=0,
    )
    combined["battery_power"] = dict(
        _sum("bat1_power", "bat2_power"),
        name="Battery Power", unit="W", device_class="power",
        state_class="measurement", precision=0,
    )
    # Economic rule summaries, mirroring the TREX maps so the EMS card renders
    # the same "rule N" rows on every model.
    for rule in range(1, 7):
        keys = [f"econ_rule_{rule}_{s}" for s in
                ("grid_charge_enable", "start_time", "stop_time", "soc", "power")]
        if not all(have(k) for k in keys):
            continue
        combined[f"econ_rule_{rule}"] = {
            "sources": keys,
            "calc": (lambda charge, start, stop, soc, power: {
                "grid_charge": charge, "soc": soc, "power": power,
                "start": f"{start >> 8:02d}:{start & 0xFF:02d}",
                "stop": f"{stop >> 8:02d}:{stop & 0xFF:02d}",
            }),
            "name": f"Economic Rule {rule}",
        }
    return combined


_COMBINED_REGISTERS_IVGM_TWENTY = _combined(_REGISTERS_IVGM_TWENTY)
_COMBINED_REGISTERS_IVGM_EIGHT = _combined(_REGISTERS_IVGM_EIGHT)


def _register_sets(registers):
    """basic / basic_plus / full selections for one IVGM model.

    `basic` is the small always-polled set the EMS and the card need.  Note
    __init__.async_setup_entry additionally force-includes the control-loop
    registers into whichever set the user picks, so the Economic-mode watchdog
    can never be silently unarmed (CLAUDE.md, step 3b).
    """
    basic_keys = {
        "total_pv_power", "total_grid_power", "total_load_power", "battery_power",
        "inverter_time", "alarm_codes", "fault_codes",
        "bat1_soc", "bat2_soc", "bat1_voltage", "bat2_voltage",
        "phase_a_ct_current", "phase_b_ct_current", "phase_c_ct_current",
        "grid_frequency", "load_frequency", "generator_frequency",
        "phase_a_home_load_power", "phase_b_home_load_power", "phase_c_home_load_power",
        "homeload_day_cost_energy", "grid_day_cost_energy", "grid_day_gen_energy",
        "generator_day_cost_energy", "microinverter_day_cost_energy",
        "pv1_day_gen_energy", "pv2_day_gen_energy",
        "pv3_day_gen_energy", "pv4_day_gen_energy",
        "eco_timeofuse", "system_mode",
    }
    return {
        "basic": {k: v for k, v in registers.items() if k in basic_keys},
        "basic_plus": {
            k: v for k, v in registers.items()
            if k.startswith(("pv", "phase_", "bat", "bus", "total_", "generator_",
                             "grid_", "load_", "econ_rule_", "eco_", "alarm", "fault",
                             "inverter_", "system_"))
        },
        "full": registers,
    }


REGISTER_SETS_IVGM_TWENTY = _register_sets(_REGISTERS_IVGM_TWENTY)
REGISTER_SETS_IVGM_EIGHT = _register_sets(_REGISTERS_IVGM_EIGHT)
