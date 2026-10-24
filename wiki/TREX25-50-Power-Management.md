# TREX-25/50K (and IVGM) Power Management

Register-level details for safe power management on TREX-25K, TREX-50K and the IVGM family. For the algorithm overview, see [Power Management Basics](Power-Management-Basics).

## Grid Current Registers

The safe power system monitors grid current via CT (current transformer) sensors:

| Register | Address | Index | Unit | Description |
|---|---|---|---|---|
| `phase_a_ct_current` | 4441 | 8 (signed, /10) | A | Phase A grid current |
| `phase_b_ct_current` | 4442 | 8 (signed, /10) | A | Phase B grid current |
| `phase_c_ct_current` | 4443 | 8 (signed, /10) | A | Phase C grid current (not on IVGM-8K) |

Index 8 means a signed value divided by 10 (e.g. 163 = 16.3A). Negative values indicate export.

### Difference from TREX-5/10K

The TREX-5/10K uses `ac_input_current` registers at 4362, 4385, 4389 with index 1. The TREX-25/50K and IVGM use dedicated CT registers at 4441-4443 with index 8 (signed).

## The Rule

The same rule applies to every model. Every poll the highest phase current is compared to `max_amperage_per_phase`:

| Condition | Response |
|---|---|
| A phase over the limit, battery charging/discharging above 1 kW | Cut the Rule 1 power in one step so the busiest phase lands at 90 % |
| A phase over 95 %, battery at 1 kW (or idle) | Step the EV current down / shed a load |
| Power Level fits again while the battery runs | Back to the Power Level in one write |
| Battery idle (EMS on) | Nothing written — the next charge/discharge starts at the Power Level |
| Within 30 s of a cut | Nothing (the inverter is still ramping down) |
| Otherwise | Nothing written |

On a three-phase inverter 1 kW of battery moves each phase by about 1.45 A; on the single-phase IVGM-8K by about 4.35 A.

## Power Register

| Register | Address | Unit | Description |
|---|---|---|---|
| `econ_rule_1_power` | 8719 | **kW** (TREX-25/50) / **W** (IVGM) | Grid charge/discharge power limit |

The integration works in watts internally and converts per model.

**TREX-25/50 / IVGM note:** going idle writes `econ_rule_1_power = 0` on purpose (part of the idle state, see [TREX25/50 Energy Management](TREX25-50-Energy-Management)). Safe power leaves it at 0 while the battery is idle; the next charge or discharge writes your Power Level again.

## Load Shedding Priority Chain

| Priority | Action | Method |
|---|---|---|
| 1st | Battery power reduction | Writes the reduced value to `econ_rule_1_power` |
| 2nd | EV charger current step-down | `number.set_value` or `select.select_option` on the configured current entity |
| 3rd | Binary load shedding | `switch.turn_off` on configured switch entities (highest shed priority first) |

A shed load is held off for 5 minutes and returns only when it fits on its phase (see [Power Management Basics](Power-Management-Basics)). During an EV Boost the charger current can be stepped down but the charger is never switched off.

## Grid Power Register

Used by the anti-conflict guard to detect grid import during discharge:

| Register | Address | Unit | Description |
|---|---|---|---|
| `total_grid_power` | 4500 | kW (signed, ÷100) | Primary grid power reading |

Falls back to summing the per-phase CT active power registers if the total is unavailable.

### Anti-Conflict Hysteresis

| Import Level | Behavior |
|---|---|
| 200-2000W for 2+ polls | Suppress discharge |
| > 2000W | Suppress immediately |
| After a suppression | Discharge held off for 5 minutes, then one retry |

## Peak Shaving Interaction

| Register | Address | Description |
|---|---|---|
| `grid_peak_shaving_enable` | 8520 | On while charging and idle, off while discharging |
| `grid_peak_shaving_power` | 8521 | Set to 0 while discharging and idle |

Peak shaving is switched off for discharging because it interferes with selling. Safe power adjusts `econ_rule_1_power` regardless of the peak shaving state.

## Safe Power Configuration

| Setting | Range | Default | Description |
|---|---|---|---|
| `safe_power_management` | auto / on / off | auto | `auto`: active when grid_mode is not off |
| `max_amperage_per_phase` | 10-63A | 16 | Match to your main breaker rating |
| `power_level` | 1 kW – model rating | 5 | The Rule 1 power the battery normally runs at |
| `flexible_load_1_phase` | unknown / L1 / L2 / L3 | unknown | The phase a one-phase EV charger is on |

## External Change Detection

If the Rule 1 power register is changed outside the integration (inverter app or panel), the integration adopts the new value. Under the rule above it then puts it back to your Power Level when that fits — change the **Power Level** setting instead if you want a different level.
