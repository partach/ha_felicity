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
    # both maps; not in this document.
    "setting_data_sn",
    # Firmware/hardware version words.  The document puts its version IDs at
    # 0xF80A-0xF80F; these six read 0xF83C-0xF841, which it does not define.
    # Read-only, so the worst case is a meaningless number — a register dump
    # will tell which block answers.
    "display_software_version", "mcu1_software_version", "mcu2_software_version",
    "display_hardware_version", "control_hardware_version", "power_hardware_version",
}

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
        if key in _UNDOCUMENTED:
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
    for key in _UNDOCUMENTED & set(registers):
        info = registers[key]
        assert info["address"] not in _WORDS, f"{key} is documented now — drop the exception"


@pytest.mark.parametrize("model", _MODELS)
def test_register_names_match_the_document(model):
    mismatched = []
    for key, info in _registers(model).items():
        if key in _UNDOCUMENTED or key in _RENAMED:
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
    assert info["options"] == ["Disabled", "Charge", "Discharge", "ECO mode"]


@pytest.mark.parametrize("model", _MODELS)
def test_rule_summary_names_value_three(model):
    """The combined rule sensor printed "Unknown(3)" for the ECO-mode value."""
    calc = const.MODEL_REGISTRY[model]["combined"]["econ_rule_1"]["calc"]
    assert calc(3, 0, 0, 0, 0, 0, 0, 0, 0)["enabled"] == "ECO mode"
    assert calc(4, 0, 0, 0, 0, 0, 0, 0, 0)["enabled"] == "Unknown(4)"


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
