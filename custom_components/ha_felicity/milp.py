"""Mixed-integer linear programming (MILP) scheduler for the EMS.

This is an *optional* alternative to the greedy scheduler in ``ems.py``.
It models the full remaining-today (+ tomorrow, when prices are known)
horizon as a single optimisation problem and lets a solver find the
cost-optimal charge/discharge plan, rather than the greedy
cheapest-first heuristic.

Why MILP
--------
The greedy scheduler picks charge and sell slots in separate passes.
That works in isolation but produces subtle bugs at the seams between
features — e.g. allocating all charge slots to a cheaper tomorrow while
today's expensive sell slots starve for energy.  A solver treats
"charge at slot A to sell at slot B" as a *single* joint decision, so
those cross-slot/cross-day interactions can't fall through the cracks.

Design
------
* Pure module: reads ``EMSConfig`` / ``EMSState`` by attribute, takes
  precomputed values (reserve target, PV confidence) as arguments.  No
  import of ``ems.py`` — keeps the dependency graph clean and the
  fallback bullet-proof.
* ``pulp`` is imported lazily inside :func:`solve_schedule`.  If it is
  not installed, or the solver fails / times out / is infeasible, the
  function returns ``None`` and the caller falls back to the greedy
  scheduler.
* Charge and discharge are **binary per-slot** decisions at full safe
  power — matching how the coordinator actually drives the inverter
  (a slot is charge / discharge / idle, never a partial power level).
  The SOC-bound constraints then prevent overflow and phantom charging
  for free.

The MILP produces both today's and tomorrow's ``scheduled_slots``
from the unified 2-day horizon.  The SOC trajectory and flexible-load
overlays are still computed by the existing ``ems.py`` machinery.
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Solver wall-clock cap (seconds).  A 192-slot binary model solves in
# well under this; the limit only guards against pathological inputs.
_SOLVE_TIME_LIMIT = 10

# Permanent MILP disable flag (process-lifetime).  When the CBC solver
# binary is missing or otherwise unrunnable, the failure is structural —
# it will recur on every 10s tick and never recover within this process.
# Retrying logs a full traceback ~8600×/day and wastes CPU building the
# model each time.  Once we detect an unrecoverable solver failure we set
# this flag so solve_schedule() short-circuits to greedy immediately.  It
# resets to False only on a fresh process start (HA restart / reload), so
# a genuine fix (installing CBC) is re-checked on the next boot — exactly
# the "only re-check on next boot" behaviour we want.
_MILP_DISABLED = False
_MILP_DISABLED_REASON = ""


def solve_schedule(
    config: Any,
    state: Any,
    *,
    remaining: list[tuple[int, float]],
    current_kwh: float,
    num_slots: int,
    current_slot: int,
    minutes_per_slot: float,
    reserve_target: float,
    pv_confidence: float,
) -> tuple[dict[int, str], dict[int, str]] | None:
    """Solve the EMS schedule as a MILP.

    Returns ``(today_slots, tomorrow_slots)`` where each is
    ``{slot_index: "charge"|"discharge"}``, or ``None`` if the solver
    is unavailable or fails (caller falls back to greedy).
    """
    global _MILP_DISABLED, _MILP_DISABLED_REASON

    # Short-circuit: a prior unrecoverable failure disabled MILP for the
    # lifetime of this process.  No retry, no traceback spam — greedy runs.
    if _MILP_DISABLED:
        return None

    try:
        import pulp  # noqa: PLC0415 — lazy import so ems.py works without pulp
    except Exception as err:  # pragma: no cover - import guard
        _MILP_DISABLED = True
        _MILP_DISABLED_REASON = f"pulp import failed: {err}"
        _LOGGER.warning(
            "pulp not installed or broken — MILP disabled for this session "
            "(greedy fallback active; re-checked on next restart). "
            "Install with: pip install pulp>=2.7.0  Error: %s", err,
        )
        return None

    try:
        return _solve(
            pulp, config, state,
            remaining=remaining,
            current_kwh=current_kwh,
            num_slots=num_slots,
            current_slot=current_slot,
            minutes_per_slot=minutes_per_slot,
            reserve_target=reserve_target,
            pv_confidence=pv_confidence,
        )
    except FileNotFoundError as err:
        # The CBC solver binary is missing / unrunnable on this platform
        # (common on uncommon CPU arches or new Python versions where the
        # prebuilt binary path doesn't exist).  This is unrecoverable for
        # the process lifetime — disable MILP so we don't rebuild the model
        # and raise the same traceback every 10s (was logged 1100+ times).
        _MILP_DISABLED = True
        _MILP_DISABLED_REASON = f"CBC solver binary unavailable: {err}"
        _LOGGER.warning(
            "MILP CBC solver binary not found — disabling MILP for this "
            "session, using greedy (re-checked on next restart). "
            "The greedy scheduler is fully functional. Error: %s", err,
        )
        return None
    except Exception:  # pragma: no cover - solver guard
        _LOGGER.warning("MILP solve failed — falling back to greedy", exc_info=True)
        return None


def _solve(
    pulp,
    config: Any,
    state: Any,
    *,
    remaining: list[tuple[int, float]],
    current_kwh: float,
    num_slots: int,
    current_slot: int,
    minutes_per_slot: float,
    reserve_target: float,
    pv_confidence: float,
) -> dict[int, str] | None:
    slot_hours = minutes_per_slot / 60.0
    cap = config.battery_capacity_kwh
    eff = config.efficiency
    soc_max = (config.battery_charge_max_pct / 100.0) * cap
    soc_min = (config.battery_discharge_min_pct / 100.0) * cap
    safe_kwh = config.safe_power_kw * slot_hours  # full-power slot energy

    if cap <= 0 or safe_kwh <= 0 or not remaining:
        return None

    # --- Build the optimisation horizon: today's remaining slots, then
    # all of tomorrow when those prices are known.  Each entry carries
    # the data needed to model one slot's energy balance. ---
    horizon: list[dict[str, Any]] = []

    def _add_day(prices: list[float | None], pv_hourly: dict[int, float],
                 confidence: float, day: str, start_idx: int) -> None:
        for idx in range(start_idx, len(prices)):
            price = prices[idx]
            if price is None:
                continue
            hour = int((idx * minutes_per_slot) / 60) % 24
            pv_kw = pv_hourly.get(hour, 0.0) * confidence  # kW ≈ kWh/h
            pv_slot = pv_kw * slot_hours
            if state.consumption_hourly_kwh and hour in state.consumption_hourly_kwh:
                load = state.consumption_hourly_kwh[hour] * slot_hours
            else:
                load = config.consumption_est_kwh / num_slots
            # Grid-side charge ceiling: inverter headroom after PV.
            grid_kw = min(config.safe_power_kw,
                          max(0.0, config.inverter_max_power_kw - pv_kw))
            horizon.append({
                "day": day,
                "slot": idx,
                "price": price,
                "net": pv_slot - load,          # natural SOC delta
                "charge_cap": grid_kw * slot_hours,  # grid-side kWh if charging
                "discharge_cap": safe_kwh,      # battery-side kWh if discharging
            })

    _add_day(state.slot_prices_today, state.pv_hourly_kwh or {},
             pv_confidence, "today", current_slot)
    if state.slot_prices_tomorrow:
        _add_day(state.slot_prices_tomorrow, state.pv_hourly_kwh_tomorrow or {},
                 1.0, "tomorrow", 0)

    if not horizon:
        return None

    K = len(horizon)
    grid_mode = config.grid_mode
    allow_charge = grid_mode in ("from_grid", "both")
    allow_discharge = grid_mode in ("to_grid", "both")

    # Effective per-kWh cycle wear (longevity priority enforces a floor).
    cycle_cost = config.battery_cycle_cost_eur_kwh
    if config.optimization_priority == "longevity":
        cycle_cost = max(cycle_cost, 0.05)

    # Arbitrage delta (both mode): block trades that can't clear the
    # user's required spread.  Approximated as per-slot price gates
    # against the horizon's cheapest buy / most expensive sell.
    delta = config.arbitrage_price_delta if grid_mode == "both" else 0.0
    prices_h = [h["price"] for h in horizon]
    cheapest = min(prices_h)
    most_exp = max(prices_h)

    prob = pulp.LpProblem("ems_schedule", pulp.LpMinimize)

    # Charge / discharge energy per slot are continuous within the slot's
    # power cap.  The slot is *marked* charge/discharge for execution if the
    # planned energy is meaningful; the inverter then runs at safe power and
    # naturally stops at the SOC floor.  Round-trip efficiency loss makes
    # charging and discharging the same slot never optimal, so no binary
    # "exclusive" variable is needed — this stays a fast, robust LP.
    c, d, spill, soc = {}, {}, {}, {}
    for k, h in enumerate(horizon):
        price = h["price"]
        charge_ub = h["charge_cap"] if allow_charge else 0.0
        if delta > 0 and price > most_exp - delta:
            charge_ub = 0.0  # too expensive to be a profitable buy
        discharge_ub = h["discharge_cap"] if allow_discharge else 0.0
        if config.block_export_on_negative_price and price < 0:
            discharge_ub = 0.0
        if delta > 0 and price < cheapest + delta:
            discharge_ub = 0.0  # spread too small to sell

        c[k] = pulp.LpVariable(f"c_{k}", lowBound=0, upBound=charge_ub)
        d[k] = pulp.LpVariable(f"d_{k}", lowBound=0, upBound=discharge_ub)
        spill[k] = pulp.LpVariable(f"spill_{k}", lowBound=0)
        soc[k] = pulp.LpVariable(f"soc_{k}", lowBound=soc_min, upBound=soc_max)

    # SOC dynamics.  soc[k] is the level at the *end* of slot k.
    prev = current_kwh
    for k in range(K):
        prob += soc[k] == prev + horizon[k]["net"] + eff * c[k] - d[k] - spill[k]
        prev = soc[k]

    # End-of-horizon reserve (overnight coverage).
    prob += soc[K - 1] >= min(reserve_target, soc_max)

    # Midnight boundary constraint: when the horizon spans today+tomorrow,
    # force the battery to reach reserve_target by end of today.  Without
    # this the solver defers all charging to cheaper tomorrow slots, the
    # battery drains overnight, and on the next day it defers again —
    # "tomorrow never comes."
    midnight_k = None
    for k in range(K):
        if horizon[k]["day"] == "tomorrow":
            midnight_k = k
            break
    if midnight_k is not None and midnight_k > 0:
        prob += soc[midnight_k - 1] >= min(reserve_target, soc_max)

    # Value energy left in the battery at the horizon end so the solver
    # doesn't pointlessly dump it at the last positive price.  Reference:
    # the average horizon price (× efficiency).  This makes the solver
    # charge any slot whose price is below avg·eff² (round-trip-profitable)
    # to store energy for later — i.e. it already tops off the battery from
    # the cheapest slots without ever charging at a loss.  This matches the
    # greedy self-consumption top-off gate (price <= eff² · mean), so the
    # two engines agree.  No per-priority boost: pushing the terminal value
    # higher (e.g. P90) would charge at uneconomic prices, which an EMS must
    # never do.  Self-consumption differentiates via the reserve floor
    # (1.25× in reserve_target), not by over-charging.
    avg_price = max(0.0, sum(prices_h) / len(prices_h))
    terminal_value = avg_price * eff

    # Objective: minimise net grid spend + wear − value of leftover energy.
    prob += (
        pulp.lpSum(horizon[k]["price"] * c[k] for k in range(K))           # buy cost
        - pulp.lpSum(horizon[k]["price"] * eff * d[k] for k in range(K))   # sell revenue
        + pulp.lpSum(cycle_cost * d[k] for k in range(K))                  # wear
        - terminal_value * soc[K - 1]                                      # leftover value
    )

    solver = pulp.PULP_CBC_CMD(msg=0, timeLimit=_SOLVE_TIME_LIMIT)
    prob.solve(solver)

    status = pulp.LpStatus[prob.status]
    if status != "Optimal":
        _LOGGER.warning("MILP non-optimal (%s) — falling back to greedy", status)
        return None

    # --- Extract slot decisions ------------------------------------------------
    # The LP uses continuous variables, so it may spread energy thinly across
    # many slots — each marginal kWh is technically profitable.  But the
    # real inverter runs at full power per slot, so a "spread thin" plan
    # can't execute as computed and produces an unrealistic interleaved
    # charge/discharge pattern.
    #
    # Fix: use the LP's *ranking* (which slots got the most energy allocated)
    # to decide which slots to activate, but cap the count using battery
    # physics — how many full-power slots are needed to fill/drain the
    # available SOC range.  This produces a concentrated plan the inverter
    # can actually execute, while preserving the LP's cost-optimal ordering.
    MIN_FRAC = 0.15  # ignore slots where LP allocated <15% of capacity

    charge_candidates: list[tuple[int, dict, float]] = []   # (k, h, kWh)
    discharge_candidates: list[tuple[int, dict, float]] = []

    for k, h in enumerate(horizon):
        cv = pulp.value(c[k]) or 0.0
        dv = pulp.value(d[k]) or 0.0
        slot_charge_cap = h["charge_cap"] or safe_kwh
        slot_discharge_cap = h["discharge_cap"] or safe_kwh
        if cv > slot_charge_cap * MIN_FRAC:
            charge_candidates.append((k, h, cv))
        if dv > slot_discharge_cap * MIN_FRAC:
            discharge_candidates.append((k, h, dv))

    # Sort by LP-allocated energy descending — highest-conviction slots first.
    charge_candidates.sort(key=lambda x: -x[2])
    discharge_candidates.sort(key=lambda x: -x[2])

    # Compute energy caps from battery physics.  The LP's continuous
    # totals include marginal fractional allocations the inverter can't
    # execute; capping by physical headroom prevents the slot-count
    # explosion that turns every marginally-profitable slot into an action.
    #
    # Charge cap: how much grid energy can the battery absorb?
    # (headroom from current SOC to max, divided by efficiency)
    charge_headroom_kwh = max(0.0, soc_max - current_kwh) / max(eff, 0.5)
    # Discharge cap: how much can we sell? (SOC above reserve)
    discharge_headroom_kwh = max(0.0, current_kwh - reserve_target)

    # In arbitrage, energy is cycled: charged first, then sold.  The
    # discharge headroom grows as charging fills the battery.  Use the
    # LP's final SOC to estimate the peak SOC the battery will reach.
    peak_soc = current_kwh
    for k, h in enumerate(horizon):
        cv = pulp.value(c[k]) or 0.0
        dv = pulp.value(d[k]) or 0.0
        peak_soc = min(soc_max, peak_soc + horizon[k]["net"] + eff * cv - dv)
    discharge_headroom_kwh = max(discharge_headroom_kwh,
                                  max(0.0, peak_soc - reserve_target))

    charge_target_kwh = charge_headroom_kwh
    discharge_target_kwh = discharge_headroom_kwh

    today_scheduled: dict[int, str] = {}
    tomorrow_scheduled: dict[int, str] = {}

    accum = 0.0
    for k, h, kw in charge_candidates:
        if accum >= charge_target_kwh:
            break
        slot_energy = h["charge_cap"] or safe_kwh
        accum += slot_energy * eff  # effective stored energy at full power
        if h["day"] == "today":
            today_scheduled[h["slot"]] = "charge"
        else:
            tomorrow_scheduled[h["slot"]] = "charge"

    accum = 0.0
    for k, h, kw in discharge_candidates:
        if accum >= discharge_target_kwh:
            break
        accum += safe_kwh
        if h["day"] == "today":
            today_scheduled[h["slot"]] = "discharge"
        else:
            tomorrow_scheduled[h["slot"]] = "discharge"

    _LOGGER.debug(
        "MILP solved: today %d slots (%d charge, %d discharge), "
        "tomorrow %d slots (%d charge, %d discharge), horizon=%d, "
        "energy targets: charge=%.1f kWh, discharge=%.1f kWh",
        len(today_scheduled),
        sum(1 for v in today_scheduled.values() if v == "charge"),
        sum(1 for v in today_scheduled.values() if v == "discharge"),
        len(tomorrow_scheduled),
        sum(1 for v in tomorrow_scheduled.values() if v == "charge"),
        sum(1 for v in tomorrow_scheduled.values() if v == "discharge"),
        K,
        charge_target_kwh,
        discharge_target_kwh,
    )
    return today_scheduled, tomorrow_scheduled
