# TREX-5/10K Power Management

Register-level details for safe power management on TREX-5 and TREX-10K models. For the algorithm overview, see [Power Management Basics](Power-Management-Basics).

## Grid Current Registers

The safe power system monitors grid current on all three phases:

| Register | Address | Index | Unit | Description |
|---|---|---|---|---|
| `ac_input_current` | 4362 | 1 (/10) | A | Phase 1 (L1) grid current |
| `ac_input_current_l2` | 4385 | 1 (/10) | A | Phase 2 (L2) grid current |
| `ac_input_current_l3` | 4389 | 1 (/10) | A | Phase 3 (L3) grid current |

Index 1 means the raw register value is divided by 10 to get the actual amperage (e.g., register value 163 = 16.3A). Values are signed.

## Response Tiers

Every 10 seconds, the coordinator reads all three phase currents and compares the highest to `max_amperage_per_phase`:

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
| `econ_rule_1_power` | 8576 | **Watts** | Charge/discharge power limit |

For example, reducing from 5 kW to 3 kW writes `3000` to this register.

## Load Shedding Priority Chain

When flexible loads are configured, the system reduces load before touching battery power:

| Priority | Action | Register/Method |
|---|---|---|
| 1st | EV charger current step-down | Via `number.set_value` or `select.select_option` on the configured current entity |
| 2nd | Binary load shedding | Via `switch.turn_off` on configured switch entities (highest shed priority first) |
| 3rd | Battery power reduction | Writes reduced wattage to `econ_rule_1_power` |

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

During an active EV Boost override, safe power can still step down the EV charger current but will never fully turn off the charger. The user explicitly requested urgent charging.

## Grid Power Register

Used by the anti-conflict guard to detect grid import during discharge:

| Register | Address | Unit | Description |
|---|---|---|---|
| `total_ac_input_power` | 4392 | Watts (signed) | Positive = import, negative = export |

### Anti-Conflict Hysteresis

| Import Level | Behavior |
|---|---|
| 200-2000W sustained 2+ cycles (~20s) | Suppress discharge |
| > 2000W | Suppress immediately |
| After suppression ends | 60-second cooldown before re-evaluation |

## Safe Power Configuration

| Setting | Range | Default | Description |
|---|---|---|---|
| `safe_power_management` | auto / on / off | auto | `auto`: active when grid_mode is not off |
| `max_amperage_per_phase` | 10-63A | 16 | Match to your main breaker rating |
| `power_level` | 1-5 kW (TREX-5) / 1-10 kW (TREX-10) | 5 | Target power and recovery ceiling |

## External Change Detection

The coordinator detects when the user adjusts power via the inverter's own app or panel. If the register value differs from the last value the integration wrote, it updates its internal tracking to prevent fighting with the user's manual adjustment.
