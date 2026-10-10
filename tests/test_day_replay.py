"""Night replays through the REAL coordinator — what reaches the inverter.

Each test replays real wall-clock hours (one poll per simulated minute) through
the production coordinator and type_specific code, against a fake inverter
whose battery only moves when the REGISTERS say so.  See tests/day_replay.py.

The first run of this harness found five defects in the plan → inverter path
that ~600 planning tests had not (CLAUDE.md, Known Issues 8f).
"""

from datetime import date, timedelta

import pytest

from tests.day_replay import DayReplay, const, day, slot

TREX_10 = const.INVERTER_MODEL_TREX_TEN
TREX_25 = const.INVERTER_MODEL_TREX_TWENTY_FIVE
D = date(2026, 10, 8)                 # a Thursday
NEXT = D + timedelta(days=1)

AUTO = {"grid_mode": "from_grid", "price_mode": "auto"}


def PRICES(d):
    """Flat, one cent cheaper each day: nothing for the EMS's own plan to buy
    (with tomorrow equal or dearer, the greedy planner pre-buys tomorrow's
    need now — a separate planning issue these tests stay clear of)."""
    return [round(0.30 - 0.01 * (d - D).days, 2)] * 96
NIGHT_CHARGE = {slot(h, m): "charge" for h in (2, 3, 4) for m in (0, 15, 30, 45)}  # 02:00-05:00


def _replay(model=TREX_10, *, options=AUTO, soc=40.0, start=None, **kw):
    return DayReplay(model=model, start=start or day(D, 22), options=dict(options),
                     prices_for=kw.pop("prices_for", PRICES),
                     capacity_kwh=kw.pop("capacity_kwh", 20.0), soc_pct=soc, **kw)


def _enable_key(model):
    return "econ_rule_1_grid_charge_enable" if model in const.ECO_TIMEOFUSE_MODELS \
        else "econ_rule_1_enable"


# ---------------------------------------------------------------------------
# The October report: overrides for the night that never charged
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("model", [TREX_10, TREX_25])
async def test_overrides_set_in_the_evening_survive_a_restart_and_charge(model):
    """Set at 21:00 for tomorrow, then HA restarts (update install) at 22:00.

    The first tick after a start runs the "new day" bookkeeping.  It used to
    rotate tomorrow's overrides onto TODAY — already past — and the real
    midnight then cleared them, so 02:00-05:00 never charged."""
    r = _replay(model, soc=90.0, start=day(D, 22))
    r.set_overrides(tomorrow=NIGHT_CHARGE)          # persisted before the restart
    r.entry.options["slot_overrides"]["date"] = D.isoformat()
    r.coordinator.slot_overrides = r.entry.options["slot_overrides"]
    await r.run_until(day(NEXT, 6))

    assert r.inverter_actions(day(D, 22, 1), day(NEXT, 2)) == {"self_use"}, r.timeline()
    assert r.rule_driven(day(NEXT, 2), day(NEXT, 5)), r.timeline()
    assert r.soc_at(day(NEXT, 4)) == pytest.approx(100.0, abs=0.5), r.timeline()
    assert r.inverter_actions(day(NEXT, 5), day(NEXT, 6)) == {"self_use"}, r.timeline()
    assert r.writes_after(_enable_key(model), day(D, 22)) == [(day(NEXT, 2), 1), (day(NEXT, 5), 0)]


@pytest.mark.asyncio
async def test_legacy_unstamped_overrides_are_not_rotated_on_a_restart():
    """Overrides saved before the date stamp existed: a restart keeps them where
    they are (a same-day restart is by far the common case)."""
    r = _replay(soc=90.0, start=day(NEXT, 1))
    r.set_overrides(today=NIGHT_CHARGE)              # no "date" key: legacy
    assert "date" not in r.entry.options["slot_overrides"]
    await r.run_until(day(NEXT, 3))
    assert r.rule_driven(day(NEXT, 2), day(NEXT, 3)), r.timeline()
    assert r.coordinator.slot_overrides["date"] == NEXT.isoformat()   # stamped now


@pytest.mark.asyncio
async def test_overrides_from_two_days_ago_are_not_replayed():
    """HA was off for a whole day: yesterday's 'tomorrow' is history."""
    r = _replay(soc=90.0, start=day(NEXT, 1))
    r.set_overrides(tomorrow=NIGHT_CHARGE)
    r.entry.options["slot_overrides"]["date"] = (D - timedelta(days=1)).isoformat()
    r.coordinator.slot_overrides = r.entry.options["slot_overrides"]
    await r.run_until(day(NEXT, 3))
    assert r.coordinator.slot_overrides["today"] == {}
    assert "charge" not in r.inverter_actions(day(NEXT, 2), day(NEXT, 3))


@pytest.mark.asyncio
async def test_overrides_set_while_running_charge_from_their_first_slot():
    """No restart: the card sets them at 23:00 through the service."""
    r = _replay(soc=90.0, start=day(D, 23))
    await r.run_until(day(D, 23, 30))
    r.coordinator.set_slot_overrides({"today": {}, "tomorrow": NIGHT_CHARGE})
    await r.run_until(day(NEXT, 6))
    assert r.inverter_actions(day(D, 23), day(NEXT, 2)) == {"self_use"}, r.timeline()
    assert r.rule_driven(day(NEXT, 2), day(NEXT, 5)), r.timeline()


@pytest.mark.asyncio
async def test_overrides_the_battery_cannot_fully_take_still_start_on_time():
    """80 % full, three hours of override charge.  The projection says the
    battery fills after ~45 min; pruning the 'overflowing' slots used to keep
    only the LAST hour (04:00-05:00) and re-prune the executing slot every
    tick, switching the charge off at :13 and on at :15."""
    r = _replay(soc=82.0, start=day(NEXT, 1, 30))
    r.coordinator.set_slot_overrides({"today": NIGHT_CHARGE, "tomorrow": {}})
    await r.run_until(day(NEXT, 5, 30))
    assert r.inverter_actions(day(NEXT, 2), day(NEXT, 2, 30)) == {"charge"}, r.timeline()
    assert r.rule_driven(day(NEXT, 2), day(NEXT, 5)), r.timeline()
    assert r.writes_after("econ_rule_1_enable", day(NEXT, 1, 30)) == [
        (day(NEXT, 2), 1), (day(NEXT, 5), 0)]


@pytest.mark.asyncio
async def test_full_battery_in_a_charge_slot_does_not_toggle_the_rule():
    """Full mid-slot: rule 1's SOC holds the battery at 100 % and the house runs
    on grid.  Going idle instead let the house drain it to 99.9 % and the next
    tick re-armed the charge — a full rule rewrite every poll."""
    r = _replay(soc=95.0, start=day(NEXT, 1, 55))
    r.coordinator.set_slot_overrides({"today": NIGHT_CHARGE, "tomorrow": {}})
    await r.run_until(day(NEXT, 5, 5))
    assert r.writes_after("operating_mode", day(NEXT, 1, 55)) == [
        (day(NEXT, 2), 2), (day(NEXT, 5), 0)]
    assert r.soc_at(day(NEXT, 4, 55)) == pytest.approx(100.0, abs=0.2)


@pytest.mark.asyncio
async def test_manual_price_mode_executes_overrides():
    r = _replay(options={"grid_mode": "from_grid", "price_mode": "manual",
                         "price_threshold_level": 1}, soc=90.0, start=day(NEXT, 1, 30))
    r.coordinator.set_slot_overrides({"today": NIGHT_CHARGE, "tomorrow": {}})
    await r.run_until(day(NEXT, 3))
    assert "charge" not in r.inverter_actions(day(NEXT, 1, 30), day(NEXT, 2))
    assert r.inverter_actions(day(NEXT, 2), day(NEXT, 2, 30)) == {"charge"}, r.timeline()


# ---------------------------------------------------------------------------
# Hands off, and what the inverter does to us
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("model", [TREX_10, TREX_25])
async def test_grid_mode_off_writes_nothing_all_night(model):
    r = _replay(model, options={"grid_mode": "off", "price_mode": "auto"})
    r.set_overrides(tomorrow=NIGHT_CHARGE)
    await r.run_until(day(NEXT, 6))
    assert r.writes() == []
    assert r.inverter_actions(day(D, 22), day(NEXT, 6)) == {"self_use"}


@pytest.mark.asyncio
async def test_a_transition_is_written_once_not_healed_on_the_next_tick():
    """The Economic-mode watchdog read the PREVIOUS poll (taken before the
    write), so every switch into charging was followed one tick later by a
    false 'dropped out of Economic mode' warning and a full rule rewrite."""
    r = _replay(soc=50.0, start=day(NEXT, 1, 55))
    r.coordinator.set_slot_overrides({"today": NIGHT_CHARGE, "tomorrow": {}})
    await r.run_until(day(NEXT, 2, 10))
    assert r.writes_after("operating_mode", day(NEXT, 1, 55)) == [(day(NEXT, 2), 2)]


@pytest.mark.asyncio
async def test_economic_mode_dropped_by_the_inverter_is_healed():
    """The watchdog still works when it should: the Felicity app (or a power
    blip) puts the inverter back in General mode mid-charge."""
    r = _replay(soc=50.0, start=day(NEXT, 1, 55))
    r.coordinator.set_slot_overrides({"today": NIGHT_CHARGE, "tomorrow": {}})
    await r.run_until(day(NEXT, 2, 30))
    r.inverter.set("operating_mode", 0)          # between two polls
    r.inverter.set("econ_rule_1_enable", 0)
    assert r.inverter.action() == "self_use"
    await r.run_until(day(NEXT, 2, 40))
    # Restored by the very next poll: mode first, then the rule.
    assert r.writes_after("operating_mode", day(NEXT, 2, 29)) == [(day(NEXT, 2, 30), 2)]
    assert r.writes_after("econ_rule_1_enable", day(NEXT, 2, 29)) == [(day(NEXT, 2, 30), 1)]
    assert r.inverter_actions(day(NEXT, 2, 30), day(NEXT, 2, 40)) == {"charge"}, r.timeline(1)


@pytest.mark.asyncio
async def test_a_charge_that_spans_midnight_keeps_charging():
    """T-REX-5/10 rule 1 carries a start/stop DATE, written as today's date on
    each transition.  A charge running at 23:59 has no transition at 00:00, so
    the rule kept yesterday's date and the inverter stopped obeying it at
    midnight while the coordinator still believed it was charging."""
    r = _replay(soc=30.0, start=day(D, 22, 55))
    late = {slot(23, m): "charge" for m in (0, 15, 30, 45)}
    early = {slot(0, m): "charge" for m in (0, 15, 30, 45)}
    r.coordinator.set_slot_overrides({"today": late, "tomorrow": early})
    await r.run_until(day(NEXT, 1, 5))
    assert r.inverter_actions(day(D, 23), day(NEXT, 1)) == {"charge"}, r.timeline(5)


@pytest.mark.asyncio
async def test_watchdog_without_fresh_data_falls_back_to_the_last_poll():
    """Callers outside the poll loop pass no data: the last poll is used."""
    r = _replay(soc=50.0, start=day(NEXT, 1, 55))
    r.coordinator.set_slot_overrides({"today": NIGHT_CHARGE, "tomorrow": {}})
    await r.run_until(day(NEXT, 2, 5))
    r.coordinator.data["operating_mode"] = 0
    await r.coordinator._ensure_economic_mode_when_active()
    assert r.writes_after("operating_mode", day(NEXT, 2, 4))[-1][1] == 2
