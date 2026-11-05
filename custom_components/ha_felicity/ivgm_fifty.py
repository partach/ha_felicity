"""Register map for the Felicity IVGM-50K — the T-REX-50 map with 0.01 kW power.

Two owners, two measurements, two answers (CLAUDE.md, "Power-register scaling"):

* **IVGM-50K driven by the T-REX-50 map** (Sept 2026): every telemetry power
  register read 10x high at /10.  Confirmed four ways — string power ten times
  the installed DC capacity, Riemann-integrated power vs the day-energy
  registers, an external Eastron sub-meter, and power factor / frequency in the
  same table already at -2.  So 1 count = 0.01 kW.  That fix was put on the
  shared T-REX-50 map (c7066fb).
* **T-REX-50KHP3G01** (issue #237, Nov 2026, firmware master 227 / slave 109 /
  comm 207): /100 reads 10x LOW.  Raw PV1 32 at 616.9 V x 5.3 A = 3270 W, PV2 25
  at 625.2 V x 4.0 A = 2500 W, Bat1 27 at 640.2 V x 4.2 A = 2689 W.  So 1 count =
  0.1 kW there, and the T-REX-50 map is back at /10.

A measurement on one model says nothing about another, so per the rule for
variants that are not register-identical (const.py, "Rating-only variants") the
IVGM-50K gets its own entry and the T-REX-50 map is not bent to fit it.

Derived from the T-REX-50 map at import time — not a hand-typed copy — so every
other register stays shared.  Only the 30 telemetry power registers differ.  The
setpoints (index 1, written) are untouched: the IVGM-50K owner ran them on the
T-REX-50 map and reported no fault, and the T-REX-50's own defaults (rated
power 50.0 kW at index 1) agree.
"""

from .trex_fifty import (
    _COMBINED_REGISTERS_TREX_FIFTY,
    _REGISTERS_TREX_FIFTY,
    REGISTER_SETS_TREX_FIFTY,
)


def _is_telemetry_power(info: dict) -> bool:
    """A power reading (kW/kVA at /10), not a setpoint (kW at index 1)."""
    return info.get("unit") in ("kW", "kVA") and info.get("index") == 8


def _centi_kilowatt(info: dict) -> dict:
    entry = dict(info)
    if _is_telemetry_power(entry):
        entry.update({"index": 9, "precision": 2})   # signed /100, 0.01 kW
    return entry


_REGISTERS_IVGM_FIFTY = {
    key: _centi_kilowatt(info) for key, info in _REGISTERS_TREX_FIFTY.items()
}

#: The combined sensors add the kW sources (and x1000 for W), so they follow
#: whatever scale their sources have.
_COMBINED_REGISTERS_IVGM_FIFTY = _COMBINED_REGISTERS_TREX_FIFTY

REGISTER_SETS_IVGM_FIFTY = {
    name: {key: _REGISTERS_IVGM_FIFTY[key] for key in regs if key in _REGISTERS_IVGM_FIFTY}
    for name, regs in REGISTER_SETS_TREX_FIFTY.items()
}
