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
├── milp.py                  # Optional MILP/LP scheduler (solver-based alternative to greedy)
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

## Optional MILP Scheduler (`milp.py`)

The greedy scheduler picks charge and sell slots in separate passes,
which produces subtle bugs at the seams between features (e.g. allocating
all charge slots to a cheaper tomorrow while today's expensive sell slots
starve for energy — the "sell coverage" bug). The MILP scheduler models
the whole remaining-today + tomorrow horizon as a single optimisation and
lets a solver find the cost-optimal plan, so cross-slot/cross-day
interactions can't fall through the cracks.

**Engine selection**: `scheduler_engine` config option (`greedy` default /
`milp`), exposed as a select entity ("Scheduler Engine") and in the EMS
card's Advanced settings ("Scheduler"). When `milp`,
`calculate_schedule` calls `_run_milp_or_none()`, which tries the solver
and **silently falls back to greedy** on any failure (pulp missing,
infeasible, timeout, non-optimal). The greedy path stays the safety net —
MILP failures never break the EMS.

**Model** (`milp.solve_schedule`, a pure LP):
- Horizon = today's remaining slots + all of tomorrow (when prices known).
- Per-slot continuous vars: `c[k]` (grid kWh to charge, ≤ inverter
  headroom after PV), `d[k]` (battery kWh discharged to sell, ≤ safe
  power), `spill[k]` (PV curtailment), `soc[k]`.
- SOC dynamics: `soc[k] = soc[k-1] + net_pv[k] - load[k] + eff·c[k] - d[k] - spill[k]`.
- Bounds: `soc_min ≤ soc[k] ≤ soc_max`; `soc[end] ≥ reserve_target`;
  `soc[midnight] ≥ reserve_target` (prevents cross-day deferral).
- Objective: minimise `Σ price·c − Σ price·eff·d + cycle_cost·Σd − terminal_value·soc[end]`.
  Terminal value boosted for `self_consumption` (P90 price × efficiency).
- Grid mode gates charge/discharge; `block_export_on_negative_price` and
  `arbitrage_price_delta` become per-slot price gates.
- Round-trip efficiency loss makes charging+discharging the same slot
  never optimal, so no binary "exclusive" var is needed — stays a fast LP.
- SOC-bound constraints prevent overflow / phantom charging for free.

**Integration surface is tiny**: the MILP produces both today's and
tomorrow's `scheduled_slots` from the unified 2-day horizon. When
tomorrow slots are provided by the MILP, `_compute_tomorrow_schedule`
(greedy reconstruction) is skipped — only the SOC trajectory is
computed from the MILP's decisions. Flexible-load overlays are still
computed by `ems.py`. `milp.py` reads `EMSConfig`/`EMSState` by
attribute and takes precomputed reserve_target + pv_confidence —
**zero import dependency on ems.py**, keeping the fallback bullet-proof.

**Blocking I/O**: PuLP writes a `.mps` model file and runs the CBC solver
as a subprocess — both are blocking operations.  The coordinator runs
`calculate_schedule` in an executor thread (`async_add_executor_job`)
so the MILP never blocks the HA event loop.

**Dependency**: `pulp>=2.7.0` (bundles the CBC solver). Lazy-imported so
`ems.py` works without it. On hardware where the CBC binary won't run, the
fallback keeps the EMS functional on greedy.

**Tests**: `TestMILPScheduler` in `test_ems.py` (skipped when pulp absent).
Loads `milp.py` via the same spec-loader trick as `ems.py` and registers
it in `sys.modules` so the lazy `import milp` resolves.

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

**Rule 1 registers always written by `_transition_to_state`**:
`econ_rule_1_enable` (0/1/2), `_voltage`, `_soc`, `_power`, `_start_day`,
`_stop_day`.

**Rule 1 registers written only when auto mode is enabled** (via
`_apply_rule1_auto_settings`, called every cycle, idempotent — only
writes when the register already differs from the target):
- `rule1_time_window=auto` → `econ_rule_1_start_time=00:00`,
  `econ_rule_1_stop_time=23:59` (Felicity's 24-hour convention; the
  firmware doesn't accept stop=00:00 or stop=24:00).
- `rule1_weekday=auto` → `econ_rule_1_effective_week=0x7F` (all 7 days).

Both default to `manual` so the integration doesn't touch user-set
values unless they explicitly opt in.  When still on `manual` and the
inverter's rule 1 window is restrictive, the inverter silently ignores
the enable command outside the window — the EMS plans to act, writes
the register, and nothing happens.  The `rule1_window_warning` check
surfaces this in the EMS card.

**Rule 1 window warning** (`coordinator._check_rule1_window_conflict`):
runs every cycle.  Builds the set of intended action slots (auto mode:
`scheduled_slots` + tomorrow; manual mode: today's remaining slots
crossing the threshold in the active direction) and checks each against
the rule 1 time-of-day window and effective-weekday mask read back from
the inverter.  `start_time == stop_time` is treated as "all day"; a full
0x7F mask as "all days".  Any mismatch is exposed as the
`rule1_window_warning` attribute on `schedule_status` and rendered as a
banner in the EMS card.  Weekday bit mapping: inverter bit0=Sunday..
bit6=Saturday, mapped from Python via `isoweekday() % 7`.

**Charge deferral** (`coordinator._determine_energy_state`): when the current
slot is flagged `charge` but a later scheduled charge slot has a cheaper
price (non-negative slots only), execution returns `idle` instead. The next
10s re-evaluation cycle adjusts the deficit naturally, so charging shifts to
the cheapest scheduled slot. This compensates for deficit shrinking as PV
confidence recovers mid-day — without it, early expensive slots would
execute while later cheaper slots got dropped from a re-plan.

### State Transitions (coordinator.py `_transition_to_state`, ~1425-1482)

| State | econ_rule_1_enable | Voltage | SOC | Power |
|---|---|---|---|---|
| charging | 1 | voltage_level (default 58V) | charge_max (default 100%) | safe_max_power (W) |
| discharging | 2 | discharge_min_voltage (default 50V) | discharge floor (see below) | safe_max_power (W) |
| idle | 0 | *(not written)* | *(not written)* | *(not written)* |

**Discharge SOC floor**: in auto mode (grid_mode active) the discharging
SOC register is written from the *computed* `_reserve_target_pct`
(the planned reserve target), not the raw `battery_discharge_min_level`.
This makes the inverter's hardware floor match the schedule's intended
reserve rather than the absolute minimum. When the EMS is off it falls
back to the user's `discharge_min` setting.

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
| arbitrage_price_delta | 0.0 | Min buy→sell spread to trade in 'both' mode (>0 gates sells at buy+delta; 0 = automatic profitability check) |
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

**Reserve exposed even when scheduler is disabled**: `calculate_schedule`
short-circuits when `grid_mode == "off"` (or no price data), but it still
computes `self_consumption_reserve` and `reserve_target_pct` before the
early return. Otherwise the frontend's "night target" line and "overnight
need" stat would collapse to the bare discharge-min floor (the line would
read e.g. "35% night target" — just Min SOC — instead of the true dynamic
target). The card also has a client-side fallback (`_overnightReserveKwh`,
mirroring `calculate_self_consumption_reserve`) that kicks in when the
backend value is 0 — relevant in `price_mode = manual`, where the optimizer
isn't run at all.

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

When tomorrow's prices are available, merges today+tomorrow slots, picks cheapest from combined pool. The tomorrow-side reserve target honours `reserve_target_pct` and the `self_consumption` 1.25× boost, same as today's.

**Intentionally no today↔tomorrow safety swap**: the inverter switches the
house to grid passthrough once SOC hits `min_kwh`, so the battery can't
drain below the floor from consumption.  Forcing expensive today slots to
"bridge" the night would cost more than consuming from grid (round-trip
losses on the same prices); charging defers to tomorrow's cheaper slots.
`test_safety_swap` pins this.

**Exception — `self_consumption` priority**: the self-sufficiency strategy
overrides the no-swap rule.  `select_unified_charge_slots` forces today's
`energy_deficit` onto today's slots even when tomorrow is cheaper.  Without
this, the user sees "tomorrow never comes" — every day defers to the next.
The MILP enforces this via a midnight SOC boundary constraint
(`soc[midnight_slot] >= reserve_target`).
`TestSelfSufficiencyTodayFirst` pins both.

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

### Arbitrage Price Delta (both mode) — the TRADE TRIGGER

`arbitrage_price_delta` is the explicit minimum buy→sell spread required
to trade.  Two regimes:

**delta > 0** — the delta *replaces* the automatic profitability check:
- Charge-to-full activates only when `max_remaining - min_remaining >= delta`
- Every sell slot must clear `buy_reference + delta`, where buy_reference =
  the most expensive scheduled charge slot (or the cheapest remaining price
  when nothing is scheduled to buy).  When no slot clears the bar, **no
  sells are scheduled at all** — "don't trade unless the spread is ≥ X".
- The round-trip + cycle-cost floor still applies on top (the gate is
  `max(round_trip_floor, ref_buy + delta)`).

**delta == 0 (default)** — automatic profitability check (legacy): trade
whenever the peak price covers round-trip losses on the cheapest buy
(`peak >= cheapest / efficiency²`), with the cycle-cost filter on top.

History: before June 2026 the delta could only *widen* activation (the
automatic check ran unconditionally first), so it could never suppress
selling — users setting 20 ct were still sold at 9 ct spreads.  The
sell-gate semantics fixed that.  Mirrored in `_compute_tomorrow_schedule`
and in both JS sims (`_simulateSchedule`, `_simulateScheduleTomorrow`).

### Headroom Constraint

Prevents over-scheduling when PV will fill the battery:
```python
headroom = max(0, max_battery_kwh - current_kwh - net_pv_surplus)
max_today_slots = floor(headroom / effective_per_slot)
# Negative-price slots pass through here (profitable to consume).
# SOC validation prunes only when PV alone wouldn't fill the battery.
```

### Power-Aware Charge Slot Selection

`select_unified_charge_slots` accumulates each candidate slot's
*actually-achievable* grid charge energy instead of a flat
`ceil(deficit / effective_per_slot)` count:

```python
grid_kw  = min(safe_power_kw, max(0, inverter_max_kw - pv_kw_at_hour))
slot_kwh = grid_kw × slot_hours × efficiency
# accumulate cheapest-first until deficit covered
```

Consequences:
- High charge power → deficit covered by just the few cheapest slots
  (fixes "starts charging too early, already full when the cheapest
  slots arrive").
- Midday slots where PV saturates the inverter deliver 0 grid kWh and
  are **skipped** (previously they were counted as full-power slots,
  inflating the selection).
- PV-throttled slots count their reduced energy, so enough companions
  are selected to actually cover the deficit.
- Falls back to the flat count when power params aren't supplied
  (legacy/test callers).  The tomorrow-schedule reconstruction in
  `_compute_tomorrow_schedule` mirrors the same accumulation.
- The JS card's client-side slider preview still uses the flat count
  (documented divergence; backend is authoritative when no sliders
  are active).

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

## Safe Power Management (coordinator.py `_check_safe_power`, ~1307-1424)

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
- Reserve target line (red dashed) — computed reserve floor as SOC%
- Threshold line (yellow dashed)
- PV stats (actual, remaining, forecast today, forecast tomorrow)
- Schedule stats (charge/discharge counts, planned kWh, reserve)

### Tomorrow PV-Only Preview
When tomorrow's prices are not yet available (typically before ~13:00)
but a PV forecast exists, the Tomorrow tab is enabled with a solar-only
preview instead of being greyed out.  Shows:
- Warm-yellow PV production bars (kWh per slot, distributed 06-18h)
- Baseline SOC trajectory (PV + consumption, no grid actions)
- Reserve target line — user can immediately see whether solar alone
  keeps the battery above overnight reserve
- Banner: "Without grid actions · Prices expected ~13:00"
- No price axis, no charge/discharge actions, no slot click overrides
When prices arrive the tab automatically switches to the full schedule
view with all normal features.

### Strategy Presets
The card shows a Strategy dropdown as the primary user-facing control.
Selecting a strategy auto-configures the underlying knobs. Individual
knobs remain accessible behind an "Advanced settings" toggle.

| Strategy | grid_mode | optimization_priority | Notes |
|---|---|---|---|
| Save Money | from_grid | cost | Buy cheap grid power, no selling |
| Self-Sufficiency | from_grid | self_consumption | Maximize PV self-use (1.25× reserve) |
| Battery Care | from_grid | longevity | Minimize cycling (0.05 €/kWh floor) |
| Trader | both | cost | Buy cheap, sell expensive (auto profitability) |
| Custom | (unchanged) | (unchanged) | User manages all knobs manually |

### Schedule Reason ("Why" Line)
Below the chart, a one-line explanation of the current schedule decision
is shown. Examples:
- "Charging 3 slots (up to 0.120/kWh) to cover 4.2 kWh deficit"
- "Not trading: spread 0.09 < your 0.20 minimum"
- "Buying 2 slots (up to 0.08), selling 3 (from 0.28) — spread 0.200/kWh"
- "Solar fills battery to 95% — no grid charging needed"
- "EMS is off — select a strategy to start optimizing"

Backend populates `schedule_reason` on `ScheduleResult`. Exposed as
`schedule_reason` attribute on the `schedule_status` sensor. The card
reads it from `_getAttr("schedule_status", "schedule_reason")`.

### Interactive Controls
- **Strategy dropdown** (save_money/self_sufficiency/battery_care/trader/custom)
- **Advanced settings toggle** — shows/hides:
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

## Flexible Load Control

Up to 3 controllable loads (EV charger, boiler, pool pump, etc.) can be
managed by the EMS.  Each load has an enable toggle, switch entity, rated
power, and shed priority.  Load slot 1 has additional EV charger support
(current stepping).

### Architecture

Loads use an **overlay** approach — they're scheduled into the same cheap /
negative-price / PV-surplus slots as the battery, but they don't affect the
battery schedule itself.  They're additive consumption.

**Scheduling**: `_schedule_flexible_loads()` in `ems.py` activates loads
during:
- Slots where price ≤ threshold (cheap)
- Negative-price slots
- PV-surplus slots (hourly PV > hourly consumption)
- Battery charge slots (already identified as cheap)

**Actuation**: `coordinator._actuate_flex_loads()` runs every 10s cycle,
turning loads on/off via `hass.services.async_call` (switch.turn_on/off).
EV charger current is set via its current_entity (number.set_value or
select.select_option).

**Fault isolation**: `_actuate_flex_loads` is wrapped in a top-level
try/except at the call site (line 2212) and per-load try/except within
the method, so a flex-load failure (entity unavailable, service call
timeout, bad config) never kills the main coordinator update cycle.
Without this, a charger entity going offline would cause `UpdateFailed`,
making all entities unavailable and taking the inverter out of eco mode.
`_safe_power_shed_loads` has the same isolation.

**Safe power priority chain** (in `_check_safe_power`):
1. EV charger current step-down (one step per tick)
2. Binary load shed (3=least important, shed first; 1=most important, shed last)
3. Battery power reduction (existing behavior, last resort)

### Configuration (per load)

| Option | Type | Load 1 | Load 2-3 |
|--------|------|--------|----------|
| `flexible_load_N_enabled` | select (off/on) | yes | yes |
| `flexible_load_N_name` | text | yes | yes |
| `flexible_load_N_switch_entity` | text (entity ID) | yes | yes |
| `flexible_load_N_power_kw` | number (0.5-25 kW) | yes | yes |
| `flexible_load_N_priority` | number (1-3) | yes | yes |
| `flexible_load_1_current_entity` | text (entity ID) | EV only | — |
| `flexible_load_1_current_steps` | text ("6,10,13,16,20,25") | EV only | — |
| `flexible_load_1_phases` | number (1-3) | EV only | — |
| `flexible_load_1_voltage` | number (110-400V) | EV only | — |
| `flexible_load_1_default_current` | number (6-32A) | EV only | — |
| `ev_charge_strategy` | select (smart/solar_only/cheap_only/always_on) | EV only | — |

### EV Charge Strategy

`ev_charge_strategy` (config option + select entity, gated on
`flexible_load_1_switch_entity`) shapes *when* the EV charger runs in
`_schedule_flexible_loads`.  It applies only to the load with
`is_ev_charger`; loads 2-3 always use the `smart` overlay.

| Strategy | Slots scheduled |
|---|---|
| `smart` (default) | cheap ∨ negative ∨ PV-surplus ∨ battery-charge |
| `solar_only` | PV-surplus only |
| `cheap_only` | at/below threshold ∨ negative |
| `always_on` | every remaining slot (safe-power then throttles current) |

`always_on` differs from EV Boost: it's a persistent schedule at the
scheduled current (not time-limited, not forced to max current).  Plumbed
through `EMSConfig.ev_charge_strategy` → `_schedule_flexible_loads`.

### Frontend

- Cyan strip at bottom of bars where loads are scheduled
- "loads" entry in legend (only when loads configured)
- "N/M loads active" stat in the stats row
- **Flexible Loads panel** (`_renderFlexLoads`, below the stats row, only
  when loads configured): one row per load showing on/off state (dim when
  off, cyan glow when on), the live power draw with a fill bar
  (`active_power_kw / max_power_kw`), the EV charger's active current
  detail (`A · φ · V`), a `BOOST` chip during EV boost, and a colour-coded
  shed-priority badge ("Sheds 1st/2nd/last").  Header shows total live kW.
  Footer reminds that loads are shed before the battery power is reduced.
- `flex_load_schedule`, `flex_load_states`, `flex_load_configs` in
  `schedule_status` attributes.  `flex_load_configs` entries carry
  `on`, `active_power_kw`, `max_power_kw`, `priority`, and (EV only)
  `current_a`, `phases`, `voltage` — built by
  `HA_FelicityScheduleStatusSensor._build_flex_load_attr`.  EV
  `active_power_kw` is the *real* draw (`current_step × voltage × phases`),
  falling back to the startup current when no step has been set yet.

### EV Boost Override

One-press "+1 hour" override that forces the EV charger on at maximum
current, regardless of the EMS schedule.  Designed for "I need to leave
soon" scenarios.

**Buttons** (`button.py`):
- `HA_FelicityEVBoostButton` — each press adds +1h from
  `max(now, current_boost_end)`, so presses stack.
- `HA_FelicityEVBoostCancelButton` — immediately cancels any active boost.

**Coordinator behaviour** (`coordinator.py`):
- `_ev_boost_until_ts`: epoch timestamp; 0 = inactive.
- `ev_boost_active` / `ev_boost_remaining_min`: exposed as
  `schedule_status` attributes for the frontend.
- During boost, `_actuate_flex_loads()` forces the EV charger on at
  max current (last step in `current_steps`), overriding the normal
  flex-load schedule.
- `_safe_power_shed_loads()` can still step down the EV current during
  a boost (grid safety), but will not fully shed the EV charger.
- Boost expires automatically when `now >= _ev_boost_until_ts`.

**Frontend** (`ha_felicity_ems.js`):
- Cyan banner above the chart: "EV Boost active — Xh Ym remaining"
- **Override +1h button** in the card header (next to the battery
  indicator), shown only when an EV charger is configured.  Resolves the
  `button.*_ev_boost_*` entity (excluding the cancel button) and calls
  `button.press` — each press adds an hour via the coordinator.
- Status bar shows a **Boost Xh Ym** chip while active.
- Auto-refreshes every minute while boost is active.

### EMS Card Status Bar

Below the Strategy dropdowns the card renders a status chip row
(`status-bar`): operational mode, current price, **Active power X kW**
(the live `safe_max_power`), **Peak Amp. X A** (`peak_grid_current_now`),
and the boost chip.  The Active-power and Peak-Amp chips turn red when
the inverter is being throttled (`safe_max_power < power_level`),
surfacing safe-power current limiting at a glance.  The energy-state chip
was removed (it duplicated the header badge).  This merges the status
info that used to live in the inverter card's grey bottom bar.

---

## Entity Reference

### Configuration Entities (stored in entry.options)

| Entity | Type | Range | Default | Description |
|---|---|---|---|---|
| ems_strategy | select | save_money/self_sufficiency/battery_care/trader/custom | save_money | Strategy preset — auto-configures underlying knobs |
| grid_mode | select | off/from_grid/to_grid/both | off | Main EMS switch |
| price_mode | select | manual/auto | manual | Price threshold mode |
| safe_power_management | select | auto/on/off | auto | Amperage protection |
| power_level | number | 1-N kW (model max) | 5 | Charge/discharge power |
| price_threshold_level | number | 1-10 | 5 | Manual price level |
| battery_charge_max_level | number | 30-100% | 100 | Max SOC for charging |
| battery_discharge_min_level | number | 10-70% | 20 | Min SOC for discharging |
| battery_capacity_kwh | number | 1-200 kWh | 10 | Usable battery capacity |
| efficiency_factor | number | 0.70-1.00 | 0.90 | Round-trip efficiency |
| daily_consumption_estimate | number | 0-120 kWh | 10 | Fallback consumption |
| reserve_target_pct | number | 0-100% | 0 | Fixed reserve floor (0=dynamic) |
| arbitrage_price_delta | number | 0-0.50 €/kWh | 0 | Min buy→sell spread to trade (0=auto profitability) |
| battery_cycle_cost_eur_kwh | number | 0-0.50 €/kWh | 0 | Battery wear cost (profitability filter) |
| optimization_priority | select | cost/longevity/self_consumption | cost | Multi-objective strategy |
| block_export_on_negative_price | select | on/off | on | Skip sell scheduling at p < 0 |
| max_amperage_per_phase | number | 10-63 A | 16 | Grid current limit |
| voltage_level | number | 48-60 V | 58 | Charge voltage setpoint |
| discharge_min_voltage | number | 48-55 V | 50 | Discharge voltage floor |
| charge_to_full_on_negative_price | select | off/on | off | Charge at every p<0 slot (revenue) |
| discharge_to_make_room_for_negative_price | select | off/on | off | Pre-discharge before p<0 PV windows |
| rule1_time_window | select | manual/auto | manual | Auto writes rule 1 start=00:00, stop=23:59 |
| rule1_weekday | select | manual/auto | manual | Auto writes rule 1 effective_week=all days |
| scheduler_engine | select | greedy/milp | greedy | greedy heuristic or MILP solver (auto-falls back to greedy) |

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

#### C5. Number Entity Default Values — IMPLEMENTED
`HA_FelicityInternalNumber` now accepts a `default_value` parameter.
When an option key is missing from `entry.options` (new installation
or upgrade that predates the option), `native_value` returns the
default instead of `None`.  HA renders `None` as greyed-out /
unavailable — this caused `max_amperage_per_phase` (and potentially
other entities) to be unusable on fresh installs.
`_get_default_options()` in `config_flow.py` now includes all option
keys (`max_amperage_per_phase`, `safe_power_management`,
`battery_cycle_cost_eur_kwh`, `optimization_priority`,
`block_export_on_negative_price`) so new installations get them
written at setup time.  The migration block `defaults_to_set` in
`__init__.py` (runs on every `async_setup_entry`) mirrors the same
keys so *existing* installs upgrading to a newer version backfill
missing options too.

#### C6. UI Entities for Multi-Objective Knobs — IMPLEMENTED
The three knobs added in the lifecycle overhaul previously had no UI
control — they lived only in `entry.options` defaults and could not be
changed by the user.  They now have entities:
- `battery_cycle_cost_eur_kwh` → number (0-0.50 €/kWh, number.py)
- `optimization_priority` → select (cost/longevity/self_consumption, select.py)
- `block_export_on_negative_price` → select (on/off, select.py)

Because `block_export_on_negative_price` is stored as a string ("on"/"off")
by the select but consumed as a bool by `EMSConfig`, the coordinator
converts it: `str(opts.get(..., "on")).lower() not in ("off","false","0")`.
This also tolerates the legacy bool value from earlier installs.

### Consistency Audit Fixes (June 2026)

A full consistency audit (ems.py vs coordinator.py vs frontend vs config
wiring) found and fixed:

**Algorithm (ems.py)**
- PV confidence was applied **twice** in the inverter-max grid power cap
  (`inverter_max - pv_kwh * pv_confidence` where pv_kwh was already
  scaled) in `_compute_scheduled_soc_trajectory` and
  `_validate_schedule_soc` — cloudy-day trajectories overestimated grid
  charge rate.
- `block_export_on_negative_price=off` was dead — both `_schedule_to_grid`
  and `_schedule_both` re-filtered `p > 0` unconditionally.  Off now
  allows negative-price sells (in both mode negatives are still claimed
  by the charge side first — getting paid to charge beats paying to sell).
- Tomorrow's deficit in `select_unified_charge_slots` ignored
  `reserve_target_pct` and the self_consumption boost — now mirrored via
  new `reserve_target_pct`/`optimization_priority` parameters.
- `_select_discharges_for_pv_headroom` now accepts `scheduled_discharge`
  (the sell set) so both-mode make-room discharges are validated against
  the combined drain (hardware floor).
- Dead "safety swap" branch removed (clamp made it unreachable;
  no-swap is intentional and economically correct — see Two-Day section).
- Sell slot count now divides grid-side sellable by grid-side per-slot
  delivery (`energy_per_slot * efficiency`) — was underselling ~10%.
- Flex loads now get a BUY-side threshold (max charge price, or None);
  previously in to_grid mode they received the min SELL price and
  switched on most of the day.
- `_compute_tomorrow_schedule` uses real `pv_hourly_kwh_tomorrow`
  (new EMSState field, supplied by coordinator) instead of a flat
  6-18h synthetic distribution.
- `calculate_net_pv_surplus` / `calculate_available_info` now receive
  `previous_pv_confidence` so all paths use the same smoothed confidence.
- `AvailableInfo.available_total_with_tomorrow` added.

**Coordinator**
- PV-confidence EMA chain repaired: `_last_pv_confidence` stores the
  *smoothed* value (was raw — degraded the EMA to a weak 2-tap blend).
- Stale-data guard repaired: `_last_modbus_success_ts` only refreshes
  when at least one register group read succeeded AND SOC parsed
  (was unconditional — guard could never trigger).
- Override re-validation parity: uses reserve-target floor (hardware
  floor in make-room mode), passes `keep_all_negative_charges`, and the
  current tick's smoothed confidence.
- `_calculate_available_info` delegates to `ems.calculate_available_info`
  with the SOH-scaled capacity (duplicate ~120 lines removed, fixed
  missing self_consumption boost in charge_likelihood); duplicated
  `_compute_reserve_target` and dead `_select_unified_charge_slots`
  removed.
- `_calculate_yesterday_deficit` uses SOH-scaled capacity.
- EV boost raises current to max once per boost session even when the
  charger was already on; a fresh press re-raises after safe-power
  step-downs.  `_actuate_flex_loads` turns loads off (not early-return)
  when slot context is lost.
- Negative-price flag conversion tolerant of legacy bools.

**Sensor / frontend contract**
- `flex_load_schedule` attribute now keyed by slot
  (`{slot: [load indices]}`) as the card expects — cyan strips rendered
  at wrong slots before.
- `sim_params.battery_capacity_kwh` is the SOH-scaled effective capacity
  (+ new `battery_soh_factor`, `backend_reserve_target_pct`).
- Card gates "backend as source of truth" on backend data *presence*
  (an all-idle backend schedule is authoritative; was falling back to
  client sim and painting phantom bars).
- PV-only synthetic tomorrow slots no longer inflate the today-preview
  deficit; client tomorrow-deficit now subtracts `tomorrow_pv_surplus`
  (mirrors backend).
- Day-view toggle selects the clicked tab (was flip-flopping); NaN-safe
  slider state parsing.

**Config wiring**
- Options-flow entity pickers use `description={"suggested_value": ...}`
  with explicit absent→clear handling — assigned entities can now be
  unassigned (was a one-way ratchet via `default=`).
- `_get_default_options` includes the negative-price + rule1 keys.
- `select.py current_option` normalizes legacy bool values.
- `number.py` nested f-string quote fixed (Python <3.12 tooling).

**Known remaining (documented, deliberate)**
- Midnight/bridge projection mixes net_pv with full consumption
  (conservative, partially offsetting errors) — needs careful rework.
- `_validate_schedule_soc` overflow bound is full capacity, not
  `charge_max_pct` (PV may legitimately charge past the grid-charge
  ceiling; modeling both bounds needs a two-limit simulation).
- Coordinator still calls two ems privates (`_validate_schedule_soc`,
  `_calculate_pv_confidence`) — coupling, not drift.

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

#### #11 EV / Heat Pump / Smart-Appliance Coordination — IMPLEMENTED (v1)
3 configurable flexible load slots with enable toggle, switch entity,
rated power, shed priority.  Load 1 has EV charger extras (current
stepping, phases, voltage).  Overlay scheduling into cheap/negative/
PV-surplus slots.  Safe-power priority chain: EV step-down → load
shed → battery reduction.  See "Flexible Load Control" section above.
**Future iterations**: deadline-aware EV scheduling (departure time +
target SOC), thermal mass modelling for heat pumps, co-optimization
in the solver (loads as decision variables, not just overlays).

---

## Testing

Tests are in `tests/test_ems.py` (178 tests). They import `ems.py` directly (bypassing HA dependencies) and test the pure scheduling functions.

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
- Urgent recovery charging (SOC below discharge_min)
- Battery cycle cost in profitability filter
- Optimization priority (longevity, self_consumption)
- Block export on negative price
- PV confidence EMA smoothing
- PV forecast fallback
- Phantom charge prevention (tomorrow schedule, full battery)
- Flexible load scheduling (cheap, negative, PV surplus, disabled, multi-load)
- EV charger current step helpers (power calculation, nearest step)

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
