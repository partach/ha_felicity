# CLAUDE.md — ha_felicity Integration Reference

This document is a mental model for understanding, debugging, and improving the ha_felicity Home Assistant integration. It covers architecture, algorithm logic, known issues, and improvement recommendations.

---

## ⚠️ READ THIS FIRST — Every Session, Before Any Work

**At the start of every session, read this entire CLAUDE.md before making
any change, answering any question, or proposing any fix.** This document
is the source of truth for the project's architecture, the algorithm, every
configuration setting, and the current implementation status. Do not rely on
memory or assumptions from training — the facts and status are HERE. If
something you intend to do contradicts this document, stop and reconcile it
first.

### The Documentation Set — keep ALL of it current (do not let it float)

There are TWO authoritative docs and they must BOTH be read and kept in sync:

| Doc | Role | Update when |
|---|---|---|
| **CLAUDE.md** (this file) | Architecture, status, every fix's rationale, known issues, settings matrix | every change |
| **docs/EMS_LOGIC.md** | The behavioural SPECIFICATION — what the EMS should do per mode/knob, traced from inputs to action | every change that alters behaviour |

A past session updated only CLAUDE.md and let `docs/EMS_LOGIC.md` go stale —
that is the "floating basis" failure.  **Before concluding any work, update
BOTH** (CLAUDE.md for the what/why/status, EMS_LOGIC.md for the behavioural
contract).  `EMS.md` and the root `EMS_LOGIC.md` are older analysis docs — do
not treat them as current.

### Validate behaviour with the simulator — don't assert from memory

`tools/ems_simulator.py` runs `ems.calculate_schedule` on a library of named
scenarios (`tools/scenarios.py`) for BOTH engines, checks expectations, and
renders charts.  When you change scheduling behaviour: add/adjust a scenario
that encodes the intended outcome, run `python tools/ems_simulator.py`, and
confirm it's green before claiming a fix works.  Reproduce customer reports as
scenarios so they become permanent, readable regression tests.

### Engine default = GREEDY (decision, June 2026)

`scheduler_engine` defaults to **greedy** (multi-month track record, no solver
dependency).  **MILP is opt-in.**  A prior session flipped the default to MILP;
it was reverted after fact-checking showed the recent customer-reported bugs
were MILP-specific or shared-reserve (not greedy scheduling), plus MILP carries
a pulp/CBC dependency and was not yet validated for determinism.  Do NOT flip
the default back to MILP without (a) the simulator harness showing MILP is
deterministic and correct across all knob scenarios, and (b) explicit user
agreement.  Greedy's two-day reconstruction is less powerful on cross-day
arbitrage, but it is the proven, dependency-free path.

### The One Rule That Keeps Breaking — Single Point of Truth

**`ems.py` is the SINGLE SOURCE OF TRUTH for all scheduling logic. It must
REMAIN so.** This has been re-established three separate times because the
code kept drifting back to duplicated logic. Do not reintroduce drift.

- All scheduling/optimization logic lives in `ems.py` (`calculate_schedule`).
- `coordinator.py` ONLY constructs `EMSConfig` + `EMSState` and calls
  `ems.calculate_schedule()` (in an executor thread). It must NOT contain
  a second copy of any scheduling decision.
- `milp.py` is an alternative *slot selector* invoked by `ems.py`; the
  shared post-processing (SOC trajectory, tomorrow schedule, flex-load
  overlay, urgent recovery) stays in `ems.py` for both engines.
- The frontend JS card is a *preview only* — it uses backend-provided
  `slot_schedule` / `backend_soc_trajectory` whenever no slider override is
  active. It is never authoritative.

**When you change the algorithm: change `ems.py` only.** Add/adjust tests in
`tests/test_ems.py`. The coordinator and frontend inherit the change. If you
ever feel tempted to "just tweak it in the coordinator," that is the drift
this rule exists to prevent — don't.

### Before Concluding Any Work

- Run `python -m pytest tests/test_ems.py` (must stay green; currently 250).
- If you added a setting, update the **Settings Traceability Matrix** below
  and confirm it is consumed by the algorithm (no "optimized-out" settings).
- Keep this document in sync with the code. If status changed, update it.

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
└── test_ems.py              # 250 tests for the pure EMS algorithm
```

---

## Architecture: Single Source of Truth

**`ems.py` is the single source of truth for all scheduling logic.**

| Location | Role | Authoritative? |
|---|---|---|
| `ems.py` — `calculate_schedule()` | Pure scheduling algorithm, both greedy and MILP dispatch | **YES — the algorithm** |
| `coordinator.py` — `_calculate_schedule()` | Constructs `EMSConfig` + `EMSState`, delegates to `ems.calculate_schedule()` via executor thread | Wrapper only |
| `milp.py` — `solve_schedule()` | Alternative LP optimizer, called by `ems._run_milp_or_none()` | Slot selection only |
| `frontend/ha_felicity_ems.js` — `_simulateSchedule()` | Client-side preview for slider interaction | Visual fallback only |

**How it works**: The coordinator builds `EMSConfig` (from `config_entry.options`)
and `EMSState` (from runtime data), then calls `ems.calculate_schedule(config, state)`
in an executor thread (PuLP writes blocking `.mps` files).  The coordinator NEVER
duplicates scheduling logic — it only handles inverter control, state transitions,
and safe power management.

**When making algorithm changes**: modify `ems.py` only.  The coordinator and
frontend inherit the change automatically.  The JS card uses backend-provided
`slot_schedule` and `backend_soc_trajectory` when no slider overrides are active;
client-side simulation only runs during live slider preview.

**History**: the coordinator previously duplicated ~340 lines of scheduling logic
from ems.py.  That duplication was eliminated.  The coordinator still calls two
ems.py private functions (`_validate_schedule_soc`, `_calculate_pv_confidence`)
for override re-validation — this is coupling but not logic drift.

---

## Optional MILP Scheduler (`milp.py`)

The greedy scheduler picks charge and sell slots in separate passes,
which produces subtle bugs at the seams between features (e.g. allocating
all charge slots to a cheaper tomorrow while today's expensive sell slots
starve for energy — the "sell coverage" bug). The MILP scheduler models
the whole remaining-today + tomorrow horizon as a single optimisation and
lets a solver find the cost-optimal plan, so cross-slot/cross-day
interactions can't fall through the cracks.

**Engine selection**: `scheduler_engine` config option (**`milp` default** /
`greedy`), exposed as a select entity ("Scheduler Engine") and in the EMS
card's Advanced settings ("Scheduler"). When `milp`,
`calculate_schedule` calls `_run_milp_or_none()`, which tries the solver
and **silently falls back to greedy** on any failure (pulp missing,
infeasible, timeout, non-optimal). The greedy path stays the safety net —
MILP failures never break the EMS.

**Why MILP is the default (robustness)**: the greedy two-day reconstruction
(`_compute_tomorrow_schedule`) is the fragile, non-deterministic part — on a
real arbitrage it scheduled 1 charge slot where the MILP scheduled 10, and a
1.4% reserve change flipped its tomorrow decisions on/off.  The MILP optimises
the whole remaining-today + tomorrow horizon jointly, so cross-slot/cross-day
and cross-mode behaviour is uniform and deterministic.  This is also why the
night-aware reserve + boost-drop apply to ALL modes in the MILP but only to
from_grid in greedy — the MILP stays robust with them everywhere; the greedy
two-day path destabilised in both/to_grid.  Greedy remains the dependency-free
fallback; MILP auto-disables to greedy when CBC/pulp is unavailable, so the
default flip can't break any install.  A one-time migration bumps existing
auto-`greedy` installs to `milp` (marker-guarded; see `__init__.py`).

**MILP feasibility guarantee** (fixed): a customer log showed ~24% of MILP
runs returning `Infeasible` (158/672, clustered 16:00–18:00) → falling back to
the fragile greedy.  Cause: the reserve constraints (`soc[midnight] >= reserve`,
`soc[end] >= reserve`) and the per-slot `soc >= soc_min` bound were HARD; in the
evening with a near-capacity reserve and few low-power slots left — or any time
consumption drains faster than charging can offset — there was no feasible LP.
Two fixes make the LP **provably feasible for any input**: (1) the reserve
constraints are SOFT (shortfall slack + heavy penalty — reach the reserve when
physically possible, get as close as possible otherwise); (2) a per-slot
emergency import slack `imp[k]` on the SOC dynamics (grid passthrough — the
house draws from grid when the battery is at min) absorbs any otherwise-
infeasible drain.  Both slacks carry a high penalty (`5× max price`) so they're
0 in normal cases (parity preserved) and only activate to keep the LP feasible.
Pinned by `test_milp_never_infeasible_extreme_drain`.

**Multi-solver selection** (`_pick_solver`): pulp ships a prebuilt CBC binary,
but it is not published for every platform/interpreter — on brand-new Python
versions or uncommon CPU arches the path simply doesn't exist (real customer on
Python 3.14: `.../pulp/apis/../solverdir/cbc/linux/i64/cbc` → FileNotFoundError).
Instead of hard-disabling MILP the moment the bundled binary is missing,
`_pick_solver` probes solvers in order — `PULP_CBC_CMD` (bundled),
`COIN_CMD` (a SYSTEM CBC, e.g. `apt install coinor-cbc` or `pip install
pulp[cbc]`; also what pulp's own deprecation notice now recommends), then
anything `listSolvers(onlyAvailable=True)` reports — using each solver's cheap
`.available()` check (path/PATH existence, no solve).  MILP runs wherever ANY
solver exists.  Only when none is available does it fall through to the disable
path below.

**Permanent disable on unrecoverable failure** (`_MILP_DISABLED` module
flag): when NO solver is available (bundled CBC missing AND no system CBC) or
the solver binary is unrunnable (`FileNotFoundError` from `subprocess.Popen`)
or pulp import fails, the failure is **structural** — it recurs on every 10s
tick and never recovers within the process.  Without a guard it logs a full
traceback ~8600×/day (one user saw 1100+).  `solve_schedule` sets a process-
lifetime flag on these unrecoverable errors and short-circuits to greedy
immediately on subsequent calls — no rebuild, no traceback spam (one clean
warning: "MILP CBC solver binary not found — … greedy is fully functional").
The flag resets only on a fresh process start (HA restart/reload), so
installing CBC is re-checked on the next boot.  Non-optimal/infeasible results
do NOT set the flag (they can be input-dependent and recover next slot).

**Model** (`milp.solve_schedule`, a pure LP):
- Horizon = today's remaining slots + all of tomorrow (when prices known).
- Per-slot continuous vars: `c[k]` (grid kWh to charge, ≤ inverter
  headroom after PV), `d[k]` (battery kWh discharged to sell, ≤ safe
  power), `spill[k]` (PV curtailment), `soc[k]`.
- SOC dynamics: `soc[k] = soc[k-1] + net_pv[k] - load[k] + eff·c[k] - d[k] - spill[k]`.
- Bounds: `soc_min ≤ soc[k] ≤ soc_max`; `soc[end] ≥ reserve_target`;
  `soc[midnight] ≥ reserve_target` **(self_consumption only, July 2026)** —
  the full-reserve-by-midnight demand is the self-sufficiency contract.  In
  cost/longevity it forced expensive-evening charging when cheap slots came
  right after midnight (low-SOC recovery at 18:00 charged 3× 0.30 slots;
  greedy correctly charged 1 + cheap after 00:00).  Cost mode relies on the
  per-slot `soc ≥ soc_min` floor (still forces SOME today charging for
  survival) + the end-of-horizon reserve, mirroring greedy's
  may-defer-to-tomorrow economics.
- Objective: minimise `Σ price·c − Σ price·eff·d + cycle_cost·Σd − terminal_value·soc[end]`.
  Terminal value = `avg_price × efficiency`.  No per-priority boost — pushing
  terminal value higher (e.g. P90) would charge at uneconomic prices.
  Self-consumption differentiates via the reserve floor (1.25×), not the
  terminal value.  The base `avg × eff` already makes the solver charge
  any slot priced below `avg × eff²` (round-trip profitable), matching
  the greedy self-consumption top-off gate.
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

**Charge execution** (`coordinator._determine_energy_state`): when the
current slot is flagged `charge` (and SOC < charge_max), the coordinator
executes it — full stop.  It does **not** second-guess the plan with a
"defer for a cheaper later slot" check; that logic was removed (see C3
below) because it fought the power-aware optimiser and stalled charging.
The cheapest-slot decision lives entirely in `ems.py`: a slot is scheduled
only if it's one of the cheapest-N slots needed to cover the deficit, so by
the time the coordinator sees it, charging now is the correct action.

### State Transitions (coordinator.py `_transition_to_state`, ~1425-1482)

| State | econ_rule_1_enable | Voltage | SOC | Power |
|---|---|---|---|---|
| charging | 1 | voltage_level (default 58V) | charge_max (default 100%) | safe_max_power (W) |
| discharging | 2 | discharge_min_voltage (default 50V) | discharge floor (see below) | safe_max_power (W) |
| idle | 0 | *(not written)* | *(not written)* | *(not written)* |

**Economic-mode atomicity (all models)**: the inverter only obeys Rule 1
when it's in "Economic mode".  The register differs by model: TREX-25/50 use
`eco_timeofuse` (1=Economic), TREX-5/10 use `operating_mode` (2=Economic,
0=General).  When in General mode the inverter **silently ignores** the
rule-1 charge enable — `econ_rule_1_enable`/`_grid_charge_enable=1` but
nothing charges.  `_transition_to_state` writes the operating mode FIRST and
now **checks the result**: if the mode write fails it aborts and does NOT
write `econ_rule_1_enable`, so the inverter is never left in the inert
"enable=charge, mode=General" state.  The transition returns False and the
next cycle retries atomically.  `_handle_operating_mode` likewise propagates
the Economic-mode write result (eco_timeofuse on TREX-25/50, operating_mode
on TREX-5/10) instead of always returning True.

**Economic-mode self-heal** (`_ensure_economic_mode_when_active`): the
inverter can drop out of Economic mode *after* a successful transition
(firmware quirk, Felicity app, power blip) while the coordinator still
believes it's charging/discharging.  No state change → nothing re-writes the
mode → battery sits inert.  This check runs every cycle: when in an active
state but the Economic-mode register reads off (`eco_timeofuse`!=1 on
TREX-25/50, `operating_mode`!=2 on TREX-5/10), it re-asserts the operating
mode.  Idempotent (only writes when the register actually shows General).

**Minimum charge commitment (anti flip-flop)**: when SOC hovers near the
reserve target, the schedule's marginal deficit can flip in/out of "charge"
every tick, producing a charge→off storm on `econ_rule_1_enable` that
hammers the grid current (seconds-scale toggling, real customer report on a
TREX-10 at ~34% SOC).  Once charging starts, the coordinator commits to it
until SOC rises ≥ `MIN_CHARGE_SOC_GAIN` (5%) **or** `MIN_CHARGE_DURATION_S`
(15 min, one slot) elapses — whichever first.  While committed, a schedule
flip to idle is overridden back to charging, so each charge episode is a
real block, never a micro-burst.  Released early when SOC reaches
`charge_max` (never overcharges) and capped at 15 min (never infinite).
Armed/disarmed on the transition edge; the held-charge ticks still run the
Economic-mode self-heal, so a held charge actively keeps the inverter in
Economic mode.

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
| reserve_target_pct | 0.0 | 0=dynamic; >0=monotonic floor, max(fixed, dynamic) — only raises |
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

The algorithm does NOT try to fill the battery to 100%. It calculates a **reserve target** — the minimum SOC to maintain at all times. By default that's "just enough to survive overnight"; the user can raise it with `reserve_target_pct`:

```
dynamic_reserve = discharge_min_kwh + overnight_reserve × boost
  where overnight_reserve = consumption_per_hour × overnight_hours
        boost = 1.25 if optimization_priority == "self_consumption" else 1.0

Dynamic (reserve_target_pct = 0):
  reserve_target = dynamic_reserve

Fixed (reserve_target_pct > 0):
  reserve_target = max(reserve_target_pct × capacity, dynamic_reserve)
```

**Time-aware `overnight_hours` (from_grid only, fixed July 2026)**:
`calculate_self_consumption_reserve` takes the current time.  During the day
`overnight_hours = (24 − sunset) + sunrise` (the full night — charge up before
sunset).  But once we're PAST sunset, it covers only the REMAINING hours to
sunrise.  Without this, a high-consumption house keeps demanding a full-night
reserve at e.g. 22:30 — which (×1.25 boost, capped at capacity) pins the
target at ~100% of a small battery and forces grid charging at PEAK evening
prices to "maintain" a reserve the battery is meant to be DISCHARGING through
overnight (then tomorrow's PV refills it for free).  Real customer report:
72 kWh/day house, 48 kWh battery at 85% SOC at 22:31, MILP booked 4 charge
slots at ~18 €/kWh.  Scoped to **from_grid** (`_schedule_from_grid` and the
MILP reserve when `grid_mode == "from_grid"`): in both/to_grid the reserve
also gates SELLING, where shrinking it at night changes trade economics and
caused two-day-optimization regressions, so those keep the full-night reserve.
The tomorrow-side reserve in `select_unified_charge_slots` is always
full-night (tomorrow's whole night is still ahead).

**Boost dropped at night (from_grid)**: the time-aware reserve alone wasn't
enough — the 1.25× self_consumption boost re-inflated the (shrunk) night
reserve to ~84%, so the battery was still topped up at peak (1 residual
slot).  `_compute_reserve_target(apply_boost=...)` now suppresses the boost
when `_is_night(...)` (past sunset / before sunrise).  Rationale: the boost
exists to hold extra *PV* energy for self-use — a daytime concept.  At night
there's no PV to preserve; the battery just needs survival to sunrise, then
tomorrow's PV refills it.  With both fixes the customer scenario charges 0
slots (reserve 69% < 85% SOC).  `_is_night` / `_sunset_sunrise_hours` are
shared helpers.  Pinned by `test_self_consumption_boost_dropped_at_night`
and `test_time_aware_no_expensive_night_charge`.

**`reserve_target_pct` is a MONOTONIC FLOOR — it can only RAISE the target,
never lower it.** This is the whole point of the setting: "keep AT LEAST this
much in the battery at all times." A higher value keeps the battery fuller
and pulls more charging into *today* (the deficit / midnight-constraint logic
charges to reach the reserve).

⚠️ **Fixed June 2026** (was a real, user-reported bug): the old formula was
`max(reserve_target_pct × capacity, discharge_min_kwh)` — it ignored the
dynamic overnight reserve and the self_consumption boost entirely. So a fixed
reserve *below* the dynamic value would **lower** the target and charge the
battery LESS full than `reserve_target_pct = 0` would. Setting "I want more
reserve" produced "charge less full." Now `max(fixed, dynamic)` makes it
monotonic. Lives in `_compute_reserve_target` (single source) → greedy and
MILP both inherit it; the tomorrow-side reserve in `select_unified_charge_slots`
and both JS-card mirrors apply the same `max(fixed, dynamic)` rule.

Example: 60 kWh battery, 35% min, 38.5 kWh/d consumption, sunset 19:00, sunrise 7:00
- min_kwh = 21 kWh
- overnight = (38.5/24) × 12h = 19.25 kWh → rounds to ~19.3 kWh
- dynamic reserve_target = 21 + 19.3 = 40.3 kWh (~67% of 60 kWh)
- A user setting reserve_target_pct=35% (21 kWh) does NOT lower it to 21 —
  `max(21, 40.3) = 40.3 kWh` stands. Setting 80% (48 kWh) raises it to 48.

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

# Consumption-deviation correction: detect unexpected loads (car charger,
# oven, etc.) draining the battery faster than the trajectory predicted.
# Compare predicted SOC (from previous schedule's trajectory) with actual.
deviation = _consumption_deviation_kwh(state, current_kwh, reserve_target, capacity)
# Guards: >1 kWh floor (noise), SOC > reserve (urgent recovery handles below),
# 0.5× damping (avoids over-reaction on transient loads like kettles).

# Use the worse case + deviation + carryover
energy_deficit = max(snapshot_deficit, predictive_deficit) + deviation + carryover
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
(`soc[midnight_slot] >= reserve_target`) — scoped to **self_consumption
only** (in cost mode it forced expensive-evening charging when cheap slots
came right after midnight; cost survival is covered by the per-slot
`soc >= soc_min` floor instead).
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

**Partial-charge keep (greedy from_grid — consumption arbitrage, July 2026)**:
when a charge slot *causes* an overflow but the battery had real room entering
it (`soc_before < capacity`), the inverter physically charges what FITS
(`capacity − soc_before`) and the rest spills — the slot still stores cheap
grid energy.  The default validation drops the whole slot; in from_grid that
threw away a genuinely-useful partial charge and left the battery to ride the
floor through expensive hours (greedy "myopia": a 10 kWh battery, zero PV,
12 kWh/day load charged only **1 of 2** cheap night slots because the two
cheapest were consecutive and the 2nd overflowed — then paid 0.30 evening
grid, ~2× the MILP cost).  `keep_partial_charges` (passed True only by
`_schedule_from_grid`, off for to_grid/both/override validation) keeps a
**substantial** partial charge (stores ≥ half the slot's energy); a near-full
battery where the slot would store only a sliver still falls through to the
normal drop, so we never schedule a charge for a negligible top-off.  Greedy
now charges both cheap slots (matching MILP) on no-PV / undersized-battery
days.  Pinned by `TestGreedyPartialChargeNoSun`.

**Phantom-charge detection**: when a charge slot is scheduled at a
moment the battery is already at capacity (`soc_before >= capacity - 0.01`),
the inverter physically cannot store the energy (BMS rejects).  These
slots are dropped regardless of price — even negative-price slots,
because the income doesn't materialise if no grid energy is drawn.
The validation simulates forward through PV-only overflows (clamping
soc and continuing) instead of breaking on the first PV-caused
violation, which lets it detect phantom charges later in the day.

**Re-shop after overflow drop (greedy from_grid, July 2026)
— `_fill_charge_to_deficit`**: the greedy selector picks the cheapest slots
cheapest-first.  When the two cheapest slots land *back-to-back* on a small
battery, charging them consecutively overflows capacity, so SOC validation
drops one — and the dropped energy was previously never re-allocated, leaving
the overnight deficit half-covered.  Real symptom: a 20 kWh battery under a
40 kWh/day load charged only **1 of 2** needed cheap night slots, then rode
the floor ~9 h earlier than the MILP plan (which placed its second charge
*later*, after consumption had freed headroom).  After validation,
`_schedule_from_grid` now re-shops: it walks the remaining slots
cheapest-first and adds each candidate that (a) is priced **≤ the marginal
price the selector already committed to**, (b) survives a fresh validation
against the kept set, and (c) increases delivered charge energy — until the
deficit is covered.  Two guards keep it cost-first:
- **Price ceiling** (`max_price` = max price of the selected set): we only
  re-shop into a slot as cheap as the ones already chosen.  We must NOT reach
  for a pricier slot to chase the deficit — bridging an overnight need by
  charging a 0.30 evening peak (while the chosen slots were 0.15, or while
  cheap 0.05 night grid follows after midnight) costs MORE than drawing the
  shortfall from the grid live (round-trip loss on a higher price).  Same
  no-safety-swap economics the two-day selector applies.
- **PV-aware headroom cap** (`min(deficit, max_battery − current − net_pv)`):
  when PV surplus will fill the battery, the target shrinks so we don't
  over-charge.
Scoped to greedy `from_grid` and skipped for
`charge_to_full_on_negative_price` (its negative-slot handling is outside the
deficit model).  No-op for healthy schedules (validation dropped nothing →
delivered energy already meets the target).  Pinned by
`TestGreedyReshopAfterOverflow` (re-shops a cheap slot; never an expensive
one).  This closes most of the greedy/MILP gap on undersized-battery /
heavy-load days without touching MILP.

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
- **PV production overlay** (very light yellow solar hump) — drawn on top of
  the price bars but UNDER the SOC line, so the SOC visibly rises as the sun
  produces.  Per-slot PV from `sim_params.pv_hourly_kwh` (× pv_confidence),
  SYNTHESIZED from the daily forecast total when no hourly breakdown exists
  (`_synthPvHourly`, mirrors `ems._synthesize_pv_hourly`) so PV is never
  invisibly flat.  Toggled by the **☀ PV** button in the chart header
  (`_showPv`, persisted to localStorage; default on).  Not drawn in the
  tomorrow PV-only preview (which already shows PV bars).  Mirrors the
  day-simulator's PV visualization.
- SOC trajectory line (blue dotted)
- Projected overnight-minimum line (purple dashed) — the lowest SOC% the
  battery is predicted to reach before tomorrow's sun refills it
  (`_projectedOvernightMinPct`: min of the future SOC trajectory + tomorrow's
  early-morning slots).  Replaced the old "night target"/reserve line, which
  showed an aspirational floor the SOC frequently sat *below* (cost-
  optimisation declines to top off at peak prices) and so told the user
  little.  The "overnight need" kWh stat still shows the reserve energy.
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

**Presets set ONLY the two knobs that DEFINE a strategy** — `grid_mode` and
`optimization_priority`.  They must NOT touch any user-owned preference:
`reserve_target_pct`, `arbitrage_price_delta`, `battery_cycle_cost_eur_kwh`,
or the negative-price flags (`block_export_on_negative_price`,
`charge_to_full_on_negative_price`,
`discharge_to_make_room_for_negative_price`).  **Bug (fixed twice):** the
presets used to also write those five, so re-selecting a strategy (or the card
re-applying one, or the user re-picking their strategy after a re-install)
silently reset the user's tuning every time — the reported "arbitrage_price_delta
/ negative-price / cycle-cost settings aren't remembered" symptom.  All five
removed from `STRATEGY_PRESETS` in `select.py`; they now persist independently.
`battery_care` still works with only `optimization_priority=longevity` because
both engines enforce a 0.05 €/kWh cycle-cost floor from the priority itself
(`max(cycle_cost, 0.05)`); `self_sufficiency` applies its 1.25× reserve boost
from the `self_consumption` priority, so neither needs an explicit numeric
knob in the preset.  Install-time defaults still come from
`config_flow._get_default_options` / the `__init__` `defaults_to_set` migration
(which only fill MISSING keys, never overwrite).  **Audit (all writers
checked):** the number/select/text entities each write only their own key via
`dict(options)` + one update; the two `slot_overrides` writers use
`{**entry.options, …}` (preserve everything); `defaults_to_set` fills missing
only.  The strategy preset was the sole clobbering path.

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

**Manual slot override intent is grid-mode-aware** (`_handleCanvasClick`):
- `from_grid`: every picked slot becomes a **charge** slot, even above the
  price threshold — a strict threshold otherwise blocks the user from
  forcing charge slots they need.
- `to_grid`: every picked slot becomes a **sell** slot, regardless of
  threshold.
- `both`: the threshold decides — below = charge, above = sell.
The coordinator's override merge mirrors this (from_grid accepts only
charge, to_grid only discharge, both accepts either), so the picked slots
pass through and execute (subject to SOC-overflow validation).  Charge
slots above the threshold are intentional and are NOT price-filtered.

### Client-Side Simulation
Mirrors coordinator logic for instant preview when dragging sliders. Uses `sim_params` from `schedule_status` sensor attributes.

**Backend as single source of truth**: For both today and tomorrow views,
when no slider or slot overrides are active, the card uses
backend-provided `slot_schedule` / `slot_schedule_tomorrow` (with actions)
and `backend_soc_trajectory` / `backend_soc_trajectory_tomorrow` for the
SOC line. Client-side simulation (`_simulateSchedule`,
`_simulateScheduleTomorrow`, `_computeSocTrajectory`) only runs when the
user is actively previewing via sliders.

**SOC line always uses the backend when slot overrides are active**: manual
slot overrides are committed to the backend and the SOC trajectory is
recomputed server-side with them merged (`coordinator._calculate_schedule`
re-runs `_compute_scheduled_soc_trajectory` on the override-merged schedule).
The client-side `_computeSocTrajectory` diverges (flat-PV fallback when
`pv_hourly_kwh` is absent, no SOC validation) and could draw a *lower* curve
even though the user just ADDED charge slots.  The trajectory gate therefore
prefers the backend whenever slot overrides exist, even if a slider preview
is also active.  Also: the Max-SOC / Min-SOC dropdown previews now CLEAR
their `_simOverrides` entry after 2 s (like `_commitPower` does for
`powerKw`) — previously they stuck forever, keeping `hasSliderOverrides`
true and pinning the card to the client trajectory permanently.

**Override recompute must mirror calculate_schedule's PV synthesis**: when
the forecast has no hourly breakdown, `calculate_schedule` internally
rebuilds `state` with `_synthesize_pv_hourly(daily_total)` so the trajectory
accounts for solar — but that rebuilt state is NOT returned.  The
coordinator's override recompute therefore re-applies the same synthesis
(`dataclasses.replace(state, pv_hourly_kwh=_synthesize_pv_hourly(...))`)
before calling `_compute_scheduled_soc_trajectory`.  Without it the override
trajectory "forgets PV" entirely and draws a far-too-low curve (real
customer report: 12 charge slots + big PV remaining, SOC line barely rose).
This is a documented coupling/duplication smell — the clean long-term fix is
to pass overrides INTO `calculate_schedule` so ems.py owns the whole merge +
trajectory, but that's a larger refactor.

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
select.select_option).  For `select`/`input_select` current entities the
option string is resolved by matching the numeric amperage against the
entity's real `options` attribute (`_match_select_option`) — charger
integrations format options inconsistently ("16", "16 A", "16A"), and
passing the bare number raises `ServiceValidationError` when the entity
expects "16 A".  Falls back to the bare number when the entity isn't
loaded yet; skips (with a warning) when no option matches.

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

**`is_ev_charger` = `bool(current_entity)` (fixed).** A load is an EV charger
as soon as it has a **current-control entity** — the amp-step list
(`current_steps`) is OPTIONAL and only enables *current stepping* (variable-amp
smart charging, safe-power step-down, boost-to-max).  It used to require BOTH
(`current_entity and current_steps`), so a blank/mistyped steps text field
silently disabled the ENTIRE EV feature — the EV Boost button vanished and
`ev_charge_strategy` stopped applying — even though the user had wired up a
switch + current entity (real customer report).  All `current_steps` consumers
already degrade gracefully when empty (they guard or fall back to
`default_current`: boost force-turns the charger on without setting amps,
stepping/step-down no-op, the sensor uses `default_current`), so recognising
the charger from the current entity alone is safe.  Add `current_steps` to get
variable-amp control; without it the charger runs on/off and Boost forces it on.

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
| reserve_target_pct | number | 0-100% | 0 | Monotonic reserve floor — max(fixed, dynamic), only raises (0=dynamic) |
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

### Settings Traceability Matrix (Audit: June 2026)

Every configuration setting must flow through the system.  This matrix
traces each setting from `config_entry.options` through to its consumers.
**Status: ALL settings verified active — none orphaned or "optimized out".**

#### Settings → EMSConfig (scheduling algorithm in ems.py)

| Option key | EMSConfig field | Greedy | MILP | Notes |
|---|---|---|---|---|
| `grid_mode` | `grid_mode` | ✅ | ✅ | Gates charge/discharge in both engines |
| `battery_capacity_kwh` | `battery_capacity_kwh` | ✅ | ✅ | SOH-scaled by coordinator before construction |
| `battery_charge_max_level` | `battery_charge_max_pct` | ✅ | ✅ | Upper SOC bound |
| `battery_discharge_min_level` | `battery_discharge_min_pct` | ✅ | ✅ | Lower SOC bound / hardware floor |
| `efficiency_factor` | `efficiency` | ✅ | ✅ | Round-trip = efficiency² |
| `power_level` | `safe_power_kw` | ✅ | ✅ | Charge/discharge power per slot |
| `daily_consumption_estimate` | `consumption_est_kwh` | ✅ | ✅ | Fallback when no profile available |
| `reserve_target_pct` | `reserve_target_pct` | ✅ | ✅ via arg | Pre-computed into reserve_target kWh for MILP |
| `arbitrage_price_delta` | `arbitrage_price_delta` | ✅ | ✅ | Per-slot price gates in both engines |
| `battery_cycle_cost_eur_kwh` | `battery_cycle_cost_eur_kwh` | ✅ | ✅ | Wear cost in profitability filter + objective |
| `optimization_priority` | `optimization_priority` | ✅ | ✅ | longevity → 0.05 floor; self_consumption → 1.25× reserve, top-off gate |
| `block_export_on_negative_price` | `block_export_on_negative_price` | ✅ | ✅ | Blocks sell at p<0 |
| `charge_to_full_on_negative_price` | `charge_to_full_on_negative_price` | ✅ | ✅ | MILP forces every p<0 slot in the extraction (mirrors greedy); the LP alone stops at SOC-max and would take fewer |
| `discharge_to_make_room_for_negative_price` | `discharge_to_make_room_for_negative_price` | ✅ | implicit | MILP: joint optimization naturally creates room when profitable |
| `scheduler_engine` | `scheduler_engine` | ✅ | — | Used by ems.py to dispatch to MILP or greedy |
| `ev_charge_strategy` | `ev_charge_strategy` | ✅ | ✅ | Applied in flex-load overlay (runs after both engines) |
| `flexible_load_*` | `flexible_loads` | ✅ | ✅ | Applied in flex-load overlay (runs after both engines) |
| n/a | `yesterday_deficit_kwh` | ✅ | implicit | MILP works from current_kwh; deficit already reflected in SOC |
| n/a | `inverter_max_power_kw` | ✅ | ✅ | From INVERTER_MAX_POWER_KW[model], not a user setting |

#### Settings → Coordinator only (inverter control, not scheduling)

| Option key | Used in | Purpose |
|---|---|---|
| `voltage_level` | `_transition_to_state` | Charge voltage register (default 58V) |
| `discharge_min_voltage` | `_transition_to_state` | Discharge voltage register (default 50V) |
| `max_amperage_per_phase` | `_check_safe_power` | Grid current limit for power throttling |
| `safe_power_management` | `_check_safe_power` | auto/on/off — enables power monitoring |
| `price_mode` | `_determine_energy_state` | manual (threshold) vs auto (schedule) |
| `price_threshold_level` | manual mode calc | Price level 1-10 for manual mode threshold |
| `rule1_time_window` | `_apply_rule1_auto_settings` | Auto-write rule 1 start/stop time |
| `rule1_weekday` | `_apply_rule1_auto_settings` | Auto-write rule 1 weekday mask |
| `ems_strategy` | frontend card only | Strategy dropdown auto-configures other knobs |
| `update_interval` | coordinator `__init__` | Polling interval (seconds) |

#### MILP vs Greedy: Known Behavioral Differences

| Feature | Greedy | MILP | Impact |
|---|---|---|---|
| `charge_to_full_on_negative_price` | Explicit: forces ALL p<0 slots + phantom-charge exemption | Explicit: forces ALL p<0 slots in the extraction (the LP alone stops at SOC-max) | Both engines now grab every negative slot when the flag is on (user opted in for the revenue, accepting PV curtailment) |
| `discharge_to_make_room_for_negative_price` | Explicit: `_select_discharges_for_pv_headroom` helper | Implicit: joint optimization sees both sell revenue + negative-buy revenue | MILP may be more or less aggressive depending on price spread |
| `yesterday_deficit_kwh` | Added as carryover to deficit | Not used; current_kwh already reflects yesterday's shortfall | No impact — same result |
| Cross-day slot selection | Separate today/tomorrow pools with today-first guard for self_consumption | Single unified horizon with midnight SOC constraint | MILP sees the whole picture; greedy needs explicit guards |
| Slot count capping | Fixed count from deficit ÷ per-slot energy | LP energy-based with MIN_FRAC + headroom caps | MILP avoids over-scheduling marginal slots |

---

## Complete Algorithm Flow (ems.py `calculate_schedule`)

This is the authoritative description of the scheduling algorithm.  Both
greedy and MILP paths share the same entry point and post-processing.

### 1. Entry and Early Exit

```
calculate_schedule(config: EMSConfig, state: EMSState) → ScheduleResult
```

- Determines `num_slots` from `slot_prices_today` length (24/48/96).
- Computes `self_consumption_reserve` and `reserve_target_pct` ALWAYS
  (even when grid_mode=off) so the frontend's night target line works.
- **Early exit** on `grid_mode == "off"` or no price data: returns a
  result with only reserve info populated.

### 2. Common Pre-computation

- `remaining`: list of `(slot_idx, price)` from current slot onward.
- `net_pv`: net PV surplus via `calculate_net_pv_surplus` (hourly
  PV × confidence − consumption, positive hours only).
- `pv_confidence`: `_calculate_pv_confidence` with sliding window +
  EMA smoothing from `state.previous_pv_confidence`.
- `energy_per_slot`: `safe_power_kw × slot_hours` (raw inverter energy).

### 3. Engine Dispatch

```
if scheduler_engine == "milp":
    result = _run_milp_or_none(...)    # try MILP
    if result is None:
        result = _run_greedy()          # fallback
        result.scheduler_active = "greedy_fallback"
else:
    result = _run_greedy()
```

### 4. Greedy Path (`_schedule_from_grid` / `_schedule_to_grid` / `_schedule_both`)

All three modes follow the same structure:

#### 4a. Reserve & Deficit

```
min_kwh = discharge_min_pct × capacity
reserve_target = _compute_reserve_target(config, overnight_reserve)
  → if reserve_target_pct > 0: fixed floor
  → else: min_kwh + overnight_hours × consumption/24
           × 1.25 if self_consumption priority
```

Deficit = max(snapshot_deficit, predictive_deficit) + yesterday_deficit:
- **Snapshot**: max(0, reserve_target − current_kwh − net_pv)
- **Predictive**: simulate SOC through remaining slots → min projected
- **Solar protection**: if max_projected ≥ 95% capacity → predictive = 0

#### 4b. Charge Slot Selection (`select_unified_charge_slots`)

- Merges today + tomorrow price pools (if tomorrow available).
- Sorts cheapest-first.
- **Power-aware accumulation**: each slot's actual grid charge =
  `min(safe_power, inverter_max − pv) × hours × efficiency`.
  Midday PV-saturated slots deliver 0 grid kWh → skipped.
- Accumulates until deficit covered (+ headroom cap).
- **Self-consumption today-first**: forces today's deficit onto today's
  slots when `optimization_priority == "self_consumption"`.
- **Self-consumption top-off**: after deficit covered, fills toward max
  SOC from cheap slots only: `price ≤ efficiency² × mean_remaining`.
  Never charges at uneconomic prices.
- **Headroom cap**: `max(0, max_battery − current − net_pv_surplus)`.
  Negative-price slots pass through; SOC validation prunes later.

#### 4c. Sell Slot Selection (to_grid / both)

- Sell slot count from sellable energy above reserve.
- Profitability filter: `min_sell_price = max_buy / efficiency² + cycle_cost`.
- **Arbitrage delta > 0**: replaces auto profitability check.
  Every sell must beat `buy_reference + delta`.  No sells if nothing
  clears the bar.
- **block_export_on_negative_price**: filters p<0 sells.

#### 4d. Negative-Price Strategies (opt-in)

- **charge_to_full**: adds ALL remaining p<0 slots to charge set.
- **discharge_to_make_room**: `_select_discharges_for_pv_headroom` adds
  pre-emptive discharge before p<0 PV windows at expensive positive
  prices.

#### 4e. SOC Validation (`_validate_schedule_soc`)

Forward-simulates SOC through every slot.  Drops violations:
- Overflow (charge → SOC > capacity): drop most expensive non-negative
  charge first, then negative-price charges unless PV alone fills battery.
- Underflow (discharge → SOC < minimum): drop least profitable discharge.
- Phantom charges (SOC already at capacity): always dropped.

### 5. MILP Path (`milp.solve_schedule`)

- Builds horizon: today's remaining + all of tomorrow.
- Per-slot continuous vars: charge energy `c[k]`, discharge `d[k]`, spill, soc.
- **Constraints**: SOC dynamics, `soc_min ≤ soc ≤ soc_max`,
  `soc[end] ≥ reserve_target`; `soc[midnight] ≥ reserve_target`
  (self_consumption only — see model section above).
- **Objective**: min `Σ price·c − Σ price·eff·d + cycle_cost·Σd − terminal·min(soc[end], reserve)`
  where `terminal = avg_price × efficiency`.  **The leftover-energy reward is
  capped at the reserve target** (not all the way to `soc_max`).  Rewarding
  every leftover kWh up to full made the solver buy any slot below `avg·eff²`
  to push the battery toward 100% even in pure **cost** mode — over-buying
  cheap-ish energy the horizon has no modelled use for (real symptom: a
  duck-curve cost day charged an extra night slot + 3rd midday slot to end at
  81% where greedy ended 52%, and cost MORE: 0.895 vs 0.600).  Capping the
  reward at the reserve makes the solver fill to the reserve (reward + soft
  penalty) but not beyond for leftover value, so it stops over-buying.
  Arbitrage is unaffected (energy above reserve is sold for the explicit sell
  *revenue* term, not the terminal reward); self_consumption still fills high
  because its reserve is the 1.25× boosted value.  The today extraction target
  is also capped at the LP's allocated charge energy (not just headroom) so the
  discrete schedule reflects the cost-correct intent.
- **Earliness tie-break**: a tiny per-slot deferral penalty on charging
  (`(avg+0.01)·1e-4 · k · c[k]`, far below any real price granularity) makes
  the solver charge the EARLIER of two equal-price slots.  Without it the LP is
  indifferent between same-price slots and tends to charge at the LAST cheap
  slot before a sell — leaving the battery low for hours and exposed to a
  sudden unexpected load.  Charging earlier gives the same-cost plan a safety
  buffer (a real customer preference: "don't hold off to the last minute").
  Never flips a genuine price difference (only breaks ties).
- **Price gates**: `arbitrage_price_delta` → charge/discharge UB = 0 for
  slots outside spread.  `block_export_on_negative_price` → discharge UB = 0.
- **Post-solve (cost-ranked discrete extraction)**: the continuous LP is
  collapsed to discrete full-power slots, capped by battery physics (charge /
  discharge headroom) to prevent over-scheduling.  The collapse executes the
  most cost-effective subset of the slots the LP used:
  - **Discharge: dearest-first.**  When the cap allows fewer sell slots than
    the LP spread energy across (the LP cycles PV — sell early to clear room,
    then sell again at the peak, so the early cheap slot and the peak tie on
    energy), keep the **highest-price** slots.  (Ranking by LP energy used to
    keep the cheap early slot over the 0.40 peak — `to_grid` bug #3.)
  - **Charge: cheapest-first.**  **Today** is capped at the battery's physical
    charge headroom (`(soc_max − current)/eff`) and the cheapest slots are
    taken first, so an expensive reserve-top-off slot the cheaper slots + PV
    already cover gets dropped (MILP charging a 0.30 peak to hit a high
    self_consumption reserve — bug #2).  **Tomorrow** is capped at the energy
    the LP allocated to it, preserving the midnight-reserve **today-first**
    split (a single global cheapest-first cap would let cheap tomorrow slots
    starve today).  The dearest slot is reached only when cheaper ones can't
    fill the battery (heavy load: `cons_heavy_flat` charges both cheap night
    slots — a per-day half-slot rule was tried and removed because it dropped
    the genuinely-needed 2nd cheap slot when the remainder fell just under half
    a slot).
  Pinned by `test_milp_sells_evening_peak_not_cheap_early_slot`,
  `test_milp_no_peak_charge_to_hit_reserve`, and the `to_grid_sell_surplus` /
  `self_suff_flat_low_soc` simulator scenarios.
- Returns `(today_slots, tomorrow_slots)` or `None` → greedy fallback.

### 6. Post-Processing (both engines)

- **Urgent recovery**: if SOC < discharge_min, force immediate charge slots.
- **SOC trajectory**: `_compute_scheduled_soc_trajectory` for today.
- **Tomorrow schedule**: MILP provides it directly; greedy runs
  `_compute_tomorrow_schedule` reconstruction.
- **Flexible loads**: `_schedule_flexible_loads` overlays loads into
  cheap / PV-surplus / negative / battery-charge slots (controlled by
  `ev_charge_strategy` for the EV charger).

### 7. Coordinator Execution (every 10s)

```
_determine_energy_state(battery_soc):
  if auto mode + slot in scheduled_slots:
    if charge slot:
      - charge now (no deferral — trust the power-aware optimiser; see C3)
    if discharge slot:
      - skip if SOC ≤ reserve_target (not just discharge_min)
  if manual mode:
    - simple price vs threshold comparison with hysteresis

_transition_to_state(new_state):
  - writes econ_rule_1_enable, _voltage, _soc, _power
  - discharge SOC floor = reserve_target in auto mode (not discharge_min)
```

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

### 4. Consumption Estimate Sensitivity — PARTLY FIXED
The SOC trajectory (`_project_soc_trajectory`) uses the 7-day **hourly**
consumption profile (`consumption_hourly_kwh`) when available, so it handles
non-flat days (evening peaks, daytime EV charging) accurately.

**Overnight-need now profile-aware too** (fixed July 2026): the reserve
(`calculate_self_consumption_reserve`) used to compute the overnight need as
`consumption_est/24 × overnight_hours` — a FLAT average.  For a daytime-heavy
load (e.g. 2 EVs charging during the day → 72 kWh/d average but low night
consumption) this hugely OVER-estimated the night need (~30 kWh vs a real ~12
kWh base load), pinning the reserve near 100% and forcing evening grid
charging — even though the trajectory (already profile-aware) showed the
battery barely draining overnight.  That mismatch was a phantom deficit
(`predictive_deficit = reserve[flat,high] − min_projected[profile,high]`).
Now the reserve SUMS the hourly profile over the night hours (when the
profile is supplied), making it consistent with the trajectory.  Falls back
to the flat average when no profile exists (new installs).  Wired into the
from_grid greedy and MILP reserve calls (`consumption_hourly_kwh=` arg).
Real customer report: 2-EV house, 80% SOC, MILP booked 5 evening charge
slots; with the profile-aware reserve the phantom deficit vanishes.

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

### 6. Generator-Port Solar Workaround — IMPROVED
TREX-25/50 with micro-inverters on the generator port need special handling.
PV registers read 0 and `generator_day_cost_energy` is unreliable (known
Felicity firmware bug).  Three-tier fallback in `pv_actual_today_kwh`:
1. PV string registers (`pv1-4_day_energy`) — used when > 0.1 kWh
2. Generator/micro-inverter day register — used when > 0.1 kWh
3. **Software-integrated PV** (`_pv_integrated_today_kwh`) — coordinator
   accumulates instantaneous power (`total_generator_power` + PV string
   power) every 10s tick using trapezoidal integration.  Reset at midnight.
   This gives an accurate PV-today reading even when both day-energy
   registers are stuck at zero.  Prevents `pv_confidence` from collapsing
   to 0.1 (which distorts the SOC trajectory and over-schedules grid
   charging) on generator-port installations.

### 7. Forecast.Solar `wh_hours` date handling
`_retrieve_pv_forecast` now filters `wh_hours` entries by today's date before
bucketing them into `pv_hourly_kwh[hour]`. Without that filter, a multi-day
forecast would sum today's + tomorrow's + day-after's values into the same
hour slot. This was especially painful at midnight when stale
`state.state` combined with hour-merged buckets broke the schedule. When
`state.state` still reports the previous day's stale total, the coordinator
falls back to the filtered hourly sum.

**Consequence — tomorrow's PV is daily-total-only.** Because `wh_hours` is
filtered to *today's* date, `pv_hourly_kwh_tomorrow` arrives empty; only the
daily total `pv_forecast_tomorrow` is known.  The MILP reads
`pv_hourly_kwh_tomorrow` directly (no internal synthesis), so when it's empty
the solver plans the WHOLE next day with **PV = 0** and over-buys grid to fill
a battery the sun would fill for free — even a Trader with a big forecast
(real customer: "buying 18/24 slots tomorrow with 42.9 kWh PV coming").  Fixed
in `calculate_schedule`: tomorrow's hourly PV is now synthesized from the daily
total **unconditionally** (`_synthesize_pv_hourly(pv_forecast_tomorrow)` via
`dataclasses.replace`) whenever it's absent — not only inside the today-rebuild
branch (which is skipped in the evening when today's hourly is present).
Greedy's `_compute_tomorrow_schedule` already synthesized internally; this
makes both engines consistent.  Pinned by
`test_milp_synthesizes_daily_only_tomorrow_pv`.

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
Hourly consumption profiles from 7-day HA recorder history. `EMSState.consumption_hourly_kwh` provides per-hour averages used in `_project_soc_trajectory()`, `_validate_schedule_soc()`, and (July 2026) the overnight reserve. Coordinator records hourly breakdown at midnight. Frontend card also uses profiles.

**Data path** (`coordinator`):
1. `_resolve_consumption_entity` — picks the source: the user's
   `consumption_override_entity` if set, else an inverter load-energy sensor
   (`homeload_day_cost_energy` / `load_day_cost_energy` / …) resolved via the
   **entity registry** by the exact `unique_id` (`{entry_id}_{key}`).
2. `_query_hourly_from_history` — HA recorder `statistics_during_period`
   ("change" per hour) → 24 hourly buckets.  Falls back to a FLAT daily/24
   distribution when no statistics exist.
3. `_record_hourly_consumption` (midnight) appends to a 7-day
   `_hourly_consumption_history`; `_calculate_hourly_profile` averages it into
   `_hourly_consumption_profile`.  Persisted in the consumption Store and
   **recomputed on startup** from the loaded history (no cold-start gap).
4. Exposed as `consumption_hourly_profile` in `schedule_status` attributes —
   inspect it to confirm the shape (low-night/high-day for EV loads).  A FLAT
   profile (all hours equal) means the history query fell back (no stats or,
   pre-fix, an unresolved entity).

**Registry-lookup fix (July 2026)**: `_resolve_consumption_entity` previously
GUESSED the entity_id as `sensor.{title}_{key}`.  HA derives the entity_id
from the slugified friendly NAME (e.g. "Homeload Day Cost Energy （0.1KWh）" →
`..._homeload_day_cost_energy_0_1kwh`), so the guess never matched → the hourly
profile silently fell back to FLAT → the profile-aware overnight reserve had no
effect (daytime-heavy / EV loads looked flat overnight again).  Now resolved
via `entity_registry.async_get_entity_id("sensor", DOMAIN, unique_id)` — exact
and rename-proof.  Best accuracy still comes from setting a
`consumption_override_entity` (a real P1/energy meter).

#### C2. SOC History Display — IMPLEMENTED
Coordinator records battery SOC at each slot boundary in `_soc_history`. Frontend draws solid line for actual past SOC, dotted line for projected future.

#### C3. Charge Execution — Trust the Schedule (deferral REMOVED June 2026)
`_determine_energy_state` executes **every** scheduled charge slot when the
current slot is flagged `charge` (and SOC < charge_max).  There is no longer
any per-slot "defer for a cheaper later slot" logic.

**Why the old deferral was removed (it could only ever harm):** the optimizer
(`select_unified_charge_slots`) is cheapest-first AND power-aware — it picks
the cheapest N slots needed to cover the deficit, where N already accounts
for the per-slot charge-rate cap (`min(safe_power, inverter_max − pv) ×
slot_h × eff`).  Two cases:

- If a cheaper LATER slot could cover the deficit, the optimizer simply
  **doesn't schedule the current slot** → `_determine_energy_state` returns
  idle naturally.  No deferral needed.
- The current slot is scheduled **only** when it is one of the cheapest-N
  slots *required*.  Then every scheduled charge slot — including the cheaper
  later ones — is needed, and the rate cap means a skipped slot **cannot be
  made up later**.

So the deferral never saved money (the optimizer already avoids expensive
slots) and only ever fired in the case where skipping was harmful.  Real
customer report: 23% SOC, 9–20 slots scheduled, price 0.042 ≪ threshold
0.161, yet the battery sat **IDLE** draining toward discharge_min because the
deferral kept "waiting for a cheaper slot" (a negative-price slot later) that
was itself already committed to the plan.  The cheapest-slot optimisation now
lives entirely in `ems.py` (the single source of truth); the coordinator just
executes the plan.

History: the deferral previously had escalating guards (SOC-floor, 1¢ gap,
proximity margin) trying to plug the stall, but the stall was structural —
the feature fought the power-aware optimiser.  Removing it is the clean fix.

#### C4. Schedule-Status Attribute Caching — IMPLEMENTED
`HA_FelicityScheduleStatusSensor` caches `extra_state_attributes` and
rebuilds only in `_handle_coordinator_update`. Avoids rebuilding the
attribute dict (with 96-slot arrays, hourly PV/consumption maps, SOC
history) on every HA state read — fixes a `helpers/entity.py:1214` slow-
update warning.

#### C7. Consumption Deviation Correction — IMPLEMENTED
When an unexpected load (car charger, oven, EV) drains the battery faster
than the 7-day consumption profile predicted, the algorithm now detects the
deviation and adds compensating charge slots.  Compares the previous
schedule's predicted SOC at the current slot with the actual SOC.  If actual
is significantly below predicted (>1 kWh floor, 0.5× damped), the deviation
feeds into the deficit calculation so the next recalc plans extra charging.

**Mechanism**: `_consumption_deviation_kwh()` helper, called in both
`_schedule_from_grid` and `_schedule_both`.  For MILP, the deviation
lowers `current_kwh` so the solver sees a less-full battery.
Coordinator feeds `predicted_soc_pct` from `_backend_soc_trajectory[current_slot]`
into `EMSState`.

**Guards** (ems side): >1 kWh noise floor; 0.5× damping to avoid
over-reaction.

**Sustained-only gate + fires below reserve** (fixed July 2026): the
correction used to be gated on `current_kwh > reserve_target` on the
assumption that "below reserve, urgent recovery charges."  But urgent
recovery only fires below `discharge_min`, leaving the entire
`reserve → discharge_min` band uncovered — exactly where a sustained
unexpected load (air-conditioning, EV) drains the battery toward the
overnight floor.  A real customer at ~62% SOC (below an 80% reserve) with
high AC load saw no extra recovery.  Now:
- The `current_kwh > reserve_target` gate is **removed** — the correction
  fires across the whole band (and above reserve).
- **Persistence gate moved to the coordinator**: it sets
  `predicted_soc_pct` (which enables the correction) ONLY when the actual
  SOC has stayed significantly below the predicted trajectory for
  `DEVIATION_MIN_DURATION_S` (30 min).  "Significant" = `max(1.5 kWh,
  3% of capacity)`.  This filters transients (kettle, oven preheat, an AC
  compressor cycling on for two minutes) — only a real sustained load (AC,
  EV) triggers extra charging.
- **Stops on normalisation**: the coordinator tracks `_deviation_since_ts`
  and resets it the moment consumption returns to trend, so it stops
  handing the prediction to the algorithm and the extra charge ends on the
  next recalc.  Energy already stored stays — it never force-discharges.
- **Cost stays minimal**: the deviation only sizes the extra deficit; the
  normal cheapest-slot selection covers it, so it buys from the cheapest
  available slots, not at peak.

**Real-world scenario**: car charger at 3.7 kW drains ~7 kWh in 2 hours.
Weekly avg consumption is 6.3 kWh/d.  Without this fix, the algorithm saw
"reserve met" and did nothing.  With the fix, after the load has run long
enough to register as sustained, the deviation-induced deficit triggers
compensating charge from the cheapest available slot.

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
| #1 | Cheap-slot deferral REMOVED (fought power-aware optimiser; see C3) | coordinator |
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
- `self_consumption`: **tops off the battery from cheap slots, cost-aware.**
  After the normal survival deficit is covered, `select_unified_charge_slots`
  fills today toward max-SOC headroom — but ONLY from slots cheap enough that
  round-trip losses still pay off: charge at price `P` only when
  `P <= efficiency² × mean_remaining_price`.  This tops the battery off using
  the cheapest slots of the day and **never charges at expensive prices** (an
  EMS minimises cost above all).  On a flat or expensive day no slot clears
  the bar, so nothing extra is charged — the battery rides on the reserve the
  survival deficit secured.  PV-aware headroom still skips what solar will
  supply.  Also multiplies the *reserve floor* by 1.25× (matters in
  to_grid/both — keeps more stored energy from being sold).

  The MILP achieves the same automatically: its terminal value
  (`mean × efficiency`) makes the solver charge any slot priced below
  `mean × efficiency²` to store energy for later — the same round-trip bar as
  the greedy gate, so the two engines agree.  **No P90/aggressive boost** —
  pushing the terminal value higher would charge at uneconomic prices.

  Reserve floor ≠ charging ceiling: the reserve target is only an
  *overnight-survival floor*.  Daytime/evening consumption is handled by the
  **predictive SOC trajectory** (`_project_soc_trajectory`), which charges (in
  any priority) when consumption would drain SOC below the floor before
  tomorrow's sunrise.  `TestSelfConsumptionFillsBattery` (incl.
  `test_self_consumption_never_charges_expensive`) pins all of this.

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
- Options-flow entity pickers use `description={"suggested_value": ...}` and
  are merged onto the existing options with `my_options.update(user_input)` —
  **absence is NOT treated as a clear** (fixed).  The old "absent→clear" loop
  wiped every entity assignment the user didn't re-touch on ANY unrelated
  Settings save, because the HA frontend can omit an untouched suggested-value
  optional selector from the submitted form.  Real customer symptom: an update
  during which Settings was opened+saved silently cleared
  `flexible_load_1_current_entity` → `is_ev_charger` went false → the EV Boost
  button disappeared (and Nordpool/forecast assignments could vanish the same
  way).  An EXPLICIT clear still works (the frontend submits a cleared picker
  as `None`/`""`, which `.update()` applies); an untouched one is preserved.
  `__init__.async_setup_entry` also logs the load-1 EV config at startup so
  "button disappeared" reports can be diagnosed from the log directly.
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

#### #2 MILP Optimizer — IMPLEMENTED
`milp.py` implements an LP scheduler using PuLP/CBC.  Selected via
`scheduler_engine=milp`.  Silently falls back to greedy on any
failure.  See "Optional MILP Scheduler" section above for the full
model description, settings traceability, and known behavioral
differences vs greedy.  Both engines are actively tested (13 MILP
tests + 5 MILP-vs-Greedy parity tests).

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

Tests are in `tests/test_ems.py` (250 tests). They import `ems.py` directly (bypassing HA dependencies) and test the pure scheduling functions.

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
- EV charge strategy (smart, always_on, solar_only, cheap_only)
- Sell coverage (both mode charges today to cover today's sell slots)
- MILP scheduler: cheapest charge, arbitrage, no-charge-when-full, tomorrow, fallback
- MILP vs Greedy parity: from_grid, both arbitrage, cross-day, cloudy, large battery
- Self-sufficiency today-first: greedy charges today, MILP midnight constraint
- Self-consumption top-off: charges above reserve, cost-gated, never charges expensive
- Schedule reason messages
- Consumption deviation correction (car charger detection, noise filter, below-reserve guard, both mode)

**Not tested**: coordinator.py runtime logic (requires HA mocking).  Since
the coordinator now delegates to `ems.calculate_schedule()`, algorithm
drift is structurally prevented.  What remains untested is the coordinator's
own logic: EMSConfig/EMSState construction, `_determine_energy_state`
(slot deferral, override bypass), `_transition_to_state` (Modbus writes),
`_check_safe_power` (current monitoring), and `_actuate_flex_loads`.

---

## Quick Reference: Debugging a Scheduling Issue

1. Check `schedule_status` sensor attributes for `sim_params` (battery state, PV, consumption)
2. Look at `self_consumption_reserve` and calculate `reserve_target`
3. Check `pv_actual_today_kwh` vs forecast → confidence factor
4. Compare `net_pv_kwh` with actual remaining PV
5. Look at `yesterday_deficit_kwh` — may be inflating today's target
6. Check `slot_schedule` for what slots were selected and their prices
7. Enable debug logging: `custom_components.ha_felicity.ems` and `custom_components.ha_felicity.coordinator`
