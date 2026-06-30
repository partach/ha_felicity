# EMS day-simulator / scenario harness

A standalone way to **see and confirm what the scheduling algorithm actually
does** — for both engines (greedy and MILP) — without Home Assistant.  It runs
`ems.calculate_schedule` on a library of realistic scenarios, prints a report,
checks per-scenario expectations, and (optionally) renders a chart per scenario.

## Do I need Home Assistant or the integration installed?

**No.** You do **not** need Home Assistant, a running inverter, or the
integration installed anywhere.  The two algorithm files — `ems.py` and
`milp.py` — import only the Python standard library (`logging`, `math`,
`dataclasses`, `typing`) plus `pulp` (for MILP).  The simulator loads those two
files *directly* and feeds them plain Python data.  Everything runs locally on
your Windows machine in plain Python.  (The HA-specific code — `coordinator.py`,
Modbus, sensors — is never imported.)

## Complete setup on a fresh Windows machine (no HA)

**1. Install Python 3.11+** from <https://www.python.org/downloads/windows/>.
   On the first installer screen tick **"Add python.exe to PATH"**.
   Verify in a new Command Prompt / PowerShell:
   ```bat
   python --version
   ```

**2. Get the code.** You only need the repository files (not an HA install).
   Either with Git:
   ```bat
   git clone https://github.com/partach/ha_felicity.git
   cd ha_felicity
   git checkout claude/expand-felicity-card-B7dl4
   ```
   …or download the branch as a ZIP from GitHub ("Code → Download ZIP"),
   unzip it, and `cd` into the unzipped folder (the one that contains the
   `tools\` and `custom_components\` folders side by side).

**3. Install the two Python packages the simulator uses:**
   ```bat
   python -m pip install pulp matplotlib
   ```
   - `pulp` = the MILP solver (bundles its own CBC binary — no extra install).
   - `matplotlib` = the charts.  Skip it and you still get the full text report.

**4. Run it** from the repository root (the folder containing `tools\`):
   ```bat
   python tools\ems_simulator.py
   ```
   You'll see a per-scenario report and `RESULT: ALL EXPECTATIONS PASSED`, and
   (with matplotlib) PNG charts under `tools\sim_output\`.

### Also run the unit tests (same — no HA needed)

The 240 unit tests load `ems.py`/`milp.py` the same direct way:
```bat
python -m pip install pytest
python -m pytest tests\test_ems.py -q
```

## Run it (quick reference)

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
| `cons_low_flat` | low flat consumption | small overnight need → little charging |
| `cons_heavy_flat` | heavy flat consumption | large overnight need → more charging |
| `cons_morning_evening_peak` | household profile | morning + evening peaks visible in the load line |
| `pv_sunny_fills_battery_no_grid` | from_grid, big PV | solar fills the battery → no grid charge |
| `pv_cloudy_low_confidence_charges_more` | PV forecast vs actual | low PV confidence → charges more |
| `pv_daily_total_only_today` | daily-only forecast | hourly PV is **synthesized** (SOC still rises) |
| `pv_no_sun_winter_relies_on_grid` | zero PV | charges the cheapest grid slots |
| `pv_surplus_sold_to_grid` | to_grid, big PV | sells the solar surplus at peak |
| `manual_from_grid_no_charge_above_threshold` | **price_mode=manual**, from_grid | **customer case**: never charge above the threshold |
| `manual_both_sell_above_charge_below` | **price_mode=manual**, both | charge below / sell above the threshold, no overlap |

Every chart overlays, on a shared kWh/h right axis:
- the **PV production** as a translucent yellow "solar hump" (synthesized from
  the daily total when the forecast has no hourly breakdown), and
- the **consumption** as a red line (hourly profile when supplied, else flat).

Where the yellow PV rises above the red load line there's a **surplus** (the
SOC climbs); where load exceeds PV the battery **drains**.  A red dotted **"now"
line** marks the current time — everything to its left is the past (the SOC
there is a flat placeholder, since the simulator has no recorder history).

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
