# TREX-5/6/10K Energy Management

Register-level details for the T-REX-5K, T-REX-6K and T-REX-10K inverter models (Felicity IVGM5048 / IVGM6048 / IVGM10048). For the algorithm overview, see [Energy Management Basics](Energy-Management-Basics).

The register map is checked against Felicity's protocol document for these models (*Inverter Communication Protocol — Series RS485*, V02 2025-09-30). The T-REX-6K uses the T-REX-5K map with a 6 kW rating.

## Operating Mode

| Register | Address | Values |
|---|---|---|
| `operating_mode` | 8451 | 0 = General (self-use), 1 = Backup (no discharge), **2 = Economic**, 3 = Oil engine (generator) |

The inverter only obeys Rule 1 in **Economic mode**. The integration writes this register itself:
- charging or discharging → `operating_mode = 2` (Economic), written **before** the rule is enabled; if it fails, the rule is not enabled;
- idle → `operating_mode = 0` (General).

While the EMS is charging/discharging it checks this register every poll; if it is not Economic (app, power blip), the whole state is written again ("self-heal"). A manual change in the app is therefore reverted while the EMS is active — switch the EMS off first.

With **Grid Mode = off** nothing is written, also not at startup.

## Economic Rule 1 Registers

| Register | Address | Range | Unit | Description |
|---|---|---|---|---|
| `econ_rule_1_enable` | 8568 | 0 / 1 / 2 (3) | — | 0 = off, 1 = charge, 2 = discharge (3 = ECO mode, shown in the Felicity app as "AC Couple"; never written by the EMS) |
| `econ_rule_1_start_time` | 8569 | 00:00-23:59 | HH:MM | Rule 1 time window start |
| `econ_rule_1_stop_time` | 8570 | 00:00-23:59 | HH:MM | Rule 1 time window end (firmware uses 23:59 for all-day) |
| `econ_rule_1_start_day` | 8571 | — | date | Start date (written by the integration) |
| `econ_rule_1_stop_day` | 8572 | — | date | Stop date (written by the integration) |
| `econ_rule_1_effective_week` | 8573 | 0x00-0x7F | bitmask | Weekday mask: bit0=Sunday .. bit6=Saturday |
| `econ_rule_1_voltage` | 8574 | 50-60 | V | Charge voltage setpoint |
| `econ_rule_1_soc` | 8575 | 0-100 | % | SOC target (charge ceiling or discharge floor) |
| `econ_rule_1_power` | 8576 | 0-rating | **Watts** | Grid charge/discharge power limit |

### Key Point: Power in Watts

The TREX-5/6/10K power register uses **Watts**. A 5 kW limit is written as `5000`. The Rule 1 power applies to *grid* charging and discharging only — solar charges the battery on top of it.

## State Transitions

### Charging

| Register | Value |
|---|---|
| `operating_mode` | 2 (Economic) — first |
| `econ_rule_1_enable` | 1 |
| `econ_rule_1_soc` | `battery_charge_max_level` (default 100%) |
| `econ_rule_1_start_day` / `stop_day` | today |
| `econ_rule_1_voltage` | `voltage_level` (default 58V) |
| `econ_rule_1_power` | current Rule 1 power in watts |

### Discharging

| Register | Value |
|---|---|
| `operating_mode` | 2 (Economic) — first |
| `econ_rule_1_enable` | 2 |
| `econ_rule_1_soc` | `reserve_target_pct` (auto mode) or `battery_discharge_min_level` (manual) |
| `econ_rule_1_start_day` / `stop_day` | today |
| `econ_rule_1_voltage` | `discharge_min_voltage` (default 50V) |
| `econ_rule_1_power` | current Rule 1 power in watts |

### Idle

| Register | Value |
|---|---|
| `operating_mode` | 0 (General) |
| `econ_rule_1_enable` | 0 |

Voltage, SOC and power are left unchanged when going idle.

## Discharge SOC Floor

In auto mode the SOC register during discharge is the **computed reserve target**, not the raw `battery_discharge_min_level`, so the inverter's own floor matches the plan. With the EMS off or in manual mode, `discharge_min` is used.

## Date Registers and Midnight

Every transition writes today's date to `econ_rule_1_start_day` / `stop_day`. A charge or sale still running at midnight has no transition, so at the day change the integration **re-writes both dates** for the new day — the action keeps running.

## Battery SOC and Voltage

| Register | Address | Description |
|---|---|---|
| `battery_capacity` | 4368 | SOC (0-100%, 0.1% resolution) |
| `battery_voltage` | 4365 | Battery voltage; below 10V is treated as invalid |

The TREX-5/6/10K has a single battery bank.

## Rule 1 Time Window

When `rule1_time_window` is `auto`, the integration writes `econ_rule_1_start_time` = 00:00 and `econ_rule_1_stop_time` = 23:59 (the firmware does not accept 00:00 or 24:00 as stop). When `rule1_weekday` is `auto`, it writes `econ_rule_1_effective_week` = 0x7F (all 7 days). Both are written only while the EMS is on.

On `manual` the integration leaves your values alone. A scheduled action outside your window silently does nothing on the inverter; the EMS card shows a warning banner when it detects this.

## Inverter Max Power Cap

| Model | Max Power | Phases |
|---|---|---|
| T-REX-5K | 5 kW | 1 |
| T-REX-6K | 6 kW | 1 |
| T-REX-10K | 10 kW | 3 |

The planner caps grid charge power when PV is active, and assumes the battery can never feed the house faster than the rating:

```
grid_kw = min(power_level_kw, inverter_max_power_kw - pv_kw)
```

## Grid Power Reading

| Register | Address | Unit | Description |
|---|---|---|---|
| `total_ac_input_power` | 4392 | Watts (signed) | Total grid power. Positive = import, negative = export |

## Serial Link

The TREX-5/6/10K runs RS485 at **2400 baud** from the factory. The integration polls the full register map in a few large reads, because on such a slow link the number of requests dominates the poll time. For RS485-to-Ethernet gateways see [Waveshare RS485 TO POE ETH (B)](Waveshare-RS485_TO_POE_ETH_%28B%29) and [Diagnostics and Troubleshooting](Diagnostics-and-Troubleshooting).
