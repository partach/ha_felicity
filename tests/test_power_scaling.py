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


def test_ivgm_eight_follows_the_family_correction():
    """The 8K carries the 20K's correction, because the ERROR is in the source.

    This is deliberately NOT the T-REX-25/50 situation.  There, two models
    legitimately diverge: the 25's firmware was altered and its scaling is
    field-proven, so copying the 50's number would overwrite a measurement with
    a guess.  Here no IVGM has ever been field-tested and both maps are
    generated from ONE document — the same "W" column produced both entries.
    The 20K report is therefore evidence about the DOCUMENT, and a wrong source
    does not stop at whichever model was plugged in first.

    Keep the two in step until an 8K is actually measured; if one is, split the
    family in the same commit as the measurement.
    """
    info = _registers(const.INVERTER_MODEL_IVGM_EIGHT)["bat1_power"]
    assert _watts(156, info) == 1560.0, (
        f"IVGM-8K should use the family's corrected scale, got {_watts(156, info)} W "
        f"(unit={info.get('unit')}, index={info.get('index')}, "
        f"precision={info.get('precision')})"
    )


def test_both_ivgm_models_share_one_power_convention():
    """One document, one generated map, one scale — pinned across the family.

    The two models differ by register SET (the 8K lacks phase C, PV3/PV4 and
    battery 2), never by scale.  A future edit that corrects only the model in
    front of it would reintroduce exactly the inconsistency this commit removed.
    """
    def convention(model):
        regs = _registers(model)
        return {(i.get("unit"), i.get("index"), i.get("precision"))
                for i in regs.values() if i.get("device_class") == "power"}

    eight = convention(const.INVERTER_MODEL_IVGM_EIGHT)
    twenty = convention(const.INVERTER_MODEL_IVGM_TWENTY)
    assert eight == twenty, (
        f"IVGM models disagree on power scaling: 8K={eight} 20K={twenty}"
    )


#: The 8xxx block is settable configuration, 4xxx is live telemetry — and on the
#: IVGM the two blocks use DIFFERENT power units.
_SETTING_BLOCK_START = 8192


@pytest.mark.parametrize("model", list(const.IVGM_MODELS))
def test_ivgm_telemetry_and_setpoint_blocks_each_have_one_convention(model):
    """Consistent WITHIN each block, and deliberately different BETWEEN them.

    An earlier version of this test demanded a single convention across every
    power register on the model. That premise was wrong, and it was wrong in the
    dangerous direction: it passed while the setpoints were mis-scaled, because
    they had been "made consistent" with the telemetry.

    The 15K dump settles the split. Telemetry counts 0.01 kW (`bat1_power` 80
    against 53.6 V x 15.1 A = 809 W). Setpoints are plain watts
    (`grid_peak_shaving_power` 15000 = exactly that unit's 15 kW nameplate; as
    0.01 kW it would read 150 kW).
    """
    regs = _registers(model)
    power = {k: i for k, i in regs.items() if i.get("device_class") == "power"}
    assert power, f"{model}: no power registers at all"

    telemetry = {k: i for k, i in power.items() if i["address"] < _SETTING_BLOCK_START}
    setpoints = {k: i for k, i in power.items() if i["address"] >= _SETTING_BLOCK_START}
    assert telemetry and setpoints, f"{model}: expected power registers in both blocks"

    tele_conv = {(i.get("unit"), i.get("index"), i.get("precision")) for i in telemetry.values()}
    assert tele_conv == {("kW", 9, 2)}, (
        f"{model}: telemetry power must be 0.01 kW (kW/index 9/precision 2), got {tele_conv}"
    )

    set_conv = {(i.get("unit"), i.get("index")) for i in setpoints.values()}
    assert set_conv == {("W", 3)}, (
        f"{model}: setpoint power must stay raw watts (W/index 3), got {set_conv}. "
        "These registers are WRITTEN — mis-scaling them mis-commands the inverter."
    )


def test_ivgm_setpoint_defaults_are_only_plausible_as_watts():
    """The decisive evidence, kept as a test so it cannot be argued away.

    A factory default equal to the nameplate is the tell: the 15K reported
    `grid_peak_shaving_power` = 15000. Read as watts that is 15.00 kW — its exact
    rating. Read as 0.01 kW it is 150 kW, which no 15 kW inverter can mean.

    Scoped to the 15K deliberately. These are the raw counts read from THAT unit;
    an 8K has its own defaults, so asserting these numbers against the 8K's 8 kW
    nameplate would be comparing one machine's settings to another's rating. That
    the 8K uses the same *convention* is covered by the block-convention test.
    """
    model = const.INVERTER_MODEL_IVGM_FIFTEEN
    regs = _registers(model)
    rating = const.INVERTER_MAX_POWER_KW[model]
    assert rating == 15

    observed = {                      # raw counts read from the 15K
        "grid_peak_shaving_power": 15000,
        "econ_rule_1_power": 7500,
        "gen_input_rate_power": 7500,
        "max_pv_input_power": 4850,
    }
    for key, raw in observed.items():
        kw = _watts(raw, regs[key]) / 1000.0
        assert kw <= rating + 0.01, (
            f"{key} raw {raw} decodes to {kw:.2f} kW, above the {rating} kW "
            "nameplate — the scale must be wrong"
        )
    assert _watts(15000, regs["grid_peak_shaving_power"]) / 1000.0 == 15.0, (
        "the peak-shaving default must decode to exactly the 15 kW nameplate"
    )


def test_ivgm_telemetry_is_kilowatt_but_setpoints_are_watt():
    """The two model lists must disagree about the IVGM — that is the point.

    `WATT_POWER_MODELS` gates the telemetry read path; `SETPOINT_WATT_MODELS`
    gates the setpoint read AND write path. Reusing the telemetry list for writes
    turned a 5 kW charge command into raw 500 where the register wants 5000.
    """
    for model in const.IVGM_MODELS:
        assert model not in const.WATT_POWER_MODELS, (
            f"{model} telemetry is 0.01 kW, not watts"
        )
        assert model in const.SETPOINT_WATT_MODELS, (
            f"{model} setpoints are watts — writing kW would under-command it 10x"
        )


def test_ivgm_temperatures_are_deci_celsius():
    """Raw 410 is 41.0 C, not 410 C.

    `lead_acid_tempe` in the same telemetry block was already index 8 and read
    correctly, so the map contradicted itself; the customer had built
    "Temperatur korrigiert" template sensors to divide by 10 by hand. Signed
    (index 8, not 2) because ambient temperature can go below zero.
    """
    regs = _registers(const.INVERTER_MODEL_IVGM_TWENTY)
    expected = {
        "environment_temperature": (410, 41.0),
        "boost_temperature": (329, 32.9),
        "inverter_temperature": (349, 34.9),
        "bms_maximum_cell_temperature": (210, 21.0),
        "bms_minmum_cell_temperature": (200, 20.0),
    }
    for key, (raw, want) in expected.items():
        info = regs[key]
        got = round(_scale(raw, info["index"]), info["precision"])
        assert got == want, f"{key}: raw {raw} should read {want} C, got {got}"
        assert info["index"] == 8, f"{key} must stay signed (index 8), got {info['index']}"


def test_ivgm_bms_total_voltage_is_centi_volt():
    """Raw 5360 is 53.60 V, not 536.0 V — no 48 V pack sits at 536 V.

    Cross-checked against `bat1_voltage` on the same pack, which read 53.6 V.
    Note the neighbouring BMS charge/discharge voltage LIMITS (57.6 / 48.0 V)
    are correct at index 1, so this is one register's scale, not the block's.
    """
    regs = _registers(const.INVERTER_MODEL_IVGM_TWENTY)
    info = regs["bms_total_voltage"]
    got = round(_scale(5360, info["index"]), info["precision"])
    assert got == 53.6, f"BMS Total Voltage raw 5360 should read 53.6 V, got {got}"
    for key, raw, want in (("bms_charge_voltage_limit", 576, 57.6),
                           ("bms_discharge_voltage_limit", 480, 48.0)):
        i = regs[key]
        assert round(_scale(raw, i["index"]), i["precision"]) == want, (
            f"{key} was already correct and must not be swept along"
        )


@pytest.mark.parametrize("model", list(const.IVGM_MODELS))
def test_ivgm_register_names_do_not_leak_the_document_annotation(model):
    """Two names carried "(8K donot 0.1KWh support)" into the HA entity name.

    Only the NAME is fixed: the dict key becomes the entity's unique_id, so
    renaming the key would orphan every existing entity.
    """
    for key, info in _registers(model).items():
        assert "donot" not in info.get("name", ""), (
            f"{model}: {key} leaks the protocol document's annotation into its "
            f"display name: {info['name']!r}"
        )


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


# --------------------------------------------------------------------------
# No model's power unit may be decided by omission
# --------------------------------------------------------------------------

def test_every_model_declares_a_power_unit():
    """POWER_UNIT_BY_MODEL must cover every supported model, explicitly.

    `WATT_POWER_MODELS` is derived from it. When it was a hand-written list, a
    model that simply wasn't in it silently became kW — a decision made by
    omission, exactly like the `type_specific` branches that had no `else`.
    Adding a model and forgetting its power unit would then mis-scale every
    power reading and every setpoint written, with nothing failing.
    """
    declared = set(const.POWER_UNIT_BY_MODEL)
    supported = set(const.SUPPORTED_MODELS)
    assert supported <= declared, (
        "these models have no declared power unit: "
        f"{sorted(supported - declared)} — add them to POWER_UNIT_BY_MODEL "
        "with the evidence, do not rely on the default"
    )
    assert declared <= supported, (
        f"POWER_UNIT_BY_MODEL names unknown models: {sorted(declared - supported)}"
    )
    bad = {m: u for m, u in const.POWER_UNIT_BY_MODEL.items() if u not in ("W", "kW")}
    assert not bad, f"power unit must be 'W' or 'kW', got {bad}"


def test_every_model_declares_a_setpoint_power_unit():
    """SETPOINT_POWER_UNIT_BY_MODEL must cover every supported model too.

    It is the second half of the same decision and carries the same hazard: a
    model missing from it silently becomes kW on the WRITE path, which
    under-commands charge power by 1000x (or 10x once the register's own index
    partially compensates). Either way the battery quietly does almost nothing.
    """
    declared = set(const.SETPOINT_POWER_UNIT_BY_MODEL)
    supported = set(const.SUPPORTED_MODELS)
    assert supported <= declared, (
        "these models have no declared SETPOINT power unit: "
        f"{sorted(supported - declared)}"
    )
    assert declared <= supported, (
        f"SETPOINT_POWER_UNIT_BY_MODEL names unknown models: {sorted(declared - supported)}"
    )
    bad = {m: u for m, u in const.SETPOINT_POWER_UNIT_BY_MODEL.items() if u not in ("W", "kW")}
    assert not bad, f"setpoint power unit must be 'W' or 'kW', got {bad}"


def test_watt_power_models_is_consistent_with_the_mapping():
    """Each derived tuple must agree with the mapping it comes from."""
    assert const.WATT_POWER_MODELS == tuple(
        m for m, u in const.POWER_UNIT_BY_MODEL.items() if u == "W"
    )
    assert const.SETPOINT_WATT_MODELS == tuple(
        m for m, u in const.SETPOINT_POWER_UNIT_BY_MODEL.items() if u == "W"
    )


# --------------------------------------------------------------------------
# Rating-only variants: same silicon, different nameplate
# --------------------------------------------------------------------------

#: (variant, sibling it is register-identical to)
_RATING_ONLY_VARIANTS = [
    (const.INVERTER_MODEL_TREX_SIX, const.INVERTER_MODEL_TREX_FIVE),
    (const.INVERTER_MODEL_IVGM_FIFTEEN, const.INVERTER_MODEL_IVGM_TWENTY),
]


@pytest.mark.parametrize(("variant", "sibling"), _RATING_ONLY_VARIANTS)
def test_rating_only_variant_shares_its_siblings_map(variant, sibling):
    """Share the map BY REFERENCE, so the two cannot drift apart.

    A copied dict literal drifts the moment one copy is edited and the other is
    forgotten — the failure this project has had to undo more than once. `is`
    rather than `==` is the assertion on purpose: equal-but-separate dicts would
    satisfy equality today and diverge tomorrow.
    """
    a, b = const.MODEL_REGISTRY[variant], const.MODEL_REGISTRY[sibling]
    assert a["registers"] is b["registers"], f"{variant} must share {sibling}'s register map"
    assert a["register_sets"] is b["register_sets"]
    assert a["combined"] is b["combined"]
    assert a["default_first_reg"] == b["default_first_reg"]


@pytest.mark.parametrize(("variant", "sibling"), _RATING_ONLY_VARIANTS)
def test_rating_only_variant_differs_only_in_nameplate(variant, sibling):
    """The rating is the whole reason the variant exists as a separate model.

    `INVERTER_MAX_POWER_KW` caps the EMS's grid-charge planning and the Power
    Level slider, so a 15K configured as a 20K gets plans assuming 5 kW it does
    not have. If the two ratings were equal the variant would be pointless.
    """
    assert const.INVERTER_MAX_POWER_KW[variant] != const.INVERTER_MAX_POWER_KW[sibling]
    for mapping in (const.POWER_UNIT_BY_MODEL, const.SETPOINT_POWER_UNIT_BY_MODEL):
        assert mapping[variant] == mapping[sibling], (
            f"{variant} shares {sibling}'s registers, so it must share its units"
        )
    # Same control path, or type_specific would treat them differently.
    for group in (const.ECO_TIMEOFUSE_MODELS, const.OPERATING_MODE_MODELS, const.IVGM_MODELS):
        assert (variant in group) == (sibling in group), (
            f"{variant} and {sibling} must belong to the same model groups"
        )


def test_declaring_kilowatts_does_not_unsupport_a_model():
    """Guards the misreading this mapping exists to prevent.

    A reviewer seeing `INVERTER_MODEL_IVGM_TWENTY` absent from the old
    watt-models list reasonably asked whether the 20K had been dropped as a
    variant. It had not — the unit declaration and model membership are
    different things, and this test says so in code.
    """
    for model, unit in const.POWER_UNIT_BY_MODEL.items():
        if unit != "kW":
            continue
        assert model in const.SUPPORTED_MODELS, f"{model} lost support"
        assert model in const.MODEL_REGISTRY, f"{model} lost its register map"
        assert const.MODEL_REGISTRY[model]["registers"], f"{model} has an empty map"
