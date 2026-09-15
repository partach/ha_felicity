"""Every supported model must be handled by every model-specific code path.

WHY
---
`type_specific.py` is written as `if model in <group A>: ... elif model in
<group B>: ...` with, historically, **no else**.  Adding a model to
SUPPORTED_MODELS without adding it to those branches does not raise — it
silently returns None/False from every accessor, so the integration loads, the
entities appear, and the EMS quietly stops being able to read the battery or
drive the inverter.  That is the worst kind of failure: it looks fine.

These tests make it impossible to add a model and forget the plumbing.  They
are deliberately structural (they check the registry and the register maps, not
any one register's value), because the values themselves can only be confirmed
against hardware — see docs/IVGM_SUPPORT_GAPS.md.
"""

from __future__ import annotations

import sys

import pytest

const = sys.modules["custom_components.ha_felicity.const"]


def _models():
    return list(const.SUPPORTED_MODELS)


@pytest.mark.parametrize("model", _models())
def test_model_is_in_the_registry(model):
    """A selectable model must have registers, combined sensors and sets."""
    assert model in const.MODEL_REGISTRY, f"{model} is selectable but has no MODEL_REGISTRY entry"
    entry = const.MODEL_REGISTRY[model]
    for field in ("registers", "combined", "register_groups", "register_sets"):
        assert entry.get(field) is not None, f"{model}: MODEL_REGISTRY missing {field!r}"
    assert entry["registers"], f"{model}: empty register map"
    for level in ("basic", "basic_plus", "full"):
        assert level in entry["register_sets"], f"{model}: register set {level!r} missing"
        assert entry["register_sets"][level], f"{model}: register set {level!r} is empty"


@pytest.mark.parametrize("model", _models())
def test_model_has_a_max_power(model):
    """INVERTER_MAX_POWER_KW feeds the power slider and the SOC trajectory's
    grid-charge cap; a missing entry would KeyError or silently cap at 0."""
    assert model in const.INVERTER_MAX_POWER_KW, f"{model} has no INVERTER_MAX_POWER_KW"
    assert const.INVERTER_MAX_POWER_KW[model] > 0


@pytest.mark.parametrize("model", _models())
def test_model_has_exactly_one_control_path(model):
    """Each model must use the operating_mode path OR the ECO_TimeOfUse path.

    Neither → every `_handle_*` in type_specific.py falls through to
    `return False` and the inverter is never driven.
    Both → the branches are ambiguous and the first one silently wins.
    """
    operating = model in const.OPERATING_MODE_MODELS
    eco = model in const.ECO_TIMEOFUSE_MODELS
    assert operating != eco, (
        f"{model} must belong to exactly one control path "
        f"(OPERATING_MODE_MODELS={operating}, ECO_TIMEOFUSE_MODELS={eco})"
    )


@pytest.mark.parametrize("model", _models())
def test_control_path_registers_exist_in_the_model_map(model):
    """The registers that model's control path writes must be in its map.

    A missing key means `async_write_register` has no address to write and the
    state transition fails at runtime — on hardware, not in review.
    """
    registers = const.MODEL_REGISTRY[model]["registers"]
    if model in const.OPERATING_MODE_MODELS:
        required = ["operating_mode", "econ_rule_1_enable",
                    "econ_rule_1_voltage", "econ_rule_1_soc", "econ_rule_1_power"]
    else:
        required = ["eco_timeofuse", "system_mode",
                    "econ_rule_1_grid_charge_enable", "econ_rule_1_voltage",
                    "econ_rule_1_soc", "econ_rule_1_power"]
    missing = [k for k in required if k not in registers]
    assert not missing, f"{model}: control path needs {missing}, absent from its register map"


@pytest.mark.parametrize("model", _models())
def test_soc_and_grid_power_sources_exist(model):
    """The EMS cannot schedule without a battery SOC or a grid power reading."""
    registers = const.MODEL_REGISTRY[model]["registers"]
    soc = ("battery_capacity",) if model in const.OPERATING_MODE_MODELS else ("bat1_soc", "bat2_soc")
    assert any(k in registers for k in soc), f"{model}: no SOC register among {soc}"
    grid = ("total_ac_input_power",) if model in const.OPERATING_MODE_MODELS else (
        "total_grid_power", "phase_a_ct_active_power")
    assert any(k in registers for k in grid), f"{model}: no grid-power register among {grid}"


@pytest.mark.parametrize("model", _models())
def test_power_unit_is_declared(model):
    """W vs kW must be an explicit decision per model.

    The IVGM is the trap this guards: it uses the TREX-25/50 register LAYOUT but
    the TREX-5/10 power UNIT.  Inheriting the layout without the unit would make
    the integration request 1000x the intended charge power.
    """
    watts = model in const.WATT_POWER_MODELS
    eco = model in const.ECO_TIMEOFUSE_MODELS
    # Only a model that is BOTH ECO-layout and watt-valued needs special care;
    # assert the combination is deliberate rather than accidental.
    if eco and watts:
        assert model in const.IVGM_MODELS, (
            f"{model} mixes the ECO register layout with watt-valued power "
            "registers — that combination is IVGM-specific; if it is genuinely "
            "correct for this model, add it to IVGM_MODELS or widen the rule"
        )


def test_ivgm_eight_is_a_strict_subset_of_the_three_phase_map():
    """The 8K map is derived from the family map, never hand-copied.

    If someone re-introduces a separate literal map for one of them they will
    drift; this fails the moment the 8K gains a register the family lacks.
    """
    ivgm = sys.modules.setdefault(
        "custom_components.ha_felicity.ivgm",
        __import__("custom_components.ha_felicity.ivgm", fromlist=["x"]),
    )
    eight = set(ivgm._REGISTERS_IVGM_EIGHT)
    twenty = set(ivgm._REGISTERS_IVGM_TWENTY)
    assert eight < twenty, "8K map must be a strict subset of the 3-phase family map"
    assert twenty - eight == set(ivgm._IVGM_EIGHT_UNSUPPORTED), (
        "the difference between the maps must be exactly the documented "
        '"(8K donot support)" list — no silent extras'
    )


def test_ivgm_never_writes_registers_its_protocol_does_not_define():
    """0x21FF-0x2204 are sell-enable flags on TREX-25/50 but sit inside the
    IVGM's grid under-frequency protection block.  The guard in
    `type_specific._write_if_defined` must cover every one of them."""
    for key in const.IVGM_UNSUPPORTED_WRITES:
        for model in const.IVGM_MODELS:
            registers = const.MODEL_REGISTRY[model]["registers"]
            assert key not in registers, (
                f"{key} is listed as unsupported on IVGM but IS in {model}'s "
                "register map — one of the two is wrong"
            )


# ---------------------------------------------------------------------------
# IVGM register provenance
# ---------------------------------------------------------------------------
# THE RULE: an IVGM register address must come from the IVGM protocol document.
# It may never be borrowed from a TREX map because "the families look similar".
# They are different product lines and the layouts genuinely differ — e.g.
# `10minovptime` is 0x2205 on IVGM but 0x2206 on TREX-25/50, and the whole
# telemetry block is offset by one versus TREX-5/10.  A borrowed address does
# not fail loudly: it reads a neighbouring register and reports a plausible
# wrong number, or writes one.
#
# tests/data/ivgm_documented_registers.json is a frozen transcript of every
# address the document defines, so this check is against the document itself
# rather than against the code that was generated from it.

import json
import os

_DOC_PATH = os.path.join(os.path.dirname(__file__), "data",
                         "ivgm_documented_registers.json")


def _documented():
    with open(_DOC_PATH, encoding="utf-8") as fh:
        return json.load(fh)["addresses"]


def _ivgm_module():
    return sys.modules["custom_components.ha_felicity.ivgm"]


@pytest.mark.parametrize("model", list(const.IVGM_MODELS))
def test_every_ivgm_address_is_in_the_protocol_document(model):
    """No IVGM register may use an address the document doesn't define."""
    documented = _documented()
    registers = const.MODEL_REGISTRY[model]["registers"]
    undocumented = {
        key: f"0x{info['address']:04X}"
        for key, info in registers.items()
        if f"0x{info['address']:04X}" not in documented
    }
    assert not undocumented, (
        f"{model}: these registers use addresses absent from the IVGM protocol "
        f"document — they must not be inferred from a TREX map: {undocumented}"
    )


def test_ivgm_addresses_are_not_silently_taken_from_trex():
    """Belt-and-braces on the rule above, stated as intent.

    Recomputes the same property from the other direction: the set of IVGM
    addresses must be a subset of the documented set, so there is no way for a
    TREX-only address to enter the map even if someone adds one by hand.
    """
    documented = {int(a, 16) for a in _documented()}
    family = {i["address"] for i in _ivgm_module()._REGISTERS_IVGM_FAMILY.values()}
    borrowed = sorted(family - documented)
    assert not borrowed, (
        "IVGM family map contains addresses not in the protocol document: "
        + ", ".join(f"0x{a:04X}" for a in borrowed)
    )


def test_ivgm_register_names_match_the_document():
    """Guards against a mis-transcribed address pointing at the wrong register.

    An address typo usually still lands on a *valid* address, so the previous
    tests pass; comparing the name we recorded against the document's name for
    that address is what actually catches it.
    """
    documented = _documented()
    family = _ivgm_module()._REGISTERS_IVGM_FAMILY
    # The generator strips "(8K donot support)" and the High/Low suffix on the
    # 32-bit pairs, so compare on a loose, punctuation-free basis.
    def norm(s):
        return "".join(ch for ch in s.lower() if ch.isalnum())

    # The four packed date/time words are deliberately renamed: the document
    # spells them out as bit layouts ("Bit8-15: Year(value+2000) Bit0-7: Month")
    # which makes a useless entity name.  They are surfaced through the combined
    # "inverter_time" sensor, and their ADDRESSES are still checked above.
    renamed = {"time_year_month", "time_day_time", "time_minutes_seconds", "time_week"}

    mismatched = []
    for key, info in family.items():
        if key in renamed:
            continue
        if info.get("size", 1) != 1:
            continue                      # 32-bit pair: name intentionally trimmed
        doc_name = documented.get(f"0x{info['address']:04X}")
        if doc_name and norm(doc_name) and norm(info["name"]) not in norm(doc_name):
            mismatched.append(f"{key} @0x{info['address']:04X}: "
                              f"map={info['name']!r} doc={doc_name!r}")
    assert not mismatched, "register name does not match the document:\n" + "\n".join(mismatched)
