# TREX-25/50K Energy Management

Register-level details for the TREX-25K and TREX-50K inverter models. For algorithm overview, see [Energy Management Basics](Energy-Management-Basics).

## Operating Mode

The TREX-25/50K uses a different mode system than the TREX-5/10K:

| Register | Address | Values |
|---|---|---|
| `system_mode` | 8516 | 0 = Selling Mode, 1 = Zero Export To Load, 2 = Zero Export To CT |
| `zero_export_to_load_sell_enable` | 8517 | 0/1 |
| `zero_export_to_ct_sell_enable` | 8518 | 0/1 |
| `zero_export_mode_selection` | 8523 | Mode selection |

The EMS reads these registers to determine the current operating mode. The user configures the mode via the inverter panel or app.

## Economic Rule 1 Registers

The TREX-25/50K uses **separate enable registers** for charge and discharge, unlike the TREX-5/10K's single `econ_rule_1_enable` register:

| Register | Address | Range | Unit | Description |
|---|---|---|---|---|
| `econ_rule_1_grid_charge_enable` | 8713 | 0 / 1 | — | Enable grid charging |
| `econ_rule_1_gen_charge_enable` | 8714 | 0 / 1 | — | Enable generator charging |
| `econ_rule_1_sell_enable` | 8703 | 0 / 1 | — | Enable grid selling (discharge) |
| `econ_rule_1_start_time` | 8715 | 00:00-23:59 | HH:MM | Rule 1 time window start |
| `econ_rule_1_stop_time` | 8716 | 00:00-23:59 | HH:MM | Rule 1 time window end |
| `econ_rule_1_voltage` | 8717 | 48-500 | V | Charge voltage setpoint (wider range for LV/HV) |
| `econ_rule_1_soc` | 8718 | 0-100 | % | SOC target |
| `econ_rule_1_power` | 8719 | 0-25/50 | **kW** | Charge/discharge power limit |

### Key Differences from TREX-5/10K

1. **Split enable registers**: Instead of `econ_rule_1_enable` with values 0/1/2, the TREX-25/50K uses separate `grid_charge_enable` and `sell_enable` registers
2. **Power in kilowatts**: Register 8719 uses kW, not watts. The coordinator divides by 1000 before writing (e.g., 5000W becomes 5)
3. **Wider voltage range**: 48-500V to cover both low-voltage and high-voltage battery configurations
4. **No date registers**: The TREX-25/50K does not use start/stop date registers
5. **Peak shaving registers**: Additional registers for grid peak shaving control

## Peak Shaving Registers

The TREX-25/50K has peak shaving registers that the EMS uses as part of state transitions:

| Register | Address | Range | Unit | Description |
|---|---|---|---|---|
| `grid_peak_shaving_enable` | 8520 | 0 / 1 | — | Enable peak shaving |
| `grid_peak_shaving_power` | 8521 | 0-25/50 | kW | Peak shaving power limit |

These are used internally by the state transition logic (see below) and are not directly configurable by the user through the EMS.

## State Transitions

The TREX-25/50K requires multi-register writes for each state change. The coordinator's `_handle_econ_rule_1_enable` translates the simple 0/1/2 state into the correct register combination:

### Charging (enable=1)

| Register | Value | Purpose |
|---|---|---|
| `grid_peak_shaving_enable` | 1 | Enable peak shaving during charge |
| `econ_rule_1_sell_enable` | 0 | Disable selling while charging |
| `econ_rule_1_grid_charge_enable` | 1 | Enable grid charging |

Additional registers written by `_transition_to_state`:

| Register | Value |
|---|---|
| `econ_rule_1_voltage` | `voltage_level` (default 58V, HV systems use 300-448V) |
| `econ_rule_1_soc` | `battery_charge_max_level` (default 100%) |
| `econ_rule_1_power` | `safe_max_power` in kW |

### Discharging (enable=2)

| Register | Value | Purpose |
|---|---|---|
| `grid_peak_shaving_enable` | 0 | Disable peak shaving (interferes with selling) |
| `econ_rule_1_sell_enable` | 1 | Enable selling |
| `econ_rule_1_grid_charge_enable` | 0 | Disable grid charging |
| `grid_peak_shaving_power` | 0 | Clear peak shaving power |

Additional registers:

| Register | Value |
|---|---|
| `econ_rule_1_voltage` | `discharge_min_voltage` (default 50V, HV: 300-448V) |
| `econ_rule_1_soc` | `reserve_target_pct` (auto) or `battery_discharge_min_level` (manual) |
| `econ_rule_1_power` | `safe_max_power` in kW |

### Idle (enable=0)

| Register | Value | Purpose |
|---|---|---|
| `grid_peak_shaving_enable` | 1 | Re-enable peak shaving |
| `econ_rule_1_sell_enable` | 1 | Re-enable selling |
| `econ_rule_1_grid_charge_enable` | 0 | Disable grid charging |
| `grid_peak_shaving_power` | 0 | Clear peak shaving power |

## Battery SOC Reading

The TREX-25/50K supports dual battery banks:

| Register | Description |
|---|---|
| `bat1_soc` | Battery bank 1 SOC (0-100%) |
| `bat2_soc` | Battery bank 2 SOC (0-100%) |

The integration uses the **minimum** of both banks as the effective SOC. This is the conservative approach — the schedule plans around the weakest bank. If only one bank is connected, the other register is ignored (bat2_soc = 0 means only bat1 is used).

## Battery Voltage Reading

| Register | Description |
|---|---|
| `bat1_voltage` | Battery bank 1 voltage |
| `bat2_voltage` | Battery bank 2 voltage |

The integration prefers `bat1_voltage` if valid (>10V), falls back to `bat2_voltage`. Values below 10V are treated as invalid.

## Inverter Max Power Cap

| Model | Max Power |
|---|---|
| TREX-25 | 25 kW |
| TREX-50 | 50 kW |

The algorithm caps grid charge power when PV is active:

```
grid_kw = min(safe_power_kw, inverter_max_power_kw - pv_kw)
```

## Grid Power Reading

| Register | Address | Unit | Description |
|---|---|---|---|
| `total_grid_power` | 4500 | kW (×10 scaling) | Total grid power (primary) |
| `phase_a_ct_active_power` | — | kW | Phase A active power (fallback) |
| `phase_b_ct_active_power` | — | kW | Phase B active power (fallback) |
| `phase_c_ct_active_power` | — | kW | Phase C active power (fallback) |

The integration prefers `total_grid_power`. If unavailable, it sums the three phase CT readings. The value is multiplied by 1000 to convert to watts for internal use.

## Generator-Port Solar Workaround

Some TREX-25/50K installations use micro-inverters connected to the generator port. In this configuration:

- Standard PV registers read 0
- The integration falls back to `generator_day_cost_energy` for production data
- Both backend and frontend handle this scenario

## First Register Address

The TREX-25/50K polling starts at register address **4357**.

## Register Data Types

The TREX-25/50K uses different scaling than the TREX-5/10K for several registers:

| Index | Scaling | Used For |
|---|---|---|
| 1 | ÷10 | Voltage, power (kW) |
| 8 | signed, ÷10 | CT currents |
| 9 | signed, ÷100 | Grid power (kW) |

When writing registers, the inverse scaling is applied (e.g., index 1 values are multiplied by 10 before writing).
