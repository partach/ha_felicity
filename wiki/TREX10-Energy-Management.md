# TREX-5/10K Energy Management

Register-level details for the TREX-5 and TREX-10K inverter models. For algorithm overview, see [Energy Management Basics](Energy-Management-Basics).

## Operating Mode

The inverter must be set to **Economic Mode** for the EMS to function.

| Register | Address | Values |
|---|---|---|
| `operating_mode` | 8451 | 0 = General (self-use), 1 = Backup (no discharge), **2 = Economic** |

The integration reads this register but does not write it. The user must set it manually via the inverter panel or app.

## Economic Rule 1 Registers

The EMS controls the inverter through Economic Rule 1. Every 10 seconds the coordinator evaluates the current slot and writes these registers when the desired state changes.

| Register | Address | Range | Unit | Description |
|---|---|---|---|---|
| `econ_rule_1_enable` | 8568 | 0 / 1 / 2 | — | 0 = idle, 1 = charge, 2 = discharge |
| `econ_rule_1_start_time` | 8569 | 00:00-23:59 | HH:MM | Rule 1 time window start |
| `econ_rule_1_stop_time` | 8570 | 00:00-23:59 | HH:MM | Rule 1 time window end (firmware uses 23:59 for all-day) |
| `econ_rule_1_start_day` | 8571 | — | date | Start date (written by coordinator) |
| `econ_rule_1_stop_day` | 8572 | — | date | Stop date (written by coordinator) |
| `econ_rule_1_effective_week` | 8573 | 0x00-0x7F | bitmask | Weekday mask: bit0=Sunday .. bit6=Saturday |
| `econ_rule_1_voltage` | 8574 | 50-60 | V | Charge voltage setpoint |
| `econ_rule_1_soc` | 8575 | 0-100 | % | SOC target (charge ceiling or discharge floor) |
| `econ_rule_1_power` | 8576 | 0-10,000 | **Watts** | Charge/discharge power limit |

### Key Point: Power in Watts

The TREX-5/10K power register uses **Watts** (not kW). The coordinator writes the `safe_max_power` value directly in watts. For example, a 5 kW power level is written as `5000`.

## State Transitions

The coordinator writes these register combinations for each state:

### Charging

| Register | Value |
|---|---|
| `econ_rule_1_enable` | 1 |
| `econ_rule_1_voltage` | `voltage_level` (default 58V) |
| `econ_rule_1_soc` | `battery_charge_max_level` (default 100%) |
| `econ_rule_1_power` | `safe_max_power` in watts |
| `econ_rule_1_start_day` | today |
| `econ_rule_1_stop_day` | today |

### Discharging

| Register | Value |
|---|---|
| `econ_rule_1_enable` | 2 |
| `econ_rule_1_voltage` | `discharge_min_voltage` (default 50V) |
| `econ_rule_1_soc` | `reserve_target_pct` (auto mode) or `battery_discharge_min_level` (manual/off) |
| `econ_rule_1_power` | `safe_max_power` in watts |
| `econ_rule_1_start_day` | today |
| `econ_rule_1_stop_day` | today |

### Idle

| Register | Value |
|---|---|
| `econ_rule_1_enable` | 0 |

When transitioning to idle, only the enable register is written. Voltage, SOC, and power registers are left unchanged.

## Discharge SOC Floor

In auto mode, the SOC register during discharge is set to the **computed reserve target**, not the raw `battery_discharge_min_level`. This makes the inverter's hardware floor match the schedule's planned reserve. If the EMS is off or in manual mode, the user's `discharge_min` setting is used instead.

## Battery SOC Reading

| Register | Description |
|---|---|
| `battery_capacity` | Single SOC register (0-100%) |

The TREX-5/10K has a single battery bank with one SOC register.

## Battery Voltage Reading

| Register | Description |
|---|---|
| `battery_voltage` | Single voltage register |

Values below 10V are treated as invalid (sensor offline).

## Rule 1 Time Window

When `rule1_time_window` is set to `auto`, the coordinator writes:
- `econ_rule_1_start_time` = 00:00
- `econ_rule_1_stop_time` = 23:59

The firmware does not accept stop=00:00 or stop=24:00, so 23:59 is used for all-day coverage.

When `rule1_weekday` is set to `auto`, the coordinator writes:
- `econ_rule_1_effective_week` = 0x7F (all 7 days)

If these remain on `manual`, the integration does not touch user-configured values. However, if the inverter's Rule 1 window is too restrictive, scheduled actions outside that window will silently fail. The EMS card shows a warning banner when this conflict is detected.

## Date Registers

The TREX-5/10K uses `econ_rule_1_start_day` and `econ_rule_1_stop_day` date registers. The coordinator writes today's date during state transitions.

## Inverter Max Power Cap

| Model | Max Power |
|---|---|
| TREX-5 | 5 kW |
| TREX-10 | 10 kW |

The schedule algorithm caps grid charge power when PV is active:

```
grid_kw = min(safe_power_kw, inverter_max_power_kw - pv_kw)
```

This prevents unrealistic charge rates (e.g., 8 kW grid + 7 kW PV = 15 kW on a 10 kW inverter).

## Grid Power Reading

| Register | Address | Unit | Description |
|---|---|---|---|
| `total_ac_input_power` | 4392 | Watts (signed) | Total grid power (2-register, big-endian) |

Positive values indicate grid import, negative values indicate grid export.

## First Register Address

The TREX-5/10K polling starts at register address **4353**.
