# Energy Management Basics

## Overview

The HA_Felicity integration implements an energy management algorithm that optimizes battery charging and discharging based on real-time electricity prices, solar forecasts, and consumption patterns. It supports manual threshold-based control and fully automatic optimization.

## Price Modes

### Manual Mode

The system obtains current electricity prices from the internet (via Nordpool or Tibber) and maps a user-selected **Price Threshold Level** (1-10) to a price point:

- **1-4**: Below average prices (good for charging)
- **5**: Average price
- **6-10**: Above average prices (good for selling)

When the current price is below the threshold, the system charges. When above, it sells. Simple and predictable.

### Auto Mode (Optimizer)

In auto mode, the integration runs a full schedule optimizer every 10 seconds. Instead of a simple threshold comparison, it:

1. Calculates how much energy the battery needs (the **deficit**)
2. Picks the **cheapest available slots** to charge
3. Picks the **most expensive slots** to sell (in to_grid/both mode)
4. Validates the schedule against battery capacity limits
5. Uses **two-day look-ahead** when tomorrow's prices are available

All advanced features described below (reserve target, PV confidence, arbitrage, negative-price strategies) only apply in auto mode.

## Operating Modes

### Off Mode

- No active energy management
- The inverter operates normally without grid scheduling
- The integration still computes the overnight reserve target (for the EMS card display) but takes no actions

### From Grid Mode (Buy Only)

**Goal**: Charge the battery from the grid at the cheapest possible prices, but only enough to survive overnight.

1. **Calculate the deficit**: How much energy is needed to reach the reserve target, accounting for expected PV production.
2. **Select cheapest charge slots**: From today's remaining slots (and tomorrow's if prices are known), pick the cheapest ones to cover the deficit.
3. **Validate**: Simulate the battery forward — drop charge slots that would overflow.

The battery charges during selected slots and idles otherwise.

### To Grid Mode (Sell Only)

**Goal**: Sell battery energy to the grid at the highest prices, while keeping enough for overnight self-consumption.

1. **Calculate sellable energy**: How much is above the reserve target (with a 15% safety margin).
2. **Select most expensive slots**: Pick the highest-price slots.
3. **Validate**: Drop discharge slots that would drain below the reserve target.

### Both Mode (Buy + Sell)

**Goal**: Buy cheap, sell expensive, while maintaining self-consumption reserve.

Combines the from_grid and to_grid logic with an additional **profitability filter**:

```
min_sell_price = (max_buy_price + battery_cycle_cost) / (efficiency * efficiency)
```

Only sells at prices above the round-trip break-even point. This ensures every sold kWh covers the cost of charging, losses, and battery wear.

When **arbitrage_price_delta** is set and the day's price spread exceeds it, the algorithm charges to full capacity instead of just the reserve target — maximizing profit on volatile price days.

## Core Concept: Reserve Target

The algorithm does NOT try to fill the battery to 100%. It calculates a **reserve target** — just enough to survive overnight:

### Dynamic Mode (reserve_target_pct = 0, the default)

```
overnight_hours = (24 - sunset_hour) + sunrise_hour
overnight_reserve = (consumption_est / 24) * overnight_hours
reserve_target = discharge_min_kwh + overnight_reserve
```

Sunset and sunrise are derived from the PV forecast (last/first hour with production).

**Example**: 60 kWh battery, 35% min SOC, 38.5 kWh/day consumption, sunset 19:00, sunrise 07:00
- discharge_min_kwh = 21.0 kWh
- overnight = (38.5/24) x 12h = 19.3 kWh
- reserve_target = 21.0 + 19.3 = 40.3 kWh (67% of battery)

### Fixed Mode (reserve_target_pct > 0)

Uses the configured percentage as a fixed floor. Useful for users who want a simple, predictable reserve.

## PV Confidence

The algorithm compares actual solar production to the forecast to detect cloudy days:

- **Cumulative**: `actual_produced / expected_by_now`
- **3-hour window**: Recovers quickly when weather improves mid-day
- **EMA smoothing** (alpha=0.3): Dampens single-hour weather oscillations
- Clamped to [0.1, 1.0]

This scales the PV forecast down on cloudy days so the algorithm doesn't over-rely on solar that won't arrive.

## Two-Day Optimization

When tomorrow's prices are available (typically from ~13:00), the algorithm creates a unified pool of today + tomorrow slots, sorted by price. It may delay charging to tomorrow if tomorrow has cheaper overnight prices.

A bridge-safety check ensures the battery survives until tomorrow's first charge slot.

## Configuration Settings

### Core Settings

| Setting | Range | Default | Description |
|---|---|---|---|
| **Grid Mode** | off / from_grid / to_grid / both | off | Main EMS switch |
| **Price Mode** | manual / auto | manual | Threshold-based or optimizer |
| **Price Threshold Level** | 1-10 | 5 | Manual mode price point |
| **Power Level** | 1 - max kW (model) | 5 | Charge/discharge power limit |
| **Battery Charge Max Level** | 30-100% | 100 | SOC ceiling |
| **Battery Discharge Min Level** | 10-70% | 20 | SOC hard floor |

### Battery & Efficiency

| Setting | Range | Default | Description |
|---|---|---|---|
| **Battery Capacity** | 1-200 kWh | 10 | Usable capacity (auto-adjusted for SOH) |
| **Efficiency Factor** | 0.70-1.00 | 0.90 | Single-direction efficiency (round-trip = squared) |
| **Daily Consumption Estimate** | 0-120 kWh | 10 | Fallback when no 7-day history |

### Reserve & Optimization

| Setting | Range | Default | Description |
|---|---|---|---|
| **Reserve Target** | 0-100% | 0 | 0 = dynamic overnight reserve, >0 = fixed floor |
| **Optimization Priority** | cost / longevity / self_consumption | cost | cost = minimize spend, longevity = enforce 0.05 EUR/kWh cycle cost floor, self_consumption = 1.25x overnight reserve |
| **Arbitrage Price Delta** | 0-0.50 EUR/kWh | 0 | Price spread threshold for full-charge mode |
| **Battery Cycle Cost** | 0-0.50 EUR/kWh | 0 | Wear cost added to profitability filter |

### Negative-Price Strategies

| Setting | Options | Default | Description |
|---|---|---|---|
| **Block Export on Negative Price** | on / off | on | Prevents selling at negative prices (paying the grid) |
| **Charge to Full on Negative Price** | off / on | off | Charges at every negative-price slot (user gets paid) |
| **Discharge to Make Room for Negative Price** | off / on | off | Pre-discharges at peak to create headroom for negative-price PV windows |

### Inverter Rule 1 Control

| Setting | Options | Default | Description |
|---|---|---|---|
| **Rule 1 Time Window** | manual / auto | manual | auto = 00:00-23:59. If manual, ensure the window covers EMS hours. |
| **Rule 1 Weekday** | manual / auto | manual | auto = all 7 days enabled. |

If scheduled actions fall outside the Rule 1 window, a warning banner appears on the EMS card.

### Voltage Settings

| Setting | Range | Default | Description |
|---|---|---|---|
| **Voltage Level** | 48-60V (LV) / 300-448V (HV) | 58 | Charge voltage setpoint |
| **Discharge Min Voltage** | 48-55V (LV) / 300-448V (HV) | 50 | Discharge voltage floor |

These are inverter protection parameters — written to Modbus registers during state transitions. The algorithm works in SOC% and kWh.

## Runtime Behavior

### State Transitions

Every 10 seconds the coordinator checks the current slot:

| State | Rule 1 Enable | SOC Register | Voltage | Power |
|---|---|---|---|---|
| Charging | 1 (charge) | charge_max (e.g. 100%) | voltage_level | safe_max_power |
| Discharging (manual) | 2 (discharge) | discharge_min (e.g. 20%) | discharge_min_voltage | safe_max_power |
| Discharging (auto) | 2 (discharge) | reserve_target (e.g. 67%) | discharge_min_voltage | safe_max_power |
| Idle | 0 (disabled) | *(not written)* | *(not written)* | *(not written)* |

In auto mode, the inverter's SOC floor register is set to the computed reserve target — the inverter hardware enforces the same floor the schedule was planned around.

### Charge Deferral

When a scheduled charge slot executes, the system checks if a later scheduled slot is cheaper (by at least 1 cent/kWh). If so, and the battery is above the reserve target, it defers to idle. This shifts charging to the cheapest available slot.

### Anti-Conflict Guard

Prevents discharge during grid import (e.g., EV charging pulls from grid while the battery is selling):

- Small/moderate import (200-2000W): Must persist 2+ cycles (~20s) before suppression
- Large import (>2000W): Suppresses immediately
- 60-second cooldown after suppression ends

## Economic Rule Set Integration

The integration uses **Economic Rule Set 1** exclusively. Users should not manually configure Rule 1 unless they keep `rule1_time_window` and `rule1_weekday` on `manual`.

**Critical requirement**: The inverter's Operating Mode must be set to **Economic Mode** by the user.

## Implementation Details

- [TREX10 Energy Management](TREX10-Energy-Management) — Register details for TREX-5/10K
- [TREX25/50 Energy Management](TREX25-50-Energy-Management) — Register details for TREX-25/50K
- [EMS_LOGIC.md](https://github.com/partach/ha_felicity/blob/main/docs/EMS_LOGIC.md) — Complete algorithm reference with code-level detail
