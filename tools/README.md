# EMS day-simulator / scenario harness

A standalone way to **see and confirm what the scheduling algorithm actually
does** — for both engines (greedy and MILP) — without Home Assistant.  It runs
`ems.calculate_schedule` on a library of realistic scenarios, prints a report,
checks per-scenario expectations, and (optionally) renders a chart per scenario.

## Run it (Windows / macOS / Linux)

```bat
cd ha_felicity
python -m pip install pulp matplotlib
python tools\ems_simulator.py
```

- `pulp` enables the **MILP** engine (without it, only greedy runs).
- `matplotlib` enables the **charts** (without it you still get the full text report).

Charts are written to `tools\sim_output\<scenario>.png` — one image per
scenario with two stacked panels (greedy on top, MILP below): price bars
coloured **green = charge / orange = sell / grey = idle**, with the projected
**SOC %** line and the **reserve** line overlaid.

### Useful flags

```bat
python tools\ems_simulator.py --name self_suff_daytime_ev   :: one scenario
python tools\ems_simulator.py --engine greedy               :: one engine
python tools\ems_simulator.py --no-plot                     :: text only
```

The process exits **0** when every scenario expectation passes, **1** if any
fail — so it can gate a release.

## What's covered

`tools/scenarios.py` holds the scenario library.  Each scenario exercises
specific knobs and asserts the intended outcome.  The current set covers:

| Scenario | Knobs exercised | What it pins |
|---|---|---|
| `save_money_cheap_night` | from_grid, cost, reserve_target_pct | charges only the cheapest slots |
| `self_suff_daytime_ev` | self_consumption, hourly profile, evening | **customer case**: no peak evening charge at 80% SOC |
| `self_suff_flat_low_soc` | self_consumption, low SOC midday | charges to cover the deficit |
| `trader_arbitrage` | both, cost, wide spread | buys cheap, sells above buy price |
| `to_grid_sell_surplus` | to_grid, high SOC, PV | never charges from grid |
| `negative_prices_charge_to_full` | charge_to_full_on_negative_price | greedy grabs all p<0; MILP charges p<0 |
| `low_soc_urgent_recovery` | from_grid, SOC < min | forces immediate charging |
| `tomorrow_pv_daily_only` | daily-only forecast, two-day | tomorrow's PV is synthesised (not zero) |
| `longevity_cycle_cost` | both, longevity | wear floor suppresses marginal trades |
| `arbitrage_delta_gate` | both, arbitrage_price_delta | no sells below the required spread |
| `manual_from_grid_no_charge_above_threshold` | **price_mode=manual**, from_grid | **customer case**: never charge above the threshold |
| `manual_both_sell_above_charge_below` | **price_mode=manual**, both | charge below / sell above the threshold, no overlap |

### Manual price mode

Scenarios with `price_mode: "manual"` in their `config` run a separate lane:
manual mode is a **threshold rule**, not the optimizer.  The harness mirrors
`coordinator._build_manual_schedule` (from_grid/both charge below the threshold,
to_grid/both sell above) and reuses the real `_compute_scheduled_soc_trajectory`
for the SOC line.  The threshold is derived from `price_threshold_level` (1-10)
exactly as the coordinator does.  These scenarios show one `[manual]` lane
(engine-agnostic) instead of greedy vs MILP.

## Add your own

Append a dict to `SCENARIOS` in `tools/scenarios.py`:

```python
{
  "name": "my_case",
  "desc": "what it checks",
  "config": dict(grid_mode="from_grid", optimization_priority="self_consumption",
                 battery_capacity_kwh=48.0, ...all the knobs...),
  "state":  dict(battery_soc_pct=80.0, slot_prices_today=[...],
                 pv_hourly_kwh={...}, consumption_hourly_kwh={...},
                 current_hour=22, current_minute=30),
  "expect": lambda r, s: (r["charge_slots"] == [], "should not charge"),
}
```

Reproduce a customer screenshot by transcribing its prices / SOC / time / knobs
into a scenario — then the expected behaviour becomes a permanent, runnable
regression test that anyone can read.
