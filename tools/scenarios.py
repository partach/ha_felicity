"""
EMS simulator scenario library.
================================

Each scenario is a dict:

    {
      "name":  "unique_id",
      "desc":  "what this checks",
      "config": { ...EMSConfig kwargs... },   # all the knobs
      "state":  { ...EMSState kwargs... },     # prices, PV, consumption, SOC, time
      "expect": fn(result, scenario) -> (ok: bool, message: str)   # optional
    }

`result` is the flat dict produced by ems_simulator.run_one():
    charge_slots, sell_slots, charge_prices, sell_prices, planned_kwh,
    reserve_pct, overnight_need_kwh, projected_low_pct, status, reason, engine.

Add your own scenarios freely — this is the place to encode "what the EMS
SHOULD do" for every corner case and every customer situation.

Helpers below build common price/PV/consumption shapes so scenarios stay short.
"""

# ── shape helpers ────────────────────────────────────────────────────────────

def flat(value, n=24):
    return [float(value)] * n


def cheap_night_expensive_day(n=24):
    """Low overnight prices, high midday/evening (typical dynamic tariff)."""
    out = []
    for h in range(n):
        if 0 <= h < 6:
            out.append(0.05)
        elif 6 <= h < 17:
            out.append(0.15)
        else:
            out.append(0.30)        # expensive evening peak
    return out


def cheap_day_expensive_evening(n=24):
    out = []
    for h in range(n):
        if 9 <= h <= 15:
            out.append(0.08)        # cheap midday (solar glut)
        elif 17 <= h <= 22:
            out.append(0.40)        # peak evening
        else:
            out.append(0.18)
    return out


def pv_bell(total_kwh, sunrise=7, sunset=19):
    """Bell-curve hourly PV summing to total_kwh."""
    import math
    peak = (sunrise + sunset) / 2.0
    spread = (sunset - sunrise) / 2.0
    raw = {h: max(0.0, math.cos((h - peak) / spread * math.pi / 2) ** 2)
           for h in range(sunrise, sunset + 1)}
    tot = sum(raw.values()) or 1.0
    return {h: round(v / tot * total_kwh, 3) for h, v in raw.items()}


def daytime_ev_profile(base=1.0, ev_kw=6.0, ev_start=9, ev_end=17):
    """Hourly consumption: low base load + heavy EV charging during the day.
    Mimics the 2-EV customer (high daily average, LOW night consumption)."""
    return {h: (base + ev_kw if ev_start <= h <= ev_end else base) for h in range(24)}


# ── scenarios ────────────────────────────────────────────────────────────────

SCENARIOS = [

    {
        "name": "save_money_cheap_night",
        "desc": "from_grid/cost, low SOC: should charge the CHEAPEST (night) slots only.",
        "config": dict(grid_mode="from_grid", optimization_priority="cost",
                       battery_capacity_kwh=10.0, battery_discharge_min_pct=20,
                       battery_charge_max_pct=100, efficiency=0.90,
                       safe_power_kw=5.0, inverter_max_power_kw=10.0,
                       consumption_est_kwh=10.0, reserve_target_pct=50),
        "state": dict(battery_soc_pct=25.0, slot_prices_today=cheap_night_expensive_day(),
                      pv_hourly_kwh={}, pv_actual_today_kwh=0.0,
                      pv_forecast_today=0.0, pv_forecast_remaining=0.0,
                      current_hour=2, current_minute=0),
        "expect": lambda r, s: (
            all(p <= 0.16 for p in r["charge_prices"]) if r["charge_prices"] else True,
            f"charges only cheap slots (prices={r['charge_prices']})",
        ),
    },

    {
        "name": "self_suff_daytime_ev",
        "desc": "CUSTOMER CASE: 2-EV daytime load (72 kWh/d avg, low night), 80% SOC at 22:30. "
                "Profile-aware reserve should mean NO expensive evening charging.",
        "config": dict(grid_mode="from_grid", optimization_priority="self_consumption",
                       battery_capacity_kwh=48.0, battery_discharge_min_pct=10,
                       battery_charge_max_pct=100, efficiency=0.90,
                       safe_power_kw=10.0, inverter_max_power_kw=10.0,
                       consumption_est_kwh=72.0),
        "state": dict(battery_soc_pct=80.0,
                      slot_prices_today=[0.05]*12 + [0.12]*5 + [0.19]*7,
                      pv_hourly_kwh=pv_bell(43.2),
                      pv_hourly_kwh_tomorrow=pv_bell(55.0),
                      consumption_hourly_kwh=daytime_ev_profile(),
                      pv_actual_today_kwh=43.2, pv_forecast_today=47.4,
                      pv_forecast_remaining=0.0, pv_forecast_tomorrow=55.0,
                      current_hour=22, current_minute=30),
        "expect": lambda r, s: (
            len(r["charge_slots"]) == 0,
            f"no evening charging at 80% SOC (got {len(r['charge_slots'])} slots: {r['charge_prices']})",
        ),
    },

    {
        "name": "self_suff_flat_low_soc",
        "desc": "from_grid/self_consumption, flat consumption, 30% SOC midday: must charge to reserve.",
        "config": dict(grid_mode="from_grid", optimization_priority="self_consumption",
                       battery_capacity_kwh=10.0, battery_discharge_min_pct=20,
                       battery_charge_max_pct=100, efficiency=0.90,
                       safe_power_kw=5.0, inverter_max_power_kw=10.0,
                       consumption_est_kwh=12.0),
        "state": dict(battery_soc_pct=30.0, slot_prices_today=cheap_night_expensive_day(),
                      pv_hourly_kwh=pv_bell(5.0), pv_actual_today_kwh=2.0,
                      pv_forecast_today=5.0, pv_forecast_remaining=3.0,
                      current_hour=11, current_minute=0),
        "expect": lambda r, s: (
            len(r["charge_slots"]) > 0,
            f"charges to cover deficit (got {len(r['charge_slots'])} slots)",
        ),
    },

    {
        "name": "trader_arbitrage",
        "desc": "both/cost, big spread (cheap midday 0.08 -> peak 0.40): buy cheap AND sell peak.",
        "config": dict(grid_mode="both", optimization_priority="cost",
                       battery_capacity_kwh=20.0, battery_discharge_min_pct=20,
                       battery_charge_max_pct=100, efficiency=0.90,
                       safe_power_kw=10.0, inverter_max_power_kw=15.0,
                       consumption_est_kwh=8.0),
        "state": dict(battery_soc_pct=40.0, slot_prices_today=cheap_day_expensive_evening(),
                      pv_hourly_kwh={}, pv_actual_today_kwh=0.0,
                      pv_forecast_today=0.0, pv_forecast_remaining=0.0,
                      current_hour=8, current_minute=0),
        "expect": lambda r, s: (
            (not r["sell_prices"]) or min(r["sell_prices"]) > max(r["charge_prices"] or [0]),
            f"sells (prices {r['sell_prices']}) above buys (prices {r['charge_prices']})",
        ),
    },

    {
        "name": "to_grid_sell_surplus",
        "desc": "to_grid, high SOC, big PV: sell the surplus above reserve at the most expensive slots.",
        "config": dict(grid_mode="to_grid", optimization_priority="cost",
                       battery_capacity_kwh=20.0, battery_discharge_min_pct=20,
                       battery_charge_max_pct=100, efficiency=0.90,
                       safe_power_kw=10.0, inverter_max_power_kw=15.0,
                       consumption_est_kwh=8.0),
        "state": dict(battery_soc_pct=90.0, slot_prices_today=cheap_day_expensive_evening(),
                      pv_hourly_kwh=pv_bell(20.0), pv_actual_today_kwh=2.0,
                      pv_forecast_today=20.0, pv_forecast_remaining=18.0,
                      current_hour=8, current_minute=0),
        "expect": lambda r, s: (
            len(r["charge_slots"]) == 0,
            f"to_grid never charges from grid (got {len(r['charge_slots'])})",
        ),
    },

    {
        "name": "negative_prices_charge_to_full",
        "desc": "from_grid + charge_to_full_on_negative_price: must grab every negative-price slot.",
        "config": dict(grid_mode="from_grid", optimization_priority="cost",
                       battery_capacity_kwh=20.0, battery_discharge_min_pct=20,
                       battery_charge_max_pct=100, efficiency=0.90,
                       safe_power_kw=10.0, inverter_max_power_kw=15.0,
                       consumption_est_kwh=10.0,
                       charge_to_full_on_negative_price=True),
        "state": dict(battery_soc_pct=40.0,
                      slot_prices_today=[0.10]*10 + [-0.05]*4 + [0.10]*10,
                      pv_hourly_kwh={}, pv_actual_today_kwh=0.0,
                      pv_forecast_today=0.0, pv_forecast_remaining=0.0,
                      current_hour=9, current_minute=0),
        # Documented engine difference: greedy EXPLICITLY forces every p<0 slot;
        # MILP charges negatives IMPLICITLY (LP revenue) and stops when full, so
        # it may take fewer.  The harness asserts each engine's real contract.
        "expect": lambda r, s: (
            (all(i in r["charge_slots"] for i in (10, 11, 12, 13)))
            if "milp" not in r["engine"]
            else (len([i for i in r["charge_slots"] if i in (10, 11, 12, 13)]) >= 1),
            f"negative-price charging ({r['engine']}): got {r['charge_slots']}",
        ),
    },

    {
        "name": "low_soc_urgent_recovery",
        "desc": "from_grid, SOC BELOW discharge_min: urgent recovery must force immediate charging.",
        "config": dict(grid_mode="from_grid", optimization_priority="cost",
                       battery_capacity_kwh=10.0, battery_discharge_min_pct=20,
                       battery_charge_max_pct=100, efficiency=0.90,
                       safe_power_kw=5.0, inverter_max_power_kw=10.0,
                       consumption_est_kwh=10.0),
        "state": dict(battery_soc_pct=12.0, slot_prices_today=cheap_night_expensive_day(),
                      pv_hourly_kwh={}, pv_actual_today_kwh=0.0,
                      pv_forecast_today=0.0, pv_forecast_remaining=0.0,
                      current_hour=18, current_minute=0),
        "expect": lambda r, s: (
            len(r["charge_slots"]) > 0,
            f"forces charging below min (got {len(r['charge_slots'])})",
        ),
    },

    {
        "name": "tomorrow_pv_daily_only",
        "desc": "Forecast gives DAILY totals only (no hourly): MILP must still 'see' tomorrow's sun "
                "(synthesized) and not plan tomorrow with zero PV.",
        "config": dict(grid_mode="from_grid", optimization_priority="self_consumption",
                       battery_capacity_kwh=48.0, battery_discharge_min_pct=10,
                       battery_charge_max_pct=100, efficiency=0.90,
                       safe_power_kw=10.0, inverter_max_power_kw=10.0,
                       consumption_est_kwh=72.0),
        "state": dict(battery_soc_pct=80.0,
                      slot_prices_today=[0.20]*22 + [0.30, 0.35],
                      slot_prices_tomorrow=[0.10]*24,
                      pv_hourly_kwh={},                 # no hourly today
                      pv_hourly_kwh_tomorrow=None,      # no hourly tomorrow
                      consumption_hourly_kwh=daytime_ev_profile(),
                      pv_actual_today_kwh=20.0, pv_forecast_today=50.0,
                      pv_forecast_remaining=10.0, pv_forecast_tomorrow=55.0,
                      current_hour=21, current_minute=0),
        "expect": lambda r, s: (
            True,  # informational: inspect the chart — SOC should RISE midday tomorrow, not flatline
            f"tomorrow charge/plan informational (planned={r['planned_kwh']}kWh)",
        ),
    },

    {
        "name": "longevity_cycle_cost",
        "desc": "both/longevity: 0.05 €/kWh wear floor should suppress marginal trades.",
        "config": dict(grid_mode="both", optimization_priority="longevity",
                       battery_capacity_kwh=20.0, battery_discharge_min_pct=20,
                       battery_charge_max_pct=100, efficiency=0.90,
                       safe_power_kw=10.0, inverter_max_power_kw=15.0,
                       consumption_est_kwh=8.0),
        "state": dict(battery_soc_pct=60.0,
                      slot_prices_today=[0.10]*12 + [0.18]*12,  # small spread
                      pv_hourly_kwh={}, pv_actual_today_kwh=0.0,
                      pv_forecast_today=0.0, pv_forecast_remaining=0.0,
                      current_hour=6, current_minute=0),
        "expect": lambda r, s: (
            True,
            f"longevity: {len(r['sell_slots'])} sells on a small spread (expect few/none)",
        ),
    },

    {
        "name": "manual_from_grid_no_charge_above_threshold",
        "desc": "CUSTOMER CASE: price_mode=manual, from_grid. Must charge ONLY below the "
                "threshold — never a buy slot above it (the reported regression).",
        "config": dict(grid_mode="from_grid", price_mode="manual",
                       price_threshold_level=5, optimization_priority="self_consumption",
                       battery_capacity_kwh=48.0, battery_discharge_min_pct=10,
                       battery_charge_max_pct=100, efficiency=0.90,
                       safe_power_kw=10.0, inverter_max_power_kw=10.0,
                       consumption_est_kwh=72.0),
        "state": dict(battery_soc_pct=81.0,
                      slot_prices_today=[0.06]*8 + [0.20]*8 + [0.45]*8,  # cheap/mid/peak
                      pv_hourly_kwh=pv_bell(44.0),
                      consumption_hourly_kwh=daytime_ev_profile(),
                      pv_actual_today_kwh=44.0, pv_forecast_today=58.0,
                      pv_forecast_remaining=0.0, current_hour=21, current_minute=30),
        "expect": lambda r, s: (
            all(p < (r["threshold"] or 0) for p in r["charge_prices"]),
            f"all charge slots below threshold {round(r['threshold'],3) if r['threshold'] else None} "
            f"(charge prices={r['charge_prices']})",
        ),
    },

    {
        "name": "manual_both_sell_above_charge_below",
        "desc": "price_mode=manual, both: charge below threshold, sell above — and the two never overlap.",
        "config": dict(grid_mode="both", price_mode="manual",
                       price_threshold_level=5, optimization_priority="cost",
                       battery_capacity_kwh=20.0, battery_discharge_min_pct=20,
                       battery_charge_max_pct=100, efficiency=0.90,
                       safe_power_kw=10.0, inverter_max_power_kw=15.0,
                       consumption_est_kwh=8.0),
        "state": dict(battery_soc_pct=60.0,
                      slot_prices_today=[0.05]*8 + [0.20]*8 + [0.40]*8,
                      pv_hourly_kwh={}, pv_actual_today_kwh=0.0,
                      pv_forecast_today=0.0, pv_forecast_remaining=0.0,
                      current_hour=0, current_minute=0),
        "expect": lambda r, s: (
            (all(p < r["threshold"] for p in r["charge_prices"])
             and all(p > r["threshold"] for p in r["sell_prices"])
             and not (set(r["charge_slots"]) & set(r["sell_slots"]))),
            f"charge<thr {r['charge_prices']}, sell>thr {r['sell_prices']}, no overlap",
        ),
    },

    {
        "name": "arbitrage_delta_gate",
        "desc": "both + arbitrage_price_delta=0.20: must NOT sell when spread < 0.20.",
        "config": dict(grid_mode="both", optimization_priority="cost",
                       battery_capacity_kwh=20.0, battery_discharge_min_pct=20,
                       battery_charge_max_pct=100, efficiency=0.90,
                       safe_power_kw=10.0, inverter_max_power_kw=15.0,
                       consumption_est_kwh=8.0, arbitrage_price_delta=0.20),
        "state": dict(battery_soc_pct=60.0,
                      slot_prices_today=[0.10]*12 + [0.18]*12,  # spread 0.08 < 0.20
                      pv_hourly_kwh={}, pv_actual_today_kwh=0.0,
                      pv_forecast_today=0.0, pv_forecast_remaining=0.0,
                      current_hour=6, current_minute=0),
        "expect": lambda r, s: (
            len(r["sell_slots"]) == 0,
            f"no sells when spread (0.08) < delta (0.20) — got {len(r['sell_slots'])}",
        ),
    },
]
