"""T-REX-5/6/10 register maps against Felicity's IVGM5048/IVGM10048 document.

`trex_five.py` was written without a Felicity document (see CLAUDE.md, Known
Issues 8d) and `trex_ten.py` was grown from it.  In Oct 2026 support sent the
"Inverter Communication Protocol - Series RS485" V02 (2025-09-30) for the
IVGM5048 / IVGM10048, whose layout is exactly these maps.  It is frozen as
``trex_documented_registers.json``; these tests hold the maps to it the way
test_model_coverage.py holds the IVGM-8K/15K/20K maps to theirs:

* every word a mapped register occupies is a word the document defines
  (a short, reasoned list of exceptions below);
* the name we give a register matches the document's name for that address,
  which is what catches a mis-typed address landing on another valid one;
* the select option lists cover the values the document enumerates.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

import pytest

const = sys.modules["custom_components.ha_felicity.const"]

_DOC = json.loads((pathlib.Path(__file__).parent.parent / "custom_components" /
                   "ha_felicity" / "trex_documented_registers.json").read_text())
_REGS = {int(a, 16): info for a, info in _DOC["registers"].items()}

#: Words each register occupies, from the document's own sizes.
_WORDS = {a + i for a, info in _REGS.items() for i in range(info["size"])}

#: Mapped registers the document does not define, and why they stay.
_UNDOCUMENTED = {
    # One address below the document's first telemetry row.  Long-standing in
    # both maps; not in this document (reads 0 on a T-REX-10).
    "setting_data_sn",
}

#: Version words the T-REX-5 map still reads at 0xF83C-0xF841, which the
#: document does not define.  On a T-REX-10 that block proved not to hold the
#: versions (see the dump tests below) and the 10 was moved to 0xF80A-0xF80F.
#: The 5K has NOT been dumped, so its map stays as it was (maintainer: a
#: measurement on one model says nothing about another).
_T5_VERSION_KEYS = {
    "display_software_version", "mcu1_software_version", "mcu2_software_version",
    "display_hardware_version", "control_hardware_version", "power_hardware_version",
}


def _undocumented(model):
    if model == const.INVERTER_MODEL_TREX_TEN:
        return _UNDOCUMENTED
    return _UNDOCUMENTED | _T5_VERSION_KEYS

#: The document's spelling, mapped onto ours (regexes), before names are compared.
_SPELLING = {
    "temperautre": "temperature", "convertion": "conversion", "exsit": "exist",
    "dischaged": "discharge", "operated": "operating", "economicmodel": "economicmode",
    r"curr(?!ent)": "current", r"addr(?!ess)": "address", "flg": "flag",
}
#: Words our names add that the document leaves to context.
_OUR_EXTRA_WORDS = ("secondary", "enable", "inquiry", "bms", "connected", "number",
                    "max", "maximum", "ongrid", "offgrid", "no")

#: Named on purpose differently from the document, with the reason.
_RENAMED = {
    # The document calls 0x121F "MaximumCellTemperature" a second time; it is
    # the minimum (0x121C-0x121F are max no., max, min no., min).
    "min_cell_temperature",
    # Battery pack serial words inside the document's five-word "SN" at 0x1220.
    "battery_pack_sn_0", "battery_pack_sn_1", "battery_pack_sn_2",
    "battery_pack_sn_3", "battery_pack_sn_4",
}

_MODELS = [m for m in const.OPERATING_MODE_MODELS]


def _norm(text: str) -> str:
    out = re.sub(r"[^a-z0-9]", "", text.lower())
    for wrong, right in _SPELLING.items():
        out = re.sub(wrong, right, out)
    return out


def _strip(text: str) -> str:
    out = _norm(text)
    out = re.sub(r"rule\d", "", out)
    for word in _OUR_EXTRA_WORDS:
        out = out.replace(word, "")
    return out.replace("date", "day")


def _registers(model):
    return const.MODEL_REGISTRY[model]["registers"]


def test_the_transcript_is_the_v02_document():
    """Guard against a truncated or re-extracted transcript."""
    assert "V02 2025-09-30" in _DOC["_source"]
    assert len(_REGS) == 458
    # V02's only change: 0x21BF-0x21C5.
    assert {0x21BF, 0x21C0, 0x21C5} <= set(_REGS)
    # Spot checks against the PDF.
    assert _REGS[0x1101]["name"] == "Working mode"
    assert _REGS[0x1110] == {"size": 1, "name": "Battery capacity", "unit": "0.1%"}
    assert _REGS[0x2178]["name"] == "Economic model rule 1"
    assert _REGS[0x2180]["rule"] == 1 and _REGS[0x219B]["rule"] == 4
    assert 0xAAAA not in _REGS            # the prose write-frame example


@pytest.mark.parametrize("model", _MODELS)
def test_every_mapped_word_is_in_the_document(model):
    undocumented = {}
    for key, info in _registers(model).items():
        if key in _undocumented(model):
            continue
        words = range(info["address"], info["address"] + info.get("size", 1))
        missing = [w for w in words if w not in _WORDS]
        if missing:
            undocumented[key] = ", ".join(f"0x{w:04X}" for w in missing)
    assert not undocumented, f"{model}: words not in the document: {undocumented}"


@pytest.mark.parametrize("model", _MODELS)
def test_the_undocumented_list_is_not_stale(model):
    """An exception that no longer applies hides the next real one."""
    registers = _registers(model)
    for key in _undocumented(model) & set(registers):
        info = registers[key]
        assert info["address"] not in _WORDS, f"{key} is documented now — drop the exception"


@pytest.mark.parametrize("model", _MODELS)
def test_register_names_match_the_document(model):
    mismatched = []
    for key, info in _registers(model).items():
        if key in _undocumented(model) or key in _RENAMED:
            continue
        doc = _REGS.get(info["address"])
        if doc is None:
            continue                      # inside a multi-word register
        ours, theirs = _strip(info["name"]), _strip(doc["name"])
        if not (ours in theirs or theirs in ours):
            mismatched.append(f"{key} @0x{info['address']:04X}: "
                              f"map={info['name']!r} doc={doc['name']!r}")
    assert not mismatched, "name does not match the document:\n" + "\n".join(mismatched)


@pytest.mark.parametrize("model", _MODELS)
def test_economic_rule_rows_belong_to_their_rule(model):
    """The document names rule rows only "Start time", "SOC"…, so the name
    check alone cannot tell rule 2's SOC from rule 3's — the rule number can."""
    wrong = []
    for key, info in _registers(model).items():
        m = re.match(r"econ_rule_(\d)_", key)
        if not m:
            continue
        doc = _REGS[info["address"]]
        rule = doc.get("rule") or int(re.search(r"rule (\d)", doc["name"]).group(1))
        if rule != int(m.group(1)):
            wrong.append(f"{key} @0x{info['address']:04X} is rule {rule} in the document")
    assert not wrong, "\n".join(wrong)


def _enumerated(address):
    """{value: label} from a document remark such as '0:Gen Mode 1:Backup …'."""
    remark = _REGS[address]["remark"].replace("：", ":")
    return {int(v): label.strip(" ,，")
            for v, label in re.findall(r"(?<![\w.])(\d{1,2})\s*:\s*(.*?)(?=(?<![\w.])\d{1,2}\s*:|$)", remark)}


@pytest.mark.parametrize("model", _MODELS)
def test_operating_mode_offers_every_documented_value(model):
    documented = _enumerated(0x2103)
    assert set(documented) == {0, 1, 2, 3}, documented
    options = _registers(model)["operating_mode"]["options"]
    assert len(options) == len(documented)
    assert options[2].startswith("Economic")      # the value the EMS writes
    assert options[3].startswith("Oil engine")


@pytest.mark.parametrize("model", _MODELS)
@pytest.mark.parametrize("rule", [1, 2, 3, 4])
def test_rule_enable_offers_every_documented_value(model, rule):
    address = 0x2178 + 9 * (rule - 1)
    documented = _enumerated(address)
    assert set(documented) == {0, 1, 2, 3}, documented
    info = _registers(model)[f"econ_rule_{rule}_enable"]
    assert info["address"] == address
    assert info["options"] == ["Disabled", "Charge", "Discharge", _VALUE_THREE[model]]


#: What value 3 of a rule's enable is called.  The document says "ECO Mode";
#: on a T-REX-10K-P3G01 the Felicity app shows that value as "AC Couple"
#: (discussion #230: rules 1-2 set to AC Couple in the app, raw 3 at 0x2178 /
#: 0x2181 in the owner's diagnostics dump on 1.3.13 — "Unknown" on 1.3.12,
#: which stopped at Discharge).  The T-REX-5/6K's app is unchecked, so its
#: label stays the document's.
_VALUE_THREE = {
    const.INVERTER_MODEL_TREX_TEN: "ECO mode (AC Couple)",
    const.INVERTER_MODEL_TREX_FIVE: "ECO mode",
    const.INVERTER_MODEL_TREX_SIX: "ECO mode",
}


def test_every_t_rex_5_6_10_names_value_three():
    assert set(_VALUE_THREE) == set(_MODELS)


@pytest.mark.parametrize("model", _MODELS)
def test_rule_summary_names_value_three(model):
    """The combined rule sensor printed "Unknown(3)" for the ECO-mode value."""
    calc = const.MODEL_REGISTRY[model]["combined"]["econ_rule_1"]["calc"]
    assert calc(3, 0, 0, 0, 0, 0, 0, 0, 0)["enabled"] == _VALUE_THREE[model]
    assert calc(4, 0, 0, 0, 0, 0, 0, 0, 0)["enabled"] == "Unknown(4)"
    for rule in (2, 3, 4):
        calc = const.MODEL_REGISTRY[model]["combined"][f"econ_rule_{rule}"]["calc"]
        assert calc(3, 0, 0, 0, 0, 0, 0, 0, 0)["enabled"] == _VALUE_THREE[model]


def test_the_document_names_the_models_these_entries_cover():
    """IVGM5048 = 101, IVGM6048 = 401, IVGM10048 = 201 in the machine-code table,
    which is why the setup dropdown labels the T-REX-5/6/10 entries with them."""
    remark = _REGS[0x21B5]["remark"]
    for code in ("IVGM5048: 101", "IVGM6048: 401", "IVGM10048: 201"):
        assert code in remark
    src = (pathlib.Path(__file__).parent.parent / "custom_components" / "ha_felicity" /
           "config_flow.py").read_text()
    for name, model in (("IVGM5048", "TREX_FIVE"), ("IVGM6048", "TREX_SIX"),
                        ("IVGM10048", "TREX_TEN")):
        assert re.search(rf"value=INVERTER_MODEL_{model},\s*label=f\"{{INVERTER_MODEL_{model}}} / {name}\"",
                         src), name


def test_the_diagnostics_loader_reads_every_word_with_its_rule():
    """Download diagnostics reads every documented word on T-REX-5/6/10, so the
    open scale questions (power-conversion registers) come with one dump."""
    from tests.conftest import load_component

    rd = load_component("register_dump")
    words = rd.load_trex_documented_addresses()
    assert set(words) == _WORDS
    assert words[0x2180] == "Economic model rule 1 Power"
    assert words[0x219A] == "Economic model rule 4 SOC"
    assert words[0x1157] == "PV Generated energy inquiry Total-High 32 bit (word 2)"


# ---------------------------------------------------------------------------
# Measured on a T-REX-10 (Download diagnostics, 2026-10-19, charging at 8.8 kW
# from the grid).  Raw words exactly as the dump read them.
# ---------------------------------------------------------------------------

_DUMP_T10 = {
    0x1109: 2287, 0x110A: 125, 0x110C: 2877,                 # grid L1: V, A, W
    0x110D: 553, 0x110E: 1596, 0x110F: 8825, 0x1110: 937,    # battery V, A, W, SOC
    0x1117: 4885, 0x1118: 13, 0x1119: 672,                   # PV1
    0x111A: 3959, 0x111B: 14, 0x111C: 580,                   # PV2
    0x1128: 0, 0x1129: 8430,                                 # total AC input W
    0x1195: 0, 0x1196: 8430, 0x1197: 0, 0x1198: 495,         # line / load conversion
    0x1199: 8825,                                            # battery conversion
    0x119D: 0, 0x119E: 495,                                  # load, line side
    0x11AE: 567, 0x11B0: 94,                                 # log snapshot V, SOC
    0xF80A: 213, 0xF80B: 311, 0xF80C: 112,                   # versions (documented)
    0xF80D: 100, 0xF80E: 100, 0xF80F: 100,
    0xF83C: 65535, 0xF83D: 523, 0xF83E: 5,                   # the old, undocumented
    0xF83F: 0, 0xF840: 0, 0xF841: 65535,                     #   block: not versions
}


def _decoded_t10(model=None):
    from tests.conftest import load_component

    rd = load_component("register_dump")
    registers = const.MODEL_REGISTRY[model or const.INVERTER_MODEL_TREX_TEN]["registers"]
    return rd, rd.decode(registers, _DUMP_T10)


def test_power_conversion_registers_are_watts_like_their_twins():
    """They were /10 in our maps; the document says W.  The dump settles it: each
    reads the identical raw word as the register reporting the same quantity
    (8430 = total AC input = 2877 + 2755 + 2798 over the phases; 8825 = battery
    power = 55.3 V x 159.6 A).  At /10 they read 843 / 50 / 882 W."""
    _, d = _decoded_t10()
    assert d["line_power_conversion"]["value"] == d["total_ac_input_power"]["value"] == 8430
    assert d["bat_power_conversion"]["value"] == d["battery_power"]["value"] == 8825
    assert d["load_power_conversion"]["value"] == d["load_power_line_side"]["value"] == 495


def test_the_log_snapshot_battery_capacity_is_whole_percent():
    """0x11B0 is "%" in the document (0x1110 is "0.1%").  Raw 94 beside a
    56.7 V pack voltage in the same snapshot is 94 %, not the 9.0 % /10 gave."""
    _, d = _decoded_t10()
    assert d["battery_capacity_secondary"]["value"] == 94


def test_versions_are_read_where_the_document_puts_them():
    """0xF83C-0xF841 is not in the document and read 65535 / 523 / 5 / 0 / 0 /
    65535 — not versions.  The document's 0xF80A-0xF80F read 213 / 311 / 112 /
    100 / 100 / 100, the T-REX-25/50 maps already read there."""
    model = const.INVERTER_MODEL_TREX_TEN
    registers = _registers(model)
    keys = ("display_software_version", "mcu1_software_version", "mcu2_software_version",
            "display_hardware_version", "control_hardware_version", "power_hardware_version")
    assert [registers[k]["address"] for k in keys] == list(range(0xF80A, 0xF810))
    _, d = _decoded_t10()
    assert [d[k]["value"] for k in keys] == [2.13, 3.11, 1.12, 1.0, 1.0, 1.0]


def test_the_dump_checks_t_rex_physics_and_twin_registers():
    """The T-REX-10 dump came back with physics_checks: [] — the checks only
    knew IVGM/IVAM key names.  Now every T-REX check runs, and on this dump
    every one is ok (the old /10 conversion registers would be SCALE SUSPECT)."""
    rd, d = _decoded_t10()
    checks = {c["check"]: c["result"] for c in rd.physics_checks(d)}
    assert {"Battery", "PV input", "PV2 input", "Grid L1",
            "Line power conversion = total AC input",
            "Battery power conversion = battery power",
            "Load power conversion = line-side load"} <= set(checks)
    assert set(checks.values()) == {"ok"}, checks


@pytest.mark.parametrize("model", [const.INVERTER_MODEL_TREX_FIVE, const.INVERTER_MODEL_TREX_SIX])
def test_the_t_rex_five_is_not_corrected_from_the_ten(model):
    """The dump above is a T-REX-10's.  The T-REX-5 (and the 6K, which shares
    its map) shares the protocol document but has not been measured, so its
    scales and version addresses stay exactly as shipped — the same rule that
    freezes the T-REX-25 against the T-REX-50.  Change this only with a 5K
    dump, in the same commit, with the evidence in the message."""
    r = _registers(model)
    assert r["line_power_conversion"]["index"] == 1
    assert r["load_power_conversion"]["index"] == 1
    assert r["bat_power_conversion"]["index"] == 1
    assert r["display_software_version"]["address"] == 0xF83C
    assert r["power_hardware_version"]["address"] == 0xF841
