# TREX-5/6/10K Power Management

Register-level details for safe power management on TREX-5K, TREX-6K and TREX-10K models. For the algorithm overview, see [Power Management Basics](Power-Management-Basics).

## Grid Current Registers

| Register | Address | Index | Unit | Description |
|---|---|---|---|---|
| `ac_input_current` | 4362 | 1 (/10) | A | Phase 1 (L1) grid current |
| `ac_input_current_l2` | 4385 | 1 (/10) | A | Phase 2 (L2) grid current — T-REX-10K only |
| `ac_input_current_l3` | 4389 | 1 (/10) | A | Phase 3 (L3) grid current — T-REX-10K only |

Index 1 means the raw register value is divided by 10 (e.g. 163 = 16.3A). The T-REX-5K/6K are single-phase and have only L1.

## The Rule

Every poll the highest phase current is compared to `max_amperage_per_phase`:

| Condition | Response |
|---|---|
| A phase over the limit, battery charging/discharging above 1 kW | Cut the Rule 1 power in one step so the busiest phase lands at 90 % |
| A phase over 95 %, battery at 1 kW (or idle) | Step the EV current down / shed a load |
| Power Level fits again (or battery idle) | Back to the Power Level in one write |
| Within 30 s of a cut | Nothing (the inverter is still ramping down) |
| Otherwise | Nothing written |

One kW of battery moves each phase by about **1.45 A on the T-REX-10K** (three phases) and **4.35 A on the T-REX-5K/6K** (one phase); the cut is sized with that.

## Power Register

| Register | Address | Unit | Description |
|---|---|---|---|
| `econ_rule_1_power` | 8576 | **Watts** | Grid charge/discharge power limit |

Cutting from 8 kW to 4 kW writes `4000`; returning to the Power Level writes e.g. `8000`.

## Load Shedding Priority Chain

| Priority | Action | Method |
|---|---|---|
| 1st | Battery power reduction | Writes the reduced wattage to `econ_rule_1_power` |
| 2nd | EV charger current step-down | `number.set_value` or `select.select_option` on the configured current entity |
| 3rd | Binary load shedding | `switch.turn_off` on configured switch entities (highest shed priority first) |

A shed load is held off for 5 minutes and returns only when it fits on its phase (see [Power Management Basics](Power-Management-Basics)). During an EV Boost the charger current can be stepped down but the charger is never switched off.

## Grid Power Register

Used by the anti-conflict guard to detect grid import during discharge:

| Register | Address | Unit | Description |
|---|---|---|---|
| `total_ac_input_power` | 4392 | Watts (signed) | Positive = import, negative = export |

### Anti-Conflict Hysteresis

| Import Level | Behavior |
|---|---|
| 200-2000W for 2+ polls | Suppress discharge |
| > 2000W | Suppress immediately |
| After a suppression | Discharge held off for 5 minutes, then one retry |

## Safe Power Configuration

| Setting | Range | Default | Description |
|---|---|---|---|
| `safe_power_management` | auto / on / off | auto | `auto`: active when grid_mode is not off |
| `max_amperage_per_phase` | 10-63A | 16 | Match to your main breaker rating |
| `power_level` | 1-5 kW (T-REX-5) / 1-6 kW (T-REX-6) / 1-10 kW (T-REX-10) | 5 | The Rule 1 power the battery normally runs at |
| `flexible_load_1_phase` | unknown / L1 / L2 / L3 | unknown | The phase a one-phase EV charger is on (T-REX-10K) |

## External Change Detection

If the Rule 1 power register is changed outside the integration (inverter app or panel), the integration adopts the new value. Under the rule above it then puts it back to your Power Level when that fits — change the **Power Level** setting instead if you want a different level.
