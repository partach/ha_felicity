"""Register map for the Felicity IVAM high-frequency inverter (IVAM6048P1G1).

Source: Felicity "IVAM High-Frequency Inverter External Communication Protocol
RS485", v01 2026-07-06 — frozen in ``ivam_documented_registers.json``.  Every
address and name below comes from that document; none is borrowed from another
map (tests/test_ivam_support.py enforces it).

⚠️ MONITOR-ONLY.  The document's only register table is read-only telemetry
(section 3.1).  It defines no operating/economic mode, no time-of-use rule and no
power limit — nothing the EMS could drive — so const.MONITOR_ONLY_MODELS lists
this model and the integration never writes to it (type_specific refuses every
write).  The telemetry addresses match the IVGM / T-REX-25/50 layout for the
same quantities, but that does NOT license borrowing their SETTING addresses:
an undocumented write is how a grid-protection threshold gets changed by
accident (see const.IVGM_UNSUPPORTED_WRITES).  docs/IVAM_SUPPORT_GAPS.md says
what would make control possible.

⚠️ SCALES ARE THE DOCUMENT'S AND UNMEASURED.  The document gives power as plain
W ("Rate 0").  The IVGM document said the same and was wrong by a factor of 10
(0.01 kW per count, measured twice) — so the power scale here is a claim to
check against a register dump (P vs V x I) before anyone relies on it, not a
fact.  Monitoring only, so a wrong scale mislabels a reading; it cannot command
anything.

Key names reuse the shared names (`bat1_soc`, `pv1_power`, `grid_a_phase_power`
…) wherever the quantity is the same, because type_specific and the cards look
registers up by key; the display NAMES are the document's own.
"""

_W = {"unit": "W", "device_class": "power", "state_class": "measurement"}
_V = {"unit": "V", "device_class": "voltage", "state_class": "measurement"}
_A = {"unit": "A", "device_class": "current", "state_class": "measurement"}
_KWH = {"unit": "kWh", "device_class": "energy", "state_class": "total_increasing"}


def _reg(address, name, index, precision, size=1, **extra):
    out = {"address": address, "name": name, "index": index, "precision": precision}
    if size != 1:
        out["size"] = size
    out.setdefault("state_class", "measurement")
    out.update(extra)
    return out


# index: 0 raw, 1 unsigned /10, 2 /100, 3 signed raw, 8 signed /10 — the
# document's "int" is signed, its "Rate -1" is /10.
_REGISTERS_IVAM_SIX = {
    "alarm_codes":               _reg(0x1109, "Alarm Code", 0, 0),
    "fault_codes":               _reg(0x110A, "Fault Code", 0, 0),
    "pv1_voltage":               _reg(0x1118, "PV1 input voltage", 1, 1, **_V),
    "pv2_voltage":               _reg(0x1119, "PV2 input voltage", 1, 1, **_V),
    "pv1_current":               _reg(0x111C, "PV1 Current", 8, 1, **_A),
    "pv2_current":               _reg(0x111D, "PV2 Current", 8, 1, **_A),
    "pv1_power":                 _reg(0x1128, "PV1 input power", 3, 0, **_W),
    "pv2_power":                 _reg(0x1129, "PV2 input power", 3, 0, **_W),
    # Battery: the document names these plainly ("Battery voltage" …), and those
    # names are exactly what the energy-flow card resolves — no aggregate needed.
    "bat1_voltage":              _reg(0x112C, "Battery voltage", 1, 1, **_V),
    "bat1_current":              _reg(0x1130, "Battery current", 8, 1, **_A),   # negative = discharging
    "bat1_power":                _reg(0x1131, "Battery power", 3, 0, **_W),     # negative = discharging
    "bat1_soc":                  _reg(0x1138, "Battery SOC", 1, 1, unit="%", device_class="battery"),
    "scc_heatsink_temperature":  _reg(0x1144, "SccHs temperature", 3, 0, unit="°C",
                                      device_class="temperature"),
    "generator_a_current":       _reg(0x114A, "Generater current", 8, 1, **_A),
    "generator_a_voltage":       _reg(0x114D, "Generater voltage", 1, 1, **_V),
    "grid_a_voltage":            _reg(0x1150, "Grid voltage", 1, 1, **_V),
    "load_a_voltage":            _reg(0x1156, "Load voltage", 1, 1, **_V),
    "grid_a_current":            _reg(0x115C, "Grid current", 8, 1, **_A),
    "load_a_current":            _reg(0x1162, "Load current", 8, 1, **_A),
    "load_frequency":            _reg(0x116F, "Load frequency", 2, 2, unit="Hz", device_class="frequency"),
    "generator_a_phase_power":   _reg(0x1170, "Generater Power", 3, 0, **_W),
    "grid_a_phase_power":        _reg(0x1173, "Grid Power", 3, 0, **_W),
    "load_aphase_power":         _reg(0x1179, "Load Power", 3, 0, **_W),
    "load_apparent_power":       _reg(0x117C, "Load apparent power", 0, 0, unit="VA",
                                      device_class="apparent_power"),
    "load_percentage":           _reg(0x117F, "Load percentage", 0, 0, unit="%"),
    "smart_load_voltage":        _reg(0x1185, "Smart Load voltage", 1, 1, **_V),
    "smart_load_current":        _reg(0x1188, "Smart Load current", 8, 1, **_A),
    "smart_load_power":          _reg(0x118C, "Smart Load power", 3, 0, **_W),
    "pv1_total_gen_energy":      _reg(0x1195, "PV1 Generated energy inquiry Total", 1, 1, size=2, **_KWH),
    "pv2_total_gen_energy":      _reg(0x1197, "PV2 Generated energy inquiry Total", 1, 1, size=2, **_KWH),
    "load_total_cost_energy":    _reg(0x11A9, "Load consumption energy inquiry Total", 1, 1, size=2, **_KWH),
    "pv1_day_gen_energy":        _reg(0x11B1, "PV1 Generated energy inquiry This day", 1, 1, **_KWH),
    "pv1_month_gen_energy":      _reg(0x11B2, "PV1 Generated energy inquiry This month", 1, 1, size=2, **_KWH),
    "pv1_year_gen_energy":       _reg(0x11B4, "PV1 Generated energy inquiry This year", 1, 1, size=2, **_KWH),
    "pv2_day_gen_energy":        _reg(0x11B6, "PV2 Generated energy inquiry This day", 1, 1, **_KWH),
    "pv2_month_gen_energy":      _reg(0x11B7, "PV2 Generated energy inquiry This month", 1, 1, size=2, **_KWH),
    "pv2_year_gen_energy":       _reg(0x11B9, "PV2 Generated energy inquiry This year", 1, 1, size=2, **_KWH),
    "load_day_cost_energy":      _reg(0x11E5, "Load consumption energy inquiry This day", 1, 1, **_KWH),
    "load_month_cost_energy":    _reg(0x11E6, "Load consumption energy inquiry This month", 1, 1, size=2, **_KWH),
    "load_year_cost_energy":     _reg(0x11E8, "Load consumption energy inquiry This year", 1, 1, size=2, **_KWH),
    # Not mapped: the "inquiry - Day/Month/Year" block at 0x1260-0x1269 and
    # 0x1294-0x1298.  The document lists them beside the "This day/month/year"
    # registers above without saying which period they answer — most likely a
    # query for a date set elsewhere — so polling them would publish numbers
    # nobody can interpret.  They stay in the transcript, so a register dump
    # still reads them.
}


def _combined(registers):
    """Aggregates carrying the names the energy-flow card resolves.

    Same contract as ivgm._combined (tests/test_card_contract.py): the card
    finds a sensor by the slug of its display NAME, so the shared names live
    here while the registers keep the document's.  Only what the IVAM measures:
    there is no grid-side meter (so no line-side "Home Load") and no BMS depth
    of discharge — the card falls back for both (see the card-contract test's
    per-model exemptions).
    """
    def _sum_w(name, *keys):
        present = [k for k in keys if k in registers]
        return {
            "sources": present,
            "calc": lambda *vals: round(sum(v or 0.0 for v in vals)),
            "name": name, "unit": "W", "device_class": "power",
            "state_class": "measurement", "precision": 0,
        }

    return {
        "total_pv_power": _sum_w("Total PV Power", "pv1_power", "pv2_power"),
        "total_ac_input_power": _sum_w("Total AC Input Power", "grid_a_phase_power"),
        "total_ac_output_active_power": _sum_w("Total AC Output Active Power", "load_aphase_power"),
        "total_generator_active_power": _sum_w("Total Generator Active Power", "generator_a_phase_power"),
        "battery_capacity": {
            "sources": ["bat1_soc"],
            "calc": lambda s: round(s, 1) if s is not None else None,
            "name": "Battery Capacity", "unit": "%", "device_class": "battery",
            "state_class": "measurement", "precision": 1,
        },
    }


_COMBINED_REGISTERS_IVAM_SIX = _combined(_REGISTERS_IVAM_SIX)

_BASIC_KEYS = {
    "alarm_codes", "fault_codes", "pv1_power", "pv2_power",
    "bat1_voltage", "bat1_current", "bat1_power", "bat1_soc",
    "grid_a_phase_power", "grid_a_current", "load_aphase_power",
    "generator_a_phase_power", "pv1_day_gen_energy", "pv2_day_gen_energy",
    "load_day_cost_energy",
}

REGISTER_SETS_IVAM_SIX = {
    "basic": {k: v for k, v in _REGISTERS_IVAM_SIX.items() if k in _BASIC_KEYS},
    "basic_plus": {
        k: v for k, v in _REGISTERS_IVAM_SIX.items()
        if not k.endswith(("_month_gen_energy", "_year_gen_energy",
                           "_month_cost_energy", "_year_cost_energy"))
    },
    "full": _REGISTERS_IVAM_SIX,
}
