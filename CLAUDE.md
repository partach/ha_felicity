# CLAUDE.md — ha_felicity Integration Reference

This document is a mental model for understanding, debugging, and improving the ha_felicity Home Assistant integration. It covers architecture, algorithm logic, known issues, and improvement recommendations.

---

## Project Overview

**ha_felicity** is a Home Assistant integration for Felicity solar inverters (TREX-5, TREX-10, TREX-25, TREX-50). It combines Modbus-based inverter monitoring with an Energy Management System (EMS) that optimizes battery charge/discharge based on electricity prices, solar forecasts, and consumption patterns.

**Version**: 0.9.9.6
**Communication**: Modbus TCP/RTU via pymodbus
**Architecture**: Local polling (10-second update cycle)

---

## File Structure

```
custom_components/ha_felicity/
├── __init__.py              # Integration setup, platform loading
├── config_flow.py           # Config flow + options flow (UI setup wizard)
├── const.py                 # Constants, register groups, model registry
├── coordinator.py           # Main coordinator (1501 lines) — polling, scheduling, state machine
├── ems.py                   # Pure EMS algorithm (1004 lines) — testable scheduling logic
├── sensor.py                # All HA sensor entities (price, schedule, PV, inverter registers)
├── number.py                # Number entities (power level, SOC limits, capacity, etc.)
├── select.py                # Select entities (grid_mode, price_mode, safe_power_management)
├── type_specific.py         # Model-specific Modbus translation layer
├── trex_five.py             # TREX-5 register map
├── trex_ten.py              # TREX-10 register map
├── trex_twenty_five.py      # TREX-25 register map
├── trex_fifty.py            # TREX-50 register map
├── date.py                  # Date entities
├── time.py                  # Time entities
└── frontend/
    └── ha_felicity_ems.js   # LitElement EMS dashboard card (1671 lines)

tests/
└── test_ems.py              # 130 tests for the pure EMS algorithm
```

---

## Critical Architecture: Three Copies of Scheduling Logic

**This is the most important thing to understand.** The scheduling algorithm exists in THREE places:

| Location | Purpose | Authoritative? |
|---|---|---|
| `ems.py` — `calculate_schedule()` | Pure functions, no HA deps | Used by tests only |
| `coordinator.py` — `_calculate_schedule()` | Runtime scheduling | **YES — this runs in production** |
| `frontend/ha_felicity_ems.js` — `_simulateSchedule()` | Client-side preview | Visual only, simplified |

**Known problem**: The coordinator duplicates logic from ems.py rather than calling it. They can (and do) diverge. The recent solar protection bug was caused by ems.py having a fix that coordinator.py was missing. When making algorithm changes:
1. Always update `ems.py` first (testable)
2. Mirror the change in `coordinator.py` (production)
3. Consider updating the JS card if the change affects the preview

**Ideal refactor**: Have the coordinator call `ems.calculate_schedule()` directly instead of duplicating the logic. This would eliminate drift between the two implementations.

---

## Data Flow

```
Nordpool Entity ──┐
PV Forecast     ──┤
Battery SOC     ──┼──▶ Coordinator (10s cycle) ──▶ Schedule Optimizer
Consumption     ──┤        │                            │
Grid Current    ──┘        │                            ▼
                           │                   _determine_energy_state()
                           │                        │
                           ▼                        ▼
                     Sensor entities        _transition_to_state()
                           │                Write Modbus registers
                           ▼                (econ_rule_1_enable, etc.)
                     EMS Card (frontend)
                     Client-side simulation
```

---

## Inverter Control

The integration controls the inverter via **Economic Rule 1** Modbus registers. It does NOT schedule individual time slots on the inverter. Instead:

1. The scheduler selects which time slots should charge/discharge
2. Every 10 seconds, the coordinator checks if the current slot is in the schedule
3. If the desired state differs from current state, it writes registers

**Charge deferral** (`coordinator._determine_energy_state`): when the current
slot is flagged `charge` but a later scheduled charge slot has a cheaper
price (non-negative slots only), execution returns `idle` instead. The next
10s re-evaluation cycle adjusts the deficit naturally, so charging shifts to
the cheapest scheduled slot. This compensates for deficit shrinking as PV
confidence recovers mid-day — without it, early expensive slots would
execute while later cheaper slots got dropped from a re-plan.

### State Transitions (coordinator.py:1195-1231)

| State | econ_rule_1_enable | Voltage | SOC | Power |
|---|---|---|---|---|
| charging | 1 | voltage_level (default 58V) | charge_max (default 100%) | safe_max_power (W) |
| discharging | 2 | discharge_min_voltage (default 50V) | discharge_min (default 20%) | safe_max_power (W) |
| idle | 0 | *(not written)* | *(not written)* | *(not written)* |

### Model Differences

| Aspect | TREX-5 | TREX-10 | TREX-25 | TREX-50 |
|---|---|---|---|---|
| Max inverter power | 5 kW | 10 kW | 25 kW | 50 kW |
| Enable register | Single `econ_rule_1_enable` (0/1/2) | Same | `econ_rule_1_grid_charge_enable` + peak shaving | Same |
| Power unit | Watts | Watts | Kilowatts (÷1000) | Kilowatts (÷1000) |
| SOC source | Single register | Single register | `min(bat1_soc, bat2_soc)` | `min(bat1_soc, bat2_soc)` |
| Date registers | Written | Written | Not used | Not used |

`INVERTER_MAX_POWER_KW` in `const.py` maps each model to its max power.
The power_level slider range and SOC trajectory calculations use this to
cap grid charge when PV is active: `grid_kw = min(safe_power_kw, inverter_max - pv_kw)`.
This prevents the SOC prediction from assuming unrealistic charge rates
(e.g., 8 kW grid + 7 kW PV = 15 kW on a 10 kW inverter).

---

## EMS Algorithm Deep Dive

### Config Parameters (EMSConfig in ems.py)

| Parameter | Default | Description |
|---|---|---|
| grid_mode | "off" | off / from_grid / to_grid / both |
| battery_capacity_kwh | 10.0 | Effective capacity (coordinator scales nominal × SOH) |
| battery_charge_max_pct | 100.0 | Max SOC limit for charging |
| battery_discharge_min_pct | 20.0 | Min SOC floor for discharging |
| efficiency | 0.90 | Single-direction efficiency (round-trip = 0.81) |
| safe_power_kw | 5.0 | Charge/discharge power limit |
| inverter_max_power_kw | 10.0 | Total inverter power limit (model-specific) |
| consumption_est_kwh | 10.0 | Daily consumption (or 7-day rolling avg) |
| yesterday_deficit_kwh | 0.0 | Carried forward from previous day; reset on grid_mode change |
| reserve_target_pct | 0.0 | 0=dynamic, >0=fixed floor % |
| arbitrage_price_delta | 0.0 | Price spread threshold for full charge in 'both' mode |
| battery_cycle_cost_eur_kwh | 0.0 | Wear cost; added to min sell price |
| optimization_priority | "cost" | cost / longevity / self_consumption |
| block_export_on_negative_price | True | Skip sell scheduling at p < 0 |
| charge_to_full_on_negative_price | False | Schedule every p<0 slot (revenue at p<0); accepts forced PV curtailment |
| discharge_to_make_room_for_negative_price | False | Pre-emptively discharge before p<0 PV windows so PV can fill the battery |

The coordinator applies a SOH factor to nominal `battery_capacity_kwh`
before constructing `EMSConfig` — ems.py treats the capacity as
already-effective.  SOH is estimated from cumulative cycle throughput
(see "Cycle counting + SOH" below).

### Core Concept: Reserve Target

The algorithm does NOT try to fill the battery to 100%. It calculates a **reserve target** — just enough to survive overnight:

```
Dynamic (reserve_target_pct = 0):
  reserve_target = discharge_min_kwh + overnight_reserve
  where overnight_reserve = consumption_per_hour × (24 - sunset + sunrise)

Fixed (reserve_target_pct > 0):
  reserve_target = max(reserve_target_pct × capacity, discharge_min_kwh)
```

Example: 60 kWh battery, 35% min, 38.5 kWh/d consumption, sunset 19:00, sunrise 7:00
- min_kwh = 21 kWh
- overnight = (38.5/24) × 12h = 19.25 kWh → rounds to ~19.3 kWh
- reserve_target = 21 + 19.3 = 40.3 kWh (~67% of 60 kWh)

### Deficit Calculation (all modes)

```python
# Snapshot: how much are we short RIGHT NOW?
battery_shortfall = max(0, reserve_target - current_kwh)
snapshot_deficit = max(0, battery_shortfall - net_pv)

# Predictive: simulate SOC through all remaining slots
_, min_projected, max_projected = _project_soc_trajectory(...)
predictive_deficit = max(0, reserve_target - min_projected)

# Solar protection: if solar fills battery to 95%+ capacity, no grid needed
if max_projected >= max_battery_kwh * 0.95:
    predictive_deficit = 0.0

# Use the worse case
energy_deficit = max(snapshot_deficit, predictive_deficit) + carryover
```

### PV Confidence Factor

Scales forecast by actual-vs-expected production to handle cloudy days:

```python
pv_confidence = actual_produced_so_far / forecast_expected_by_now
# Clamped to [0.1, 1.0]
# Only activates when >1 kWh was expected (avoids early-morning noise)
```

### Net PV Surplus (Hourly Model)

Only counts hours where PV > consumption (battery can only charge from surplus):

```python
for each remaining hour:
    surplus = pv_hourly[hour] × pv_confidence - consumption_per_hour
    if surplus > 0:
        total_surplus += surplus
```

### Slot Selection

**from_grid**: Pick cheapest slots to cover deficit
**to_grid**: Pick most expensive slots to sell surplus above reserve
**both**: Do both, with profitability filter:
```python
min_sell_price = max_buy_price / (efficiency × efficiency)
# Only sell if revenue > round-trip cost
```

### Unified Two-Day Optimization

When tomorrow's prices are available, merges today+tomorrow slots, picks cheapest from combined pool. Safety swap ensures battery survives until tomorrow's first charge slot.

### Per-Slot SOC Validation

After selecting slots, simulates battery forward through every slot. Drops slots that would violate bounds:
- Charge pushing SOC > capacity → drop most expensive non-negative charge first, then negative-price charges if needed
- Discharge pulling SOC < minimum → drop least profitable discharge

Negative-price slots are preserved when PV alone would fill the battery
(surplus ≥ 90% of remaining capacity).  In that case the overflow is
PV-caused — pruning negative-price slots won't prevent it, and the
negative-price income is pure profit.  When PV is insufficient to cause
overflow on its own, negative-price charge slots are still pruned to
prevent forced grid export at penalty rates.

**Phantom-charge detection**: when a charge slot is scheduled at a
moment the battery is already at capacity (`soc_before >= capacity - 0.01`),
the inverter physically cannot store the energy (BMS rejects).  These
slots are dropped regardless of price — even negative-price slots,
because the income doesn't materialise if no grid energy is drawn.
The validation simulates forward through PV-only overflows (clamping
soc and continuing) instead of breaking on the first PV-caused
violation, which lets it detect phantom charges later in the day.

### Arbitrage Price Delta (both mode)

When `arbitrage_price_delta > 0` and `max_remaining_price - min_remaining_price >= delta`:
- Charges to **full capacity** instead of just reserve target
- Sellable energy recalculated based on full capacity
- Profitability filter still applies

### Headroom Constraint

Prevents over-scheduling when PV will fill the battery:
```python
headroom = max(0, max_battery_kwh - current_kwh - net_pv_surplus)
max_today_slots = floor(headroom / effective_per_slot)
# Negative-price slots pass through here (profitable to consume).
# SOC validation prunes only when PV alone wouldn't fill the battery.
```

### Negative-Price Strategies (charge_to_full / discharge_to_make_room)

Two orthogonal opt-in flags that change how negative-price slots are
handled.  Both are off by default and compose with all grid modes
(from_grid, to_grid, both).

**`charge_to_full_on_negative_price`** — acts *during* p<0 slots
- In `_schedule_from_grid` / `_schedule_both`, after the normal
  cheapest-slot selection, every negative-price slot in the remaining
  window is added to the charge set (deduplicated).
- `_validate_schedule_soc` is called with `keep_all_negative_charges=True`.
  When set, negative-price slots are exempt from overflow pruning even
  when PV alone wouldn't fill the battery (the legacy `pv_fills_battery`
  exemption is broadened to "all negatives").
- Phantom-charge slots (battery already at capacity entering the slot)
  are kept too — the inverter may try to charge and the BMS will gate
  it.  No harm; the schedule reflects user intent.
- Trade-off: the user accepts some forced PV curtailment in exchange
  for guaranteed revenue at every p<0 slot.

**`discharge_to_make_room_for_negative_price`** — acts *before* p<0 slots
- New helper `_select_discharges_for_pv_headroom` runs after charge
  selection.  Walks the SOC trajectory forward; whenever a negative-
  price slot with PV surplus would overflow the battery, schedules
  discharge in the *most expensive* earlier positive-price slot to
  create headroom.
- Validity: SOC must never drop below the absolute `min_kwh` floor
  (hardware safety), and end-of-day SOC must remain >= reserve_target
  (overnight coverage).  Temporary dips below reserve during the day
  are allowed — the negative-window PV will refill the battery.
- Works in `from_grid` mode (which normally never discharges) as well
  as `to_grid` / `both`.  In `both` mode, the make-room discharges
  are merged with the regular sell-side selection (sells take
  precedence on conflicting slots).

**Composition**: when both flags are on, make-room discharges are
scheduled before negative windows, then charge slots fire during the
negatives.  Net effect: maximum profit on negative-price days
(discharge at peak + buy at p<0 + PV fills the cleared battery).

Exposed as off/on select entities (`HA_FelicitySpecialModeSelect`)
in the UI.

---

## Safe Power Management (coordinator.py:1077-1193)

Monitors grid current per phase and adjusts inverter power:

| Condition | Action |
|---|---|
| Current > 95% of max_amperage | Reduce by 2 kW (emergency) |
| Current > 80% of max_amperage | Reduce by 1 kW (caution) |
| Current < 70% of max_amperage | Recover by 1 kW (up to user limit) |
| Current = 0 | Jump to user's Power Level |

Also detects external changes (user adjusting via inverter app).

---

## Frontend Card (ha_felicity_ems.js)

### Display Elements
- Battery indicator (10-segment, color-coded by SOC)
- Price chart (canvas) — bars per slot, colored by action
- SOC trajectory line (blue dotted)
- Threshold line (red dashed)
- PV stats (actual, remaining, forecast today, forecast tomorrow)
- Schedule stats (charge/discharge counts, planned kWh, reserve)

### Interactive Controls
- Grid Mode dropdown (off/from_grid/to_grid/both)
- Price Mode dropdown (manual/auto)
- Max SOC / Min SOC dropdowns
- Power Level slider (live preview)
- Price Threshold Level slider (live preview)

### Client-Side Simulation
Mirrors coordinator logic for instant preview when dragging sliders. Uses `sim_params` from `schedule_status` sensor attributes.

**Backend as single source of truth**: For both today and tomorrow views,
when no slider or slot overrides are active, the card uses
backend-provided `slot_schedule` / `slot_schedule_tomorrow` (with actions)
and `backend_soc_trajectory` / `backend_soc_trajectory_tomorrow` for the
SOC line. Client-side simulation (`_simulateSchedule`,
`_simulateScheduleTomorrow`, `_computeSocTrajectory`) only runs when the
user is actively previewing via sliders or manual slot clicks.

### Past Slot History
Fetches `energy_state` history from HA API (throttled 60s), shows what actually happened vs what was planned.

---

## Entity Reference

### Configuration Entities (stored in entry.options)

| Entity | Type | Range | Default | Description |
|---|---|---|---|---|
| grid_mode | select | off/from_grid/to_grid/both | off | Main EMS switch |
| price_mode | select | manual/auto | manual | Price threshold mode |
| safe_power_management | select | auto/on/off | auto | Amperage protection |
| power_level | number | 1-N kW (model max) | 5 | Charge/discharge power |
| price_threshold_level | number | 1-10 | 5 | Manual price level |
| battery_charge_max_level | number | 30-100% | 100 | Max SOC for charging |
| battery_discharge_min_level | number | 10-70% | 20 | Min SOC for discharging |
| battery_capacity_kwh | number | 1-100 kWh | 10 | Usable battery capacity |
| efficiency_factor | number | 0.70-1.00 | 0.90 | Round-trip efficiency |
| daily_consumption_estimate | number | 0-100 kWh | 10 | Fallback consumption |
| reserve_target_pct | number | 0-100% | 0 | Fixed reserve floor (0=dynamic) |
| arbitrage_price_delta | number | 0-0.50 €/kWh | 0 | Price spread for full charge |
| max_amperage_per_phase | number | 10-63 A | 16 | Grid current limit |
| voltage_level | number | 48-60 V | 58 | Charge voltage setpoint |
| discharge_min_voltage | number | 48-55 V | 50 | Discharge voltage floor |
| charge_to_full_on_negative_price | select | off/on | off | Charge at every p<0 slot (revenue) |
| discharge_to_make_room_for_negative_price | select | off/on | off | Pre-discharge before p<0 PV windows |

All `HA_FelicityInternalNumber` configuration entities render as
input boxes (`NumberMode.BOX`) so users can type precise values.
Sliders are awkward for fractional / fine-grained settings like
`efficiency_factor` (step 0.01) or `arbitrage_price_delta` (step
0.01 €/kWh).  Pass `mode=NumberMode.SLIDER` to the constructor for
entities that benefit from scrubbing.

### Key Sensor Entities

| Sensor | Description |
|---|---|
| energy_state | Current state: charging/discharging/idle |
| schedule_status | Optimizer status + rich attributes for card |
| charge_likelihood | on_track/tight/at_risk/insufficient |
| current_price | Real-time electricity price |
| price_threshold | Calculated threshold |
| safe_max_power | Current power after safety adjustment |
| pv_forecast_today/remaining/tomorrow | Solar forecasts |
| weekly_avg_consumption | 7-day rolling average |

---

## Known Issues and Gotchas

### 1. Code Duplication Between coordinator.py and ems.py
The coordinator has ~300 lines of scheduling logic that duplicates ems.py. Changes to one must be manually mirrored to the other. This has caused bugs (e.g., missing solar protection in coordinator).

### 2. Frontend Simulation Divergence
The JS card has a simplified version of the algorithm. It doesn't do SOC
trajectory projection or per-slot validation. Complex scenarios (predictive
deficit, SOC validation pruning) won't match the backend.

Settings now mirrored in the card's simulation: `reserve_target_pct`,
`arbitrage_price_delta`, PV-aware headroom for negative-price slots.
Backend-computed SOC trajectory (`backend_soc_trajectory` in `sim_params`)
is now used for the today-view line chart when no slider overrides are
active. The card falls back to client-side trajectory only during live
slider preview. Still missing in client-side sim: validation pruning, PV
confidence sliding window. The backend-provided `slot_schedule` attribute
is authoritative — the card overlays its
own simulation only for live slider previews.

### 3. PV Confidence Can Over-React
The confidence factor can drop to 0.1 early in the day on partially cloudy mornings, causing excessive grid charging. It doesn't recover when clouds clear.

### 4. Consumption Estimate Sensitivity
The algorithm uses consumption_est/24 for hourly drain — assumes flat consumption. Houses with evening peaks (cooking, heating) may see under-predicted evening drain.

### 5. Anti-Conflict Guard Hysteresis — IMPLEMENTED
Previously the 200W grid import check suppressed discharge on a single
tick, causing flipper behaviour (discharge → idle → discharge every
~16s) on short load spikes (kettle, microwave, EV start).  Two writes
per flip is brutal on the inverter and customers notice.

Now uses thresholded hysteresis:
- Small/moderate import (200–2000W) must persist for ≥ 2 consecutive
  cycles (≈ 32s) before suppression triggers.
- Large import (> 2000W) suppresses immediately (genuine sustained
  draw like EV charging or oven preheat).
- After suppression ends, a 60-second cooldown blocks re-suppression
  so the inverter stabilises before the next decision.
- Each cycle now logs the grid_power + state decision at DEBUG level
  so the flipper pattern is easy to spot in retrospect:
  `State decision: desired=X, current=Y, soc=%, price=, threshold=, grid_power=W`

### 6. Generator-Port Solar Workaround
TREX-25/50 with micro-inverters on the generator port need special handling. PV registers read 0, falling back to generator_day_cost_energy. Both backend and frontend handle this but it's fragile.

### 7. Forecast.Solar `wh_hours` date handling
`_retrieve_pv_forecast` now filters `wh_hours` entries by today's date before
bucketing them into `pv_hourly_kwh[hour]`. Without that filter, a multi-day
forecast would sum today's + tomorrow's + day-after's values into the same
hour slot. This was especially painful at midnight when stale
`state.state` combined with hour-merged buckets broke the schedule. When
`state.state` still reports the previous day's stale total, the coordinator
falls back to the filtered hourly sum.

### 8. Midnight should not force inverter to idle
The day-rollover block in `_async_update_data` used to unconditionally
call `_transition_to_state("idle")` and then skip the normal cycle for
that tick.  This cancelled valid charge/discharge actions that should
continue across midnight (e.g., a customer selling overnight to clear
the battery before negative-midday PV refills it next day).  The block
now does only the bookkeeping (yesterday deficit, daily consumption,
SOC history reset, slot overrides rotation) and falls through to the
normal cycle, which re-determines the desired state and only writes a
transition if the state actually changes.

---

## Algorithm Assessment and Improvement Recommendations

### Current Strengths
1. **Solar-first design** — Grid is a last resort, not default behavior
2. **Two-day optimization** — Unified slot selection across today+tomorrow is elegant
3. **Multiple safety layers** — SOC validation, headroom cap, profitability filter, anti-conflict guard
4. **PV confidence scaling** — Handles forecast uncertainty reasonably well
5. **Reserve target concept** — Charges only what's needed, not maximum

### Stability Improvements (High Priority)

#### A. Eliminate Code Duplication
**IMPLEMENTED**: coordinator.py now delegates entirely to `ems.calculate_schedule()` via `EMSConfig` + `EMSState` dataclasses. ~340 lines of duplicated scheduling logic removed.

#### B. PV Confidence Recovery — IMPLEMENTED
Sliding window approach: `_calculate_pv_confidence()` returns `max(cumulative, recent_3h_window)` so confidence recovers when weather improves mid-day.

**EMA smoothing**: now also takes `previous_confidence` and applies an
exponential moving average (alpha=0.3) to dampen single-hour weather
oscillations.  Coordinator passes `_last_pv_confidence` on every tick.

**Forecast.solar fallback**: when the forecast service is unavailable,
`pv_fallback_today_kwh` is supplied by the coordinator (rough daylight-
extrapolation from today's actual production) so the algorithm doesn't
default to PV=0 and over-aggressively grid-charge.

#### C. Consumption Profile Awareness — IMPLEMENTED
Hourly consumption profiles from 7-day HA recorder history. `EMSState.consumption_hourly_kwh` provides per-hour averages used in `_project_soc_trajectory()` and `_validate_schedule_soc()`. Coordinator records hourly breakdown at midnight. Frontend card also uses profiles.

#### C2. SOC History Display — IMPLEMENTED
Coordinator records battery SOC at each slot boundary in `_soc_history`. Frontend draws solid line for actual past SOC, dotted line for projected future.

#### C3. Cheapest-First Charge Execution — IMPLEMENTED
`_determine_energy_state` defers execution at a scheduled charge slot if a
later scheduled charge slot has a lower price. Prevents expensive-early-slot
commitment when the deficit would have shrunk (e.g., PV confidence
recovering) by the time the cheaper late slot would execute. Negative-price
slots are exempt.

**Deferral stall prevention**: never defer when SOC is at/below
reserve_target (battery needs charging now, regardless of price), and only
defer when the future slot is cheaper by ≥ 1¢/kWh.  Without these guards,
recovering PV confidence could keep deferring the same slot indefinitely
until the day ended without any charging.

#### C4. Schedule-Status Attribute Caching — IMPLEMENTED
`HA_FelicityScheduleStatusSensor` caches `extra_state_attributes` and
rebuilds only in `_handle_coordinator_update`. Avoids rebuilding the
attribute dict (with 96-slot arrays, hourly PV/consumption maps, SOC
history) on every HA state read — fixes a `helpers/entity.py:1214` slow-
update warning.

### Correctness Improvements (Medium Priority)

#### D. SOC Trajectory in Frontend — IMPLEMENTED
Backend now computes `soc_trajectory` (list of SOC% per slot) in
`ScheduleResult` via `_compute_scheduled_soc_trajectory()`. Passed to
frontend via `sim_params.backend_soc_trajectory`. The card uses this
authoritative trajectory for the today-view line chart when no overrides
are active, falling back to client-side simulation only during live slider
preview. `reserve_target_pct` and `arbitrage_price_delta` are also mirrored
in both `_simulateSchedule` and `_simulateScheduleTomorrow`.

#### D2. Negative-Price PV Headroom Protection — IMPLEMENTED
`_validate_schedule_soc` no longer fully exempts negative-price charge
slots from overflow pruning. When PV production combined with grid charging
would push SOC above capacity, negative-price slots are dropped (least
negative first, after non-negative slots). This prevents the inverter
from being forced to export PV surplus to grid at penalty rates because
it cannot disconnect PV panels. The headroom cap in
`select_unified_charge_slots` still allows all negative-price slots through;
the downstream SOC validation handles the pruning.

#### D3. Available Slots Counter Clarification — IMPLEMENTED
`available_slots_at_threshold` now counts only today's remaining slots
(not tomorrow's). Tomorrow's slots are still included in the energy
capacity calculation for charge likelihood. In `both` mode,
`cheap_slots_remaining` now counts only charge slots (not charge +
discharge combined).

#### E. Arbitrage in Both Mode — Charge Slot Selection
**Problem**: When arbitrage_price_delta triggers full charging, the algorithm uses the same "cheapest slots" logic. But for arbitrage, you specifically want cheap-buy + expensive-sell pairs.
**Fix**: When arbitrage is active, only select charge slots that are cheaper than the cheapest available sell slot minus round-trip losses. This ensures every charged kWh has a profitable destination.

#### F. Cross-Validate SOC Between Coordinator and Inverter
**Problem**: The coordinator tracks SOC from the inverter register but doesn't validate it against expected changes. If a register read fails or returns stale data, the algorithm could make wrong decisions.
**Fix**: After a charge/discharge cycle, compare expected SOC change with actual. Log a warning if they diverge by >5%. This helps catch communication issues early.

### Feature Improvements (Lower Priority)

#### G. Dynamic Overnight Reserve by Season
**Problem**: The overnight reserve calculation uses current sunset/sunrise. On equinox transitions, the reserve changes rapidly.
**Fix**: Smooth the overnight hours estimate over a 7-day average to avoid sudden jumps.

#### H. Multi-Day Optimization
**Problem**: Only looks 1 day ahead. Can't optimize for weekend patterns or weather fronts.
**Fix**: When 2-3 day forecasts are available, extend the unified slot pool. Complexity increases significantly though.

#### I. Grid Export Limit Awareness
**Problem**: Some grid connections have export limits (e.g., 5 kW max feed-in). The algorithm doesn't account for this when scheduling discharge.
**Fix**: Add a `grid_export_limit_kw` config. When discharging, cap the power to this limit and adjust slot count accordingly.

#### J. Time-of-Use Tariff Support
**Problem**: Algorithm assumes dynamic (hourly) pricing. Fixed time-of-use tariffs (peak/off-peak) aren't directly supported.
**Fix**: Allow a virtual price schedule when no Nordpool entity is configured. Map time-of-use periods to synthetic price slots.

### Priority Order
1. **A (Eliminate duplication)** — Prevents future bugs, reduces maintenance
2. **B (PV confidence recovery)** — Directly fixes over-charging on variable days
3. **C (Consumption profiles)** — Improves prediction accuracy
4. **D (Frontend alignment)** — User trust in the preview
5. **E-J** — Feature enhancements as needed

---

### Multi-Objective + Lifecycle Fixes (Implemented)

A second wave of fixes addressing pitfalls flagged in a professional EMS
review.  These extend the algorithm beyond pure cost minimisation and add
runtime safeguards.

| ID | Fix | Where |
|---|---|---|
| #1 | Cheap-slot deferral stall guards (SOC-floor + 1¢ gap) | coordinator |
| #3 | EMA smoothing on PV confidence (alpha=0.3) | ems |
| #4 | Forecast.solar fallback (`pv_fallback_today_kwh`) | ems + coordinator |
| #5 | `block_export_on_negative_price` config | ems |
| #6 | Modbus stale-data guard (refuse to plan on stale data) | coordinator |
| #8 | Skip recalc when input hash + slot unchanged | coordinator |
| #9 | Re-validate SOC after slot-override merge | coordinator |
| #10 | Reset `yesterday_deficit` on grid_mode change | coordinator |
| #12 | `optimization_priority` (cost / longevity / self_consumption) | ems |
| #13 | SOH tracking from cycle throughput (persisted) | coordinator |
| #14 | `battery_cycle_cost_eur_kwh` in profitability filter | ems |

**Cycle counting + SOH (#13)**: coordinator's `_track_cycle_throughput`
accumulates positive/negative SOC deltas (kWh, jitter floor 0.5%).
Equivalent full cycles = min(charged, discharged) / capacity.
SOH curve: `max(0.80, 1.0 - cycles × 5e-5)` — conservative LFP.
Persisted in the consumption store so it survives restarts.  The
SOH factor multiplies nominal `battery_capacity_kwh` before the
`EMSConfig` is constructed; ems.py treats it as already-effective.

**`optimization_priority`**:
- `cost` (default): legacy behaviour, minimise grid spend.
- `longevity`: enforces a 0.05 €/kWh cycle-cost floor (regardless of
  the explicit `battery_cycle_cost_eur_kwh` setting).
- `self_consumption`: multiplies the dynamic overnight reserve by
  1.25× to keep more PV-stored energy in the battery for self-use
  (less grid-export of solar).

**Override SOC validation (#9)**: after merging `slot_overrides` into
`scheduled_slots`, the coordinator re-runs `_validate_schedule_soc`.
Manually-added charge slots that would overflow the battery, or
discharge slots that would drain below the reserve, are dropped
(with a log entry).  Previously a user click could set up an
infeasible schedule.

**Skip-recalc-when-unchanged (#8)**: hash of (grid_mode, SOC,
prices, PV forecast, deficit, overrides, power) — when unchanged
AND we're still in the same slot, the algorithm is not re-run.
Recomputed on slot boundaries.  Cuts CPU on the 10-second tick.

### Deferred Items (Need Separate Iteration)

These were flagged in the same review but require larger changes
or design discussion before implementation:

#### #2 MILP Optimizer
Current scheduler is greedy (cheapest-first).  A mixed-integer linear
programme over the 24-48h horizon would find provably optimal
schedules — typically 5-15% better on complex price patterns.
**Why deferred**: requires adding `pulp` or `scipy.optimize`
(scipy is heavy), a fundamentally different approach, and careful
tuning of the objective function to reflect the existing
constraints (SOC bounds, headroom, anti-conflict).  Expected
2-week effort with substantial test coverage.

#### #7 Frontend Simulation Drift
The JS card has a simplified `_simulateSchedule` that doesn't fully
match the backend (no SOC trajectory pruning, no PV-overflow
exemption).  Slider previews show *roughly* the expected schedule
but can diverge from what the backend will produce.
**Partial mitigation**: backend is authoritative when no slider
overrides are active.  **Full fix** would either remove client-side
simulation entirely (slider preview becomes a backend round-trip,
~1s lag) or align the simulation logic exactly (significant code
duplication risk, same drift pattern as before refactor #A).
Needs UX discussion before action.

#### #11 EV / Heat Pump / Smart-Appliance Coordination
A modern EMS orchestrates flexible loads (EV charging, heat pumps,
water heaters) alongside the battery.  A heat pump with thermal
mass is essentially a "thermal battery" that can shift consumption
to cheap hours.
**Why deferred**: requires new entity contracts (which heat
pump?  which EV charger?), control logic to set their schedules,
and a model of their constraints (min/max power, thermal lag,
SOC-equivalent).  Each integration would be its own design.
A reasonable starting point would be a `flexible_load_kw` config
that the algorithm adds to the consumption estimate during
specific time windows.

---

## Testing

Tests are in `tests/test_ems.py` (146 tests). They import `ems.py` directly (bypassing HA dependencies) and test the pure scheduling functions.

```bash
# Run all tests
python -m pytest tests/test_ems.py -v

# Run specific test class
python -m pytest tests/test_ems.py::TestSolarProtection -v
```

**Test coverage areas**:
- All three modes (from_grid, to_grid, both)
- Sunny/cloudy/negative price scenarios
- Cross-day optimization
- SOC validation
- Headroom constraints
- PV confidence
- Reserve target override
- Arbitrage price delta
- Slot granularity (24/48/96 slots)
- Inverter max power cap (per-model)
- Both-mode sell with charge energy (low PV confidence)
- Integration tests: real-world TREX-5/10/25/50 scenarios
- Tomorrow schedule computation and SOC trajectory

**Not tested**: coordinator.py runtime logic (requires HA mocking). This is a gap — when the coordinator diverges from ems.py, tests won't catch it.

---

## Quick Reference: Debugging a Scheduling Issue

1. Check `schedule_status` sensor attributes for `sim_params` (battery state, PV, consumption)
2. Look at `self_consumption_reserve` and calculate `reserve_target`
3. Check `pv_actual_today_kwh` vs forecast → confidence factor
4. Compare `net_pv_kwh` with actual remaining PV
5. Look at `yesterday_deficit_kwh` — may be inflating today's target
6. Check `slot_schedule` for what slots were selected and their prices
7. Enable debug logging: `custom_components.ha_felicity.ems` and `custom_components.ha_felicity.coordinator`
