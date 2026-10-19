"""The exact planner (the "optimal" engine) — held to the RULES, not to itself.

Why this file exists
--------------------
For half a year the from_grid planner was a stack of heuristics: a reserve
target, a deficit, a cheapest-N selector, a validation pass that dropped
slots, a re-shop that added some back, a spill relocator, a top-off gate, a
horizon cap, a today-first override.  Each patch fixed one report and moved
the weirdness somewhere else, because nothing tested the plan against what
it is FOR.  Measured on 40 random situations, the old selector paid more
than necessary in 20 — up to 96 slots bought at 99 % SOC for no benefit.

The planner now solves one cost model exactly (ems.plan_optimal_from_grid).
These tests check its plans against an INDEPENDENT model written here: a
plain continuous simulation of the battery and the house that knows nothing
about the planner's lattice.  The rules, in the words a user would use:

1.  Never buy when buying nothing is as cheap.                    (test_rule_1)
2.  Every bought quarter-hour pays for itself.                    (test_rule_2)
3.  Never buy where a cheaper moment would do the same job.       (test_rule_3)
4.  A fuller battery never makes it buy more; nor more sun; nor a
    smaller house; nor a cheaper night.                     (test_monotone_*)
5.  Only price RATIOS matter (scaling every price changes nothing).
6.  Same input, same plan; and the next plan agrees with this one
    (re-planning one step later after following the plan changes nothing
    already decided).                                     (test_replan_*)
7.  The battery stays within its floor and capacity; grid charging stops
    at max SOC.                                             (test_physics_*)
8.  Among equal prices it charges the EARLIEST (fill as soon as possible —
    maintainer decision, Oct 2026).
9.  Exact: no plan is cheaper — brute force over every combination on short
    horizons, a finer independent optimiser on long ones.  (test_exact_*)

Every generated situation is seeded and the test id names the seed, so a
failure reproduces exactly.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import itertools
import math
import os
import random
import sys

import pytest

_ems_path = os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "ha_felicity", "ems.py"
)
if "ems" in sys.modules and getattr(sys.modules["ems"], "__file__", None) and os.path.samefile(
        sys.modules["ems"].__file__, _ems_path):
    ems = sys.modules["ems"]
else:
    _spec = importlib.util.spec_from_file_location("ems", _ems_path)
    ems = importlib.util.module_from_spec(_spec)
    sys.modules["ems"] = ems
    _spec.loader.exec_module(ems)

EMSConfig = ems.EMSConfig
EMSState = ems.EMSState

#: Plans are compared on money.  The planner works on a fine lattice, so its
#: own figure and the continuous simulation below differ by a few cents at
#: most; anything beyond this is a real difference.
def _tol(cost: float) -> float:
    return 0.02 + 0.005 * abs(cost)


# ── situations ──────────────────────────────────────────────────────────────

def _prices(rng: random.Random, n: int) -> list[float]:
    kind = rng.choice(["duck", "night", "flat", "walk", "evening", "negative"])
    base = rng.uniform(0.08, 0.35)
    hourly = []
    level = base
    for h in range(24):
        if kind == "duck":
            p = base + 0.15 * (17 <= h <= 21) - 0.12 * (10 <= h <= 15)
        elif kind == "night":
            p = base - 0.10 * (h < 6) + 0.10 * (7 <= h <= 9 or 17 <= h <= 21)
        elif kind == "flat":
            p = base
        elif kind == "evening":
            p = base + 0.25 * (16 <= h <= 22)
        elif kind == "negative":
            p = base - (base + 0.05) * (11 <= h <= 14) + 0.1 * (18 <= h <= 21)
        else:
            level += rng.uniform(-0.04, 0.04)
            p = level
        hourly.append(p)
    per = n // 24
    return [round(hourly[i // per] + rng.uniform(-0.01, 0.01), 4) for i in range(n)]


def _profile(rng: random.Random, daily: float) -> dict[int, float]:
    shape = rng.choice(["flat", "evening", "day", "night_ev"])
    weights = {}
    for h in range(24):
        w = 1.0
        if shape == "evening":
            w = 2.2 if 17 <= h <= 22 else (0.5 if h < 6 else 1.0)
        elif shape == "day":
            w = 2.0 if 9 <= h <= 16 else 0.6
        elif shape == "night_ev":
            w = 3.0 if 0 <= h <= 4 else 0.8
        weights[h] = w
    total = sum(weights.values())
    return {h: daily * w / total for h, w in weights.items()}


def situation(seed: int, *, slots: int | None = None, tomorrow: bool | None = None,
              priority: str | None = None, hour: int | None = None):
    """A realistic, reproducible from_grid situation."""
    rng = random.Random(seed)
    cap = rng.choice([5.0, 10.0, 15.0, 30.0, 48.0, 74.0, 100.0])
    floor = rng.choice([10, 20, 30])
    inverter = rng.choice([5.0, 6.0, 10.0, 15.0])
    power = min(inverter, rng.choice([2.0, 3.0, 5.0, 8.0, 10.0]), cap / 2)
    sunrise, sunset = rng.choice([(7.5, 17.0), (6.0, 20.5), (8.5, 16.5), (5.5, 21.5)])
    pv_today = rng.choice([0.0, 2.0, 8.0, 20.0, 40.0]) * cap / 30
    pv_tomorrow = rng.choice([0.0, 2.0, 8.0, 20.0, 40.0]) * cap / 30
    daily = rng.uniform(4.0, 60.0) * cap / 30
    n = slots or rng.choice([96, 96, 96, 48, 24])
    has_tomorrow = rng.random() < 0.65 if tomorrow is None else tomorrow
    config = EMSConfig(
        grid_mode="from_grid",
        optimization_priority=priority or rng.choice(["cost", "self_consumption", "longevity"]),
        battery_capacity_kwh=cap,
        battery_discharge_min_pct=floor,
        battery_charge_max_pct=rng.choice([100, 100, 90]),
        efficiency=rng.choice([0.85, 0.9, 0.95]),
        safe_power_kw=power,
        inverter_max_power_kw=inverter,
        consumption_est_kwh=daily,
        scheduler_engine="optimal",
    )
    state = EMSState(
        battery_soc_pct=rng.uniform(floor, 100.0),
        slot_prices_today=_prices(rng, n),
        slot_prices_tomorrow=_prices(rng, n) if has_tomorrow else None,
        pv_hourly_kwh=ems._synthesize_pv_hourly(pv_today, sunrise, sunset),
        pv_hourly_kwh_tomorrow=ems._synthesize_pv_hourly(pv_tomorrow, sunrise, sunset),
        pv_forecast_today=pv_today,
        pv_forecast_tomorrow=pv_tomorrow,
        pv_actual_today_kwh=None,        # confidence 1: the forecast is the truth here
        consumption_hourly_kwh=_profile(rng, daily),
        current_hour=rng.randrange(24) if hour is None else hour,
        current_minute=rng.choice([0, 7, 15, 30, 44, 45]),
        sunrise_hour=sunrise,
        sunset_hour=sunset,
    )
    return config, state


def _current_slot(state: EMSState) -> int:
    n = len(state.slot_prices_today)
    return min(n - 1, int((state.current_hour * 60 + state.current_minute) / (1440 / n)))


def _plan(config: EMSConfig, state: EMSState):
    return ems.plan_optimal_from_grid(
        config, state, state.battery_soc_pct / 100.0 * config.battery_capacity_kwh,
        _current_slot(state), 1.0)


# ── the independent model ───────────────────────────────────────────────────

def _limits(config: EMSConfig):
    cap = config.battery_capacity_kwh
    floor = cap * config.battery_discharge_min_pct / 100.0
    cmax = max(floor, cap * config.battery_charge_max_pct / 100.0)
    cycle = config.battery_cycle_cost_eur_kwh
    if config.optimization_priority == "longevity":
        cycle = max(cycle, 0.05)
    return cap, floor, cmax, cycle


def simulate(config: EMSConfig, plan, charging, start_kwh: float | None = None):
    """(cost, socs, bought_kwh) of a charging decision per step, continuously.

    ``charging`` per step is either a bool (charge the whole step at the
    Power Level) or a float (grid kWh to take in that step).  The house is
    fed by PV, then the battery; below the floor by the grid at that step's
    price, and a charge then goes into the battery on top.  Charging stops at
    max SOC; PV beyond full is spilled.  After the horizon, each kWh short of
    the night's need costs the night price.  That is the whole cost model —
    written out here so the planner's lattice cannot agree with itself by
    construction.
    """
    cap, floor, cmax, cycle = _limits(config)
    eff = config.efficiency
    s = plan.start_kwh if start_kwh is None else start_kwh
    s = min(cap, max(floor, s))
    cost = bought = 0.0
    socs = []
    checkpoint = None
    if config.reserve_target_pct > 0 and any(st.day == 1 for st in plan.horizon):
        fixed = max(0.0, min(cmax, cap * config.reserve_target_pct / 100.0) - floor)
        last_today = max((k for k, st in enumerate(plan.horizon) if st.day == 0), default=None)
        if last_today is not None:
            checkpoint = last_today, fixed
    for k, (st, want) in enumerate(zip(plan.horizon, charging)):
        p = st.price
        x = s + st.pv_kwh - st.cons_kwh
        if x < floor:
            cost += p * (floor - x)
            x = floor
        x = min(cap, x)
        amount = st.grid_kwh if want is True else (0.0 if want is False else float(want))
        b = min(amount, st.grid_kwh, max(0.0, cmax - x) / eff)
        if b > 0:
            x += b * eff
            cost += p * b + cycle * b * eff
            bought += b
        s = x
        socs.append(s)
        if checkpoint and checkpoint[0] == k:
            cost += plan.tonight_price * max(0.0, checkpoint[1] - (s - floor))
    cost += plan.night_price * max(0.0, plan.night_need_kwh - (s - floor))
    return cost, socs, bought


def brute_force(config, plan) -> float:
    """Every whole-step combination: an upper bound for the planner, which
    may also charge part of a step."""
    n = len(plan.horizon)
    best = math.inf
    chargeable = [k for k, st in enumerate(plan.horizon) if st.grid_kwh > 1e-9]
    for combo in itertools.product((False, True), repeat=len(chargeable)):
        charging = [False] * n
        for k, c in zip(chargeable, combo):
            charging[k] = c
        best = min(best, simulate(config, plan, charging)[0])
    return best


def lp_lower_bound(config, plan) -> float:
    """A linear program (pulp) that is strictly MORE permissive than the
    model: the grid may feed the house whenever it likes, i.e. the battery
    may also "hold" at no loss.  No real plan can be cheaper, so a plan below
    this bound means the simulation itself is broken.  (How much "hold" would
    save is visible here too — the inverter's charge rule cannot hold below
    max SOC today.)  Only for charge_max = 100 %."""
    pulp = pytest.importorskip("pulp")
    cap, floor, cmax, cycle = _limits(config)
    assert cmax >= cap - 1e-9
    eff = config.efficiency
    m = pulp.LpProblem("oracle", pulp.LpMinimize)
    n = len(plan.horizon)
    b = [pulp.LpVariable(f"b{k}", 0, st.grid_kwh) for k, st in enumerate(plan.horizon)]
    # The grid feeds at most the house (it may do so while the battery holds).
    imp = [pulp.LpVariable(f"i{k}", 0, st.cons_kwh) for k, st in enumerate(plan.horizon)]
    spill = [pulp.LpVariable(f"x{k}", 0) for k in range(n)]
    soc = [pulp.LpVariable(f"s{k}", floor, cap) for k in range(n)]
    short = pulp.LpVariable("short", 0)
    prev = min(cap, max(floor, plan.start_kwh))
    for k, st in enumerate(plan.horizon):
        m += soc[k] == prev + st.pv_kwh - st.cons_kwh + imp[k] - spill[k] + eff * b[k]
        prev = soc[k]
    m += short >= plan.night_need_kwh - (prev - floor)
    m += (pulp.lpSum(st.price * (b[k] + imp[k]) + cycle * eff * b[k]
                     for k, st in enumerate(plan.horizon)) + plan.night_price * short)
    m.solve(pulp.PULP_CBC_CMD(msg=False))
    assert pulp.LpStatus[m.status] == "Optimal"
    return pulp.value(m.objective)


def fine_optimum(config, plan, points: int = 1200) -> float:
    """A second independent optimiser for any charge_max: backward induction
    on a finer grid, with each step allowed nothing, a quarter, half,
    three quarters or all of its charge — its decisions then SIMULATED, so
    the cost is a real plan's cost."""
    cap, floor, cmax, cycle = _limits(config)
    eff = config.efficiency
    d = (cap - floor) / points
    grid = [floor + i * d for i in range(points + 1)]

    def interp(values, x):
        f = (x - floor) / d
        i = int(f)
        if i >= points:
            return values[points]
        if i < 0:
            return values[0]
        t = f - i
        return values[i] * (1 - t) + values[i + 1] * t

    def step(st, s, share):
        x = s + st.pv_kwh - st.cons_kwh
        c = 0.0
        if x < floor:
            c += st.price * (floor - x)
            x = floor
        x = min(cap, x)
        b = min(st.grid_kwh * share, max(0.0, cmax - x) / eff)
        x += b * eff
        return c + st.price * b + cycle * b * eff, x, b

    shares = (0.0, 0.25, 0.5, 0.75, 1.0)
    tables = [None] * (len(plan.horizon) + 1)
    values = [plan.night_price * max(0.0, plan.night_need_kwh - (s - floor)) for s in grid]
    tables[-1] = values
    for k in range(len(plan.horizon) - 1, -1, -1):
        st = plan.horizon[k]
        values = [min(c + interp(values, x) for c, x, _ in (step(st, s, a) for a in shares))
                  for s in grid]
        tables[k] = values
    s = min(cap, max(floor, plan.start_kwh))
    amounts = []
    for k, st in enumerate(plan.horizon):
        _, s, b = min((step(st, s, a) for a in shares),
                      key=lambda o: o[0] + interp(tables[k + 1], o[1]))
        amounts.append(b)
    return simulate(config, plan, amounts)[0]


SEEDS = list(range(120))


# ── 1-3: every decision earns its place ─────────────────────────────────────

@pytest.mark.parametrize("seed", SEEDS)
def test_rule_1_never_buy_when_buying_nothing_is_as_cheap(seed):
    config, state = situation(seed)
    plan = _plan(config, state)
    cost = simulate(config, plan, plan.bought_kwh)[0]
    nothing, _, _ = simulate(config, plan, [False] * len(plan.horizon))
    assert cost <= nothing + _tol(nothing), (seed, cost, nothing)


@pytest.mark.parametrize("seed", SEEDS)
def test_rule_2_every_bought_step_pays_for_itself(seed):
    """Take any single bought quarter-hour away: the plan must not get cheaper."""
    config, state = situation(seed)
    plan = _plan(config, state)
    cost = simulate(config, plan, plan.bought_kwh)[0]
    for k, kwh in enumerate(plan.bought_kwh):
        if kwh <= 0:
            continue
        without = list(plan.bought_kwh)
        without[k] = 0.0
        other = simulate(config, plan, without)[0]
        st = plan.horizon[k]
        assert other >= cost - _tol(cost), (
            f"seed {seed}: dropping the {st.day}/{st.idx} step at {st.price} saves "
            f"{cost - other:.3f}")


@pytest.mark.parametrize("seed", SEEDS)
def test_rule_3_no_cheaper_moment_does_the_same_job(seed):
    """Move what any quarter-hour buys to any cheaper quarter-hour with room
    to take it: not cheaper."""
    config, state = situation(seed)
    plan = _plan(config, state)
    cost = simulate(config, plan, plan.bought_kwh)[0]
    for k, kwh in enumerate(plan.bought_kwh):
        if kwh <= 1e-6:
            continue
        for j, other_kwh in enumerate(plan.bought_kwh):
            st = plan.horizon[j]
            if st.price >= plan.horizon[k].price or st.grid_kwh - other_kwh < 1e-6:
                continue
            moved = list(plan.bought_kwh)
            shift = min(kwh, st.grid_kwh - other_kwh)
            moved[k] -= shift
            moved[j] += shift
            other = simulate(config, plan, moved)[0]
            assert other >= cost - _tol(cost), (
                f"seed {seed}: buying at {st.price} ({st.day}/{st.idx}) instead of "
                f"{plan.horizon[k].price} ({plan.horizon[k].day}/{plan.horizon[k].idx}) "
                f"saves {cost - other:.3f}")


# ── 9: exact ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("seed", range(80))
def test_exact_against_every_combination(seed):
    """Late evening, today only: short enough to try every whole-step plan
    there is.  The planner may also charge part of a step, so it must be at
    least as cheap as the best of them."""
    config, state = situation(seed, slots=96, tomorrow=False, hour=21)
    state = dataclasses.replace(state, current_minute=random.Random(seed).choice([0, 15, 30, 45]))
    plan = _plan(config, state)
    if len(plan.horizon) > 13:
        pytest.skip("horizon longer than the brute force covers")  # pragma: no cover
    best = brute_force(config, plan)
    cost = simulate(config, plan, plan.bought_kwh)[0]
    assert cost <= best + _tol(best), (seed, cost, best)


@pytest.mark.parametrize("seed", range(40))
def test_no_plan_beats_the_linear_lower_bound(seed):
    config, state = situation(2000 + seed)
    config = dataclasses.replace(config, battery_charge_max_pct=100)
    plan = _plan(config, state)
    bound = lp_lower_bound(config, plan)
    cost = simulate(config, plan, plan.bought_kwh)[0]
    assert cost >= bound - _tol(bound), (seed, cost, bound)


@pytest.mark.parametrize("seed", range(24))
def test_exact_against_an_independent_optimiser(seed):
    """Any situation, up to two days, any charge_max: no plan the second
    optimiser finds is cheaper."""
    config, state = situation(1000 + seed)
    plan = _plan(config, state)
    best = fine_optimum(config, plan, points=600)
    cost = simulate(config, plan, plan.bought_kwh)[0]
    assert cost <= best + _tol(best), (seed, cost, best)


# ── 4: monotone ─────────────────────────────────────────────────────────────

def _bought(config, state) -> float:
    plan = _plan(config, state)
    return simulate(config, plan, plan.bought_kwh)[2]


def _cost(config, state) -> float:
    plan = _plan(config, state)
    return simulate(config, plan, plan.bought_kwh)[0]


def _one_step(config) -> float:
    """What one charging quarter-hour can buy — the plan's granularity."""
    return config.safe_power_kw * 0.25 + 1e-6


def _priced(state) -> bool:
    """Every price clearly positive.  Near zero, how MUCH free energy is
    bought is a matter of indifference (it costs nothing either way), and
    below zero buying earns — so "buys less" is only a rule above zero."""
    prices = list(state.slot_prices_today) + list(state.slot_prices_tomorrow or [])
    return min(prices) > 0.02


@pytest.mark.parametrize("seed", range(60))
def test_monotone_a_fuller_battery_never_buys_more(seed):
    config, state = situation(seed)
    lower = dataclasses.replace(state, battery_soc_pct=max(
        config.battery_discharge_min_pct, state.battery_soc_pct - 15))
    if not _priced(state):
        # Below zero, room in the battery is worth money (it gets paid to
        # charge), so a fuller battery can legitimately earn less.
        return
    assert _cost(config, state) <= _cost(config, lower) + _tol(_cost(config, lower)), seed
    assert _bought(config, state) <= _bought(config, lower) + _one_step(config), seed


@pytest.mark.parametrize("seed", range(60))
def test_monotone_more_sun_never_buys_more(seed):
    config, state = situation(seed)
    sunnier = dataclasses.replace(
        state,
        pv_hourly_kwh={h: v * 1.5 + 0.2 for h, v in state.pv_hourly_kwh.items()},
        pv_hourly_kwh_tomorrow={h: v * 1.5 + 0.2
                                for h, v in (state.pv_hourly_kwh_tomorrow or {}).items()},
    )
    if not _priced(state):
        return
    assert _cost(config, sunnier) <= _cost(config, state) + _tol(_cost(config, state)), seed
    assert _bought(config, sunnier) <= _bought(config, state) + _one_step(config), seed


@pytest.mark.parametrize("seed", range(60))
def test_monotone_a_bigger_house_never_costs_less(seed):
    """Using more never costs less.  (Not "buys more": a house that already
    runs the battery to its floor gains nothing from charging in the hours it
    imports anyway, so a bigger house can rightly buy LESS into the battery —
    seed 23 does, and the independent optimiser agrees.)"""
    config, state = situation(seed)
    bigger = dataclasses.replace(
        state, consumption_hourly_kwh={h: v * 1.3 for h, v in state.consumption_hourly_kwh.items()})
    if not _priced(state):
        return
    assert _cost(config, bigger) + _tol(_cost(config, bigger)) >= _cost(config, state), seed


@pytest.mark.parametrize("seed", range(60))
def test_monotone_a_cheaper_slot_never_costs_more(seed):
    """Make any one price cheaper: the plan can only get cheaper."""
    config, state = situation(seed)
    rng = random.Random(seed)
    i = rng.randrange(_current_slot(state), len(state.slot_prices_today))
    cheaper = list(state.slot_prices_today)
    cheaper[i] -= 0.05
    other = dataclasses.replace(state, slot_prices_today=cheaper)
    assert _cost(config, other) <= _cost(config, state) + _tol(_cost(config, state)), seed


@pytest.mark.parametrize("seed", range(60))
def test_scaling_every_price_changes_nothing(seed):
    """Only price ratios matter.  (Cycle cost is a fixed €/kWh, so it is
    switched off here: scaling prices against it legitimately changes the
    plan.)"""
    config, state = situation(seed, priority=random.Random(seed).choice(
        ["cost", "self_consumption"]))
    k = random.Random(seed).choice([0.5, 2.0, 3.7])
    scaled = dataclasses.replace(
        state,
        slot_prices_today=[p * k for p in state.slot_prices_today],
        slot_prices_tomorrow=[p * k for p in state.slot_prices_tomorrow]
        if state.slot_prices_tomorrow else None,
    )
    a, b = _plan(config, scaled), _plan(config, state)
    assert a.charging == b.charging, seed
    assert all(abs(x - y) < 1e-6 for x, y in zip(a.bought_kwh, b.bought_kwh)), seed


@pytest.mark.parametrize("seed", range(40))
def test_deterministic(seed):
    config, state = situation(seed)
    first = ems.calculate_schedule(config, state)
    second = ems.calculate_schedule(config, state)
    assert first.scheduled_slots == second.scheduled_slots
    assert first.tomorrow_scheduled_slots == second.tomorrow_scheduled_slots
    assert first.soc_trajectory == second.soc_trajectory


# ── 6: the next plan agrees with this one ───────────────────────────────────

def _advance(state: EMSState, minutes: int) -> EMSState:
    total = state.current_hour * 60 + state.current_minute + minutes
    return dataclasses.replace(state, current_hour=total // 60, current_minute=total % 60)


@pytest.mark.parametrize("seed", range(60))
def test_replan_after_following_the_plan_keeps_its_decisions(seed):
    """Follow the plan quarter-hour by quarter-hour, re-planning at every step
    from where the plan said the battery would be: each step is executed
    exactly as the plan made one step earlier said — no toggling, no
    sliding forward with the clock (the October 2026 replay bugs)."""
    rng = random.Random(seed)
    config, state = situation(seed, slots=rng.choice([96, 24]), hour=rng.randrange(0, 21))
    state = dataclasses.replace(state, current_minute=0)
    plan = _plan(config, state)
    for _ in range(12):
        if len(plan.horizon) < 2 or plan.horizon[1].day != 0:
            break
        decided_next = plan.charging[1]
        soc = plan.planned_kwh[0]
        state = dataclasses.replace(_advance(state, 15), charging_now=plan.charging[0],
                                    battery_soc_pct=soc / config.battery_capacity_kwh * 100.0)
        plan = _plan(config, state)
        assert plan.charging[0] == decided_next, (
            f"seed {seed}: at {state.current_hour:02d}:{state.current_minute:02d} the plan "
            f"made 15 min earlier said charge={decided_next}, now {plan.charging[0]}")


@pytest.mark.parametrize("seed", range(80))
def test_replan_inside_a_quarter_does_not_toggle(seed):
    """The coordinator re-plans every poll.  Execute a quarter-hour minute by
    minute the way the inverter does — full Power Level while charging — and
    re-plan each minute, telling the planner whether it is charging: at most
    ONE change in the quarter, and only the planned stop (charging → done).
    The replay found the planner dropping the last minute of each quarter
    (charge → self-use → charge every 15 min) because a one-minute remainder
    "was not needed"; and near-equal prices let rounding flip a decision."""
    rng = random.Random(seed)
    config, state = situation(seed, slots=96, hour=rng.randrange(0, 22))
    state = dataclasses.replace(state, current_minute=15 * rng.randrange(4))
    plan = _plan(config, state)
    charging = plan.charging[0]
    first = plan.horizon[0]
    cap, floor, cmax, _ = _limits(config)
    per_min = 1.0 / round(first.hours * 60)
    soc = plan.start_kwh
    changes = []
    for minute in range(1, round(first.hours * 60)):
        soc += (first.pv_kwh - first.cons_kwh) * per_min
        if charging:
            soc += min(first.grid_kwh * per_min * config.efficiency, max(0.0, cmax - soc))
        soc = min(cap, max(floor, soc))
        now = dataclasses.replace(_advance(state, minute), charging_now=charging,
                                  battery_soc_pct=soc / config.battery_capacity_kwh * 100.0)
        decision = _plan(config, now).charging[0]
        if decision != charging:
            changes.append((minute, charging, decision))
            charging = decision
    assert len(changes) <= 1 and all(was for _, was, _ in changes), (seed, changes)


@pytest.mark.parametrize("seed", range(30))
def test_replan_the_schedule_the_inverter_follows_is_stable(seed):
    """The same through calculate_schedule, at slot level: a slot the previous
    plan marked "charge" (or not) is still marked so when it starts."""
    rng = random.Random(seed)
    config, state = situation(seed, slots=96, hour=rng.randrange(0, 20))
    state = dataclasses.replace(state, current_minute=0)
    result = ems.calculate_schedule(config, state)
    for _ in range(8):
        slot = _current_slot(state) + 1
        if slot >= len(state.slot_prices_today):
            break
        decided = result.scheduled_slots.get(slot)
        soc = result.soc_trajectory[slot]
        running = result.scheduled_slots.get(_current_slot(state)) == "charge"
        state = dataclasses.replace(_advance(state, 15), battery_soc_pct=soc,
                                    charging_now=running)
        result = ems.calculate_schedule(config, state)
        assert result.scheduled_slots.get(slot) == decided, (seed, slot)


# ── 7-8: physics and ties ───────────────────────────────────────────────────

@pytest.mark.parametrize("seed", SEEDS)
def test_physics_the_battery_stays_within_its_limits(seed):
    config, state = situation(seed)
    result = ems.calculate_schedule(config, state)
    cap, floor, cmax, _ = _limits(config)
    lo = config.battery_discharge_min_pct
    for traj in (result.soc_trajectory, result.tomorrow_soc_trajectory):
        for pct in traj[_current_slot(state) if traj is result.soc_trajectory else 0:]:
            assert lo - 0.15 <= pct <= 100.0, (seed, pct)
    plan = _plan(config, state)
    prev = plan.start_kwh
    for st, act, soc in zip(plan.horizon, plan.charging, plan.planned_kwh):
        assert floor - 1e-6 <= soc <= cap + 1e-6
        if act:
            # Grid charging never lifts the battery above max SOC by itself.
            assert soc <= max(cmax, prev + st.pv_kwh - st.cons_kwh) + 0.05 * cap / 100 + 0.05
        prev = soc


@pytest.mark.parametrize("seed", SEEDS)
def test_physics_tomorrow_starts_where_today_ends(seed):
    config, state = situation(seed, tomorrow=True)
    result = ems.calculate_schedule(config, state)
    plan = _plan(config, state)
    end_today = [soc for st, soc in zip(plan.horizon, plan.planned_kwh) if st.day == 0]
    if not end_today or not result.tomorrow_soc_trajectory:
        return
    first = result.tomorrow_soc_trajectory[0]
    assert abs(first - end_today[-1] / config.battery_capacity_kwh * 100.0) <= 0.15, seed


def test_equal_prices_charge_the_earliest():
    """Flat price, a battery that will run empty tonight: the charging happens
    at the first opportunity, not at the last cheap moment before it is needed."""
    config = EMSConfig(grid_mode="from_grid", battery_capacity_kwh=10,
                       battery_discharge_min_pct=10, safe_power_kw=5,
                       inverter_max_power_kw=5, consumption_est_kwh=24,
                       efficiency=0.9, scheduler_engine="optimal")
    state = EMSState(battery_soc_pct=20, slot_prices_today=[0.20] * 96,
                     pv_hourly_kwh={}, current_hour=10, current_minute=0,
                     consumption_hourly_kwh={h: 1.0 for h in range(24)})
    plan = _plan(config, state)
    bought = [k for k, a in enumerate(plan.charging) if a]
    # With a flat price, buying is no cheaper than drawing the grid live —
    # after losses it is dearer — so nothing at all is bought.
    assert bought == []
    cheap = dataclasses.replace(state, slot_prices_today=[0.10] * 48 + [0.30] * 48)
    plan = _plan(config, cheap)
    bought = [plan.horizon[k].idx for k, a in enumerate(plan.charging) if a]
    assert bought and bought[0] == 40, bought        # 10:00, the first cheap slot
    assert bought == list(range(40, 40 + len(bought))), bought


@pytest.mark.parametrize("seed", range(30))
def test_paid_to_charge_it_charges_while_there_is_room(seed):
    """A negative price pays for every kWh taken: with room in the battery
    and nothing cheaper ahead, the step is charged."""
    config, state = situation(seed, slots=96, tomorrow=False, hour=8)
    state = dataclasses.replace(state, current_minute=0, battery_soc_pct=config.battery_discharge_min_pct + 5)
    prices = list(state.slot_prices_today)
    for i in range(48, 52):                         # 12:00-13:00 pays 0.10
        prices[i] = -0.10
    state = dataclasses.replace(state, slot_prices_today=prices)
    plan = _plan(config, state)
    *_, cmax, _ = _limits(config)
    lowest = min(st.price for st in plan.horizon)
    soc = plan.start_kwh
    for st, act, after in zip(plan.horizon, plan.charging, plan.planned_kwh):
        # The best-paid steps: nothing later pays more for the same room.
        if (st.price == lowest and st.grid_kwh * config.efficiency > 0.05
                and soc + st.pv_kwh - st.cons_kwh < cmax - 0.2):
            assert act, (seed, st.idx, soc)
        soc = after


@pytest.mark.parametrize("seed", range(40))
def test_priorities_order_the_amount_bought(seed):
    """Battery care (wear priced in) never buys more than Save Money, and
    Self-Sufficiency (25 % more for the night) never less."""
    config, state = situation(seed)
    if not _priced(state):
        return
    by = {p: _bought(dataclasses.replace(config, optimization_priority=p), state)
          for p in ("longevity", "cost", "self_consumption")}
    step = _one_step(config)
    assert by["longevity"] <= by["cost"] + step, (seed, by)
    assert by["cost"] <= by["self_consumption"] + step, (seed, by)


@pytest.mark.parametrize("seed", range(40))
def test_a_higher_reserve_never_buys_less(seed):
    config, state = situation(seed)
    if not _priced(state):
        return
    low = _bought(dataclasses.replace(config, reserve_target_pct=0), state)
    high = _bought(dataclasses.replace(config, reserve_target_pct=80), state)
    assert high + _one_step(config) >= low, (seed, low, high)


def test_a_reserve_is_reached_from_the_cheap_slots_not_the_peak():
    """"Keep at least 60 %": reached from the cheap midday, never by buying
    the evening peak to hold it (the duck-curve scenario)."""
    prices = ([0.14] * 24 + [0.18] * 16 + [0.03] * 16 + [0.20] * 12 + [0.37] * 20 + [0.22] * 8)
    config = EMSConfig(grid_mode="from_grid", battery_capacity_kwh=15,
                       battery_discharge_min_pct=20, safe_power_kw=5,
                       inverter_max_power_kw=10, consumption_est_kwh=12,
                       reserve_target_pct=60, scheduler_engine="optimal")
    state = EMSState(battery_soc_pct=30, slot_prices_today=prices, pv_hourly_kwh={},
                     current_hour=0, current_minute=0)
    result = ems.calculate_schedule(config, state)
    bought = [prices[i] for i, a in result.scheduled_slots.items() if a == "charge"]
    # 0.14 at midnight is right: the battery would run empty at 03:00 and the
    # house would buy 06:00-10:00 at 0.18.  The 0.20+ evening is never bought.
    assert bought and max(bought) <= 0.14, bought
    assert 0.03 in bought
    assert result.soc_trajectory[-1] >= 60 - 1


def test_an_hourly_tariff_buys_a_quarter_not_the_whole_hour():
    """Hourly prices: the ~3.9 kWh the evening needs (17:00-24:00 at 0.30) is
    bought as three quarter-hours at 0.15, not as whole hours of 5 kWh each
    (the old whole-slot rounding would buy 5 or 10)."""
    config = EMSConfig(grid_mode="from_grid", battery_capacity_kwh=10,
                       battery_discharge_min_pct=20, safe_power_kw=5,
                       inverter_max_power_kw=10, consumption_est_kwh=12,
                       efficiency=0.9, scheduler_engine="optimal")
    prices = [0.05] * 6 + [0.15] * 11 + [0.30] * 7
    state = EMSState(battery_soc_pct=30, slot_prices_today=prices, pv_hourly_kwh={},
                     current_hour=15, current_minute=0,
                     consumption_hourly_kwh={h: 0.5 for h in range(24)})
    plan = _plan(config, state)
    bought = simulate(config, plan, plan.bought_kwh)[2]
    assert 3.0 <= bought <= 4.6, bought               # what the evening needs, at 0.15
    assert all(st.price == 0.15 for st, a in zip(plan.horizon, plan.charging) if a)


# ── the reports that made this ─────────────────────────────────────────────

def _customer_oct_19(profile):
    """Screenshot, 19 Oct 2026, 21:45: ~74 kWh at 81 %, Self-Sufficiency, 8 kW,
    tonight 0.33-0.36, tomorrow cheaper at midday, 24.5 kWh of sun tomorrow."""
    today = [0.30] * 28 + [0.33] * 40 + [0.36] * 16 + [0.339] * 12
    tomorrow = [0.33] * 24 + [0.30] * 16 + [0.27] * 24 + [0.40] * 20 + [0.36] * 12
    config = EMSConfig(grid_mode="from_grid", optimization_priority="self_consumption",
                       battery_capacity_kwh=74.0, battery_discharge_min_pct=20,
                       efficiency=0.9, safe_power_kw=8.0, inverter_max_power_kw=10.0,
                       consumption_est_kwh=38.5, scheduler_engine="optimal")
    state = EMSState(battery_soc_pct=81.0, slot_prices_today=today,
                     slot_prices_tomorrow=tomorrow,
                     pv_hourly_kwh=ems._synthesize_pv_hourly(30.7, 8, 18),
                     pv_hourly_kwh_tomorrow=ems._synthesize_pv_hourly(24.5, 8, 18),
                     pv_forecast_today=30.7, pv_forecast_remaining=0.0,
                     pv_actual_today_kwh=10.8, pv_forecast_tomorrow=24.5,
                     consumption_hourly_kwh=profile, current_hour=21, current_minute=45,
                     sunrise_hour=8.0, sunset_hour=18.0)
    return config, state


REAL_PROFILE = {h: v for h, v in enumerate([1.6] * 7 + [2.5] * 10 + [2.0] * 7)}


def test_customer_81_percent_at_night_buys_nothing_tonight():
    """(a) "the price is high now and lower tomorrow, why charge at 81 %?"  It
    must not.  (b) "why so many slots tomorrow?"  With the real profile the
    battery and tomorrow's sun carry the house through tomorrow; at most the
    cheapest moment of tomorrow tops up for the night after it (Self-
    Sufficiency holds 25 % extra for that night)."""
    config, state = _customer_oct_19(REAL_PROFILE)
    result = ems.calculate_schedule(config, state)
    assert result.scheduled_slots == {}
    assert result.status == "no_action_needed"
    tomorrow = state.slot_prices_tomorrow
    bought = sorted(result.tomorrow_scheduled_slots)
    assert len(bought) <= 4, bought                  # at most an hour ...
    assert all(tomorrow[i] == min(tomorrow) for i in bought), bought   # ... at the cheapest
    # In cost mode (no 25 % extra) nothing at all.
    cost = ems.calculate_schedule(dataclasses.replace(config, optimization_priority="cost"), state)
    assert cost.scheduled_slots == {} and cost.tomorrow_scheduled_slots == {}
    assert "no grid charging needed" in cost.schedule_reason
    # And the line on the card never dips: no quarter-hour of the night drains
    # more than the house can use in it.
    traj = result.tomorrow_soc_trajectory
    drops = [a - b for a, b in itertools.pairwise(traj)]
    assert max(drops) < 2.5 / 74 * 100 / 4 + 0.2


def test_customer_case_even_on_the_glitched_profile_tonight_is_not_bought():
    """The polluted profile (a whole day at 04:00) did make the old planner buy
    7 slots at 0.34-0.36 tonight.  The exact planner prices the alternative —
    drawing the grid at 04:30 at the cheaper night price — and does not."""
    glitched = dict(REAL_PROFILE)
    glitched[4] = 51.8
    config, state = _customer_oct_19(glitched)
    result = ems.calculate_schedule(config, state)
    assert result.scheduled_slots == {}


# ── engine wiring ───────────────────────────────────────────────────────────

def test_the_planner_is_the_default_engine():
    assert EMSConfig().scheduler_engine == "optimal"
    config, state = situation(3)
    assert ems.calculate_schedule(config, state).scheduler_active == "optimal"


@pytest.mark.parametrize("mode", ["to_grid", "both"])
def test_selling_modes_keep_the_greedy_selector(mode):
    config, state = situation(3)
    result = ems.calculate_schedule(dataclasses.replace(config, grid_mode=mode), state)
    assert result.scheduler_active == "greedy"


def test_schedule_reason_matches_the_plan():
    for seed in range(60):
        config, state = situation(seed)
        result = ems.calculate_schedule(config, state)
        n_today = sum(1 for a in result.scheduled_slots.values() if a == "charge")
        if n_today:
            assert f"{n_today} today" in result.schedule_reason, (seed, result.schedule_reason)
        elif not result.tomorrow_scheduled_slots:
            assert "Charging" not in result.schedule_reason, (seed, result.schedule_reason)
