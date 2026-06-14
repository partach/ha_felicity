# Flexible Load Control

The EMS can manage up to 3 controllable loads (EV charger, boiler, pool pump, etc.) by switching them on during cheap or negative-price electricity slots.

## Quick Start

1. Go to **Settings > Devices > Felicity Inverter > Configure** (gear icon)
2. Assign a **switch entity** for the load (e.g. `switch.ev_charger`)
3. For EV chargers: also assign a **current entity** (e.g. `number.ev_charger_current`)
4. Enable the load via the **Load Enabled** select entity on the device page

## How It Works

Loads are scheduled as an **overlay** on the battery schedule. They activate during:
- Slots where the electricity price is at or below the threshold
- Negative-price slots
- Slots with PV surplus (solar production > consumption)
- Battery charge slots (already identified as cheap)

Loads don't affect the battery schedule — they're additive consumption on top of it.

## Configuration Entities

### Per Load (1-3)

| Entity | Description |
|---|---|
| **Name** | Display name for the load (shown in logs and frontend) |
| **Enabled** | on/off toggle — the EMS won't touch the load unless enabled |
| **Rated Power** | The load's power draw in kW. Used for display and safe-power calculations. For EV chargers with current stepping, the actual power is computed from `current × voltage × phases`. |
| **Shed Priority (1=last 3=first)** | When grid current exceeds safe limits, loads are shed in order: priority 3 first (least important), priority 1 last (most important). The battery power reduction is always the last resort, after all loads have been shed. |

### EV Charger (Load 1 Only)

These entities appear only after assigning a **current entity** in the integration options:

| Entity | Description |
|---|---|
| **Current Steps** | Comma-separated list of the charger's supported current levels in amps (e.g. `6,10,13,16,20,25`). The EMS will only set current to one of these values — never an intermediate value. |
| **Number of Phases** | 1 or 3 phase charger. Used to calculate actual power: `amps × voltage × phases / 1000 = kW`. A 16A single-phase charger at 230V draws 3.7 kW; the same 16A on 3 phases draws 11 kW. |
| **Grid Voltage** | Your grid voltage (typically 230V in Europe, 110V in North America). Used in the power calculation together with phases and current. |
| **Startup Current** | The current the EMS writes to the charger when turning it on. Must be one of the values in Current Steps. If it's not an exact match, the EMS rounds down to the nearest available step (e.g. 11A with steps `6,10,13,16,20` → sets 10A). |

### How Power, Current, Voltage, and Phases Relate

For an EV charger, the actual power draw is:

```
power_kw = current_amps × voltage × phases / 1000
```

Examples:
- 16A × 230V × 1 phase = **3.68 kW** (typical home charger)
- 16A × 230V × 3 phases = **11.04 kW** (three-phase wallbox)
- 32A × 230V × 3 phases = **22.08 kW** (full-speed three-phase)

The **Rated Power** entity is for display only — the EMS uses the current/voltage/phases formula for actual EV charger control.

## Safe Power Protection

When grid current exceeds the **Max Amperage Per Phase** limit, loads are shed in this order:

1. **EV charger current step-down** — reduces by one step per 10s cycle (e.g. 16A → 13A → 10A)
2. **Binary load shed** — turns off loads by priority (3 = shed first, 1 = shed last)
3. **Battery power reduction** — last resort, reduces inverter charge/discharge power

After current drops below the safe threshold, loads recover in reverse order.

## EV Boost Override

Press the **EV Boost** button to force the EV charger on at maximum current for 1 hour, regardless of the schedule. Each press adds +1 hour (stacking). The **Cancel EV Boost** button stops it immediately.

During a boost:
- The charger runs at the highest current step
- Safe power protection can still step down the current (grid safety)
- The charger won't be fully shed (only stepped down)
- The boost expires automatically after the timer runs out

## Frontend Display

- **Cyan strip** at the bottom of price bars where loads are scheduled
- **"N/M loads active"** stat in the stats row
- **Cyan banner** during EV Boost showing remaining time
- **Flexible Loads panel** — a dedicated section listing every configured
  load with:
  - **On/off state** — the row glows cyan and brightens when the load is
    active, stays dim when off
  - **Live power draw** — the current kW with a fill bar showing how much
    of the load's maximum it is drawing. For the EV charger this is the
    real draw (active current × voltage × phases), shown alongside the
    `A · φ · V` detail
  - **`BOOST` chip** — appears on the EV charger row while EV Boost is active
  - **Shed-priority badge** — colour-coded ("Sheds 1st" red / "Sheds 2nd"
    amber / "Sheds last" green) so you can see at a glance which load drops
    off first when grid current is too high
  - **Header total** — the combined live power of all active loads
  - **Footer note** — a reminder that loads are shed before the battery
    power is reduced
