# TREX-25/50K (and IVGM) Energy Management

Register-level details for the TREX-25K and TREX-50K inverter models. The **IVGM family (8K / 15K / 20K, provisional)** uses the same control layout — see [IVGM differences](#ivgm-differences-provisional) below. For the algorithm overview, see [Energy Management Basics](Energy-Management-Basics).

## Operating Mode

| Register | Address | Values |
|---|---|---|
| `system_mode` | 8516 | 0 = Selling Mode, 1 = Zero Export To Load, 2 = Zero Export To CT |
| `zero_export_to_load_sell_enable` | 8517 | 0/1 |
| `zero_export_to_ct_sell_enable` | 8518 | 0/1 |
| `eco_timeofuse` | 8711 | 0/1 — **1 = Economic (time-of-use) mode** |
| `eco_effectiveweek` | 8712 | Weekday mask (0x7F = all days) |

The inverter only obeys Rule 1 when `eco_timeofuse` = 1. The integration writes it itself when it charges or discharges, **before** enabling the rule; if that write fails, the rule is not enabled. While an action runs, the integration checks it every poll and re-writes the whole state if it reads 0 ("self-heal"). With **Grid Mode = off** nothing is written, also not at startup.

## Economic Rule 1 Registers

The TREX-25/50K uses **separate enable registers** for charge and discharge, unlike the TREX-5/10K's single `econ_rule_1_enable`:

| Register | Address | Range | Unit | Description |
|---|---|---|---|---|
| `econ_rule_1_sell_enable` | 8703 | 0 / 1 | — | Enable grid selling (discharge) — **not on IVGM** |
| `econ_rule_1_grid_charge_enable` | 8713 | 0 / 1 | — | Enable grid charging |
| `econ_rule_1_gen_charge_enable` | 8714 | 0 / 1 | — | Enable generator charging |
| `econ_rule_1_start_time` | 8715 | 00:00-23:59 | HH:MM | Rule 1 time window start |
| `econ_rule_1_stop_time` | 8716 | 00:00-23:59 | HH:MM | Rule 1 time window end |
| `econ_rule_1_voltage` | 8717 | 48-500 | V | Voltage setpoint (wide range for LV/HV batteries) |
| `econ_rule_1_soc` | 8718 | 0-100 | % | SOC target |
| `econ_rule_1_power` | 8719 | 0-25/50 | **kW** (0.1 kW resolution) | Grid charge/discharge power limit (IVGM: watts) |

### Key Differences from TREX-5/10K

1. **Split enable registers** instead of one 0/1/2 register
2. **Power in kilowatts** on TREX-25/50 (the integration converts from its internal watts); **watts** on IVGM
3. **Wider voltage range** (48-500V) for LV and HV batteries
4. **No date registers**
5. **Peak shaving registers** that the state transitions set

## Peak Shaving Registers

| Register | Address | Range | Unit | Description |
|---|---|---|---|---|
| `grid_peak_shaving_enable` | 8520 | 0 / 1 | — | Enable peak shaving |
| `grid_peak_shaving_power` | 8521 | 0-25/50 | kW (IVGM: W) | Peak shaving power limit |

## State Transitions

Each state change writes the mode registers first, then the rule registers.

### Charging

| Register | Value | Purpose |
|---|---|---|
| `system_mode` | 2 | Allows charging |
| `eco_timeofuse` | 1 | Economic mode — the critical write |
| `zero_export_to_ct_sell_enable` | 0 | |
| `grid_peak_shaving_enable` | 1 | |
| `econ_rule_1_sell_enable` | 0 | No selling while charging (skipped on IVGM) |
| `econ_rule_1_grid_charge_enable` | 1 | Enable grid charging |
| `econ_rule_1_soc` | `battery_charge_max_level` (default 100%) | |
| `econ_rule_1_voltage` | `voltage_level` (default 58V, HV systems 300-448V) | |
| `econ_rule_1_power` | current Rule 1 power | |

### Discharging

| Register | Value | Purpose |
|---|---|---|
| `zero_export_to_ct_sell_enable` | 1 | Allow export |
| `system_mode` | 0 | Selling mode |
| `eco_timeofuse` | 1 | Economic mode — the critical write |
| `grid_peak_shaving_enable` | 0 | Peak shaving interferes with selling |
| `econ_rule_1_sell_enable` | 1 | Enable selling (skipped on IVGM) |
| `econ_rule_1_grid_charge_enable` | 0 | Disable grid charging |
| `grid_peak_shaving_power` | 0 | |
| `econ_rule_1_soc` | `reserve_target_pct` (auto) or `battery_discharge_min_level` (manual) | |
| `econ_rule_1_voltage` | `discharge_min_voltage` (default 50V, HV 300-448V) | |
| `econ_rule_1_power` | current Rule 1 power | |

### Idle

| Register | Value |
|---|---|
| `system_mode` | 2 |
| `zero_export_to_ct_sell_enable` | 0 |
| `grid_peak_shaving_enable` | 1 |
| `econ_rule_1_sell_enable` | 0 (skipped on IVGM) |
| `econ_rule_1_power` | 0 |
| `econ_rule_1_grid_charge_enable` | 0 |
| `grid_peak_shaving_power` | 0 |

## Battery SOC Reading

| Register | Address | Description |
|---|---|---|
| `bat1_soc` | 4408 | Battery bank 1 SOC (0-100%) |
| `bat2_soc` | 4411 | Battery bank 2 SOC (0-100%) |

The integration uses the **minimum** of both banks — the plan follows the weakest bank. If only one bank is connected (bat2_soc = 0), bat1 is used.

## Battery Voltage Reading

The integration prefers `bat1_voltage` if valid (>10V), falls back to `bat2_voltage`. Values below 10V are treated as invalid.

## Inverter Max Power Cap

| Model | Max Power |
|---|---|
| TREX-25 | 25 kW |
| TREX-50 | 50 kW |
| IVGM-8K / 15K / 20K | 8 / 15 / 20 kW |

```
grid_kw = min(power_level_kw, inverter_max_power_kw - pv_kw)
```

## Grid Power Reading

| Register | Address | Unit | Description |
|---|---|---|---|
| `total_grid_power` | 4500 | kW (signed, ÷100) | Total grid power (primary) |
| `phase_a/b/c_ct_active_power` | — | kW | Per-phase CT power (fallback, summed) |

## Power Scaling (telemetry)

Felicity's document gives the power registers a ÷10 scale; on the hardware they are **÷100 (0.01 kW)**. The TREX-50 reported ten times too much until September 2026. The TREX-25 scaling is field-proven and kept as it is (its firmware differs from the 50's). The *setpoint* registers (8xxx) keep their own scale per model — see the table above.

## Generator-Port Solar Workaround

Some TREX-25/50K installations use micro-inverters on the generator port. Then the PV registers read 0 and the generator day-energy register is unreliable. The integration falls back in order: PV string day energy → generator day energy → its **own integration of PV + generator power** every poll (reset at midnight), so the PV confidence does not collapse.

## IVGM Differences (provisional)

The IVGM-8KLP1G1 / 15KLP3G1 / 20KLP3G1 use this layout with these differences:

- **Rule 1 power and peak shaving power are in watts** (raw), while telemetry power is 0.01 kW — both confirmed from customer register dumps.
- `econ_rule_1_sell_enable` (8703) is **never written**: on the IVGM those addresses belong to the grid under-frequency protection block. **Selling is therefore unproven** on IVGM.
- The IVGM-15K uses the 20K register map with a 15 kW rating. The 8K is single-phase (no phase C, no battery 2).
- Only the ECO-rule settings are exposed as editable entities; Work Mode (`system_mode`) and the weekday mask are read-only because their values are not documented.

Read [IVGM_SUPPORT_GAPS.md](https://github.com/partach/ha_felicity/blob/main/docs/IVGM_SUPPORT_GAPS.md) before enabling the EMS on an IVGM.

## Known Issue

Setting **ECO1 Power** from its entity on a TREX-25/50 currently writes 0 (a unit mix-up between kW and W). Set the **Power Level** instead; the EMS writes the rule power itself.
