"""Night replays through the REAL coordinator — what reaches the inverter.

Each test replays real wall-clock hours (one poll per simulated minute) through
the production coordinator and type_specific code, against a fake inverter
whose battery only moves when the REGISTERS say so.  See tests/day_replay.py.

The first run of this harness found five defects in the plan → inverter path
that ~600 planning tests had not (CLAUDE.md, Known Issues 8f).
"""

import itertools
import os
import random
from datetime import date, datetime, timedelta

import pytest

from tests.day_replay import DayReplay, const, day, pv_bell, slot

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


# ---------------------------------------------------------------------------
# Randomised: an override is an override, whatever its time, length or day
# ---------------------------------------------------------------------------
# Overrides are manual: any slot, any length, today or tomorrow, set at any
# time, with or without a restart in between.  The fixed-window tests above
# can hide a bug that only shows for some windows (one that crosses midnight,
# starts on the hour the user set it, or is a single slot), so these draw the
# scenario at random.  Seeded, so a failure reproduces exactly; the test id
# names the scenario.

STEP_S = 300                          # one poll per 5 simulated minutes


def _quarter(dt: datetime) -> datetime:
    return dt.replace(minute=dt.minute - dt.minute % 15, second=0, microsecond=0)


def _random_override_case(seed: int) -> dict:
    rng = random.Random(seed)
    set_at = day(D, rng.randrange(24), rng.choice((0, 15, 30, 45)))
    # Window starts at least one slot after it is set, up to the end of tomorrow.
    first = _quarter(set_at) + timedelta(minutes=15)
    last_start = day(NEXT, 23, 45)
    length = rng.choice((1, 2, 3, rng.randrange(4, 33)))     # 15 min .. 8 h
    if length > 1 and rng.random() < 0.25:
        # Straddle midnight: a uniform draw almost never lands there, and it is
        # where the day rollover, the override rotation and the rule date meet.
        start = day(NEXT) - timedelta(minutes=15 * rng.randrange(1, length))
        set_at = min(set_at, start - timedelta(minutes=15))
    else:
        start = first + timedelta(minutes=15 * rng.randrange(
            int((last_start - first).total_seconds() // 900) + 1))
    end = min(start + timedelta(minutes=15 * length), day(NEXT + timedelta(days=1)))
    restart_at = None
    if rng.random() < 0.5:                                  # HA restarts in between
        restart_at = set_at + (start - set_at) * rng.random()
        restart_at = _quarter(restart_at)
    return {"seed": seed, "model": rng.choice((TREX_10, TREX_25)),
            "price_mode": rng.choice(("auto", "manual")),
            "soc": rng.randrange(45, 86), "set_at": set_at, "start": start,
            "end": end, "restart_at": restart_at}


def _case_id(c: dict) -> str:
    restart = f"-restart{c['restart_at']:%d%H%M}" if c["restart_at"] else ""
    return (f"s{c['seed']}-{c['model'][:9]}-{c['price_mode']}-set{c['set_at']:%d%H%M}-"
            f"{c['start']:%d%H%M}to{c['end']:%d%H%M}{restart}")


def _split_by_day(start: datetime, end: datetime, set_on: date):
    """The card's {"today", "tomorrow"} dicts, as seen on the day it was set."""
    today, tomorrow = {}, {}
    t = start
    while t < end:
        (today if t.date() == set_on else tomorrow)[slot(t.hour, t.minute)] = "charge"
        t += timedelta(minutes=15)
    return today, tomorrow


RANDOM_CASES = [_random_override_case(seed) for seed in range(40)]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", RANDOM_CASES, ids=_case_id)
async def test_random_override_runs_exactly_when_set(case):
    today, tomorrow = _split_by_day(case["start"], case["end"], D)
    begin = case["restart_at"] or case["set_at"]
    # A frugal house (2 kWh/day, estimated and actual) leaves the EMS's own
    # plan nothing to buy, so every charge in the replay is the override's.
    options = {"grid_mode": "from_grid", "price_mode": case["price_mode"],
               "price_threshold_level": 1, "daily_consumption_estimate": 2.0}
    r = _replay(case["model"], options=options, soc=case["soc"], capacity_kwh=40.0,
                start=begin, load_kw=lambda _t: 2.0 / 24)
    if case["restart_at"]:
        r.restore_overrides(today=today, tomorrow=tomorrow, set_on=D)
    else:
        await r.tick()                                      # running before the click
        r.coordinator.set_slot_overrides({"today": today, "tomorrow": tomorrow})
    await r.run_until(case["end"] + timedelta(minutes=30), step_s=STEP_S)

    key = _enable_key(case["model"])
    msg = f"{_case_id(case)}\n{r.timeline(15)}"
    # Charging (or holding full) for the whole window, from its first slot...
    assert r.rule_driven(case["start"], case["end"]), msg
    # ...switched on exactly at the window start, never earlier...
    ons = [t for t, v in r.writes_after(key, begin) if v == 1]
    assert ons and ons[0] == case["start"], msg
    # ...and off again at the window end.
    assert (case["end"], 0) in r.writes_after(key, case["start"]), msg
    assert "charge" not in r.inverter_actions(case["end"], case["end"] + timedelta(minutes=30)), msg


# ---------------------------------------------------------------------------
# Discharge and PV days
# ---------------------------------------------------------------------------
# A duck-curve day: 0.20 at night, 0.28 morning peak, 0.08 midday, 0.40 from
# 18:00 to 21:00.  Set REPLAY_PLOT_DIR to save a chart of each replay below
# (needs matplotlib): price bars coloured by what the INVERTER did, the
# measured SOC, PV and grid power.


def DUCK(d):
    def price(h):
        if 18 <= h < 21:
            return 0.40
        if 7 <= h < 9:
            return 0.28
        if 10 <= h < 16:
            return 0.08
        return 0.20
    return [round(price(i / 4) - 0.005 * (d - D).days, 3) for i in range(96)]


SUN = pv_bell(6.0)                      # ~40 kWh clear-sky day, 07:00-19:00
TRADER = {"grid_mode": "both", "price_mode": "auto"}
SELL_EVENING = {slot(h, m): "discharge" for h in (18, 19) for m in (0, 15, 30, 45)}


def _chart(r, name):
    out = os.environ.get("REPLAY_PLOT_DIR")
    if out:
        os.makedirs(out, exist_ok=True)
        r.plot(os.path.join(out, f"{name}.png"), name)


def _episodes(r, key, t0):
    """(on, off) pairs of a rule flag written after t0."""
    out, on = [], None
    for at, value in r.writes_after(key, t0):
        if value and on is None:
            on = at
        elif not value and on is not None:
            out.append((on, at))
            on = None
    return out + ([(on, None)] if on else [])


def _sell_key(model):
    return "econ_rule_1_sell_enable" if model in const.ECO_TIMEOFUSE_MODELS \
        else "econ_rule_1_enable"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [TREX_10, TREX_25])
async def test_sell_override_discharges_for_its_window_down_to_the_floor(model):
    """Manual mode, threshold at the maximum: nothing sells but the override."""
    r = _replay(model, options={"grid_mode": "to_grid", "price_mode": "manual",
                                "price_threshold_level": 10},
                soc=90.0, start=day(D, 16), prices_for=DUCK)
    r.coordinator.set_slot_overrides({"today": SELL_EVENING, "tomorrow": {}})
    await r.run_until(day(D, 21))
    _chart(r, f"sell_override_{model[:9]}")

    assert r.inverter_actions(day(D, 16), day(D, 18)) == {"self_use"}, r.timeline()
    assert r.inverter_actions(day(D, 18), day(D, 20)) == {"discharge"}, r.timeline()
    assert r.inverter_actions(day(D, 20), day(D, 21)) == {"self_use"}, r.timeline()
    assert min(t.soc for t in r.ticks) >= 20.0          # battery_discharge_min_level
    assert r.inverter.grid_export_kwh > 7.0 and r.inverter.grid_import_kwh == 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [TREX_10, TREX_25])
async def test_trader_sells_from_the_start_of_an_equal_priced_peak(model):
    """Every 18:00-21:00 slot costs 0.40.  SOC validation gave up the EARLIEST
    equal-priced sell first, so each re-plan slid the window later: selling
    began at 20:00, the executing slot was dropped at :12 and re-sold at :15,
    and the rest went at the 0.20 night price."""
    r = _replay(model, options=TRADER, soc=95.0, start=day(D, 16), prices_for=DUCK)
    await r.run_until(day(NEXT, 1))
    _chart(r, f"trader_evening_{model[:9]}")

    episodes = _episodes(r, _sell_key(model), day(D, 16))
    assert len(episodes) == 1, episodes                 # one sale, no flip-flop
    on, off = episodes[0]
    assert on == day(D, 18) and off <= day(D, 21), episodes
    sold_at = {t.price for t in r.ticks if t.inverter == "discharge"}
    assert sold_at == {0.40}, sold_at
    floor = r.coordinator._reserve_target_pct           # the sale stops at the reserve
    assert min(t.soc for t in r.ticks if t.inverter == "discharge") >= floor - 0.5


@pytest.mark.asyncio
async def test_a_heavy_load_while_selling_is_not_toggled_every_poll():
    """An 8 kW load during a 5 kW sale imports 3 kW: the guard suppresses the
    sale.  Idle, the battery covers the house, the import vanishes — and the
    guard used to let the sale straight back in, every minute for as long as
    the load ran (156 rule writes in two hours).  Now it holds off 5 min."""
    heavy = lambda t: 8.0 if day(D, 18, 20) <= t < day(D, 18, 50) else 0.6
    r = _replay(options=TRADER, soc=95.0, start=day(D, 17, 55), prices_for=DUCK,
                load_kw=heavy)
    await r.run_until(day(D, 19), step_s=10)            # the real poll interval
    _chart(r, "heavy_load_while_selling")

    episodes = _episodes(r, "econ_rule_1_enable", day(D, 17, 55))
    assert episodes[0][0] == day(D, 18)
    pauses = [(nxt[0] - prev[1]).total_seconds()
              for prev, nxt in itertools.pairwise(episodes)]
    assert pauses and all(p >= 300 for p in pauses), episodes
    assert len(episodes) <= 8, episodes


@pytest.mark.asyncio
async def test_a_one_poll_import_spike_does_not_stop_a_sale():
    """A moderate spike (6 kW for one 10 s poll: 1 kW import) is tolerated."""
    spike = lambda t: 6.0 if t == day(D, 18, 30) else 0.6
    r = _replay(options=TRADER, soc=95.0, start=day(D, 17, 55), prices_for=DUCK,
                load_kw=spike)
    await r.run_until(day(D, 18, 45), step_s=10)
    assert r.inverter_actions(day(D, 18), day(D, 18, 45)) == {"discharge"}, r.timeline(5)


@pytest.mark.asyncio
async def test_a_sunny_day_needs_no_grid():
    """Forecast and sky agree: the sun fills the battery, nothing is bought."""
    r = _replay(options=AUTO, soc=40.0, start=day(D, 5), prices_for=DUCK,
                pv_kw=SUN, forecast_kw=SUN)
    await r.run_until(day(D, 22))
    _chart(r, "sunny_save_money")

    assert "charge" not in r.inverter_actions(day(D, 5), day(D, 22)), r.timeline(60)
    assert r.writes_after("econ_rule_1_enable", day(D, 5)) == []
    assert r.soc_at(day(D, 14)) == pytest.approx(100.0, abs=0.5)
    assert r.inverter.grid_import_kwh == 0.0


@pytest.mark.asyncio
async def test_a_cloudy_day_under_a_sunny_forecast_buys_at_the_cheap_midday():
    """The forecast promises 6 kW, the sky delivers 1 kW.  PV confidence
    (actual vs forecast, read back from the inverter's PV energy register)
    must notice, and the shortfall must be bought cheap, not at a peak."""
    r = _replay(options={**AUTO, "daily_consumption_estimate": 24.0}, soc=30.0,
                start=day(D, 8), prices_for=DUCK,
                pv_kw=pv_bell(1.0), forecast_kw=SUN, load_kw=lambda _t: 1.0)
    await r.run_until(day(D, 23))
    _chart(r, "cloudy_under_sunny_forecast")

    bought = [t.price for t in r.ticks if t.inverter == "charge"]
    assert max(bought) < 0.40, r.timeline(60)                # never at the peak
    assert bought.count(0.08) / len(bought) > 0.8            # the bulk at the trough
    peak = [t.grid_kw for t in r.ticks if day(D, 18) <= t.at < day(D, 21)]
    assert max(peak) <= 0.0, r.timeline(60)                  # house never on peak grid
    assert r.coordinator._last_pv_confidence < 0.5


@pytest.mark.asyncio
async def test_a_charge_override_runs_on_a_sunny_noon():
    r = _replay(TREX_25, options=AUTO, soc=40.0, start=day(D, 9), prices_for=DUCK,
                pv_kw=SUN, forecast_kw=SUN)
    r.coordinator.set_slot_overrides({"today": {slot(12, m): "charge" for m in (0, 15, 30, 45)},
                                      "tomorrow": {}})
    await r.run_until(day(D, 15))
    _chart(r, "charge_override_sunny_noon")

    assert "charge" not in r.inverter_actions(day(D, 9), day(D, 12)), r.timeline()
    assert r.rule_driven(day(D, 12), day(D, 13)), r.timeline()
    assert r.inverter_actions(day(D, 13), day(D, 15)) == {"self_use"}, r.timeline()


@pytest.mark.asyncio
async def test_negative_midday_prices_charge_to_full():
    """charge_to_full_on_negative_price: paid to import, so every p<0 slot
    charges (or holds full), even though the sun would have filled it too."""
    def negative_midday(d):
        return [-0.05 if 11 <= i / 4 < 14 else p for i, p in enumerate(DUCK(d))]
    r = _replay(options={**AUTO, "charge_to_full_on_negative_price": "on"}, soc=40.0,
                start=day(D, 6), prices_for=negative_midday, pv_kw=SUN, forecast_kw=SUN)
    await r.run_until(day(D, 16))
    _chart(r, "negative_midday_charge_to_full")

    assert r.rule_driven(day(D, 11), day(D, 14)), r.timeline()
    assert {t.price for t in r.ticks if t.inverter == "charge"} == {-0.05}
    assert r.soc_at(day(D, 13)) == pytest.approx(100.0, abs=0.5)


@pytest.mark.asyncio
async def test_a_sunny_trader_day_sells_the_solar_at_the_evening_peak():
    r = _replay(options=TRADER, soc=40.0, start=day(D, 5), prices_for=DUCK,
                pv_kw=SUN, forecast_kw=SUN)
    await r.run_until(day(NEXT, 1))
    _chart(r, "sunny_trader")

    assert "charge" not in r.inverter_actions(day(D, 5), day(NEXT, 1))
    assert {t.price for t in r.ticks if t.inverter == "discharge"} == {0.40}
    assert r.inverter_actions(day(D, 18), day(D, 18, 15)) == {"discharge"}, r.timeline()
    assert r.inverter.grid_import_kwh == 0.0


@pytest.mark.asyncio
async def test_cheap_today_is_not_deferred_to_a_marginally_cheaper_tomorrow():
    """13:30, tomorrow's prices are out and its midday is half a cent cheaper.
    The old two-day rule moved today's whole deficit to tomorrow ("skipping
    today is free: the house runs on grid") — and the house then bought the
    evening at 0.40 and the night at 0.20: €3.44 vs €1.38 on this replay.
    Now a today slot keeps the deficit when it beats tonight's grid after
    losses, earliest first (maintainer decision, Oct 2026)."""
    sunny_tomorrow = lambda t: SUN(t) if t.date() > D else 0.0
    r = _replay(options={**AUTO, "daily_consumption_estimate": 24.0}, soc=45.0,
                start=day(D, 13, 30), prices_for=DUCK, load_kw=lambda _t: 1.0,
                pv_kw=sunny_tomorrow, forecast_kw=sunny_tomorrow)
    await r.run_until(day(NEXT, 12))
    _chart(r, "cheap_today_not_deferred")

    assert r.inverter_actions(day(D, 13, 30), day(D, 15)) == {"charge"}, r.timeline(60)
    assert max(t.price for t in r.ticks if t.inverter == "charge") < 0.40
    peak = [t.grid_kw for t in r.ticks if day(D, 18) <= t.at < day(D, 21)]
    assert max(peak) <= 0.0, r.timeline(60)
    assert r.inverter.grid_cost < 2.0


# ---------------------------------------------------------------------------
# Grid current protection with a flexible load running
# ---------------------------------------------------------------------------

EV = "switch.phoenix_charger"
EV_OPTIONS = {"flexible_load_1_enabled": "on", "flexible_load_1_name": "Phoenix charger",
              "flexible_load_1_switch_entity": EV, "flexible_load_1_power_kw": 3.7,
              "power_level": 8, "max_amperage_per_phase": 18}


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [TREX_10, TREX_25])
async def test_grid_current_limit_holds_while_an_ev_charges(model):
    """Field report: 18 A limit, L2 at 23 A then 28 A, Active power still 8 kW.

    Over 95 % of the limit safe power sheds a flexible load FIRST and skips
    the battery reduction when it did.  It switched the charger off — and
    _actuate_flex_loads, later in the same poll, switched it straight back on
    because the schedule wanted it.  So every poll "shed" the EV, the battery
    kept charging at 8 kW, and the current never came down."""
    r = _replay(model, options={**AUTO, **EV_OPTIONS}, soc=40.0, start=day(D, 11, 55),
                prices_for=DUCK, load_kw=lambda _t: 2.0, switched_loads={EV: 3.7})
    r.coordinator.set_slot_overrides({"today": {slot(12, m): "charge" for m in (0, 15, 30, 45)},
                                      "tomorrow": {}})
    await r.run_until(day(D, 13), step_s=10)
    _chart(r, f"grid_limit_with_ev_{model[:9]}")

    settled = [t.max_amps for t in r.ticks if day(D, 12, 2) <= t.at < day(D, 13)]
    assert max(settled) <= 18.0, f"peak {max(settled)} A\n{r.timeline(5)}"
    assert "charge" in r.inverter_actions(day(D, 12), day(D, 13))     # still charging, slower
    assert r.coordinator.safe_max_power < 8


@pytest.mark.asyncio
async def test_a_shed_load_stays_off_while_the_current_is_high():
    r = _replay(options={**AUTO, **EV_OPTIONS}, soc=40.0, start=day(D, 11, 55),
                prices_for=DUCK, load_kw=lambda _t: 2.0, switched_loads={EV: 3.7})
    r.coordinator.set_slot_overrides({"today": {slot(12, m): "charge" for m in (0, 15, 30, 45)},
                                      "tomorrow": {}})
    calls = []
    real = r.hass._service_call

    async def record(domain, service, data=None, **kw):
        calls.append((r.clock.now, service))
        await real(domain, service, data, **kw)
    r.hass.services.async_call = record
    await r.run_until(day(D, 12, 30), step_s=10)

    toggles = [c for c in calls if c[0] >= day(D, 12)]
    assert len(toggles) <= 2 * (30 // 5 + 1), toggles       # at most once per 5 min hold


@pytest.mark.asyncio
async def test_grid_current_limit_holds_with_a_current_controlled_ev():
    """The reporter's charger has a current entity (16 A, steps 6-16): safe
    power steps it down first — and above the limit now also cuts the battery
    in the same poll instead of waiting for the EV to run out of steps."""
    r = _replay(options={**AUTO, **EV_OPTIONS,
                         "flexible_load_1_current_entity": "number.phoenix_current",
                         "flexible_load_1_current_steps": "6,10,13,16"},
                soc=40.0, start=day(D, 11, 55), prices_for=DUCK, load_kw=lambda _t: 2.0,
                switched_loads={EV: 3.7}, current_entities={EV: "number.phoenix_current"})
    r.coordinator.set_slot_overrides({"today": {slot(12, m): "charge" for m in (0, 15, 30, 45)},
                                      "tomorrow": {}})
    await r.run_until(day(D, 13), step_s=10)
    _chart(r, "grid_limit_with_stepped_ev")

    settled = [t.max_amps for t in r.ticks if day(D, 12, 2) <= t.at < day(D, 13)]
    assert max(settled) <= 18.0, f"peak {max(settled)} A\n{r.timeline(5)}"


@pytest.mark.asyncio
async def test_battery_power_is_cut_before_any_load_is_shed():
    """Maintainer order: lower the battery power limit first; shed flexible
    loads only when the battery is at its minimum and the current is still
    too high.  With a 25 A limit, cutting the battery alone is enough here —
    the EV must keep charging the whole time."""
    r = _replay(options={**AUTO, **EV_OPTIONS, "max_amperage_per_phase": 25}, soc=40.0,
                start=day(D, 11, 55), prices_for=DUCK, load_kw=lambda _t: 2.0,
                switched_loads={EV: 3.7})
    r.coordinator.set_slot_overrides({"today": {slot(12, m): "charge" for m in (0, 15, 30, 45)},
                                      "tomorrow": {}})
    await r.run_until(day(D, 13), step_s=10)
    _chart(r, "battery_cut_before_load_shed")

    assert r.hass.switch_on.get(EV) is True
    assert not r.coordinator._flex_load_shed_until               # never shed
    settled = [t.max_amps for t in r.ticks if day(D, 12, 2) <= t.at < day(D, 13)]
    assert max(settled) <= 25.0, f"peak {max(settled)} A\n{r.timeline(5)}"
    assert r.coordinator.safe_max_power < 8                     # the battery paid
    assert "charge" in r.inverter_actions(day(D, 12), day(D, 13))


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [TREX_10, TREX_25])
async def test_battery_power_is_cut_in_one_step(model):
    """Maintainer: cut the battery to the level the current needs in ONE write,
    not 2 kW per poll.  30.6 A on L2 against a 25 A limit: the excess over 80 %
    (10.6 A) times 230 V times three phases is 7.3 kW, so 8 kW drops straight
    to the 1 kW minimum on the next poll — no 6/4/2 kW staircase in between."""
    r = _replay(model, options={**AUTO, **EV_OPTIONS, "max_amperage_per_phase": 25}, soc=40.0,
                start=day(D, 11, 55), prices_for=DUCK, load_kw=lambda _t: 2.0,
                switched_loads={EV: 3.7})
    await r.run_until(day(D, 12, 10), step_s=10)

    first = r.writes("econ_rule_1_power")[0]
    cuts = [w for w in r.writes("econ_rule_1_power")
            if w.at > first.at and w.values[0] != first.values[0]]
    assert len(cuts) == 1, cuts
    assert cuts[0].values[0] * 8 == first.values[0]            # 8 kW → 1 kW, unit-agnostic
    assert (cuts[0].at - first.at).total_seconds() <= 10
    settled = [t.max_amps for t in r.ticks if t.at > cuts[0].at]
    assert max(settled) <= 25.0, f"peak {max(settled)} A\n{r.timeline(5)}"
    assert r.hass.switch_on.get(EV) is True
