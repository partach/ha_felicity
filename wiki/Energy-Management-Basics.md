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

In auto mode the integration re-plans every poll (10 s) whenever an input changed — SOC, prices, forecast, a new quarter hour — and executes the plan. Which engine plans depends on the grid mode and the **Scheduler Engine** setting:

| Grid Mode | Engine |
|---|---|
| From Grid | **Exact planner** (`optimal`, the default) — or greedy / MILP if you select them |
| To Grid, Both | Greedy selector (or MILP if you select it) |

All advanced features below only apply in auto mode.

## The Exact Planner (From Grid, default)

The planner solves one cost model exactly, in 15-minute steps, from now to the end of the last day with known prices (today, or today + tomorrow once tomorrow's prices are published, ~13:00):

- The battery covers the house and the sun fills it; sun beyond a full battery is lost.
- Below the **discharge floor** the house runs on the grid at that moment's price.
- In each step the battery may take any amount from nothing up to the **Power Level** (less what the PV already uses of the inverter's rating). Stored grid energy loses `1 − efficiency`. **Battery Cycle Cost** (at least 0.05 €/kWh for Battery Care) is charged per stored kWh.
- The battery can never supply the house faster than the inverter's rating.
- **After the horizon**, the battery must carry the house from midnight to sunrise. Each kWh short is priced at that night's grid price.
- **This week's price**: the integration remembers the average price of each of the last 7 days. Energy kept for the next day (as much as that day will use beyond its sun) is worth this week's average price after losses. So the **cheapest day of the week fills the battery** — grid energy is stored when it costs less than about 81 % (efficiency²) of the week's average — while an ordinary day buys only what the night needs. The weekly price needs 3 days of history; at startup it is rebuilt from Home Assistant's long-term statistics of your price entity when those exist.
- Starting a charge costs a tiny amount (0.05 kWh worth), so the plan never starts the inverter for a sliver and never flips on/off between two nearly equal prices.
- Equal cost → charge as early and as much as possible.

What follows from that, without any extra rule:
- It buys only when that is cheaper (after losses) than the grid energy it replaces, and only in the cheapest such moments.
- It never buys what tomorrow's sun delivers for free.
- A battery that lasts until a cheaper moment, or until the sun, waits.
- Today vs tomorrow is decided by tonight's real grid price.
- On an hourly tariff it can buy part of an hour; the coordinator stops as soon as the plan has what it needs.

The card's reason line tells you what the plan decided, e.g. *"Charging 3 today slot(s) at 0.081–0.094/kWh; without them the battery runs empty at 19:15 (saves ~1.20)"* or *"Battery runs empty at 20:00 — buying those 6.3 kWh live (up to 0.450/kWh) is cheaper than charging earlier after losses"*. When it fills the battery for the coming days it adds *"stores cheap energy for the coming days (this week's average 0.255/kWh)"*.

### Below the floor

When the battery is at (or a hair under) its floor, the inverter runs the house from the grid. The planner charges then only if that is cheaper than buying the same energy live. It does **not** force a charge just because SOC is under the floor. (The older engines do — "urgent recovery" — see below.)

## Operating Modes

### Off Mode

- No energy management, and **no automatic writes to the inverter** — not even at startup. The only exception: if you switch the EMS off while it is charging or discharging, it stops that action once.
- The integration still computes the overnight reserve for the EMS card display.

### From Grid Mode (Buy Only)

**Goal**: charge from the grid only when it saves money. Planned by the exact planner (above) by default.

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

The fixed percentage can only **raise** the reserve, never lower it: `max(fixed, dynamic)`. In the planner it raises the night need, and with tomorrow's prices known it also applies tonight. It is not a hard per-quarter floor — the planner reaches it from the cheapest moments, not at the evening peak.

## Consumption Profile

The planner uses a **per-hour consumption profile** built from the last 7 days of Home Assistant's statistics for your load meter (the inverter's load energy sensor, or a meter you pick as *consumption override entity* in the options flow — a real P1/energy meter is most accurate). It is rebuilt at every start.

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
| **Scheduler Engine** | optimal / greedy / milp | optimal | `optimal` = exact planner (From Grid). `greedy` = legacy. `milp` = solver (falls back to the planner when no solver is installed) |
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

A scheduled charge slot is executed as planned — there is no "wait for a cheaper slot" at run time; the plan already chose the cheapest moments. With the exact planner the charge stops as soon as the plan has what it needs (also part-way through a slot). The greedy/MILP engines hold a started charge for at least 15 minutes or +5% SOC, to avoid on/off toggling.

### Urgent Recovery (greedy / MILP only)

With the greedy or MILP engine, a SOC below the discharge floor forces an immediate charge. The exact planner does not do this (see *Below the floor* above): at the evening peak it bought at the dearest price and, with SOC hovering at the floor, toggled the charge on and off every few minutes.

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
