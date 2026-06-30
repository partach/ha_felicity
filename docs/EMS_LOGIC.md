# EMS Decision Logic: How Felicity Decides What To Do

This document traces the complete decision path of the Felicity EMS, from raw inputs to inverter action, for each mode.

> **This is the behavioural SPECIFICATION.** It must be kept in sync with the
> code on every behaviour change (alongside CLAUDE.md). The body below predates
> June 2026; the **Recent behavioural decisions** section is the current
> authority where it conflicts with older text. Validate with
> `tools/ems_simulator.py`.

---

## Recent behavioural decisions (June–July 2026) — current authority

These refine/override the older sections below. Each is pinned by a simulator
scenario and/or `tests/test_ems.py`.

1. **Engine default = greedy; MILP opt-in.** Greedy has a multi-month track
   record and no solver dependency. MILP is the joint 2-day optimiser but is
   validated per-scenario before it can become default again.
2. **Reserve is time-aware (from_grid + MILP all modes).** Past sunset the
   overnight reserve covers only the REMAINING hours to sunrise, not the full
   night — so a high-consumption house isn't forced to charge at peak evening
   prices to "maintain" a full-night reserve the battery should be discharging.
3. **Self-consumption boost dropped at night.** The ×1.25 boost (hold extra PV
   energy for self-use) is a daytime concept; at night the reserve is bare
   survival so the battery rides down and refills from tomorrow's PV.
4. **Overnight need is profile-aware.** The reserve SUMS the 7-day *hourly*
   consumption profile over the night hours (not flat daily/24). Critical for
   daytime-heavy loads (EVs): high daily average but low night use → small
   reserve → no phantom evening charging. Falls back to flat with no profile.
   The hourly profile is resolved via the entity registry (exact unique_id),
   not a guessed entity_id.
5. **MILP is provably feasible.** Reserve constraints are soft (shortfall slack
   + penalty) and the SOC dynamics carry a per-slot grid-passthrough slack, so
   the LP never returns Infeasible (which used to drop ~24% of runs to greedy).
6. **Manual price mode is a threshold rule, not the optimizer.** `scheduled_slots`
   in manual mode is rebuilt from the threshold (from_grid/both charge BELOW it,
   to_grid/both sell ABOVE it) — never the stale optimizer plan. It is also
   **SOC-aware**: a forward simulation (PV + consumption) stops marking 'charge'
   once the battery is full (a low-price slot then *holds* the battery full —
   grid serves the load) and stops marking 'sell' once it's empty — so the card
   never shows charging-while-full or selling-while-empty.
6b. **Both engines compute the SAME reserve** for the same inputs: the
   time-aware + night-boost-drop reserve is scoped to `from_grid` in BOTH greedy
   and MILP. (Earlier the MILP applied it to all modes, producing a different
   reserve % than greedy for the same both/to_grid scenario — confusing and
   unpredictable.) Greedy and MILP are still different optimizers and may pick
   different slots, but the reserve they protect is identical.
7. **No charge deferral in the coordinator.** Every scheduled charge slot
   executes; the cheapest-slot decision lives entirely in `ems.py`.
8. **Manual slot picks are grid-mode-aware.** from_grid → any picked slot is a
   charge slot (even above threshold); to_grid → any is a sell slot; both →
   threshold decides.
9. **Greedy re-shops charge energy dropped for overflow (from_grid).** When SOC
   validation drops a back-to-back charge slot that overflowed a small battery,
   `_schedule_from_grid` re-shops the dropped energy into a LATER slot where
   prior drain freed headroom — but only at a price **≤ the marginal price the
   selector already committed to**, and bounded by PV-aware headroom. So a
   heavy-load / undersized-battery day charges the second *cheap* slot (closing
   the greedy/MILP gap — both reach the same planned kWh), while a PV-fed small
   battery never buys an expensive evening peak to chase the deficit
   (cost-first / no-safety-swap: drawing the shortfall live from cheap night
   grid beats a lossy round-trip at a higher price). Pinned by
   `TestGreedyReshopAfterOverflow` + the `cons_heavy_flat` /
   `self_suff_flat_low_soc` simulator scenarios.

10. **MILP discrete-extraction is cost-ranked (July 2026).** The MILP solves a
   continuous LP, then collapses it to discrete full-power slots capped by
   battery physics.  That collapse now executes the most cost-effective
   subset:
   - **Sells dearest-first.** When the cap allows fewer sell slots than the LP
     spread energy across (the LP cycles PV — sell early to clear room, then
     sell again at the peak — so the early cheap slot and the peak tie on
     energy), the extraction keeps the **highest-price** slots.  Fixes MILP
     selling a cheap 0.18 slot instead of the 0.40 evening peak
     (`to_grid_sell_surplus`).
   - **Charges cheapest-first.** Within **today** the charge is capped at the
     battery's physical charge headroom (`(soc_max − current)/eff`) and the
     cheapest slots are taken first; an expensive reserve-top-off slot the
     cheaper slots + PV already cover is therefore dropped (fixes MILP charging
     a 0.30 evening peak to hit a high self_consumption reserve —
     `self_suff_flat_low_soc` now charges the 0.15 slots, like greedy).
     **Tomorrow** is capped at the energy the LP allocated to it, which
     preserves the midnight-reserve "today-first" split (self-sufficiency:
     charge today rather than defer everything to a cheaper tomorrow — a single
     global cheapest-first cap would let cheap tomorrow slots starve today).
     The dearest slot is reached only when the cheaper ones genuinely can't
     fill the battery (e.g. heavy load: `cons_heavy_flat` correctly charges
     both cheap night slots).
   Pinned by `test_milp_sells_evening_peak_not_cheap_early_slot`,
   `test_milp_no_peak_charge_to_hit_reserve`, and the two simulator scenarios
   (whose expectations now assert "no peak charge" / "sells the peak" for BOTH
   engines).
11. **MILP leftover-energy reward is capped at the reserve (July 2026).** The
    objective's terminal value (`avg·eff × soc[end]`) used to reward leftover
    SOC all the way to `soc_max`, so in pure **cost** mode the solver bought
    any slot below `avg·eff²` to push the battery toward 100% — over-buying
    cheap-ish energy with no modelled use (duck-curve cost day: charged an
    extra night slot to end 81% vs greedy's 52%, and cost MORE).  The reward is
    now `terminal × min(soc[end], reserve_target)`, and the today extraction
    target is capped at the LP's allocated charge energy.  The solver fills to
    the reserve but not beyond for leftover value, so cost mode stops
    over-buying (duck curve now charges just the two cheapest midday slots).
    Arbitrage (sell revenue) and self_consumption (high boosted reserve) are
    unaffected.
12. **Greedy keeps substantial partial charges (from_grid, July 2026).** When a
    charge slot causes overflow but the battery had room entering it, the
    inverter charges what fits and the rest spills — the slot still stores
    cheap energy.  The default validation dropped the whole slot, so on a
    no-PV / undersized-battery day (two cheapest night slots consecutive)
    greedy charged only 1 of 2 needed cheap slots and rode the floor through
    the expensive evening (~2× the MILP cost: 1.53 vs 0.77).
    `keep_partial_charges` (from_grid only) keeps a partial charge that stores
    ≥ half the slot; a near-full sliver top-off is still dropped.  Greedy now
    charges both cheap slots, matching MILP.  This is the greedy
    "consumption-arbitrage myopia" fix — greedy buys cheap night energy to
    displace pricier daytime/evening consumption, which the joint MILP did
    natively.

---

## 1. User Configuration: The Settings That Shape Every Decision

These are the user-configurable parameters. Every calculation in the algorithm traces back to one or more of these settings.

### Battery Settings

| Setting | Range | Default | How It's Used |
|---|---|---|---|
| **battery_capacity_kwh** | 1-200 kWh | 10 | The total usable battery size. All kWh calculations scale from this: `current_kwh = SOC% * capacity`. The integration multiplies nominal capacity by a SOH factor (see Section 12) to get the effective value — ems.py treats it as already-effective. A wrong value here corrupts every decision. |
| **battery_charge_max_level** | 30-100% | 100% | The SOC ceiling. The scheduler won't charge above this, and the inverter register is set to this value during charging. Lowering it (e.g., 90%) preserves battery longevity but reduces available storage. |
| **battery_discharge_min_level** | 10-70% | 20% | The SOC hard floor. In **manual mode**, this is written directly to the inverter Modbus register as the discharge limit. In **auto mode**, the inverter register is set to the higher `reserve_target` instead (see below), with `discharge_min_level` serving as the absolute backstop that the reserve target can never go below. |
| **efficiency_factor** | 0.70-1.00 | 0.90 | Single-direction charging efficiency. Round-trip = efficiency² (default 0.81). Affects: how much grid energy actually reaches the battery (`effective_per_slot = power * hours * efficiency`), the profitability filter in "both" mode (`min_sell_price = buy_price / efficiency²`), and how much sellable energy can be extracted (`sellable * efficiency`). A too-high value (e.g., 0.95) makes the system overestimate what's available, leading to deeper drain. |

### Voltage Settings (Inverter Protection)

| Setting | Range | Default | How It's Used |
|---|---|---|---|
| **voltage_level** | 48-60V (LV) / 300-448V (HV) | 58V | Written to the inverter when entering **charge** state. This is the target charge voltage — the inverter's constant-voltage phase begins here. Higher values charge fuller but stress the battery more. Range auto-adjusts based on detected battery system. |
| **discharge_min_voltage** | 48-55V (LV) / 300-448V (HV) | 50V | Written to the inverter when entering **discharge** state. This is the voltage floor below which the inverter stops discharging. Protects battery cells from over-discharge damage. |

These voltage settings are **not used by the scheduling algorithm** — they are pure inverter protection parameters written to Modbus registers during state transitions. The algorithm works in SOC percentages and kWh; the voltages are the inverter's own safety layer.

### Reserve & Self-Consumption

| Setting | Range | Default | How It's Used |
|---|---|---|---|
| **reserve_target_pct** | 0-100% | 0 (dynamic) | Controls how much battery to keep for self-consumption. **When 0** (default): the reserve is calculated dynamically as `discharge_min + overnight_consumption`. **When > 0**: uses this fixed percentage as the floor (e.g., 50% means always keep at least half the battery). Increasing this is the most direct way to prevent the battery draining too low in "both" mode — it raises the bar that the scheduler plans around. |
| **daily_consumption_estimate** | 0-120 kWh | 10 | Fallback when no 7-day average is available. Used to estimate overnight drain, hourly consumption, and deficit calculations. |

### Power & Grid

| Setting | Range | Default | How It's Used |
|---|---|---|---|
| **power_level** | 1 - max kW (model dependent) | 5 | Base charge/discharge power. Determines energy per slot: `energy_per_slot = power * slot_hours`. Higher = fewer slots needed but more grid stress. Model limits: TREX-5 = 5 kW, TREX-10 = 10 kW, TREX-25 = 25 kW, TREX-50 = 50 kW. |
| **safe_power_management** | auto/on/off | auto | When active, monitors grid amperage per phase and reduces power to prevent overload (see Section 9). The actual power used may be lower than `power_level`. |
| **max_amperage_per_phase** | 10-63A | 16A | Grid current limit for safe power management. |

### Pricing & Arbitrage

| Setting | Range | Default | How It's Used |
|---|---|---|---|
| **grid_mode** | off/from_grid/to_grid/both | off | The master switch. Determines which algorithm runs. "off" = no grid interaction. |
| **price_mode** | manual/auto | manual | "manual" uses a price threshold (level 1-10); "auto" uses the optimizer to pick slots. All algorithm logic described in this document runs in "auto" mode. |
| **price_threshold_level** | 1-10 | 5 | Manual mode only: maps to a price point between min and max. Slots below this charge, above this sell. |
| **arbitrage_price_delta** | 0-0.50 EUR/kWh | 0 | **Both mode only.** The minimum buy→sell spread required to trade. When > 0: charge-to-full activates only if the day's spread (max - min) clears the delta, and every sell slot must beat the buy reference by at least the delta — below the bar, **nothing is sold**. When 0 (default): automatic profitability check (trade whenever the peak covers round-trip losses on the cheapest buy). |

### Optimization & Lifecycle

| Setting | Range | Default | How It's Used |
|---|---|---|---|
| **optimization_priority** | cost/longevity/self_consumption | cost | Multi-objective strategy. **cost**: minimizes grid spend (default). **longevity**: enforces a minimum 0.05 EUR/kWh cycle cost floor regardless of the `battery_cycle_cost` setting — fewer cycles, longer battery life. **self_consumption**: multiplies the dynamic overnight reserve by 1.25×, keeping more PV-stored energy for self-use instead of selling. |
| **battery_cycle_cost_eur_kwh** | 0-0.50 EUR/kWh | 0 | Estimated cost per kWh of battery wear. Added to the minimum sell price in the profitability filter: `min_sell_price = (buy_price + cycle_cost) / efficiency²`. If you know your battery cost per cycle, enter it here to prevent unprofitable trading. When `optimization_priority = longevity`, this is raised to at least 0.05 EUR/kWh. |

### Negative-Price Strategies

These three settings control behavior during negative electricity prices (when you get paid to consume energy). They are orthogonal to `grid_mode` and compose with all modes.

| Setting | Options | Default | How It's Used |
|---|---|---|---|
| **block_export_on_negative_price** | on/off | on | When `on`, prevents the EMS from scheduling battery discharge (sell) during negative price hours. Selling at negative prices means paying the grid to take your energy. Acts as a sell-price floor of 0.0 EUR/kWh. |
| **charge_to_full_on_negative_price** | off/on | off | When `on`, adds every negative-price slot to the charge schedule regardless of the reserve target or headroom cap. Phantom-charge slots (battery already full) are kept too — the inverter/BMS will gate it. The user accepts some forced PV curtailment in exchange for guaranteed revenue at every negative-price slot. |
| **discharge_to_make_room_for_negative_price** | off/on | off | When `on`, pre-emptively discharges at the most expensive positive-price slots before a negative-price PV window to create headroom. This allows PV + grid charging during the negative window to fill the cleared battery. Works even in `from_grid` mode (which normally never discharges). SOC must never drop below `discharge_min_pct` and end-of-day SOC must remain >= `reserve_target`. |

### Inverter Rule 1 Control

| Setting | Options | Default | How It's Used |
|---|---|---|---|
| **rule1_time_window** | manual/auto | manual | `auto` writes Rule 1 start=00:00, stop=23:59 every cycle. When `manual`, the user manages the inverter's Rule 1 time window — if it's too restrictive, the EMS plans actions the inverter silently ignores. |
| **rule1_weekday** | manual/auto | manual | `auto` writes Rule 1 effective_week=0x7F (all 7 days) every cycle. |

If scheduled actions fall outside the inverter's Rule 1 window, the integration detects this and surfaces a `rule1_window_warning` banner on the EMS card.

### How These Settings Interact

The settings form a hierarchy of constraints:

```
optimization_priority  -->  Modifies cycle cost floor and reserve multiplier
        |
        v
arbitrage_price_delta  -->  Trade trigger: charging target (reserve vs full)
        |                  AND sell gate (sells must beat buy + delta)
        |
        v
reserve_target_pct  -->  The floor for scheduling AND runtime discharge (auto mode)
        |                  Written to inverter register when discharging
        v
battery_cycle_cost  -->  Raises min sell price (profitability filter)
        |
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
| **PV forecast today** | Forecast entity (e.g., Forecast.Solar, Solcast) | Total expected solar kWh today |
| **PV forecast remaining** | Forecast entity | Solar kWh still expected today |
| **PV forecast tomorrow** | Forecast entity | Tomorrow's expected solar |
| **PV hourly breakdown** | Forecast entity `wh_hours` | Per-hour solar production curve (filtered by date) |
| **PV actual today** | Inverter register (`pv_day_cost_energy`) | kWh actually produced so far |
| **Consumption estimate** | 7-day rolling average, or user-set fallback | Expected daily household consumption |
| **Consumption hourly profile** | 7-day HA recorder history | Per-hour average consumption pattern |
| **Yesterday's deficit** | Stored from previous day | Carried-over shortfall (reset on grid_mode change) |

### Derived Values

From these inputs, several intermediate values are calculated before any mode-specific logic runs:

#### Current Battery Energy (kWh)
```
current_kwh = (battery_soc_pct / 100) * battery_capacity_kwh
```

#### PV Confidence Factor
Compares actual production to forecast to detect cloudy days. Uses a two-window approach with EMA smoothing:
```
cumulative_confidence = actual_produced / expected_by_now
window_confidence = (actual - expected_before_3h_window) / expected_in_3h_window
raw_confidence = max(cumulative, window)
confidence = EMA(previous_confidence, raw_confidence, alpha=0.3)
```
- `evidence_weight` ramps from 0 to 1 as expected production reaches 20% of daily total (or 3 kWh minimum)
- Early morning: confidence stays near 1.0 (not enough data to judge)
- Cloudy morning + sunny afternoon: window confidence recovers even if cumulative is low
- EMA smoothing (alpha=0.3) dampens single-hour weather oscillations
- Clamped to [0.1, 1.0]

**Forecast.solar fallback**: when the forecast service is unavailable, a rough daylight-extrapolation from today's actual production is used so the algorithm doesn't default to PV=0 and over-aggressively grid-charge.

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
The battery level the algorithm tries to protect. This is NOT `discharge_min` — it's higher:

**Dynamic mode** (reserve_target_pct = 0, the default):
```
overnight_hours = (24 - sunset_hour) + sunrise_hour
overnight_reserve = (consumption_est / 24) * overnight_hours
reserve_target = discharge_min_kwh + overnight_reserve
```
Sunset/sunrise are derived from the PV hourly profile (last/first hour with >0.1 kWh).

When `optimization_priority = self_consumption`, the overnight reserve is multiplied by 1.25× to keep more PV energy for self-use.

**Fixed mode** (reserve_target_pct > 0):
```
reserve_target = max(reserve_target_pct * capacity, discharge_min_kwh)
```

**Example**: 60 kWh battery, 35% discharge min, 38.5 kWh/day consumption, sunset 19:00, sunrise 07:00
- discharge_min_kwh = 21.0 kWh
- overnight = (38.5/24) × 12 = 19.3 kWh
- reserve_target = 21.0 + 19.3 = 40.3 kWh (67% of battery)

**Important**: The reserve target is always computed, even when `grid_mode = off` or there's no price data. The frontend's "night target" line and "overnight need" stat read these values to show the user how much battery is needed for overnight, regardless of whether the scheduler is active.

#### SOC Trajectory Projection
Simulates battery level forward through every remaining slot, assuming NO charge/discharge actions — just PV production minus consumption:
```
for each remaining slot:
    pv_this_slot = hourly_pv[hour] * confidence * (slot_minutes / 60)
    consumption_this_slot = hourly_consumption[hour] * (slot_minutes / 60)
    projected_soc += pv_this_slot - consumption_this_slot
    track min_projected and max_projected
```

When calculating the charge energy per slot, the inverter's maximum power caps the grid contribution:
```
grid_kw = min(safe_power_kw, max(0, inverter_max_power_kw - pv_kw))
```
This prevents the SOC prediction from assuming unrealistic charge rates (e.g., 8 kW grid + 7 kW PV = 15 kW on a 10 kW inverter).

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

The algorithm pools today's remaining slots with tomorrow's slots (if prices are known), sorts by price, and accumulates the cheapest ones until their **actually-achievable** charge energy covers the deficit:

```
per-slot achievable energy:
    grid_kw  = min(safe_power_kw, max(0, inverter_max_kw - pv_kw_at_hour))
    slot_kwh = grid_kw * slot_duration_hours * efficiency

accumulate cheapest-first until sum(slot_kwh) >= energy_deficit
```

This is **power-aware**: with a high charge power the deficit is covered by just the few cheapest slots (the battery loads fast — no need to start early on more expensive slots), while midday slots where PV throttles or saturates the inverter count their reduced (or zero) deliverable energy. A PV-saturated slot is skipped entirely instead of being burned as a no-op "charge" slot.

All negative-price slots are always selected (you get PAID to charge).

**Headroom constraint**: Prevents over-charging when PV will also fill the battery:
```
headroom = max(0, max_battery_kwh - current_kwh - net_pv_surplus)
max_today_slots = floor(headroom / effective_per_slot)
```
Excess today slots are replaced with tomorrow slots when possible. Negative-price slots pass through the headroom cap (they are profitable to consume).

**Bridge to tomorrow — intentionally no swap**: When tomorrow slots are selected and the overnight projection would dip toward the floor, the algorithm does NOT swap them for expensive today slots. The inverter switches the house to grid passthrough once SOC reaches `discharge_min_kwh`, so the battery cannot drain below the floor from consumption. Forcing today-charging to "bridge" the night would cost more than simply consuming from grid overnight (round-trip losses on top of the same prices) — charging stays deferred to tomorrow's cheaper slots.

**Charge-to-full on negative price**: When `charge_to_full_on_negative_price = on`, every negative-price slot in the remaining window is added to the charge set after normal selection (deduplicated).

### Step 3: SOC Validation

Simulates the battery forward through every slot WITH the selected charge actions. If any charge slot would push SOC above battery capacity, it's removed (most expensive first).

**Phantom-charge detection**: When a charge slot starts with the battery already at capacity (SOC >= capacity - 0.01 kWh), the slot is dropped regardless of price — the inverter physically cannot store the energy. Exception: when `charge_to_full_on_negative_price = on`, phantom-charge slots are kept (user explicitly accepts this).

**PV-caused overflow**: When PV alone would fill the battery (surplus >= 90% of remaining capacity), negative-price slots are preserved even during overflow — the overflow is PV-caused, not grid-caused, and the negative-price income is pure profit. When PV alone is insufficient, negative-price charge slots are pruned to prevent forced grid export at penalty rates.

### Step 4: Discharge-to-Make-Room (Optional)

When `discharge_to_make_room_for_negative_price = on`, a post-processing step runs after charge selection. It walks the SOC trajectory forward; whenever a negative-price slot with PV surplus would overflow the battery, it schedules discharge in the **most expensive earlier positive-price slot** to create headroom.

Constraints:
- SOC must never drop below `discharge_min_pct` (hardware safety)
- End-of-day SOC must remain >= `reserve_target` (overnight coverage)
- Temporary dips below reserve during the day are allowed — the negative-window PV will refill the battery

This is the only way `from_grid` mode can schedule discharge slots.

### Result
A set of slot indices marked "charge" (and optionally "discharge" for make-room).

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

Only positive-price slots are considered. When `block_export_on_negative_price = on` (default), negative-price slots are excluded (selling at negative prices means paying the grid).

### Step 3: SOC Validation

Simulates forward with discharge actions. **Critically, the floor used here is `reserve_target`, not `discharge_min_pct`.** If any discharge would cause SOC to drop below reserve_target, the least profitable discharge slot is removed.

### Result
A set of slot indices marked "discharge".

---

## 5. Mode: `both` (Buy + Sell)

**Goal**: Buy cheap, sell expensive, while maintaining self-consumption reserve.

### Step 1: Calculate Energy Deficit (Same as `from_grid`)

Identical logic: snapshot + predictive deficit, solar protection, yesterday carryover.

### Step 2: Arbitrage Check (the trade trigger)

Two regimes, depending on whether you set an explicit spread requirement:

**`arbitrage_price_delta > 0`** — the delta is the explicit trade trigger:
```
if (max_remaining - min_remaining) >= arbitrage_price_delta:
    arbitrage_active = True
    energy_deficit = max(energy_deficit, max_battery_kwh - current_kwh - net_pv)
else:
    # No trading: no charge-to-full, and the sell side (Step 5) is
    # gated at buy_reference + delta — typically zero sells.
```
**This changes the goal from "charge to reserve target" to "charge to 100%"
— but only when the spread you demanded actually exists.**

**`arbitrage_price_delta = 0`** (default) — automatic profitability check:
arbitrage activates whenever the peak price covers round-trip losses on the
cheapest buy (`peak >= cheapest / efficiency²`). Selling then only needs to
clear the round-trip + cycle-cost floor per slot.

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

This is the key difference from `to_grid`. A minimum sell price ensures round-trip profitability, including battery wear cost:
```
max_buy_price = max price among selected charge slots
min_sell_price = (max_buy_price + battery_cycle_cost) / (efficiency * efficiency)
```
Only slots with `price >= min_sell_price` are eligible for selling.

**Arbitrage delta sell gate**: when `arbitrage_price_delta > 0`, the floor is
raised to `max(min_sell_price, buy_reference + delta)`, where buy_reference
is the most expensive scheduled charge slot (or the cheapest remaining price
when nothing is scheduled to buy). A 20 ct delta with buys at 0.05 means no
slot below 0.25 is ever sold — if no slot qualifies, nothing is sold at all.

When `block_export_on_negative_price = on` (default), negative-price slots are additionally excluded.

**Example**: If cheapest charge was at 0.05 EUR/kWh, efficiency is 0.90, and cycle cost is 0.02 EUR/kWh:
- Round-trip efficiency = 0.81
- min_sell_price = (0.05 + 0.02) / 0.81 = 0.086 EUR/kWh
- Only sell at slots priced above 0.086

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

The tomorrow reserve target honours the same settings as today's: a fixed `reserve_target_pct` floor when set, and the `self_consumption` priority's 1.25× overnight boost.

This means if tomorrow has very cheap overnight prices, the algorithm may delay charging to tomorrow rather than buying at today's more expensive prices.

---

## 7. Runtime Execution (Every 10 Seconds)

The schedule is recalculated periodically. Every 10 seconds, the coordinator:

1. Reads current battery SOC from inverter
2. Determines current time slot index
3. Checks if current slot is in the schedule
4. Determines desired state (charging/discharging/idle)
5. If state differs from current, writes Modbus registers

### Skip-Recalc Optimization

A hash of the schedule inputs (prices, SOC, PV forecast, deficit, overrides, power) is computed each cycle. When the hash and the current slot index are both unchanged from the previous cycle, the expensive schedule recalculation is skipped entirely. The hash resets on grid_mode changes.

### Slot Override Validation

Users can manually override slot actions via the EMS card (click a slot to force charge/discharge/idle). After merging overrides into the schedule, the full merged schedule is re-validated through `_validate_schedule_soc`. Manually-added charge slots that would overflow the battery, or discharge slots that would drain below reserve, are dropped with a log entry — this prevents users from setting up infeasible schedules.

### Charge Deferral (Cheapest-First Execution)

When the current slot is a scheduled charge slot, the EMS checks whether a later scheduled charge slot has a cheaper price (by at least 1¢/kWh). If so, and the battery SOC is above the reserve target, the current slot is deferred (state = idle). The next 10-second cycle re-evaluates, so charging naturally shifts to the cheapest scheduled slot. This compensates for the deficit shrinking as PV confidence recovers mid-day — without it, early expensive slots would execute while later cheaper slots get dropped from a re-plan.

**Stall prevention**: Never defers when SOC is at or below `reserve_target` (battery needs charging now, regardless of price). Negative-price slots are exempt from deferral.

### State Transitions (What Gets Written to the Inverter)

| State | econ_rule_1_enable | SOC limit | Voltage | Power |
|---|---|---|---|---|
| charging | 1 | `battery_charge_max_level` (e.g., 100%) | `voltage_level` (e.g., 58V) | `safe_max_power` (W) |
| discharging (manual) | 2 | `battery_discharge_min_level` (e.g., 20%) | `discharge_min_voltage` (e.g., 50V) | `safe_max_power` (W) |
| discharging (auto) | 2 | `reserve_target_pct` (e.g., 70%) | `discharge_min_voltage` (e.g., 50V) | `safe_max_power` (W) |
| idle | 0 | *(not written)* | *(not written)* | *(not written)* |

In auto mode, the inverter's SOC floor register is set to the computed `reserve_target_pct` rather than `discharge_min_level`. This means the inverter hardware itself enforces the same floor the schedule was planned around, even if the coordinator's polling cycle is delayed.

Secondary register writes (SOC, voltage, power, date) that fail are logged as warnings instead of silently ignored.

### SOC Boundary Enforcement in `_determine_energy_state`

Before entering a state:
- **Charging**: only if `battery_soc < charge_max` (e.g., < 100%)
- **Discharging (manual)**: only if `battery_soc > discharge_min` (e.g., > 20%)
- **Discharging (auto)**: only if `battery_soc > max(discharge_min, reserve_target_pct)` (e.g., > 70%). If SOC has dropped to or below the reserve target, the discharge slot is skipped and logged.

If SOC hits the limit mid-slot, the state transitions to idle on the next 10-second cycle.

### Anti-Conflict Guard (Discharge Hysteresis)

Prevents discharge during grid import (e.g., EV charging pulls from grid while the battery is selling — a wasteful round-trip). Uses three-tier hysteresis to avoid flipper behavior:

| Condition | Action |
|---|---|
| Small/moderate import (200–2000W) | Must persist ≥ 2 consecutive cycles (~20s) before suppression |
| Large import (> 2000W) | Suppresses immediately (genuine sustained draw) |
| After suppression ends | 60-second cooldown blocks re-suppression |

This prevents the inverter from flipping between discharge → idle → discharge every ~16s on short load spikes (kettle, microwave, EV startup).

### Midnight Rollover

The day-rollover block resets yesterday deficit, daily consumption, SOC history, and rotates slot overrides — but does NOT force the inverter to idle. The normal cycle re-determines the desired state, so valid charge/discharge actions continue across midnight (e.g., a customer selling overnight to clear the battery before negative-midday PV).

### Modbus Staleness Guard

If the last successful Modbus read was more than a configurable threshold ago, the scheduler refuses to re-plan — it keeps the last valid schedule rather than making decisions on stale data.

---

## 8. Rule 1 Window Conflict Detection

Every cycle, `_check_rule1_window_conflict` compares the set of intended action slots against the inverter's Economic Rule 1 time window (start_time, stop_time) and effective weekday mask.

- `start_time == stop_time` is treated as "all day"
- A full 0x7F weekday mask means "all days"
- Weekday mapping: inverter bit0=Sunday..bit6=Saturday, mapped from Python `isoweekday() % 7`

Any mismatch produces a `rule1_window_warning` attribute on `schedule_status`, rendered as a banner in the EMS card. This prevents the silent failure where the EMS plans to act, writes the register, but the inverter ignores it because the action falls outside its configured Rule 1 window.

---

## 9. Safe Power Management

Monitors grid current per phase and adjusts inverter power to prevent overcurrent. The priority chain has been extended with flexible load shedding:

| Priority | Action | Detail |
|---|---|---|
| **1st** | EV charger current step-down | Reduce one current step at a time (e.g., 25A → 20A → 16A). One step per 10-second cycle to avoid oscillation. |
| **2nd** | Binary load shedding | Turn off active flexible loads in shed-priority order — highest number sheds first (3 before 2 before 1). One load per cycle. |
| **3rd** | Battery power reduction | Reduce charge/discharge power by 1-2 kW. Last resort. |

| Grid Condition | Response |
|---|---|
| > 95% of max amperage | Emergency — reduces by 2 kW immediately |
| > 80% of max amperage | Caution — reduces by 1 kW |
| < 70% of max amperage | Recovery — restores by 1 kW (up to Power Level) |
| Current = 0 | Jumps to full Power Level |

Recovery works in reverse: loads are restored one per cycle in reverse priority order, and battery power is increased back toward the user's Power Level.

---

## 10. Flexible Load Control

Up to 3 controllable loads (EV charger, boiler, pool pump) can be managed by the EMS. Loads are assigned in the integration options flow (entity picker dropdowns for switch and current entities).

### Scheduling

`_schedule_flexible_loads()` in ems.py activates loads as an overlay on the battery schedule — they don't affect the battery plan itself. Each enabled load with a switch entity is scheduled into slots matching any of:

- **Price below threshold** (cheap)
- **Negative-price slots** (paid to consume)
- **PV surplus slots** (hourly solar > hourly consumption)
- **Battery charge slots** (already identified as cheap)

### Actuation

`_actuate_flex_loads()` runs every 10-second cycle. For each load, it compares `should_be_on` (load scheduled for current slot?) with the current state and calls `switch.turn_on` / `switch.turn_off` via HA services.

For the EV charger (load 1), when turning on, the current is set to `default_current` via the assigned current entity (`number.set_value` or `select.select_option`).

### EV Boost Override

A one-press "+1 hour" override that forces the EV charger on at maximum current regardless of the EMS schedule:

- **EV Boost +1h button**: Each press adds 1 hour from `max(now, current_boost_end)`, so presses stack.
- **EV Boost Cancel button**: Immediately cancels any active boost.

During a boost:
1. Charger is forced on at max current (highest step in `current_steps`).
2. Normal schedule is bypassed for the charger.
3. Safe Power Management can still step down current but will NOT fully shed the charger.
4. Boost expires automatically when the timer runs out.

---

## 11. SOC History and SOH Tracking

### SOC History

The coordinator records battery SOC at each slot boundary in `_soc_history`. The frontend draws a solid line for actual past SOC and a dotted line for the projected future trajectory.

### Cycle Counting and SOH Estimation

`_track_cycle_throughput()` accumulates positive/negative SOC deltas (kWh) each cycle, with a 0.5% jitter floor to filter noise.

```
equivalent_full_cycles = min(total_charged_kwh, total_discharged_kwh) / battery_capacity_kwh
SOH = max(0.80, 1.0 - cycles × 5e-5)
```

This is a conservative LFP degradation curve. The SOH factor multiplies the nominal `battery_capacity_kwh` before EMSConfig is constructed — ems.py treats the capacity as already-effective. Persisted in the consumption store so it survives restarts.

---

## 12. Design Notes

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

When the spread requirement is met (`arbitrage_price_delta > 0` and the day's spread clears it, or delta = 0 and the automatic profitability check passes), the system charges to full capacity and sells everything above reserve. This is inherently more aggressive than normal operation. The three protection layers above still apply, but margins are thinner. Users experiencing unwanted selling should consider:
- **Raising `arbitrage_price_delta`** — it is the trade trigger: sells must beat the buy price by at least the delta, so e.g. 0.15 EUR/kWh stops all trading on narrow-spread days
- Setting `battery_cycle_cost_eur_kwh` (or `optimization_priority = longevity`) to raise the per-slot sell floor
- Increasing `reserve_target_pct` to keep a larger buffer
- All changes can be combined

### Negative-Price Strategy Composition

When both `charge_to_full_on_negative_price` and `discharge_to_make_room_for_negative_price` are on:
1. Make-room discharges are scheduled before negative windows (sell at peak)
2. Charge slots fire during the negative windows (paid to consume)
3. Net effect: maximum profit on negative-price days (discharge at peak + buy at negative + PV fills the cleared battery)

---

## 13. Summary Decision Flowchart

```
START (every 10 seconds)
  │
  ▼
Read battery SOC, prices, PV, consumption
  │
  ▼
Input hash unchanged AND same slot? ──yes──▶ Skip recalc, use cached schedule
  │ no
  ▼
grid_mode == "off"? ──yes──▶ Compute reserve (informational) → IDLE
  │ no
  ▼
Recalculate schedule
  │
  ├── from_grid: deficit = max(snapshot, predictive) + carryover
  │     pick cheapest slots to cover deficit
  │     + make-room discharges (if enabled)
  │
  ├── to_grid: sellable = (peak_soc - reserve) * efficiency * 0.85
  │     pick most expensive slots
  │
  └── both: deficit + sellable with profitability filter
  │     charge cheap, sell expensive
  │     cycle cost raises min sell price
  │
  ▼
Negative-price post-processing
  │ charge_to_full → add all p<0 to charge set
  │ discharge_to_make_room → pre-discharge before p<0 PV windows
  │
  ▼
SOC validation (prune infeasible slots)
  │ phantom-charge detection
  │ PV-overflow exemption for negatives
  │
  ▼
Merge slot overrides → re-validate
  │
  ▼
Schedule flexible loads (overlay on cheap/negative/PV-surplus slots)
  │
  ▼
Current slot in schedule?
  │
  ├── charge slot → defer if cheaper slot later? → CHARGING or IDLE
  │     inverter SOC register = charge_max
  │
  ├── discharge slot & SOC > reserve_target → anti-conflict check → DISCHARGING or IDLE
  │     inverter SOC register = reserve_target (auto) or discharge_min (manual)
  │
  └── not in schedule → IDLE
  │
  ▼
Actuate flexible loads (on/off based on slot schedule, EV boost override)
  │
  ▼
Safe power check (EV step-down → load shed → battery power reduction)
  │
  ▼
Write Modbus registers if state changed
  │
  ▼
Check Rule 1 window conflict → surface warning if needed
```
