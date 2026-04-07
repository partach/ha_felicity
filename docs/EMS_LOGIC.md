# EMS Decision Logic: How Felicity Decides What To Do

This document traces the complete decision path of the Felicity EMS, from raw inputs to inverter action, for each mode.

---

## 1. User Configuration: The Settings That Shape Every Decision

These are the user-configurable parameters. Every calculation in the algorithm traces back to one or more of these settings.

### Battery Settings

| Setting | Range | Default | How It's Used |
|---|---|---|---|
| **battery_capacity_kwh** | 1-100 kWh | 10 | The total usable battery size. All kWh calculations scale from this: `current_kwh = SOC% * capacity`. A wrong value here corrupts every decision. |
| **battery_charge_max_level** | 30-100% | 100% | The SOC ceiling. The scheduler won't charge above this, and the inverter register is set to this value during charging. Lowering it (e.g., 90%) preserves battery longevity but reduces available storage. |
| **battery_discharge_min_level** | 10-70% | 20% | The SOC hard floor. In **manual mode**, this is written directly to the inverter Modbus register as the discharge limit. In **auto mode**, the inverter register is set to the higher `reserve_target` instead (see below), with `discharge_min_level` serving as the absolute backstop that the reserve target can never go below. |
| **efficiency_factor** | 0.70-1.00 | 0.90 | Single-direction charging efficiency. Round-trip = efficiency^2 (default 0.81). Affects: how much grid energy actually reaches the battery (`effective_per_slot = power * hours * efficiency`), the profitability filter in "both" mode (`min_sell_price = buy_price / efficiency^2`), and how much sellable energy can be extracted (`sellable * efficiency`). A too-high value (e.g., 0.95) makes the system overestimate what's available, leading to deeper drain. |

### Voltage Settings (Inverter Protection)

| Setting | Range | Default | How It's Used |
|---|---|---|---|
| **voltage_level** | 48-60V | 58V | Written to the inverter when entering **charge** state. This is the target charge voltage -- the inverter's constant-voltage phase begins here. Higher values charge fuller but stress the battery more. |
| **discharge_min_voltage** | 48-55V | 50V | Written to the inverter when entering **discharge** state. This is the voltage floor below which the inverter stops discharging. Protects battery cells from over-discharge damage. Lower values extract more energy but reduce battery lifespan. |

These voltage settings are **not used by the scheduling algorithm** -- they are pure inverter protection parameters written to Modbus registers during state transitions. The algorithm works in SOC percentages and kWh; the voltages are the inverter's own safety layer.

### Reserve & Self-Consumption

| Setting | Range | Default | How It's Used |
|---|---|---|---|
| **reserve_target_pct** | 0-100% | 0 (dynamic) | Controls how much battery to keep for self-consumption. **When 0** (default): the reserve is calculated dynamically as `discharge_min + overnight_consumption`. **When > 0**: uses this fixed percentage as the floor (e.g., 50% means always keep at least half the battery). Increasing this is the most direct way to prevent the battery draining too low in "both" mode -- it raises the bar that the scheduler plans around. |
| **daily_consumption_estimate** | 0-100 kWh | 10 | Fallback when no 7-day average is available. Used to estimate overnight drain, hourly consumption, and deficit calculations. |

### Power & Grid

| Setting | Range | Default | How It's Used |
|---|---|---|---|
| **power_level** | 1-10 kW | 5 | Base charge/discharge power. Determines energy per slot: `energy_per_slot = power * slot_hours`. Higher = fewer slots needed but more grid stress. |
| **safe_power_management** | auto/on/off | auto | When active, monitors grid amperage per phase and reduces power to prevent overload (see Section on Safe Power). The actual power used may be lower than `power_level`. |
| **max_amperage_per_phase** | 10-63A | 16A | Grid current limit for safe power management. |

### Pricing & Arbitrage

| Setting | Range | Default | How It's Used |
|---|---|---|---|
| **grid_mode** | off/from_grid/to_grid/both | off | The master switch. Determines which algorithm runs. "off" = no grid interaction. |
| **price_mode** | manual/auto | manual | "manual" uses a price threshold (level 1-10); "auto" uses the optimizer to pick slots. All algorithm logic described in this document runs in "auto" mode. |
| **price_threshold_level** | 1-10 | 5 | Manual mode only: maps to a price point between min and max. Slots below this charge, above this sell. |
| **arbitrage_price_delta** | 0-0.50 EUR/kWh | 0 | **Both mode only.** When > 0 and the day's price spread (max - min) exceeds this threshold, the algorithm switches from conservative "charge to reserve" to aggressive "charge to 100% and sell everything above reserve". **This is the biggest amplifier of aggressive selling.** Set to 0 to disable. When active, it maximizes both charge and discharge slot counts, leaving thinner margins for error. |

### How These Settings Interact

The settings form a hierarchy of constraints:

```
arbitrage_price_delta  -->  Determines charging target (reserve vs full)
        |
        v
reserve_target_pct  -->  The floor for scheduling AND runtime discharge (auto mode)
        |                  Written to inverter register when discharging
        v
efficiency_factor  -->  How much energy is actually usable (scales everything)
        |                  Sellable energy further reduced by 15% safety margin
        v
battery_discharge_min_level  -->  Hard floor (manual mode) / absolute backstop (auto mode)
        |
        v
discharge_min_voltage  -->  Hardware protection (inverter stops here regardless)
```

In auto mode, the scheduler, the runtime discharge guard, and the inverter register all use `reserve_target` as the discharge floor. The `discharge_min_level` only acts as the absolute minimum that the reserve target cannot go below.

---

## 2. Live Inputs: What the EMS Reads Each Cycle

Every 10 seconds, the coordinator gathers these inputs and feeds them to the scheduler:

| Input | Source | Used For |
|---|---|---|
| **Electricity prices (today)** | Nordpool entity | Slot-by-slot price array (24, 48, or 96 slots/day) |
| **Electricity prices (tomorrow)** | Nordpool entity (available ~13:00) | Two-day unified optimization |
| **Battery SOC** | Inverter Modbus register | Current energy in battery (converted to kWh) |
| **PV forecast today** | Forecast entity (e.g., Solcast) | Total expected solar kWh today |
| **PV forecast remaining** | Forecast entity | Solar kWh still expected today |
| **PV forecast tomorrow** | Forecast entity | Tomorrow's expected solar |
| **PV hourly breakdown** | Forecast entity `wh_hours` | Per-hour solar production curve |
| **PV actual today** | Inverter register (`pv_day_cost_energy`) | kWh actually produced so far |
| **Consumption estimate** | 7-day rolling average, or user-set fallback | Expected daily household consumption |
| **Consumption hourly profile** | 7-day HA recorder history | Per-hour average consumption pattern |
| **Yesterday's deficit** | Stored from previous day | Carried-over shortfall |

### Derived Values

From these inputs, several intermediate values are calculated before any mode-specific logic runs:

#### Current Battery Energy (kWh)
```
current_kwh = (battery_soc_pct / 100) * battery_capacity_kwh
```

#### PV Confidence Factor
Compares actual production to forecast to detect cloudy days:
```
cumulative_confidence = actual_produced / expected_by_now
window_confidence = (actual - expected_before_3h_window) / expected_in_3h_window
raw_confidence = max(cumulative, window)
confidence = blend(1.0, raw_confidence, evidence_weight)
```
- `evidence_weight` ramps from 0 to 1 as expected production reaches 20% of daily total (or 3 kWh minimum)
- Early morning: confidence stays near 1.0 (not enough data to judge)
- Cloudy morning + sunny afternoon: window confidence recovers even if cumulative is low
- Clamped to [0.1, 1.0]

#### Net PV Surplus
Only counts hours where solar exceeds consumption (the battery can only charge from surplus):
```
for each remaining hour:
    pv_this_hour = forecast_hourly[hour] * pv_confidence
    surplus = pv_this_hour - consumption_per_hour
    if surplus > 0:
        net_pv += surplus
```
Hours where consumption exceeds PV contribute zero (the shortfall drains the battery but can't be "banked" as surplus).

#### Reserve Target
The battery level the algorithm tries to protect. This is NOT `discharge_min` -- it's higher:

**Dynamic mode** (reserve_target_pct = 0, the default):
```
overnight_hours = (24 - sunset_hour) + sunrise_hour
overnight_reserve = (consumption_est / 24) * overnight_hours
reserve_target = discharge_min_kwh + overnight_reserve
```
Sunset/sunrise are derived from the PV hourly profile (last/first hour with >0.1 kWh).

**Fixed mode** (reserve_target_pct > 0):
```
reserve_target = max(reserve_target_pct * capacity, discharge_min_kwh)
```

**Example**: 10 kWh battery, 20% discharge min, 10 kWh/day consumption, sunset 19:00, sunrise 07:00
- discharge_min_kwh = 2.0 kWh
- overnight = (10/24) * 12 = 5.0 kWh
- reserve_target = 2.0 + 5.0 = 7.0 kWh (70% of battery)

#### SOC Trajectory Projection
Simulates battery level forward through every remaining slot, assuming NO charge/discharge actions -- just PV production minus consumption:
```
for each remaining slot:
    pv_this_slot = hourly_pv[hour] * confidence * (slot_minutes / 60)
    consumption_this_slot = hourly_consumption[hour] * (slot_minutes / 60)
    projected_soc += pv_this_slot - consumption_this_slot
    track min_projected and max_projected
```
This gives:
- `min_projected`: lowest SOC the battery will hit if we do nothing
- `max_projected`: highest SOC the battery will reach (usually midday from solar)

---

## 3. Mode: `from_grid` (Buy Only)

**Goal**: Charge the battery from the grid at the cheapest possible prices, but only enough to survive overnight.

### Step 1: Calculate Energy Deficit

Two deficit estimates are computed; the worse one wins:

**Snapshot deficit** (how short are we RIGHT NOW?):
```
battery_shortfall = max(0, reserve_target - current_kwh)
snapshot_deficit = max(0, battery_shortfall - net_pv)
```

**Predictive deficit** (will we dip below reserve at ANY future point?):
```
predictive_deficit = max(0, reserve_target - min_projected)
```

**Solar protection**: If `max_projected >= 95% of max_battery_kwh`, set `predictive_deficit = 0`. Rationale: solar will fill the battery, grid charging would waste money/energy.

```
base_deficit = max(snapshot_deficit, predictive_deficit)
```

**Yesterday's carryover**: If there was an unmet deficit yesterday AND the battery is still short:
```
carryover = min(yesterday_deficit, battery_shortfall - base_deficit)
energy_deficit = base_deficit + carryover
```

### Step 2: Select Cheapest Charge Slots (Unified Two-Day)

The algorithm pools today's remaining slots with tomorrow's slots (if prices are known), sorts by price, and picks the cheapest ones to cover the deficit:

```
slots_needed = ceil(energy_deficit / effective_per_slot)
effective_per_slot = safe_power_kw * slot_duration_hours * efficiency
```

All negative-price slots are always selected (you get PAID to charge).

**Headroom constraint**: Prevents over-charging when PV will also fill the battery:
```
headroom = max(0, max_battery_kwh - current_kwh - net_pv_surplus)
max_today_slots = floor(headroom / effective_per_slot)
```
Excess today slots are replaced with tomorrow slots when possible.

**Bridge safety**: If tomorrow slots were selected, ensures the battery survives until tomorrow's first charge slot:
```
hours_to_tomorrow_charge = (24 - current_hour) + earliest_tomorrow_charge_hour
bridge_consumption = consumption_per_hour * hours_to_tomorrow_charge
projected_at_charge = current_kwh + net_pv + today_charge - bridge_consumption

if projected_at_charge < discharge_min_kwh:
    swap expensive tomorrow slots for cheap today slots
```

### Step 3: SOC Validation

Simulates the battery forward through every slot WITH the selected charge actions. If any charge slot would push SOC above battery capacity, it's removed (most expensive first). This prevents wasting energy charging an already-full battery.

### Result
A set of slot indices marked "charge". The inverter charges during these slots and idles otherwise.

---

## 4. Mode: `to_grid` (Sell Only)

**Goal**: Sell battery energy to the grid at the highest prices, while keeping enough for overnight self-consumption.

### Step 1: Calculate Sellable Energy

Uses the SOC trajectory projection to find the peak battery level, with a 15% safety margin to account for forecast/consumption errors and the gap between peak SOC time (midday) and discharge time (evening):
```
sellable = max(0, max_projected - reserve_target) * efficiency * 0.85
```

Key point: `max_projected` is the PEAK SOC from the passive trajectory. If PV pushes the battery to 95% at noon, that's the peak, even though by evening the battery may be at 60%. The sellable energy is the amount ABOVE the reserve target at that peak, scaled by efficiency and the safety margin.

### Step 2: Select Most Expensive Sell Slots

```
slots_needed = ceil(sellable / energy_per_slot)
sorted by price descending
select top N
```

Only positive-price slots are considered.

### Step 3: SOC Validation

Simulates forward with discharge actions. **Critically, the floor used here is `reserve_target`, not `discharge_min_pct`.** If any discharge would cause SOC to drop below reserve_target, the least profitable discharge slot is removed.

### Result
A set of slot indices marked "discharge".

---

## 5. Mode: `both` (Buy + Sell)

**Goal**: Buy cheap, sell expensive, while maintaining self-consumption reserve. This is where the aggressive selling issue occurs.

### Step 1: Calculate Energy Deficit (Same as `from_grid`)

Identical logic: snapshot + predictive deficit, solar protection, yesterday carryover.

### Step 2: Arbitrage Check

If `arbitrage_price_delta > 0` and the price spread (max - min of remaining prices) exceeds it:
```
arbitrage_active = True
energy_deficit = max(energy_deficit, max_battery_kwh - current_kwh - net_pv)
```
**This changes the goal from "charge to reserve target" to "charge to 100%".**

### Step 3: Calculate Sellable Energy

A 15% safety margin is applied to all sellable calculations to account for consumption/PV forecast errors:
```
if arbitrage_active:
    sellable = (max_battery_kwh - reserve_target) * efficiency * 0.85
else:
    sellable = (max_projected - reserve_target) * efficiency * 0.85
```

### Step 4: Select Charge Slots (Same as `from_grid`)

Uses `select_unified_charge_slots` with the (potentially inflated) deficit.

### Step 5: Select Sell Slots with Profitability Filter

This is the key difference from `to_grid`. A minimum sell price ensures round-trip profitability:
```
max_buy_price = max price among selected charge slots
min_sell_price = max_buy_price / (efficiency * efficiency)
```
Only slots with `price >= min_sell_price` are eligible for selling.

**Example**: If cheapest charge was at 0.05 EUR/kWh and efficiency is 0.90:
- Round-trip efficiency = 0.81
- min_sell_price = 0.05 / 0.81 = 0.062 EUR/kWh
- Only sell at slots priced above 0.062

Then picks the most expensive eligible slots, up to `sell_needed`.

### Step 6: SOC Validation (Combined)

**This is the critical safety step.** Runs a single forward simulation with BOTH charge and discharge slots active:

```
for each remaining slot in time order:
    delta = pv_per_slot - consumption_per_slot
    if slot is charge: delta += energy_per_slot * efficiency
    if slot is discharge: delta -= energy_per_slot
    soc += delta

    if soc < reserve_target: remove least profitable discharge
    if soc > battery_capacity: remove most expensive charge
```

The validation iterates until no violations remain. The floor is `reserve_target` (not `discharge_min_pct`).

---

## 6. Two-Day Unified Optimization

When tomorrow's prices are available (typically from ~13:00), the algorithm creates a unified pool:

```
combined_pool = today_remaining_slots + tomorrow_all_slots
sort by price ascending
pick cheapest to cover total_deficit (today + tomorrow)
```

**Tomorrow's deficit** is calculated independently:
```
tomorrow_reserve_target = discharge_min_kwh + tomorrow_overnight_reserve
projected_midnight_soc = current_kwh + net_pv + today_charge - drain_to_midnight
daytime_gap = max(0, consumption - tomorrow_pv)
tomorrow_pv_surplus = max(0, tomorrow_pv - consumption)
tomorrow_deficit = max(0, tomorrow_reserve_target + daytime_gap - projected_midnight - tomorrow_pv_surplus)
```

This means if tomorrow has very cheap overnight prices, the algorithm may delay charging to tomorrow rather than buying at today's more expensive prices.

---

## 7. Runtime Execution (Every 10 Seconds)

The schedule is recalculated periodically. Every 10 seconds, the coordinator:

1. Reads current battery SOC from inverter
2. Determines current time slot index
3. Checks if current slot is in the schedule
4. Determines desired state (charging/discharging/idle)
5. If state differs from current, writes Modbus registers

### State Transitions (What Gets Written to the Inverter)

| State | econ_rule_1_enable | SOC limit | Voltage | Power |
|---|---|---|---|---|
| charging | 1 | `battery_charge_max_level` (e.g., 100%) | `voltage_level` (e.g., 58V) | `safe_max_power` (W) |
| discharging (manual) | 2 | `battery_discharge_min_level` (e.g., 20%) | `discharge_min_voltage` (e.g., 50V) | `safe_max_power` (W) |
| discharging (auto) | 2 | `reserve_target_pct` (e.g., 70%) | `discharge_min_voltage` (e.g., 50V) | `safe_max_power` (W) |
| idle | 0 | *(not written)* | *(not written)* | *(not written)* |

In auto mode, the inverter's SOC floor register is set to the computed `reserve_target_pct` rather than `discharge_min_level`. This means the inverter hardware itself enforces the same floor the schedule was planned around, even if the coordinator's polling cycle is delayed.

### SOC Boundary Enforcement in `_determine_energy_state`

Before entering a state:
- **Charging**: only if `battery_soc < charge_max` (e.g., < 100%)
- **Discharging (manual)**: only if `battery_soc > discharge_min` (e.g., > 20%)
- **Discharging (auto)**: only if `battery_soc > max(discharge_min, reserve_target_pct)` (e.g., > 70%). If SOC has dropped to or below the reserve target, the discharge slot is skipped and logged.

If SOC hits the limit mid-slot, the state transitions to idle on the next 10-second cycle.

---

## 8. Design Notes

### Sellable Energy Safety Margin

The sellable calculation uses `max_projected` (the peak SOC from the passive trajectory, usually midday). Discharge slots typically fire in the evening when prices are highest, by which time the battery has drained from afternoon consumption. To account for this timing gap, plus PV forecast uncertainty and consumption estimate errors, a 15% safety margin is applied:

```
sellable = max(0, peak_soc - reserve_target) * efficiency * 0.85
```

This means the system schedules fewer discharge slots than the theoretical maximum, leaving buffer for real-world conditions.

### Three Layers of Discharge Protection (Auto Mode)

In auto mode, three independent mechanisms prevent the battery from draining below the reserve target:

1. **Schedule planner** (`ems.py`): SOC validation simulates every slot forward and prunes discharge slots that would cause SOC to drop below `reserve_target`.
2. **Runtime guard** (`coordinator.py` — `_determine_energy_state`): Before entering discharge, checks `battery_soc > max(discharge_min, reserve_target_pct)`. If SOC has already fallen to the reserve, the slot is skipped.
3. **Inverter register** (`coordinator.py` — `_transition_to_state`): The inverter's `econ_rule_1_soc` register is set to `reserve_target_pct` (not `discharge_min_level`), so the inverter hardware itself stops at the intended floor even if polling is delayed.

In manual mode, `discharge_min_level` is used at all three layers.

### Arbitrage Mode Considerations

When `arbitrage_price_delta > 0` and the day's price spread exceeds it, the system charges to full capacity and sells everything above reserve. This is inherently more aggressive than normal operation. The three protection layers above still apply, but margins are thinner. Users experiencing unwanted deep discharge should consider:
- Setting `arbitrage_price_delta = 0` to disable arbitrage
- Increasing `reserve_target_pct` to keep a larger buffer
- Both changes can be combined

---

## 9. Summary Decision Flowchart

```
START (every 10 seconds)
  │
  ▼
Read battery SOC, prices, PV, consumption
  │
  ▼
grid_mode == "off"? ──yes──▶ IDLE
  │ no
  ▼
Recalculate schedule (periodically)
  │
  ├── from_grid: deficit = max(snapshot, predictive) + carryover
  │     pick cheapest slots to cover deficit
  │
  ├── to_grid: sellable = (peak_soc - reserve) * efficiency * 0.85
  │     pick most expensive slots
  │
  └── both: deficit + sellable with profitability filter
        charge cheap, sell expensive
  │
  ▼
Current slot in schedule?
  │
  ├── charge slot & SOC < charge_max ──▶ CHARGING
  │     inverter SOC register = charge_max
  │
  ├── discharge slot & SOC > reserve_target ──▶ DISCHARGING
  │     inverter SOC register = reserve_target (auto) or discharge_min (manual)
  │
  └── not in schedule ──▶ IDLE
  │
  ▼
Write Modbus registers if state changed
```
