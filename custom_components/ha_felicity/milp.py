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
    c, d, spill, soc, imp = {}, {}, {}, {}, {}
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
        # Emergency feasibility slack — physically, grid passthrough keeps the
        # battery from dropping below soc_min (the house draws from grid when
        # the battery is empty).  Without it the SOC dynamics can be
        # INFEASIBLE: when consumption drains the battery faster than charging
        # can offset (e.g. to_grid mode where charging is disallowed, or a very
        # high-consumption slot), `soc[k] == prev + net + ... ` cannot satisfy
        # `soc[k] >= soc_min`, and the whole MILP collapses to greedy.  imp[k]
        # absorbs exactly that deficit.  It carries a high penalty so it's 0 in
        # every normal case (no behavioural change / parity preserved) and only
        # activates to keep the LP feasible.
        imp[k] = pulp.LpVariable(f"imp_{k}", lowBound=0)

    # SOC dynamics.  soc[k] is the level at the *end* of slot k.
    prev = current_kwh
    for k in range(K):
        prob += soc[k] == prev + horizon[k]["net"] + eff * c[k] - d[k] - spill[k] + imp[k]
        prev = soc[k]

    # Reserve constraints are SOFT (slack + penalty), never hard.  A hard
    # `soc >= reserve_target` can be INFEASIBLE — e.g. late evening with the
    # reserve near capacity and only a few low-power slots before the
    # boundary, the battery physically cannot charge fast enough against
    # consumption to reach it (real customer log: "MILP non-optimal
    # (Infeasible) — falling back to greedy", repeatedly).  Infeasibility
    # made the whole MILP plan collapse to greedy.  With a slack the solver
    # instead gets AS CLOSE to the reserve as physically possible and the LP
    # is always feasible.  The penalty is set well above any per-kWh charge
    # cost so the solver still charges to reach the reserve whenever it CAN —
    # the slack only absorbs the genuinely-unreachable remainder.
    reserve_clamped = min(reserve_target, soc_max)
    reserve_penalty = max(1.0, most_exp) * 5.0
    reserve_shortfalls = []

    # End-of-horizon reserve (overnight coverage).
    end_short = pulp.LpVariable("reserve_short_end", lowBound=0)
    prob += soc[K - 1] + end_short >= reserve_clamped
    reserve_shortfalls.append(end_short)

    # Midnight boundary: when the horizon spans today+tomorrow, push the
    # battery toward reserve_target by end of today.  Without this the solver
    # defers all charging to cheaper tomorrow slots, the battery drains
    # overnight, and on the next day it defers again — "tomorrow never comes."
    midnight_k = None
    for k in range(K):
        if horizon[k]["day"] == "tomorrow":
            midnight_k = k
            break
    if midnight_k is not None and midnight_k > 0:
        mid_short = pulp.LpVariable("reserve_short_mid", lowBound=0)
        prob += soc[midnight_k - 1] + mid_short >= reserve_clamped
        reserve_shortfalls.append(mid_short)

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

    # Reward leftover SOC only UP TO the reserve target — the energy genuinely
    # needed for overnight survival (and, in self_consumption, the boosted
    # reserve).  Rewarding ALL leftover SOC (up to soc_max) made the solver
    # buy any slot below avg·eff² to push the battery toward FULL, even in pure
    # cost mode where that is over-buying: it spends money now to store energy
    # the horizon has no modelled use for.  Real symptom: on a duck-curve cost
    # day MILP charged an extra night slot and a 3rd midday slot to end at 81%
    # where greedy ended at 52% — and cost MORE (0.895 vs 0.600).  Capping the
    # reward at the reserve makes the solver fill to the reserve (driven by
    # reward + soft penalty) but never beyond it for the sake of leftover
    # value, so it stops over-buying.  Arbitrage (both mode) is unaffected:
    # energy above the reserve is sold for the explicit sell REVENUE term, not
    # the terminal reward, so the solver still cycles the battery when a peak
    # pays for it.  Self-consumption still fills high because its reserve is
    # the 1.25× boosted value.
    reward_soc = pulp.LpVariable("reward_soc", lowBound=0, upBound=reserve_clamped)
    prob += reward_soc <= soc[K - 1]

    # Objective: minimise net grid spend + wear − value of leftover energy
    # (capped at the reserve) + reserve-shortfall penalty (drives charging
    # toward the reserve when physically feasible; absorbs the unreachable
    # remainder instead of making the model infeasible).
    prob += (
        pulp.lpSum(horizon[k]["price"] * c[k] for k in range(K))           # buy cost
        - pulp.lpSum(horizon[k]["price"] * eff * d[k] for k in range(K))   # sell revenue
        + pulp.lpSum(cycle_cost * d[k] for k in range(K))                  # wear
        - terminal_value * reward_soc                                      # leftover value (≤ reserve)
        + pulp.lpSum(reserve_penalty * s for s in reserve_shortfalls)      # soft reserve
        + pulp.lpSum(reserve_penalty * imp[k] for k in range(K))           # feasibility slack
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

    # Rank for the capped, discrete, full-power execution.  The LP spreads
    # energy across more slots than the inverter can run at full power, so we
    # take a *subset* up to the physical headroom (below).  Among the slots the
    # LP chose to use, execute the most COST-EFFECTIVE subset: most-expensive
    # slots to SELL (charge is ranked per-day in the extraction below, to
    # respect the today/tomorrow split).  Ranking discharge by LP-allocated
    # energy instead (the old behaviour) could keep an early CHEAP-priced sell
    # over the evening PEAK when both got similar energy — the LP cycles PV
    # (sell early to make room, then sell again at the peak), so both tie on
    # energy and the cheaper one used to win.  Price is the primary key; LP
    # energy is the tiebreaker (conviction).
    discharge_candidates.sort(key=lambda x: (-x[1]["price"], -x[2]))

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

    # --- Charge execution: per day, cheapest-first ---
    # Collapse the LP's continuous charge plan to discrete full-power slots.
    # WITHIN each day, execute the CHEAPEST slots first; stop once the
    # delivered energy reaches the day's target.  TODAY is capped at the
    # battery's physical charge headroom (so an expensive reserve-top-off slot
    # the cheap slots already cover gets dropped — #2: a 0.30 evening slot used
    # to be added after the two 0.15 slots had filled the headroom).  TOMORROW
    # is capped at the energy the LP allocated to it, which preserves the
    # midnight-reserve TODAY-FIRST split (self-sufficiency: charge today rather
    # than defer everything to a cheaper tomorrow — a single global cheapest-
    # first cap would let cheap tomorrow slots starve today).  Cheapest-first +
    # the headroom cap means the dearest slot is reached only when the cheaper
    # ones (plus PV) genuinely can't fill the battery.
    for day, sched in (("today", today_scheduled),
                       ("tomorrow", tomorrow_scheduled)):
        cands = sorted(
            ((h, cv) for (k, h, cv) in charge_candidates if h["day"] == day),
            key=lambda x: (x[0]["price"], -x[1]),
        )
        # Target = the effective energy the LP actually allocated to this day.
        # Because the terminal reward is capped at the reserve, that allocation
        # is the cost-correct amount (fill to the reserve, not toward 100% just
        # to bank cheap leftover).  Cap TODAY additionally by the battery's
        # physical charge headroom so a non-slot-multiple LP allocation can't
        # pull in one slot too many.
        target = eff * sum(cv for _, cv in cands)
        if day == "today":
            target = min(target, charge_target_kwh)
        accum = 0.0
        for h, cv in cands:
            if accum >= target - 0.01:
                break
            accum += (h["charge_cap"] or safe_kwh) * eff
            sched[h["slot"]] = "charge"

    # charge_to_full_on_negative_price: the user has explicitly opted to grab
    # EVERY negative-price slot for the revenue (you're paid to charge),
    # accepting that some PV may be curtailed.  The LP only charges negatives
    # up to the SOC-max bound (it can't model charging a battery past full), so
    # on its own it stops once full and may take fewer than all of them.  Mirror
    # greedy's explicit behaviour: force every remaining p<0 slot to charge.
    if getattr(config, "charge_to_full_on_negative_price", False):
        for k, h in enumerate(horizon):
            if h["price"] < 0:
                sched = (today_scheduled if h["day"] == "today"
                         else tomorrow_scheduled)
                if sched.get(h["slot"]) != "discharge":
                    sched[h["slot"]] = "charge"

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
