# TREX-25/50K Power Management

Register-level details for safe power management on TREX-25K and TREX-50K models. For the algorithm overview, see [Power Management Basics](Power-Management-Basics).

## Grid Current Registers

The safe power system monitors grid current via CT (current transformer) sensors on all three phases:

| Register | Address | Index | Unit | Description |
|---|---|---|---|---|
| `phase_a_ct_current` | 4441 | 8 (signed, /10) | A | Phase A grid current |
| `phase_b_ct_current` | 4442 | 8 (signed, /10) | A | Phase B grid current |
| `phase_c_ct_current` | 4443 | 8 (signed, /10) | A | Phase C grid current |

Index 8 means the raw register value is a signed integer divided by 10 to get the actual amperage (e.g., register value 163 = 16.3A). Negative values indicate export (current flowing to grid).

### Difference from TREX-5/10K

The TREX-5/10K uses `ac_input_current` registers at different addresses (4362, 4385, 4389) with index 1. The TREX-25/50K uses dedicated CT current registers at consecutive addresses (4441-4443) with index 8 (signed).

## Response Tiers

The same algorithm applies across all models. Every 10 seconds, the highest phase current is compared to `max_amperage_per_phase`:

| Condition | Response |
|---|---|
| Any phase > 95% of max | Emergency: reduce/shed 2 kW |
| Any phase > 80% of max | Caution: reduce/shed 1 kW |
| All phases < 70% of max | Recovery: restore 1 kW |
| All phase currents = 0 | Jump to full Power Level |

## Power Register

When safe power management adjusts the power level, it writes to:

| Register | Address | Unit | Description |
|---|---|---|---|
| `econ_rule_1_power` | 8719 | **kW** | Charge/discharge power limit |

The coordinator divides the internal watt value by 1000 before writing. For example, reducing from 5 kW to 3 kW writes `3` to this register (not 3000).

## Load Shedding Priority Chain

When flexible loads are configured, the system reduces load before touching battery power:

| Priority | Action | Method |
|---|---|---|
| 1st | EV charger current step-down | Via `number.set_value` or `select.select_option` on the configured current entity |
| 2nd | Binary load shedding | Via `switch.turn_off` on configured switch entities (highest shed priority first) |
| 3rd | Battery power reduction | Writes reduced kW value to `econ_rule_1_power` |

### EV Current Stepping

The EV charger (load slot 1) uses configurable current steps. Each safe-power cycle reduces by one step:

**Example steps**: 6, 10, 13, 16, 20, 25A

If the charger is at 20A and overcurrent is detected, first cycle drops to 16A, next to 13A, etc. During recovery, steps increase in reverse.

### Binary Load Shedding

Loads 1-3 each have a shed priority (1-3). Higher numbers are shed first:
- Priority 3 shed before priority 2, priority 2 before priority 1
- One load is shed per cycle
- Recovery restores in reverse order (lowest priority number first)

### EV Boost Protection

During an active EV Boost override, safe power can still step down the EV charger current but will never fully turn off the charger.

## Grid Power Register

Used by the anti-conflict guard to detect grid import during discharge:

| Register | Address | Unit | Description |
|---|---|---|---|
| `total_grid_power` | 4500 | kW (×10 scaling) | Primary grid power reading |

The integration multiplies by 1000 to convert to watts for internal comparisons. Falls back to summing per-phase CT active power registers if the total register is unavailable.

### Anti-Conflict Hysteresis

| Import Level | Behavior |
|---|---|
| 200-2000W sustained 2+ cycles (~20s) | Suppress discharge |
| > 2000W | Suppress immediately |
| After suppression ends | 60-second cooldown before re-evaluation |

## Peak Shaving Interaction

The TREX-25/50K has peak shaving registers that interact with safe power:

| Register | Address | Description |
|---|---|---|
| `grid_peak_shaving_enable` | 8520 | Enabled during charge and idle, disabled during discharge |
| `grid_peak_shaving_power` | 8521 | Set to 0 during discharge and idle |

During state transitions, peak shaving is disabled when discharging (it interferes with grid selling). Safe power management adjusts `econ_rule_1_power` regardless of peak shaving state.

## Safe Power Configuration

| Setting | Range | Default | Description |
|---|---|---|---|
| `safe_power_management` | auto / on / off | auto | `auto`: active when grid_mode is not off |
| `max_amperage_per_phase` | 10-63A | 16 | Match to your main breaker rating |
| `power_level` | 1-25 kW (TREX-25) / 1-50 kW (TREX-50) | 5 | Target power and recovery ceiling |

## External Change Detection

The coordinator detects when the user adjusts power via the inverter's own app or panel. If the register value differs from the last value the integration wrote, it updates its internal tracking to prevent fighting with the user's manual adjustment.
