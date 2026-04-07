# EMS Decision Logic: How Felicity Decides What To Do

This document traces the complete decision path of the Felicity EMS, from raw inputs to inverter action, for each mode. It is written to allow a logic review of the algorithm, particularly the "both" mode where aggressive selling has been observed.

---

## 1. Inputs: What the EMS Knows

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

## 2. Mode: `from_grid` (Buy Only)

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

## 3. Mode: `to_grid` (Sell Only)

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

## 4. Mode: `both` (Buy + Sell)

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

## 5. Two-Day Unified Optimization

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

## 6. Runtime Execution (Every 10 Seconds)

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

## 7. Analysis: Why "Both" Mode Sells Too Aggressively

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

---

## 8. Suggested Fixes for Aggressive Selling

### Fix 1: Use Reserve Target as Runtime Discharge Floor (High Impact)

In `_determine_energy_state`, when in auto/both mode, check against `reserve_target` instead of `discharge_min`:

```python
# Instead of: if battery_soc > discharge_min
# Use: if battery_soc_kwh > reserve_target_kwh
```

This aligns the runtime guard with the planning assumption.

### Fix 2: Write Reserve Target as Inverter SOC Floor

When transitioning to discharge state, write `reserve_target_pct` (not `discharge_min_level`) to `econ_rule_1_soc`. This way, even if the 10-second polling is delayed, the inverter itself stops at the intended floor.

### Fix 3: Reduce Sellable by Safety Margin

Apply a conservative factor to sellable energy:
```python
sellable = max(0, max_projected - reserve_target) * efficiency * 0.85  # 15% safety margin
```

### Fix 4: Re-Validate Against Actual SOC Before Each Discharge

Before entering discharge state, check if current SOC is still consistent with the schedule's expectations. If SOC is significantly lower than projected, skip the discharge slot.

---

## 9. Summary Decision Flowchart

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
  +-- discharge slot & SOC > discharge_min --> DISCHARGING  <-- HERE: should check reserve_target
  +-- not in schedule --> IDLE
  |
  v
Write Modbus registers if state changed
```
