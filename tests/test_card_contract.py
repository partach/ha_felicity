"""The Lovelace cards' contract with the register maps.

WHY THIS FILE EXISTS
--------------------
The cards do not import anything. `ha_felicity.js` finds a sensor by matching
the **end of its entity_id**, and Home Assistant derives that entity_id from the
display NAME. So a register map and a card are coupled through a string that
neither file mentions, and nothing fails when they stop agreeing — the tile just
renders 0 W, or picks a plausible-looking wrong entity, forever.

That is not hypothetical. When the IVGM family was added, its registers took
their names from its own protocol document ("Grid Total Power", "Load APhase
Power", "Bat1 SOC") instead of the T-REX names the cards look for. Every model
passed every existing test. On real hardware the energy-flow card showed:

  * grid, load and generator power  -> nothing resolved, 0 W
  * battery SOC                     -> nothing resolved, no icon
  * battery voltage                 -> **"SmartLoad Open Battery Voltage"**, a
                                       54 V setpoint, because its entity_id also
                                       ends in `_battery_voltage`
  * battery current                 -> **"Grid Charge Battery Current"**, a limit
                                       reading 0.0 A, same reason
  * PV and battery power            -> 1000x low, see the unit test below

The wrong-entity cases are the nasty ones: a card showing 54.0 V for a 48 V pack
looks entirely believable.

These tests encode the contract in the only place both sides can be checked
against each other.
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest

const = sys.modules["custom_components.ha_felicity.const"]

_FRONTEND = (pathlib.Path(__file__).parent.parent / "custom_components" /
             "ha_felicity" / "frontend")


def _slug(name: str) -> str:
    """Approximate Home Assistant's entity_id slugification of a display name."""
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", str(name).lower())).strip("_")


def _name_slugs(model: str) -> dict[str, str]:
    """{slugified display name: map key} for every entity a model exposes."""
    entry = const.MODEL_REGISTRY[model]
    out: dict[str, str] = {}
    for section in ("registers", "combined"):
        for key, info in (entry.get(section) or {}).items():
            out.setdefault(_slug(info.get("name", key)), key)
    return out


def _entry(model: str, key: str) -> dict | None:
    """The register/combined definition a card key resolves to, or None."""
    entry = const.MODEL_REGISTRY[model]
    for section in ("registers", "combined"):
        for k, info in (entry.get(section) or {}).items():
            if _slug(info.get("name", k)) == key:
                return info
    return None


#: Sensors the energy-flow card needs on every model.
#: The card renders a tile per concept; a key it cannot resolve shows 0 W.
_REQUIRED = [
    "total_pv_power",
    "battery_power",
    "battery_voltage",
    "battery_current",
    "battery_capacity",
    "total_ac_input_power",
    "total_ac_output_active_power",
    "loadpower_lineside",
    "battery_discharge_depth_on_grid_bms",
]

#: Legitimately absent on models without a generator port (T-REX-5/6/10).
_OPTIONAL = ["total_generator_active_power"]

#: What each key must actually measure. Resolving to *an* entity is not enough —
#: it has to be the right kind of quantity.
_EXPECTED_KIND = {
    "total_pv_power": ("power", "W"),
    "battery_power": ("power", "W"),
    "total_ac_input_power": ("power", "W"),
    "total_ac_output_active_power": ("power", "W"),
    "total_generator_active_power": ("power", "W"),
    "loadpower_lineside": ("power", "W"),
    "battery_voltage": ("voltage", "V"),
    "battery_current": ("current", "A"),
    "battery_capacity": ("battery", "%"),
    "battery_discharge_depth_on_grid_bms": ("battery", "%"),
}


@pytest.mark.parametrize("model", list(const.SUPPORTED_MODELS))
@pytest.mark.parametrize("key", _REQUIRED)
def test_card_key_resolves_on_every_model(model, key):
    """Every model must expose an entity NAMED for each card key.

    Named, not keyed: the card matches the entity_id, which comes from the
    display name. A map may call the register whatever its document calls it —
    `pv_total_power` on the T-REX-10 is named "Total PV Power" and resolves
    fine — but *something* has to carry the name the card looks for.
    """
    slugs = _name_slugs(model)
    assert key in slugs, (
        f"{model} exposes no entity whose name slugifies to {key!r}, so the "
        f"energy-flow card's tile for it renders 0 W / blank. Add a combined "
        f"sensor with that display name (see ivgm._combined)."
    )


@pytest.mark.parametrize("model", list(const.SUPPORTED_MODELS))
@pytest.mark.parametrize("key", _REQUIRED + _OPTIONAL)
def test_card_key_resolves_to_the_right_kind_of_sensor(model, key):
    """A resolved entity of the wrong kind is worse than none at all."""
    info = _entry(model, key)
    if info is None:
        assert key in _OPTIONAL, f"{model}: {key} missing (covered by the other test)"
        return
    want_class, want_unit = _EXPECTED_KIND[key]
    assert info.get("device_class") == want_class, (
        f"{model}: {key} resolves to a {info.get('device_class')!r} sensor, "
        f"expected {want_class!r}"
    )
    assert info.get("unit") == want_unit, (
        f"{model}: {key} reports {info.get('unit')!r}, but the card assumes "
        f"{want_unit!r} — _formatPower() only converts W->kW above 1000, so a "
        f"kW-valued sensor renders 1000x low (1.46 kW shows as '1 W')"
    )


@pytest.mark.parametrize("model", list(const.SUPPORTED_MODELS))
def test_shortest_match_wins_for_every_card_key(model):
    """Pins the card's disambiguation rule against the real entity lists.

    Several entities can end with the same suffix. The card takes the SHORTEST
    match, on the reasoning that it has the least extra wording in front and so
    is the entity actually named for the quantity. This test proves that rule
    picks the intended entity on real maps — it is what stops an IVGM showing
    "SmartLoad Open Battery Voltage" as the battery voltage.
    """
    slugs = _name_slugs(model)
    for key in _REQUIRED:
        matches = [s for s in slugs if s == key or s.endswith(f"_{key}")]
        assert matches, f"{model}: {key} unresolvable"
        winner = min(matches, key=len)
        assert winner == key, (
            f"{model}: {key} would resolve to {winner!r} (candidates "
            f"{sorted(matches)}) — the shortest-match rule picks the wrong entity"
        )


@pytest.mark.parametrize("model", list(const.SUPPORTED_MODELS))
def test_combined_power_sensors_report_the_unit_they_declare(model):
    """A combined sensor must convert its sources, not just add them up.

    The IVGM aggregates were a plain sum labelled "W". That was correct while
    its telemetry registers were raw watts, and became silently wrong the moment
    the measured 0.01 kW scale was applied — reporting 1.46 where the truth was
    1460, still labelled W. The T-REX maps had always done the x1000 inside
    their own `calc`, so nothing compared the two.

    Feed each aggregate a value meaning exactly 1000 W, expressed in its
    source's own unit, and require the declared unit back.
    """
    entry = const.MODEL_REGISTRY[model]
    registers = entry["registers"]
    wrong = []
    for key, c in (entry.get("combined") or {}).items():
        if c.get("device_class") != "power" or not c.get("unit") or not c.get("sources"):
            continue
        # Put the whole 1000 W on the first source; the rest read zero.
        values = []
        for n, src in enumerate(c["sources"]):
            unit = registers.get(src, {}).get("unit")
            values.append((1000.0 if unit == "W" else 1.0) if n == 0 else 0.0)
        got = c["calc"](*values)
        want = 1000.0 if c["unit"] == "W" else 1.0
        if abs(got - want) > 0.01:
            wrong.append(f"{key}: 1000 W in -> {got} {c['unit']} (want {want})")
    assert not wrong, f"{model}: combined power sensors mis-scale:\n  " + "\n  ".join(wrong)


def test_cards_disambiguate_by_shortest_match():
    """Both cards must implement the rule the tests above assume.

    Guards against someone simplifying the resolver back to `.find()`, which
    returns whichever entity Home Assistant happened to list first.
    """
    for name in ("ha_felicity.js", "ha_felicity_ems.js"):
        src = (_FRONTEND / name).read_text()
        assert "eid.length < best.length" in src, (
            f"{name} no longer prefers the shortest suffix match — entity "
            "resolution is back to depending on HA's listing order"
        )


def test_ems_card_uses_no_raw_register_sensors():
    """The EMS card must stay model-agnostic.

    It reads EMS-computed entities only (schedule_status, energy_state, prices,
    the config numbers/selects), all of which the integration creates identically
    for every model. That is why adding a new inverter family cannot break it —
    a property worth keeping, so this fails if a raw register key creeps in.
    """
    src = (_FRONTEND / "ha_felicity_ems.js").read_text()
    keys = set()
    for pat in (r"_getValue\(\s*['\"]([a-z0-9_]+)['\"]",
                r"_getState\(\s*['\"]([a-z0-9_]+)['\"]",
                r"_getEntityId\(\s*['\"]([a-z0-9_]+)['\"]",
                r"_getAttr\(\s*['\"]([a-z0-9_]+)['\"]"):
        keys |= set(re.findall(pat, src))

    # Anything a model's own map also publishes under that name is a register
    # sensor, and would make the card model-dependent.
    offenders = {}
    for model in const.SUPPORTED_MODELS:
        slugs = _name_slugs(model)
        hits = sorted(k for k in keys if k in slugs or any(
            s.endswith(f"_{k}") for s in slugs))
        if hits:
            offenders[model] = hits
    assert not offenders, (
        "the EMS card now depends on model-specific register sensors: "
        f"{offenders} — it should read only EMS-computed entities"
    )
