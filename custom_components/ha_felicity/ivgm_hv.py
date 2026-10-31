"""Register map for the Felicity IVGM100600 — 10 kW three-phase, HIGH-VOLTAGE battery.

PROVISIONAL and MONITOR-ONLY (issue #206, Oct 2026).

Felicity support said this inverter is "the same as the T-REX-10K", and on the
T-REX-10 profile it does answer like one: battery voltage (0.1 V), battery
power, SOC, AC input voltage and frequency all decode plausibly.  But a register
dump from one unit (HA-Felicity 1.3.13, 2026-10-31, via a Waveshare RS485 TO POE
ETH (B) gateway) shows it is NOT register-identical, so — the rule for rating
variants (const.py, "Rating-only variants") — it gets its own entry and the
shared T-REX-10 map is left untouched:

* 0x1225-0x1238 (the line-load / total-load "consumption energy inquiry"
  totals) answer ILLEGAL DATA ADDRESS on this unit; on the maintainer's T-REX-10
  they answer.  Left out.
* 0x120C TotalCurrent is SIGNED: raw 65530 = -6 -> -0.6 A, and physics agrees
  (battery -148 W / 212.5 V = -0.70 A).  The T-REX-10 map reads it unsigned.
* 0x120D TotalVoltage is 0.1 V here: raw 2130 -> 213.0 V beside a 212.5 V
  battery.  Felicity's document says 0.01 V (21.3 V).

Not settled (one dump): 0x119D/0x119E LoadPower_LineSide read the words 4502 /
4503 — the decimal numbers of the neighbouring addresses, not a power.  Possibly
the gateway's "Auto query storage" mode.  Kept as mapped; the plausibility guard
drops a value beyond 10x the rating.

MONITOR-ONLY (const.MONITOR_ONLY_MODELS), at the user's own risk: the battery is
high-voltage (180-580 V), while every voltage the EMS writes (Rule 1 charge
58 V / discharge 50 V) and every battery setting in the T-REX-10 map is for a
48 V pack.  So nothing is written: only telemetry (0x1xxx), the version words
(0xF8xx) and the BMS discharge depth (0x212A, read-only here; the energy-flow
card uses it) are mapped, and every writable entity type is removed.  Control
needs its own evidence from a high-voltage unit first.

Derived from the T-REX-10 map at import time — not a hand-typed copy, so a fix
to a shared register reaches this model too (CLAUDE.md, "never hand-type a copy
of production code").
"""

from .trex_ten import (
    _COMBINED_REGISTERS_TREX_TEN,
    _REGISTERS_TREX_TEN,
    REGISTER_SETS_TREX_TEN,
)

#: Answer ILLEGAL DATA ADDRESS on the IVGM100600 (dump, issue #206).
_ABSENT = {
    "line_load_consumption_energy_total",
    "line_load_consumption_energy_year",
    "line_load_consumption_energy_month",
    "line_load_consumption_energy_day",
    "total_load_consumption_energy_total",
    "total_load_consumption_energy_year",
    "total_load_consumption_energy_month",
    "total_load_consumption_energy_day",
}

#: Read-only settings kept as plain sensors (no "type", so no control entity).
_READ_ONLY_SETTINGS = {"battery_discharge_depth_ongrid_bms"}

#: Entity types that would create a control (number/select/time/date).  A
#: "status" type is a read-only enum sensor and stays.
_WRITABLE_KEYS = ("type", "options", "min", "max", "step", "dynamic_battery")

#: Measured on this hardware (issue #206 dump).
_CORRECTIONS = {
    "total_current": {"index": 8, "precision": 1},   # signed, 0.1 A
    "total_voltage": {"index": 1, "precision": 1},   # 0.1 V, not 0.01 V
}


def _keep(key: str, info: dict) -> bool:
    if key in _ABSENT:
        return False
    if key in _READ_ONLY_SETTINGS:
        return True
    address = info.get("address", 0)
    return address < 0x2000 or address >= 0xF000


def _monitor_entry(key: str, info: dict) -> dict:
    entry = dict(info)
    if entry.get("type") != "status":
        for field in _WRITABLE_KEYS:
            entry.pop(field, None)
    entry.update(_CORRECTIONS.get(key, {}))
    return entry


_REGISTERS_IVGM_HUNDRED = {
    key: _monitor_entry(key, info)
    for key, info in _REGISTERS_TREX_TEN.items()
    if _keep(key, info)
}

#: Combined sensors whose sources all remain (the Economic Rule summaries go:
#: their registers are not mapped on a monitor-only model).
_COMBINED_REGISTERS_IVGM_HUNDRED = {
    key: info
    for key, info in _COMBINED_REGISTERS_TREX_TEN.items()
    if all(src in _REGISTERS_IVGM_HUNDRED for src in info.get("sources", []))
}

REGISTER_SETS_IVGM_HUNDRED = {
    name: {key: _REGISTERS_IVGM_HUNDRED[key] for key in regs if key in _REGISTERS_IVGM_HUNDRED}
    for name, regs in REGISTER_SETS_TREX_TEN.items()
}
