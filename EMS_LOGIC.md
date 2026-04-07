# Felicity EMS Decision Logic — Full Analysis

This document traces exactly how the EMS decides what to do in each mode, what inputs drive those decisions, and where the logic may be unsound — particularly in "both" mode.

---

## 1. Inputs That Drive Every Decision

Before any mode-specific logic runs, the algorithm gathers these inputs:

| Input | Source | Used For |
|---|---|---|
| **Battery SOC** | Modbus register (%) | Current energy in kWh = SOC% × capacity |
| **Slot prices today** | Nordpool entity (24/48/96 slots) | Which slots are cheap/expensive |
| **Slot prices tomorrow** | Nordpool (when available, typically after 13:00) | Two-day unified optimization |
| **PV forecast remaining** | Forecast entity (kWh left today) | How much solar is still coming |
| **PV hourly breakdown** | Forecast entity per-hour, or synthesized bell curve | Per-slot solar contribution |
| **PV actual today** | Inverter register (kWh produced so far) | PV confidence calculation |
| **Consumption estimate** | 7-day rolling average or manual config (kWh/day) | Overnight reserve, hourly drain |
| **Consumption hourly profile** | 7-day HA recorder history per hour | More accurate drain prediction |
| **Yesterday's deficit** | Carried forward from previous day | Extra charging if yesterday fell short |

---

## 2. Common Calculations (All Modes)

### 2.1 Reserve Target — "How much battery do I need to keep?"

This is the most important number. It determines how much energy the system protects.

**Dynamic mode** (reserve_target_pct = 0, the default):
```
min_kwh = discharge_min_pct × capacity
    e.g. 20% × 50 kWh = 10 kWh

overnight_hours = (24 - sunset_hour) + sunrise_hour
    e.g. (24 - 19) + 7 = 12 hours

overnight_reserve = (consumption / 24) × overnight_hours
    e.g. (30 / 24) × 12 = 15 kWh

reserve_target = min_kwh + overnight_reserve
    e.g. 10 + 15 = 25 kWh (50% of a 50 kWh battery)
```

**Fixed mode** (reserve_target_pct > 0):
```
reserve_target = max(reserve_target_pct × capacity, min_kwh)
```

**Key point**: The reserve_target is always HIGHER than discharge_min. The gap between them is the overnight consumption buffer.

### 2.2 PV Confidence — "Can I trust the solar forecast?"

Compares actual production so far vs what the forecast said should have been produced by now.

```
cumulative_confidence = actual_produced / expected_by_now
window_confidence    = last_3h_actual / last_3h_expected   (recovery mechanism)
raw_confidence       = max(cumulative, window)              (takes the better one)

evidence_weight = ramp from 0→1 as expected reaches 20% of daily total
final_confidence = blend(1.0, raw_confidence, evidence_weight)
Result: clamped to [0.1, 1.0]
```

- Early morning: confidence stays near 1.0 (not enough evidence)
- Cloudy morning then sunny afternoon: window_confidence recovers it
- Consistently cloudy: confidence drops, system assumes less PV coming

### 2.3 Net PV Surplus — "How much solar actually charges the battery?"

Only counts hours where PV exceeds consumption (the surplus goes to battery):

```
For each remaining hour:
    surplus = pv_hourly[hour] × pv_confidence - consumption_per_hour
    if surplus > 0:
        total_surplus += surplus
```

Hours where consumption > PV contribute zero (battery drains, PV just offsets some drain).

### 2.4 SOC Trajectory Projection — "What will my battery look like through the day?"

Simulates battery SOC forward through every remaining slot, accounting for:
- PV production per slot (scaled by confidence)
- Consumption per slot (flat or from hourly profile)
- No charge/discharge actions (passive trajectory)

Returns: `min_projected` (lowest SOC the battery will hit) and `max_projected` (highest SOC, typically after solar peak).

### 2.5 Energy Deficit — "How much grid energy do I need?"

```
battery_shortfall = max(0, reserve_target - current_kwh)
snapshot_deficit  = max(0, battery_shortfall - net_pv_surplus)

predictive_deficit = max(0, reserve_target - min_projected)

# Solar protection: if PV will fill battery to 95%+ of max, no grid needed
if max_projected >= max_battery_kwh × 0.95:
    predictive_deficit = 0

energy_deficit = max(snapshot_deficit, predictive_deficit) + carryover_from_yesterday
```

Two perspectives: "am I short right now?" vs "will I be short at any point today?". Uses the worse case.

---

## 3. Mode: `from_grid` — Buy Cheap

**Goal**: Charge the battery at the cheapest prices to cover the energy deficit.

**Steps**:
1. Calculate energy_deficit (as above)
2. If deficit = 0 → no action needed
3. Call `select_unified_charge_slots()`:
   - All negative-price slots are always selected (free/paid-to-charge)
   - From remaining: pick cheapest slots until deficit is covered
   - If tomorrow's prices are known: merge into one pool, pick cheapest across both days
4. **Headroom constraint**: Don't schedule more charge than the battery can physically accept
   - `headroom = max_battery_kwh - current_kwh - pv_surplus`
   - Cap today's charge slots to fit headroom (excess moved to tomorrow)
5. **SOC validation**: Simulate forward through all slots, prune any charge slot that would push SOC above capacity
6. Result: `{slot_index: "charge"}` for each selected slot

**No selling happens in this mode.**

---

## 4. Mode: `to_grid` — Sell High

**Goal**: Discharge the battery at the most expensive prices, protecting the reserve.

**Steps**:
1. Calculate reserve_target
2. Project SOC trajectory → get `max_projected` (peak SOC, typically after solar fills battery)
3. Calculate sellable energy:
   ```
   sellable = (max_projected - reserve_target) × efficiency
   ```
4. If sellable ≤ 0 → no action needed
5. Pick the most expensive positive-price slots, up to `ceil(sellable / energy_per_slot)` slots
6. **SOC validation**: Simulate forward, prune any discharge slot that would drop SOC below **reserve_target**
7. Result: `{slot_index: "discharge"}` for each selected slot

**No buying happens in this mode.**

---

## 5. Mode: `both` — Buy Cheap AND Sell Expensive

**Goal**: Charge at cheap prices AND sell at expensive prices, making a profit on the spread.

This is the most complex mode and where the aggressive selling issue lives.

### 5.1 Charge Side (identical to from_grid)

1. Calculate energy_deficit exactly as in from_grid
2. **Arbitrage override**: If `arbitrage_price_delta > 0` and price spread ≥ delta:
   - Charges to **full capacity** instead of just reserve_target
   - `energy_deficit = max(deficit, full_capacity - current_kwh - net_pv)`
3. Select cheapest charge slots via `select_unified_charge_slots()`

### 5.2 Sell Side

1. Calculate sellable energy:
   ```
   # Normal:
   sellable = (max_projected - reserve_target) × efficiency

   # With arbitrage active:
   sellable = (max_battery_kwh - reserve_target) × efficiency
   ```
2. **Profitability filter**: Only sell at slots where:
   ```
   sell_price >= max_buy_price / (efficiency × efficiency)
   ```
   This ensures every sold kWh earns more than the round-trip cost of buying it.
   **But**: If no charge slots were selected (PV covers the deficit), `max_buy_price` defaults to the cheapest charge slot price, and if there are NO charge slots at all, `min_sell_price = 0` — meaning ALL positive-price slots pass the filter.
3. Pick most expensive qualifying slots, up to `ceil(sellable / energy_per_slot)`

### 5.3 Combined SOC Validation

Both charge AND discharge slots are validated together in one forward simulation:
- Charge slots that push SOC above capacity → pruned (most expensive first)
- Discharge slots that drop SOC below **reserve_target** → pruned (least valuable first)

---

## 6. Two-Day Unified Optimization

When tomorrow's prices arrive (typically after 13:00 from Nordpool):

### 6.1 Tomorrow's Deficit Calculation

```
tomorrow_reserve_target = min_kwh + overnight_reserve_for_tomorrow

# Project battery at midnight
projected_midnight = current_kwh + net_pv + today_charge - drain_to_midnight
projected_midnight = max(min_kwh, projected_midnight)  # inverter stops at min

# Tomorrow's shortfall
daytime_gap       = max(0, consumption - tomorrow_pv)
tomorrow_pv_surplus = max(0, tomorrow_pv - consumption)
tomorrow_deficit  = max(0, tomorrow_reserve_target + daytime_gap
                         - projected_midnight - tomorrow_pv_surplus)
```

### 6.2 Unified Slot Pool

```
total_deficit = today_deficit + tomorrow_deficit

combined_pool = today_slots + tomorrow_slots
Sort by price → pick cheapest until total_deficit is covered
```

This means a cheap slot tomorrow beats an expensive slot today. The algorithm picks the globally cheapest option across both days.

### 6.3 Safety Swap — Bridge to Tomorrow

After unified selection, checks: "Can the battery survive until tomorrow's first charge slot?"

```
hours_until_tomorrow_charge = (24 - now) + earliest_tomorrow_charge_hour
bridge_consumption = consumption_per_hour × hours_until_tomorrow_charge
projected_at_bridge = current_kwh + net_pv + today_charge - bridge_consumption

If projected_at_bridge < min_kwh:
    Swap expensive tomorrow slots → cheap today slots to survive the gap
```

---

## 7. The "Both" Mode Aggressive Selling Problem — Analysis

The user reports that in "both" mode, the battery frequently drops below the minimum level. Here are the likely causes:

### 7.1 Sellable Based on max_projected, Not Current SOC

```python
sellable = max(0.0, max_projected - reserve_target) * efficiency
```

`max_projected` is the PEAK the battery will reach (typically after solar noon). But sell slots are selected by PRICE — the most expensive slots might be in the morning (before solar) or evening (after solar has passed). The battery at those times is much lower than `max_projected`.

**The SOC validation should catch this**, but see 7.3 below.

### 7.2 No Profitability Floor When PV Covers Deficit

When solar covers the entire energy deficit (common in summer), no charge slots are selected. This means:
```python
if charge_slots:
    max_buy_price = max(s[1] for s in charge_slots)
    min_sell_price = max_buy_price / round_trip_eff
```
**This code doesn't execute.** `min_sell_price` stays at 0. Every positive-price slot qualifies for selling. The algorithm may schedule many sell slots because the profitability filter is effectively disabled.

### 7.3 Ceiling Rounding Over-Allocates Sell Slots

```python
sell_needed = math.ceil(sellable / energy_per_slot)
```

Example: sellable = 8.1 kWh, energy_per_slot = 2.5 kWh
- `sell_needed = ceil(3.24) = 4 slots`
- 4 slots × 2.5 kWh = 10 kWh drawn from battery
- But only 9 kWh (8.1/0.9) was actually available above reserve

The SOC validation should prune the extra slot, but only if the over-draw happens in sequence. If sell slots are spread across the day with PV production between them, each individual slot may look safe, but the cumulative effect drains below reserve.

### 7.4 PV Confidence Overestimates Early in the Day

The schedule runs every 10 seconds, recalculating. In the morning, PV confidence is near 1.0 (not enough evidence). The system projects a sunny day, calculates a high `max_projected`, and schedules aggressive sells. If the day turns out cloudy:
- `max_projected` was too optimistic
- More sell slots were scheduled than the battery can sustain
- By the time PV confidence drops, some sells have already executed

The 10-second recalculation mitigates this somewhat, but each cycle re-commits to sell slots based on the current (still optimistic) projection.

### 7.5 Reserve Target vs Discharge Min — The Gap

The SOC validation protects `reserve_target`, NOT `discharge_min_pct`. With:
- discharge_min = 20%, capacity = 50 kWh → min_kwh = 10 kWh
- reserve_target = 25 kWh (50%)

If the user considers 20% as "the minimum," the system is actually protecting 50%. That's correct behavior — but if reserve_target is calculated too LOW (low consumption estimate, or short overnight hours in summer), it could be close to discharge_min, leaving little buffer.

**In summer** with long days (sunset 21:00, sunrise 5:00):
- overnight_hours = (24-21) + 5 = 8 hours
- overnight_reserve = (consumption/24) × 8

If consumption is underestimated, reserve_target is too low, and selling drains too aggressively.

### 7.6 Consumption Estimate Flat Model

The algorithm uses `consumption / 24` for hourly drain. If actual consumption peaks in the evening (cooking, heating, EV charging), the flat model underestimates evening drain. The battery drops faster than projected, and sell slots in the evening push it further below.

The hourly consumption profile (from HA recorder) should help, but only if the profile data is populated and accurate.

---

## 8. Summary: Is the Logic Sound?

### What's Well-Designed
- Solar-first philosophy (grid is last resort)
- Two-day unified optimization (globally cheapest slots)
- Multiple safety layers (SOC validation, headroom cap, profitability filter)
- PV confidence with recovery window
- Reserve target concept (charge only what's needed)

### What Causes the "Both" Mode Problem

The root issue is a **timing mismatch**: sellable energy is calculated from `max_projected` (a future peak), but sell slots execute at different times when SOC may be far from that peak. The SOC validation should catch this, but:

1. **Ceiling rounding** schedules one extra sell slot
2. **No profitability floor** when PV covers charging (all positive prices qualify)
3. **Optimistic early-day projections** commit to sells before PV confirms
4. **Flat consumption model** underestimates evening drain during sell periods

The combination of these factors — not any single one — leads to systematic over-selling in "both" mode.

### Potential Fixes

1. **Add a safety margin to reserve_target in both mode**: `reserve_target_for_sell = reserve_target + buffer_kwh` — sell less aggressively
2. **Apply a profitability floor even without charge slots**: Use average price as baseline instead of defaulting to 0
3. **Use floor() instead of ceil() for sell_needed**: Under-sell rather than over-sell
4. **Scale sellable by PV confidence**: `sellable × pv_confidence` — if forecast uncertain, sell less
5. **Defer morning sell decisions**: Don't schedule sell slots before solar peak has been confirmed by actual production
