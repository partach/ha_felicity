# EMS Decision Logic: How Felicity Decides What To Do

This document traces the complete decision path of the Felicity EMS, from raw inputs to inverter action, for each mode. It is written to allow a logic review of the algorithm, particularly the "both" mode where aggressive selling has been observed.

---

## 1. User Configuration: The Settings That Shape Every Decision

These are the user-configurable parameters. Every calculation in the algorithm traces back to one or more of these settings.

### Battery Settings

| Setting | Range | Default | How It's Used |
|---|---|---|---|
| **battery_capacity_kwh** | 1-100 kWh | 10 | The total usable battery size. All kWh calculations scale from this: `current_kwh = SOC% * capacity`. A wrong value here corrupts every decision. |
| **battery_charge_max_level** | 30-100% | 100% | The SOC ceiling. The scheduler won't charge above this, and the inverter register is set to this value during charging. Lowering it (e.g., 90%) preserves battery longevity but reduces available storage. |
| **battery_discharge_min_level** | 10-70% | 20% | The SOC hard floor. The inverter is told never to discharge below this. **Critical**: this is the value written to the Modbus register -- it's the last line of defence. The scheduler's `reserve_target` (see below) is always higher, but if estimates are wrong, the battery drains to THIS level, not the planned reserve. |
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
reserve_target_pct  -->  The PLANNED floor for scheduling
        |
        v
efficiency_factor  -->  How much energy is actually usable (scales everything)
        |
        v
battery_discharge_min_level  -->  The ACTUAL floor written to inverter (last defence)
        |
        v
discharge_min_voltage  -->  Hardware protection (inverter stops here regardless)
```

**The gap between `reserve_target` and `discharge_min_level` is where the aggressive selling problem lives.** The scheduler plans to keep SOC above reserve_target (~60-70% typically), but the inverter is told the floor is discharge_min (20%). When reality diverges from the plan (cloud cover, consumption spike, forecast error), the battery drains through that gap.

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

Uses the SOC trajectory projection to find the peak battery level:
```
sellable = max(0, max_projected - reserve_target) * efficiency
```

Key point: `max_projected` is the PEAK SOC from the passive trajectory. If PV pushes the battery to 95% at noon, that's the peak, even though by evening the battery may be at 60%. The sellable energy is the amount ABOVE the reserve target at that peak, multiplied by single-direction efficiency.

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

```
if arbitrage_active:
    sellable = (max_battery_kwh - reserve_target) * efficiency
else:
    sellable = (max_projected - reserve_target) * efficiency
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
| discharging | 2 | `battery_discharge_min_level` (e.g., 20%) | `discharge_min_voltage` (e.g., 50V) | `safe_max_power` (W) |
| idle | 0 | *(not written)* | *(not written)* | *(not written)* |

### SOC Boundary Enforcement in `_determine_energy_state`

Before entering a state:
- **Charging**: only if `battery_soc < charge_max` (e.g., < 100%)
- **Discharging**: only if `battery_soc > discharge_min` (e.g., > 20%)

If SOC hits the limit mid-slot, the state transitions to idle on the next 10-second cycle.

---

## 8. Analysis: Why "Both" Mode Sells Too Aggressively

After tracing the logic, here are the potential causes of the battery draining below minimum in "both" mode:

### Issue A: Sellable Energy Based on Peak SOC, Not SOC at Time of Selling

```python
sellable = max(0, max_projected - reserve_target) * efficiency
```

`max_projected` is the PEAK battery level from the passive trajectory -- typically the midday solar peak. But discharge slots are usually scheduled in the evening (highest prices). By evening, the battery has already drained from afternoon consumption. The SOC validation should catch this, but there's a subtle gap (see Issue C).

### Issue B: SOC Validation Floor vs. Inverter Floor Mismatch

The SOC validation uses `reserve_target` as its floor (e.g., 70% for a 10 kWh battery with dynamic reserve). But when the inverter is put into discharge mode, the Modbus register is set to `battery_discharge_min_level` (e.g., 20%).

**This means**: The schedule is designed to keep SOC above 70%, but the inverter is told it can discharge down to 20%. If actual consumption is higher than estimated, or PV underperforms, the inverter will happily drain the battery well below the intended reserve.

The 10-second polling loop will transition to idle once `battery_soc <= discharge_min`, but the damage is done -- the battery has been drained below the planned reserve_target.

**This is likely the primary cause.** The schedule plans conservatively, but the inverter has a much lower hard floor. Any estimation error (consumption spike, cloud cover, forecast miss) results in deeper drain than intended.

### Issue C: Consumption Estimate Sensitivity

The default flat consumption model (`consumption_est / 24`) underestimates evening consumption for most households. Even with hourly profiles, estimation errors compound across multiple discharge slots.

If actual evening consumption is 30% higher than the profile suggests, each discharge slot drains the battery faster than the validation predicted.

### Issue D: PV Confidence Over-Estimation

If PV confidence stays at 1.0 (forecast matches production), the net_pv surplus is trusted fully. But forecasts have inherent uncertainty. A 10 kWh surplus forecast that only delivers 7 kWh means 3 kWh less battery energy than planned, making every discharge slot drain deeper than expected.

### Issue E: Arbitrage Mode Amplifies the Problem

When `arbitrage_price_delta > 0` and is triggered:
- Energy deficit inflates to charge the battery to 100%
- Sellable inflates to `max_battery_kwh - reserve_target` (the entire range)
- This schedules maximum charge AND maximum discharge
- Any estimation error hits harder because the margins are thinner

### Issue F: No Intra-Day Re-Evaluation of Sell Slots

The schedule is recalculated periodically, but if the battery is already lower than expected when an evening discharge slot arrives, the system still discharges. The `_determine_energy_state` only checks `battery_soc > discharge_min` (the LOW floor, e.g., 20%), not `battery_soc > reserve_target`.

**This is a second critical gap**: The runtime execution doesn't enforce the same floor that the schedule was designed around. The schedule says "this is safe because SOC will stay above reserve_target" but the runtime only prevents discharge below `discharge_min`.

### Worked Example: How Settings Combine to Cause Over-Discharge

Consider a typical setup:
```
battery_capacity_kwh = 10
battery_charge_max_level = 100%
battery_discharge_min_level = 20%    --> 2.0 kWh hard floor
efficiency_factor = 0.90
reserve_target_pct = 0 (dynamic)
arbitrage_price_delta = 0.10 EUR/kWh
daily_consumption = 12 kWh/day
```

At 14:00 on a sunny day:
- Battery SOC: 90% (9.0 kWh), peak projected 95% (9.5 kWh) from PV
- Dynamic reserve_target = 2.0 + (12/24 * 12h) = 8.0 kWh (80%)
- Price spread today: 0.25 EUR/kWh --> exceeds arbitrage_delta (0.10)
- **Arbitrage activates**: deficit inflated to charge to 100%
- Sellable = (10.0 - 8.0) * 0.90 = **1.8 kWh**
- At 5 kW, 30-min slots: 1 discharge slot scheduled at 18:00 (2.5 kWh capacity)

At 18:00 when discharge fires:
- PV has stopped, consumption drained battery to ~7.5 kWh (75%)
- Discharge removes 2.5 kWh at 5 kW for 30 min
- **BUT** house is also consuming ~0.5 kWh/h during that slot
- Battery drops to: 7.5 - 2.5 - 0.25 = **4.75 kWh (47.5%)**
- This is well below reserve_target (8.0 kWh / 80%) but well above discharge_min (2.0 kWh / 20%)
- The inverter doesn't stop because its floor is 20%, not 80%

After 18:30, the house keeps consuming overnight:
- 12h * 0.5 kWh/h = 6.0 kWh needed
- Battery has 4.75 kWh above the 2.0 kWh floor = 2.75 kWh available
- **Battery hits 20% floor around 23:30** -- 7.5 hours of no power until sunrise

This example shows why even a SINGLE discharge slot can cause problems when the inverter's hard floor (20%) is far below the planned reserve (80%).

---

## 9. Fixes Implemented

All four fixes from the analysis have been implemented:

### Fix 1: Use Reserve Target as Runtime Discharge Floor -- IMPLEMENTED

**File**: `coordinator.py` — `_determine_energy_state()`

In auto mode, the discharge guard now checks `battery_soc > max(discharge_min, reserve_target_pct)` instead of just `battery_soc > discharge_min`. When SOC is at or below the computed reserve target, the discharge slot is skipped and logged. This aligns the runtime guard with the floor the schedule was planned around.

### Fix 2: Write Reserve Target as Inverter SOC Floor -- IMPLEMENTED

**File**: `coordinator.py` — `_transition_to_state()`

When entering discharge state in auto mode, the inverter's `econ_rule_1_soc` register is now written with the computed `reserve_target_pct` (e.g., 70%) instead of `discharge_min_level` (e.g., 20%). This means even if the 10-second polling cycle is delayed or the coordinator misses a cycle, the inverter hardware itself stops discharging at the intended reserve level. Manual mode continues using `discharge_min_level` as before.

### Fix 3: Reduce Sellable by Safety Margin -- IMPLEMENTED

**Files**: `ems.py` — `_schedule_to_grid()` and `_schedule_both()`

A 15% safety margin is now applied to all sellable energy calculations:
```python
sellable = max(0, max_projected - reserve_target) * efficiency * 0.85
```
This accounts for consumption estimate errors, PV forecast uncertainty, and the inherent gap between peak SOC (midday) and actual SOC when discharge slots fire (evening). The margin reduces the number of discharge slots scheduled, leaving more buffer.

### Fix 4: Reserve Target Passed Through Schedule Result -- IMPLEMENTED

**Files**: `ems.py` — `ScheduleResult.reserve_target_pct`, `coordinator.py`

The computed reserve target (as a battery %) is now included in `ScheduleResult` and stored on the coordinator. This enables Fixes 1 and 2 — the runtime code can enforce the same floor the scheduler planned around, without recomputing it.

### Combined Effect

Using the worked example from Section 8 (10 kWh battery, 20% min, 80% reserve target):

**Before fixes**: Inverter told floor is 20%, sells aggressively, battery hits 20% by 23:30.

**After fixes**:
- Sellable reduced by 15%: 1.8 kWh → 1.53 kWh (may eliminate marginal discharge slots)
- `_determine_energy_state` checks SOC > 80% before allowing discharge
- Inverter register set to 80% (not 20%) — hardware won't discharge below reserve
- If battery is at 75% when evening discharge slot arrives → **slot is skipped**, battery preserved for overnight

---

## 10. Summary Decision Flowchart

```
START (every 10 seconds)
  |
  v
Read battery SOC, prices, PV, consumption
  |
  v
grid_mode == "off"? --> IDLE
  |
  v
Recalculate schedule (periodically)
  |
  +-- from_grid: deficit = max(snapshot, predictive) + carryover
  |     pick cheapest slots to cover deficit
  |
  +-- to_grid: sellable = (peak_soc - reserve) * efficiency
  |     pick most expensive slots
  |
  +-- both: deficit + sellable with profitability filter
  |     charge cheap, sell expensive
  |
  v
Current slot in schedule?
  |
  +-- charge slot & SOC < charge_max --> CHARGING
  +-- discharge slot & SOC > reserve_target --> DISCHARGING  (auto mode uses reserve_target, not discharge_min)
  +-- not in schedule --> IDLE
  |
  v
Write Modbus registers if state changed
```
