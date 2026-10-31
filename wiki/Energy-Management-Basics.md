# Energy Management Basics

## Overview

The HA_Felicity integration implements an energy management system that plans battery charging and discharging from real-time electricity prices, solar forecasts and your own consumption pattern. It supports manual threshold-based control and fully automatic planning.

## Price Modes

### Manual Mode

The system obtains current electricity prices (via Nordpool or Tibber) and maps a user-selected **Price Threshold Level** (1-10) to a price point:

- **1-4**: Below average prices (good for charging)
- **5**: Average price
- **6-10**: Above average prices (good for selling)

When the current price is below the threshold, the system charges. When above, it sells. Simple and predictable. Manual slot overrides set on the EMS card are executed in manual mode too.

### Auto Mode (Scheduler)

In auto mode the integration re-plans every poll (10 s) whenever an input changed — SOC, prices, forecast, a new slot — and executes the plan. Two engines, chosen with **Scheduler Engine**:

| Engine | What it is |
|---|---|
| **Greedy** (default) | Works out the deficit (what the battery needs to stay above Min SOC through the night), buys it in the **cheapest known slots** (today, plus tomorrow once its prices are out, ~13:00), and validates the plan against the battery's limits. No extra software needed. |
| **MILP** | One optimisation over today + tomorrow with a solver (HiGHS or CBC). Better at cross-day trading (Trader). Falls back to greedy when no solver runs — see *MILP will not load* in Diagnostics and Troubleshooting. |

All advanced features below only apply in auto mode.

### At Min SOC: the house runs on the grid

When the battery reaches its minimum, the inverter feeds the house from the grid — that is the inverter's own behaviour at its minimum SOC (set in the Felicity app). The EMS does **not** force a charge at that moment's price: the missing energy becomes part of the deficit and is bought back in the cheapest known slots. (Until 31 Oct 2026 both engines forced an immediate charge below Min SOC — "urgent recovery". On 22 Oct 2026 it bought the evening peak and switched the charge on and off every few minutes for hours.)

Note: the EMS **Min SOC** setting is a planning value. The integration does not write it to the inverter as a stop, so while the battery is not charging the inverter's self-use keeps feeding the house down to **its own** minimum. If you want a reserve the battery really keeps, set the inverter's own minimum in its app.

### This week's price

The integration remembers the average price of each of the last 7 days (rebuilt at startup from Home Assistant's long-term statistics of your price entity, when those exist; needs 3 days). Beyond what the plan needs, energy is bought **for the coming days** only from slots cheaper than about 81 % (efficiency²) of this week's average, and only as much as the battery, the sun and tomorrow's use leave room for. So the **cheapest day of the week fills the battery**, an ordinary day buys only what it needs. Both engines, From Grid only. The reason line then adds *"store cheap energy for the coming days (this week's average 0.233/kWh)"*.

## Operating Modes

### Off Mode

- No energy management, and **no automatic writes to the inverter** — not even at startup. The only exception: if you switch the EMS off while it is charging or discharging, it stops that action once.
- The integration still computes the overnight reserve for the EMS card display.

### From Grid Mode (Buy Only)

**Goal**: keep the battery above Min SOC and through the night, charging from the grid at the cheapest possible moments.

1. **Calculate the deficit**: how much energy is needed to reach the reserve target (Min SOC + what the night needs), accounting for expected PV.
2. **Select cheapest charge slots**: from today's remaining slots (and tomorrow's once its prices are known), the cheapest ones that cover the deficit.
3. **Validate**: simulate the battery forward — drop charge slots that would overflow.

### To Grid Mode (Sell Only)

**Goal**: sell battery energy at the highest prices while keeping enough for overnight self-consumption.

1. **Calculate sellable energy**: how much is above the reserve target (with a 15% safety margin).
2. **Select most expensive slots**: pick the highest-price slots.
3. **Validate**: drop discharge slots that would drain below the reserve target.

### Both Mode (Buy + Sell)

**Goal**: buy cheap, sell expensive, while keeping the self-consumption reserve.

Combines from_grid and to_grid with a **profitability filter**:

```
min_sell_price = (max_buy_price + battery_cycle_cost) / (efficiency * efficiency)
```

Only sells above the round-trip break-even point.

**Arbitrage Price Delta**: when set (> 0) it *replaces* the automatic check — every sale must beat the buy price by at least this spread, otherwise nothing is sold.

## Reserve Target

The reserve target is what the battery should hold for the night.

### Dynamic (reserve_target_pct = 0, the default)

The night need is your consumption from midnight to sunrise, summed hour by hour from your **consumption profile**. Sunrise is the first hour of tomorrow's PV forecast, else Home Assistant's sun times, else 07:00. **Self-Sufficiency** multiplies it by 1.25.

### Fixed (reserve_target_pct > 0)

The fixed percentage can only **raise** the reserve, never lower it: `max(fixed, dynamic)`.

## Consumption Profile

The EMS uses a **per-hour consumption profile** built from the last 7 days of Home Assistant's statistics for your load meter (the inverter's load energy sensor, or a meter you pick as *consumption override entity* in the options flow — a real P1/energy meter is most accurate). It is rebuilt at every start.

The EMS card shows an amber chip when the profile is **flat** (no history yet) or **partial**. Impossible hours (more than the house can physically draw) are left out.

## PV Confidence

The algorithm compares actual solar production to the forecast:

- **Cumulative**: `actual_produced / expected_by_now`
- **3-hour window**: recovers quickly when the weather improves mid-day
- **EMA smoothing** (alpha=0.3): dampens single-hour swings
- Clamped to [0.1, 1.0]

Supported forecast formats: Forecast.Solar (`wh_hours`), Open-Meteo Solar Forecast (`wh_period`) and Solcast (`detailedHourly` / `detailedForecast`). With only a daily total, the hourly curve is synthesised between today's sunrise and sunset.

## Two-Day Optimization

When tomorrow's prices are available (typically from ~13:00), the plan covers today and tomorrow together. Charging moves to tomorrow only when tomorrow is cheaper *including* what the house pays on the grid tonight in the meantime.

## Manual Slot Overrides

Click slots on the EMS card's chart to force them: in From Grid every picked slot charges, in To Grid every picked slot sells, in Both the price threshold decides. Overrides:
- always win over the EMS's own slots and are never dropped (the inverter itself stops a charge at max SOC and a sale at the floor);
- survive a Home Assistant restart, and tomorrow's overrides move to "today" at midnight;
- also run in manual price mode.

## Configuration Settings

### Core Settings

| Setting | Range | Default | Description |
|---|---|---|---|
| **EMS Strategy** | save_money / self_sufficiency / battery_care / trader / custom | save_money | Sets Grid Mode + Optimization Priority |
| **Grid Mode** | off / from_grid / to_grid / both | off | Main EMS switch |
| **Price Mode** | manual / auto | manual | Threshold-based or scheduler |
| **Scheduler Engine** | greedy / milp | greedy | `greedy` = cheapest-slot selector (default). `milp` = solver (falls back to greedy when no solver runs) |
| **Price Threshold Level** | 1-10 | 5 | Manual mode price point |
| **Power Level** | 1 - max kW (model) | 5 | Charge/discharge power; the plan always assumes this level |
| **Battery Charge Max Level** | 30-100% | 100 | SOC ceiling for grid charging |
| **Battery Discharge Min Level** | 10-70% | 20 | SOC floor |

### Battery & Efficiency

| Setting | Range | Default | Description |
|---|---|---|---|
| **Battery Capacity** | 1-200 kWh | 10 | Usable capacity (auto-adjusted for SOH) |
| **Efficiency Factor** | 0.70-1.00 | 0.90 | Single-direction efficiency (round-trip = squared) |
| **Daily Consumption Estimate** | 0-120 kWh | 10 | Fallback when no history profile exists |

### Reserve & Optimization

| Setting | Range | Default | Description |
|---|---|---|---|
| **Reserve Target** | 0-100% | 0 | 0 = dynamic night reserve, >0 = can only raise it |
| **Optimization Priority** | cost / longevity / self_consumption | cost | longevity = 0.05 €/kWh cycle-cost floor, self_consumption = 1.25× night need |
| **Arbitrage Price Delta** | 0-0.50 €/kWh | 0 | Minimum buy→sell spread to trade (Both mode) |
| **Battery Cycle Cost** | 0-0.50 €/kWh | 0 | Wear cost per stored kWh |

### Negative-Price Strategies

| Setting | Options | Default | Description |
|---|---|---|---|
| **Block Export on Negative Price** | on / off | on | Never sell at negative prices |
| **Charge to Full on Negative Price** | off / on | off | Charge at every negative-price slot (you get paid) |
| **Discharge to Make Room for Negative Price** | off / on | off | Discharge at a dear moment before a negative-price PV window, so the sun can fill the battery |

### Inverter Rule 1 Control

| Setting | Options | Default | Description |
|---|---|---|---|
| **Rule 1 Time Window** | manual / auto | manual | auto = 00:00-23:59. If manual, make sure the window covers the EMS hours. |
| **Rule 1 Weekday** | manual / auto | manual | auto = all 7 days enabled. |

If scheduled actions fall outside the Rule 1 window, a warning banner appears on the EMS card.

### Voltage Settings

| Setting | Range | Default | Description |
|---|---|---|---|
| **Voltage Level** | 48-60V (LV) / 300-448V (HV) | 58 | Charge voltage setpoint |
| **Discharge Min Voltage** | 48-55V (LV) / 300-448V (HV) | 50 | Discharge voltage floor |

These are inverter protection parameters, written during state transitions. The algorithm itself works in SOC% and kWh.

## Runtime Behavior

### State Transitions

Every poll the coordinator checks the current slot and writes only when the desired state changes:

| State | Rule 1 | SOC Register | Voltage | Power |
|---|---|---|---|---|
| Charging | charge | charge_max (e.g. 100%) | voltage_level | current Rule 1 power |
| Discharging (manual) | discharge | discharge_min (e.g. 20%) | discharge_min_voltage | current Rule 1 power |
| Discharging (auto) | discharge | reserve_target (e.g. 67%) | discharge_min_voltage | current Rule 1 power |
| Idle | off | *(not written)* | *(not written)* | *(not written)* |

The **integration switches the inverter into Economic mode itself** when it charges or discharges (Operating Mode on TREX-5/6/10, ECO time-of-use on TREX-25/50/IVGM). If that write fails, the rule is not enabled. If the inverter drops out of Economic mode while an action runs (app, power blip), the integration re-applies the whole state within one poll ("self-heal").

A charge that is running at midnight keeps running: on TREX-5/6/10 the rule's date is re-stamped for the new day.

### Charge Execution

A scheduled charge slot is executed as planned — there is no "wait for a cheaper slot" at run time; the plan already chose the cheapest moments. A started charge is held for at least 15 minutes or +5% SOC, to avoid on/off toggling.

### Anti-Conflict Guard

Prevents selling while the house imports from the grid (e.g. an EV starts during a sale):

- Moderate import (200-2000 W): must persist 2 polls before discharge is suppressed
- Large import (> 2000 W): suppresses immediately
- A suppression holds discharge off for **5 minutes**, then allows one retry

## Economic Rule Set Integration

The integration uses **Economic Rule Set 1** exclusively. Leave Rule 1 to the integration; if you configure it yourself, keep `rule1_time_window` and `rule1_weekday` on `manual` so the integration does not overwrite your window. While the EMS is active, a manual change of the Operating Mode is reverted by the self-heal — switch the EMS off first.

## Implementation Details

- [TREX10 Energy Management](TREX10-Energy-Management) — Register details for TREX-5/6/10K
- [TREX25/50 Energy Management](TREX25-50-Energy-Management) — Register details for TREX-25/50K and IVGM
- [EMS_LOGIC.md](https://github.com/partach/ha_felicity/blob/main/docs/EMS_LOGIC.md) — Complete algorithm reference with code-level detail
