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

The MILP only produces *today's* ``scheduled_slots`` (informed by the
2-day lookahead).  Tomorrow's display schedule, the SOC trajectory, and
flexible-load overlays are still computed by the existing ``ems.py``
machinery, so the integration surface stays tiny.
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Solver wall-clock cap (seconds).  A 192-slot binary model solves in
# well under this; the limit only guards against pathological inputs.
_SOLVE_TIME_LIMIT = 10


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
) -> dict[int, str] | None:
    """Solve the EMS schedule as a MILP.

    Returns ``{today_slot_index: "charge"|"discharge"}`` for today's
    remaining slots, or ``None`` if the solver is unavailable or fails
    (caller should fall back to the greedy scheduler).
    """
    try:
        import pulp  # noqa: PLC0415 — lazy import so ems.py works without pulp
    except Exception:  # pragma: no cover - import guard
        _LOGGER.debug("pulp not available — MILP scheduler disabled")
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
    except Exception:  # pragma: no cover - solver guard
        _LOGGER.exception("MILP solve failed — falling back to greedy")
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

    # Value energy left in the battery at the horizon end so the solver
    # doesn't pointlessly dump it at the last positive price.  Conservative
    # reference: the average horizon price (× efficiency for sell value).
    terminal_value = max(0.0, sum(prices_h) / len(prices_h)) * eff

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

    # Extract today's slot decisions.  Threshold guards against solver
    # numerical dust on the binary variables.
    scheduled: dict[int, str] = {}
    for k, h in enumerate(horizon):
        if h["day"] != "today":
            continue
        cv = pulp.value(c[k]) or 0.0
        dv = pulp.value(d[k]) or 0.0
        if cv > safe_kwh * 0.05:
            scheduled[h["slot"]] = "charge"
        elif dv > safe_kwh * 0.05:
            scheduled[h["slot"]] = "discharge"

    _LOGGER.debug(
        "MILP solved: %d today slots (%d charge, %d discharge), horizon=%d",
        len(scheduled),
        sum(1 for v in scheduled.values() if v == "charge"),
        sum(1 for v in scheduled.values() if v == "discharge"),
        K,
    )
    return scheduled
