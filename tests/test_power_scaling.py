"""Power-register scaling, pinned PER MODEL from measured hardware behaviour.

WHY THIS FILE EXISTS
--------------------
Power scaling is the highest-consequence number in a register map and the
easiest to get wrong, because the vendor documentation is wrong about it and
because two models that share a document do not necessarily share a scale.

Two independent customer reports, Sept 2026:

* **IVGM-20K** — `bat1_power` (0x1131) read raw **156** where the true power was
  **1560 W**, i.e. 0.01 kW per count.  The IVGM protocol document says "W" for
  that register.  It is wrong.
* **IVGM-50K driven by the T-REX-50 map** — every telemetry power sensor read
  **10x too high**, corroborated four independent ways: string power exceeding
  the installed DC capacity tenfold, Riemann-integrated power vs the (correct)
  day-energy registers, an external Eastron sub-meter, and the fact that power
  factor and frequency in the same table already use the -2 scale.  Root cause:
  the "Multiple" column says -1 for every Kw/KVA row; the device uses -2.

⚠️ **DO NOT assert that two models' maps agree.**  It is tempting — the T-REX-25
and T-REX-50 share a protocol document — but the maintainer reports the
**T-REX-25's firmware was altered**, so the two legitimately differ.  Cross-map
equality would encode a false premise and block a correct divergence.  This is
the same rule as the IVGM addresses rule in docs/IVGM_SUPPORT_GAPS.md: every
model's scaling is that model's own data, evidenced per model.

Each assertion below therefore pins ONE map against what was measured on THAT
hardware, and says which evidence it came from.
"""

from __future__ import annotations

import sys

import pytest

const = sys.modules["custom_components.ha_felicity.const"]


def _registers(model):
    return const.MODEL_REGISTRY[model]["registers"]


def _scale(raw: int, index: int) -> float:
    """Mirror of coordinator._apply_scaling for the indices used here."""
    if index in (1, 8):
        return raw / 10.0
    if index in (2, 9):
        return raw / 100.0
    if index == 4:
        return raw / 1000.0
    return raw


def _watts(raw: int, info: dict) -> float:
    """What the integration would report, in watts, for this raw register."""
    value = round(_scale(raw, info.get("index", 0)), info.get("precision", 0))
    return value * 1000.0 if info.get("unit") in ("kW", "kVA") else value


# --------------------------------------------------------------------------
# T-REX-50 — reported 10x too high; measured scale is 0.01 kW per count
# --------------------------------------------------------------------------

def test_trex_fifty_telemetry_power_is_centi_kilowatt():
    """All 30 telemetry power registers must be signed /100 (index 9).

    Index 8 (/10) made a 50 kW inverter report up to 500 kW — physically
    impossible against both the nameplate and the installed array.
    """
    regs = _registers(const.INVERTER_MODEL_TREX_FIFTY)
    telemetry = {k: i for k, i in regs.items()
                 if i.get("unit") in ("kW", "kVA") and i.get("index") != 1}
    wrong = {k: i["index"] for k, i in telemetry.items() if i["index"] != 9}
    assert not wrong, f"T-REX-50 telemetry power must be index 9 (/100), got {wrong}"
    assert len(telemetry) == 30, f"expected 30 telemetry power registers, found {len(telemetry)}"


def test_trex_fifty_power_precision_keeps_the_registers_resolution():
    """precision must be 2, because the coordinator rounds with it.

    `_async_update_data` applies `round(value, precision)` BEFORE the EMS ever
    sees the number, so precision 1 would discard the register's real 0.01 kW
    resolution — turning a 1.56 kW reading into 1.6 kW for scheduling, not just
    for display.
    """
    regs = _registers(const.INVERTER_MODEL_TREX_FIFTY)
    coarse = {k: i.get("precision") for k, i in regs.items()
              if i.get("unit") in ("kW", "kVA") and i.get("index") == 9
              and i.get("precision", 0) < 2}
    assert not coarse, f"index-9 power registers need precision 2, got {coarse}"


def test_trex_fifty_reports_a_plausible_reading():
    """Regression on the actual complaint: a 50 kW unit cannot read 1560 kW."""
    info = _registers(const.INVERTER_MODEL_TREX_FIFTY)["pv1_power"]
    kw = round(_scale(15600, info["index"]), info["precision"])
    assert kw == 156.0, f"raw 15600 should read 156.0 kW, got {kw}"


def test_trex_fifty_setpoints_are_left_alone():
    """The 9 kW SETPOINT registers stay at index 1 until hardware says otherwise.

    `max_export_power_to_grid`, `grid_peak_shaving_power`, `econ_rule_N_power`
    and `geninputratepower` are WRITTEN, not read.  No one has measured them, and
    the reporter explicitly flagged them as unverified and safety-relevant
    (export limiting, peak shaving).  Changing a write scale on a guess is how
    you ask an inverter for ten times the power you meant.  This test exists so
    the change is a deliberate act with evidence attached, not a tidy-up.
    """
    regs = _registers(const.INVERTER_MODEL_TREX_FIFTY)
    setpoints = {k: i for k, i in regs.items()
                 if i.get("unit") in ("kW", "kVA") and i.get("index") == 1}
    assert len(setpoints) == 9, f"expected 9 unverified setpoints, found {sorted(setpoints)}"
    for name in ("econ_rule_1_power", "grid_peak_shaving_power", "max_export_power_to_grid"):
        assert name in setpoints, f"{name} should still be an unverified index-1 setpoint"


# --------------------------------------------------------------------------
# IVGM — 20K measured at 0.01 kW; 8K left in W as its own document states
# --------------------------------------------------------------------------

def test_ivgm_twenty_bat1_power_matches_the_hardware_report():
    """raw 156 -> 1560 W, the exact number the customer reported."""
    info = _registers(const.INVERTER_MODEL_IVGM_TWENTY)["bat1_power"]
    assert _watts(156, info) == 1560.0, (
        f"IVGM-20K raw 156 should be 1560 W, got {_watts(156, info)} "
        f"(unit={info.get('unit')}, index={info.get('index')}, "
        f"precision={info.get('precision')})"
    )


def test_ivgm_eight_stays_in_watts():
    """The 8K keeps its documented W scale — nothing has contradicted it.

    Only the 20K was measured.  Carrying the 20K's correction onto the 8K would
    be the cross-model inference this project has repeatedly decided not to make.
    """
    info = _registers(const.INVERTER_MODEL_IVGM_EIGHT)["bat1_power"]
    assert info.get("unit") == "W" and info.get("index") == 3, (
        f"IVGM-8K bat1_power should stay raw watts, got "
        f"unit={info.get('unit')} index={info.get('index')}"
    )


@pytest.mark.parametrize("model", list(const.IVGM_MODELS))
def test_ivgm_power_registers_are_internally_consistent(model):
    """Within one IVGM model every power register uses the same convention."""
    regs = _registers(model)
    power = {k: i for k, i in regs.items() if i.get("device_class") == "power"}
    assert power, f"{model}: no power registers at all"
    conventions = {(i.get("unit"), i.get("index")) for i in power.values()}
    assert len(conventions) == 1, (
        f"{model}: power registers disagree with each other: {conventions}"
    )


def test_ivgm_twenty_is_not_declared_watt_valued():
    """WATT_POWER_MODELS drives the WRITE path as well as the read path.

    With the 20K measured at 0.01 kW, writing watts into ECO1_Power would ask
    for 1000x the intended power.  The 8K remains watt-valued.
    """
    assert const.INVERTER_MODEL_IVGM_TWENTY not in const.WATT_POWER_MODELS
    assert const.INVERTER_MODEL_IVGM_EIGHT in const.WATT_POWER_MODELS


# --------------------------------------------------------------------------
# T-REX-25 — FROZEN by maintainer decision
# --------------------------------------------------------------------------

def test_trex_twenty_five_scaling_is_frozen():
    """T-REX-25 power scaling must not drift. It is proven in the field.

    This is a maintainer decision, not an inference: the 25's firmware was
    **altered**, so it can legitimately differ from the 50 even though the two
    share a protocol document — and its current values are known to work on real
    installations. That makes "harmonise it with the 50" an actively harmful
    kind of tidy-up, and a tempting one, because the two maps sit side by side
    and differ by a single digit.

    Note precision stays **1** here while the T-REX-50 uses 2. That asymmetry is
    intentional: if the altered firmware reports at 0.1 kW resolution, precision
    1 is correct for it and 2 would invent a digit. Nobody has measured it, so
    it stays as shipped.

    If a measurement ever justifies changing it, change this test in the same
    commit and put the evidence in the message — that is the whole point of the
    guard.
    """
    regs = const.MODEL_REGISTRY[const.INVERTER_MODEL_TREX_TWENTY_FIVE]["registers"]
    power = {k: i for k, i in regs.items() if i.get("unit") in ("kW", "kVA")}

    telemetry = {k: (i["index"], i["precision"]) for k, i in power.items() if i["index"] != 1}
    setpoints = {k: (i["index"], i["precision"]) for k, i in power.items() if i["index"] == 1}

    assert len(telemetry) == 30, f"expected 30 telemetry power registers, found {len(telemetry)}"
    assert set(telemetry.values()) == {(9, 1)}, (
        "T-REX-25 telemetry power must stay index 9 / precision 1 (field-proven); "
        f"found {sorted(set(telemetry.values()))}"
    )
    assert len(setpoints) == 9, f"expected 9 setpoint power registers, found {len(setpoints)}"
    assert set(setpoints.values()) == {(1, 1)}, (
        f"T-REX-25 setpoints must stay index 1 / precision 1; found {sorted(set(setpoints.values()))}"
    )
