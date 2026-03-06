# Energy Management System (EMS) — ha_felicity

## Default State on First Install

On first install, **the EMS is completely inactive**. No inverter registers are written for power management or economic rules. The integration only reads sensor data.

| Setting | Default | Effect |
|---|---|---|
| Grid Mode | `off` | No charge/discharge decisions |
| Price Mode | `manual` | User-set threshold (inactive until Grid Mode enabled) |
| Safe Power Management | `auto` | Inactive (auto = only active when Grid Mode is on) |
| Nordpool Entity | not configured | No price data available |
| Forecast Entity | not configured | No PV forecast available |

---

## How to Enable the EMS

### Step 1 — Configure a price entity (required)

Go to **Settings → Integrations → Felicity Inverter → Configure** and set:

- **Nordpool Entity**: Select your Nordpool or energy price sensor (must have `device_class: monetary`). Supports 15-min (96 slots), 30-min (48 slots), or hourly (24 slots) granularity.
- **Nordpool Override** *(optional)*: An alternative price entity that takes precedence over the primary.

Without a price entity, the EMS has no price data and cannot make charge/discharge decisions.

### Step 2 — Set Grid Mode

Use the **Grid Mode** selector entity on your dashboard:

- `from_grid` — Charge battery from the grid during cheap price slots
- `to_grid` — Sell battery energy back to the grid during expensive price slots
- `both` — Automatic arbitrage: charge at cheap prices AND sell at expensive prices within the same day

This is the main on/off switch for the EMS. When set to `off`, no economic rules are activated on the inverter.

### Step 3 — Set Power Level

Use the **Power Level** number entity (1–10) to set how many kW the inverter should use for charging/discharging. This value is written to the inverter's `econ_rule_1_power` register.

### Step 4 *(optional)* — Choose Price Mode

Use the **Price Mode** selector entity:

- `manual` — You control the price threshold via the **Price Threshold Level** slider (1–10). Level 1 = cheapest prices only, level 10 = almost always active.
- `auto` — The schedule optimizer automatically selects the cheapest (or most expensive) time slots based on battery state, PV forecast, and consumption estimate.

### Step 5 *(optional)* — Configure PV Forecast

In **Configure**, set:

- **Forecast Entity**: A Forecast.Solar or Solcast sensor showing today's expected kWh
- **Forecast Entity Tomorrow**: Tomorrow's forecast (if available as separate entity)

This allows the EMS to factor in expected solar production when calculating how much grid energy is needed.

### Step 6 *(optional)* — Configure Consumption Override

In **Configure**, set:

- **Consumption Override Entity**: A P1 meter, utility meter, or template sensor that provides daily kWh consumption. The EMS builds a 7-day rolling average from this and uses it instead of the manual estimate.

---

## How to Disable the EMS

### Option A — Disable charge/discharge decisions only

Set **Grid Mode** to `off`. This:
- Stops all charge/discharge state transitions
- Stops writing `econ_rule_1_enable`, `econ_rule_1_soc`, `econ_rule_1_voltage`, etc.
- Sets `econ_rule_1_enable` to 0 (idle) on the next cycle
- **Safe Power Management** (if set to `auto`) also becomes inactive

Price data, slot calculations, and informational sensors continue to update (read-only).

### Option B — Full EMS disable (for external EMS)

1. Set **Grid Mode** to `off`
2. Set **Safe Power Management** to `off`
3. *(Optional)* Remove the Nordpool entity from **Configure**

This ensures **zero register writes** related to EMS. The integration only reads sensor data. An external EMS component can safely manage the inverter without conflicts.

### Option C — Disable only amperage protection

Set **Safe Power Management** to `off` while keeping Grid Mode active. The EMS will still make charge/discharge decisions but will not monitor grid current or adjust the power level for safety. Use this only if another system handles overcurrent protection.

---

## EMS Settings Reference

### Selector Entities (Dashboard)

| Entity | Options | Default | Description |
|---|---|---|---|
| **Grid Mode** | `off` / `from_grid` / `to_grid` / `both` | `off` | Main EMS switch. `from_grid` = charge from grid, `to_grid` = sell to grid, `both` = charge cheap + sell expensive. |
| **Price Mode** | `manual` / `auto` | `manual` | `manual` = user sets price level, `auto` = optimizer picks best slots. |
| **Safe Power Management** | `auto` / `on` / `off` | `auto` | Controls amperage-based power limiting. `auto` = active only when Grid Mode is on. `on` = always active. `off` = never active (for external EMS). |

### Number Entities (Dashboard)

| Entity | Range | Default | Description |
|---|---|---|---|
| **Power Level** | 1–10 | 5 | Charge/discharge power in kW. Written to `econ_rule_1_power`. |
| **Price Threshold Level** | 1–10 | 5 | Where the price threshold sits between min and max price. Only used in manual mode. 1 = only cheapest, 10 = almost all slots. |
| **Voltage Level** | 48–60 V *(48V)* / 300–448 V *(HV)* | 58 | Max battery voltage during **charging**. The inverter stops charging when battery voltage reaches this level. Range auto-adjusts based on battery system voltage. |
| **Discharge Min Voltage** | 48–60 V *(48V)* / 300–448 V *(HV)* | 50 | Min battery voltage during **discharging**. The inverter stops discharging when battery voltage drops to this level. Range auto-adjusts based on battery system voltage. |
| **Battery Charge Max Level** | 30–100% | 100 | Target SOC for charging. Charging stops when SOC reaches this level. |
| **Battery Discharge Min Level** | 10–70% | 20 | Minimum SOC for discharging. Discharging stops when SOC drops to this level. |
| **Battery Capacity** | 1–100 kWh | 10 | Total usable battery capacity. Used by the schedule optimizer for energy calculations. |
| **Efficiency Factor** | 0.70–1.00 | 0.90 | Round-trip charge/discharge efficiency. Accounts for conversion losses. |
| **Daily Consumption Estimate** | 0–100 kWh | 10 | Fallback daily consumption estimate. Replaced by 7-day rolling average when consumption data is available. |
| **Max Amperage Per Phase** | 10–63 A | 16 | Grid current safety limit. Used by Safe Power Management to prevent breaker trips. |

### Configuration Options (Settings → Configure)

| Option | Description |
|---|---|
| **Nordpool Entity** | Primary energy price sensor. Required for any EMS functionality. |
| **Nordpool Override** | Alternative price entity (takes precedence when set). |
| **Forecast Entity** | PV forecast sensor (today's total kWh). Forecast.Solar or Solcast. |
| **Forecast Entity Tomorrow** | Tomorrow's PV forecast (separate entity). |
| **Consumption Override Entity** | Daily consumption sensor (P1 meter / utility meter). Feeds the 7-day rolling average. |

---

## How the EMS Works

### Overall Flow

Every ~10 seconds the coordinator runs an update cycle. The following diagram shows the high-level decision path:

```
┌─────────────────────────────────────────────────────────────┐
│                    UPDATE CYCLE (~10s)                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. Read inverter registers (Modbus)                        │
│  2. Read price data from Nordpool entity                    │
│  3. Read PV forecast (if configured)                        │
│  4. Read battery SOC                                        │
│                                                             │
│         ┌──────────────┐                                    │
│         │ New day?     │                                    │
│         └──────┬───────┘                                    │
│           yes  │  no                                        │
│           ▼    │                                            │
│  ┌─────────────────┐  │                                     │
│  │ MIDNIGHT RESET   │  │                                     │
│  │ • Record daily   │  │                                     │
│  │   consumption    │  │                                     │
│  │ • Calc deficit   │  │                                     │
│  │ • Reset → idle   │  │                                     │
│  └─────────────────┘  │                                     │
│                        ▼                                    │
│              ┌──────────────────┐                            │
│              │ price_mode?      │                            │
│              └────┬────────┬───┘                            │
│            manual │        │ auto                           │
│                   ▼        ▼                                │
│  ┌─────────────────┐  ┌──────────────────────┐              │
│  │ Calc threshold   │  │ Run schedule         │              │
│  │ from user level  │  │ optimizer            │              │
│  │ (1–10)           │  │ (select cheapest /   │              │
│  │                  │  │  most expensive       │              │
│  │                  │  │  slots for the day)   │              │
│  └────────┬────────┘  └──────────┬───────────┘              │
│           │                      │                          │
│           ▼                      ▼                          │
│         ┌────────────────────────────────┐                  │
│         │  _determine_energy_state()     │                  │
│         │  → "charging" / "discharging"  │                  │
│         │     / "idle"                   │                  │
│         └───────────────┬────────────────┘                  │
│                         │                                   │
│              ┌──────────┴───────────┐                       │
│              │ State changed?       │                       │
│              └──────┬──────────┬────┘                       │
│               yes   │          │ no                         │
│                     ▼          └─── (skip)                  │
│         ┌───────────────────────┐                           │
│         │ _transition_to_state()│                           │
│         │ Write inverter regs   │                           │
│         └───────────────────────┘                           │
│                                                             │
│  (parallel) Safe Power Management                           │
│         → monitor grid amps, adjust econ_rule_1_power       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### State Decision Logic

```
┌──────────────────────────────────────────────────────────────┐
│              _determine_energy_state(battery_soc)            │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  grid_mode == "off"?  ──yes──▶  return "idle"                │
│         │ no                                                 │
│         ▼                                                    │
│  battery_soc unknown?  ──yes──▶  return "idle"               │
│         │ no                                                 │
│         ▼                                                    │
│  ┌─────────────────────────────────────────┐                 │
│  │          price_mode == "auto"?          │                 │
│  └────────┬────────────────────┬───────────┘                 │
│      yes  │                    │ no (manual)                 │
│           ▼                    ▼                             │
│  ┌──────────────────┐  ┌───────────────────────────────┐     │
│  │ AUTO MODE        │  │ MANUAL MODE                   │     │
│  │                  │  │                               │     │
│  │ Look up current  │  │ grid_mode includes            │     │
│  │ slot_idx in      │  │ "from_grid" or "both"?        │     │
│  │ scheduled_slots  │  │ AND price < threshold?        │     │
│  │                  │  │ AND SOC < charge_max?         │     │
│  │ "charge" slot    │  │    ──yes──▶ "charging"        │     │
│  │ + SOC < max?     │  │                               │     │
│  │  → "charging"    │  │ grid_mode includes            │     │
│  │                  │  │ "to_grid" or "both"?          │     │
│  │ "discharge" slot │  │ AND price > threshold?        │     │
│  │ + SOC > min?     │  │ AND SOC > discharge_min?      │     │
│  │  → "discharging" │  │    ──yes──▶ "discharging"     │     │
│  │                  │  │                               │     │
│  │ otherwise        │  │ otherwise                     │     │
│  │  → "idle"        │  │  → "idle"                     │     │
│  └──────────────────┘  └───────────────────────────────┘     │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### Manual Mode (`price_mode = manual`)

Each update cycle (~10 seconds):

1. Read current price from Nordpool entity
2. Calculate price threshold from min/avg/max prices using the user's **Price Threshold Level** (1–10)
3. Apply **hysteresis band** around the threshold to prevent oscillation:
   - Margin = 5% of price spread (max_price − min_price)
   - To **enter** charging: price must drop below `threshold − margin`
   - To **enter** discharging: price must rise above `threshold + margin`
   - To **stay** in current state: price only needs to remain past `threshold` (no margin)
   - Dead zone between `threshold ± margin` → remains in current state or idle
4. SOC limits also apply:
   - Charging stops when SOC reaches **Battery Charge Max Level**
   - Discharging stops when SOC drops to **Battery Discharge Min Level**
5. Write economic rule registers on state change

```
Price ───────────────────────────────────────────────────────▶

                    charge         dead        discharge
                    zone           zone        zone
        ◀──────────────────▶ ◀──────────▶ ◀──────────────────▶

  ──────────────────|────────────|─────────|──────────────────
                threshold     threshold   threshold
                − margin                  + margin

  • Entering charge requires price < threshold − margin
  • Entering discharge requires price > threshold + margin
  • Once in a state, stays until price crosses raw threshold
  • If price is in dead zone and idle: stays idle (no flip-flop)
```

### Auto Mode (`price_mode = auto`)

Each update cycle:

1. Retrieve all price slots for today (supports 15-min, 30-min, or hourly granularity)
2. Retrieve PV forecast (if configured) — including per-hour production breakdown
3. Calculate energy deficit using **hourly PV surplus model** (see below)
4. Build the schedule based on grid mode:
   - **`from_grid`**: Select cheapest N slots to fill the deficit. Negative-price slots are always included (you get paid to charge).
   - **`to_grid`**: Select most expensive N slots for sellable energy. Negative-price slots are excluded (never sell at a loss).
   - **`both`**: Build both charge and discharge schedules, then filter discharge slots so they are profitable after round-trip losses: `sell_price >= buy_price / efficiency²`. Charge and discharge never overlap in the same slot.
5. If the current time slot is in the scheduled set → activate charge/discharge
6. The price threshold is automatically set to the boundary price of selected slots

### Hourly PV Surplus Model

The deficit calculation uses per-hour solar production data (from Forecast.Solar `wh_hours` or Solcast `detailedHourly`) to accurately determine how much solar surplus is available to charge the battery.

**Why this matters:** A flat calculation like `PV_total - consumption_total` is misleading. If daily PV is 35 kWh and daily consumption is 50 kWh, the flat model says net_pv = 0 (no surplus). But in reality, solar peaks during midday hours and exceeds the load — that surplus charges the battery. The flat model would incorrectly schedule grid charging.

```
Example day: consumption = 2 kWh/hour (flat), PV varies by hour

Hour:   06  07  08  09  10  11  12  13  14  15  16  17  18  19
PV:     0   1   2   4   6   7   7   6   4   2   1   0   0   0   = 40 kWh
Load:   2   2   2   2   2   2   2   2   2   2   2   2   2   2   = 28 kWh (remaining)
                                                                    (14h × 2)

Flat model:  net_pv = 40 - 28 = 12 kWh surplus
             (only counts total difference)

Hourly model: surplus per hour (only positive values):
Hour:   06  07  08  09  10  11  12  13  14  15  16  17  18  19
Diff:   -2  -1   0  +2  +4  +5  +5  +4  +2   0  -1  -2  -2  -2
Surplus: 0   0   0   2   4   5   5   4   2   0   0   0   0   0  = 22 kWh

The hourly model yields MORE surplus (22 vs 12) because it correctly
recognizes that solar peak hours produce enough to charge the battery,
even though evening hours have no sun. The deficit hours (evening) will
be served by the battery, not by buying from the grid.
```

When hourly PV data is unavailable, the system falls back to the flat model.

### `both` Mode — Self-Sufficiency-First Arbitrage (Auto)

The `both` mode prioritizes self-consumption over grid trading. The algorithm follows this order:

1. **Self-consume solar** — let PV charge the battery naturally (idle state)
2. **Protect overnight reserve** — never sell below what's needed until tomorrow's sun
3. **Only buy grid when solar can't cover** — grid charging is last resort
4. **Only sell true surplus** — energy above both discharge_min AND overnight reserve

```
┌────────────────────────────────────────────────────────────────┐
│              BOTH MODE — SELF-SUFFICIENCY FIRST                │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  PHASE 0 — SELF-CONSUMPTION RESERVE                            │
│  ├─ Estimate sunset hour (last hour with PV > 0.1 kWh)        │
│  ├─ Estimate sunrise tomorrow (first hour with PV today)       │
│  ├─ overnight_hours = (24 - sunset) + sunrise                  │
│  └─ reserve = consumption_per_hour × overnight_hours           │
│                                                                │
│  PHASE 1 — CHARGE SIDE (grid only if solar can't cover)       │
│  ├─ reserve_target = max(discharge_min, overnight_reserve)     │
│  ├─ battery_shortfall = reserve_target − current_kwh           │
│  │  (NOT charge_max — only target the overnight reserve)       │
│  ├─ Subtract hourly PV surplus (solar covers most/all)         │
│  ├─ energy_deficit = shortfall − net_pv (often 0 on sunny days)│
│  ├─ Always include negative-price slots (paid to charge)       │
│  └─ Fill remaining deficit with cheapest non-negative slots    │
│                                                                │
│  PHASE 2 — DISCHARGE SIDE (only sell true surplus)             │
│  ├─ reserve_floor = max(discharge_min, overnight_reserve)      │
│  ├─ sellable = (current_kwh − reserve_floor) × efficiency      │
│  └─ Select most expensive positive-price slots                 │
│                                                                │
│  PHASE 3 — PROFITABILITY FILTER                                │
│  ├─ Remove any discharge slot that overlaps a charge slot      │
│  ├─ min_sell_price = max_buy_price / (eff × eff)               │
│  └─ Only keep discharge slots where price ≥ min_sell_price     │
│                                                                │
│  PHASE 4 — ANTI-CONFLICT GUARD (real-time)                     │
│  ├─ Before activating discharge, check grid power direction    │
│  └─ If house is importing >200W → suppress discharge (idle)    │
│     (prevents selling battery while buying from grid)          │
│                                                                │
│  Example: battery=30kWh, reserve=15kWh, discharge_min=6kWh    │
│  ├─ reserve_floor = max(6, 15) = 15 kWh                       │
│  ├─ sellable = (30 − 15) × 0.9 = 13.5 kWh                    │
│  └─ Only sell 13.5 kWh at profitable prices                   │
│     (remaining 15 kWh reserved for overnight)                  │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

### How the Inverter is Controlled

When the EMS decides on a state change, `_transition_to_state()` writes the economic rule registers. The **same parameters** are written every time, but the **values differ** based on charging vs discharging:

| Register | Charging | Discharging | Idle |
|---|---|---|---|
| `econ_rule_1_enable` | 1 | 2 | 0 |
| `econ_rule_1_soc` | Battery Charge Max Level | Battery Discharge Min Level | *(not written)* |
| `econ_rule_1_voltage` | Voltage Level *(max)* | Discharge Min Voltage *(min)* | *(not written)* |
| `econ_rule_1_power` | Safe Max Power (watts) | Safe Max Power (watts) | *(not written)* |
| `econ_rule_1_start_day` | today | today | *(not written)* |
| `econ_rule_1_stop_day` | today | today | *(not written)* |

The voltage parameter has **different meaning** depending on state:
- **Charging**: max voltage — the inverter stops charging when the battery reaches this voltage
- **Discharging**: min voltage — the inverter stops discharging when the battery drops to this voltage

This is why `Voltage Level` and `Discharge Min Voltage` are separate settings.

### Inverter-Specific Register Mapping

The `TypeSpecificHandler` translates the EMS commands into model-specific Modbus writes. The same logical command produces different register writes depending on inverter model:

#### TREX-5 / TREX-10 (Low Voltage, 48V)

```
┌──────────────────────────────────────────────────────────────┐
│         _transition_to_state("charging")                     │
│         TREX-5 / TREX-10                                     │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  Coordinator                    TypeSpecificHandler           │
│      │                               │                       │
│      │─ econ_rule_1_enable = 1 ─────▶│                       │
│      │                               │─▶ Write reg 8568 = 1  │
│      │                               │   (direct: 1=charge)  │
│      │                               │                       │
│      │─ econ_rule_1_soc = 100 ──────▶│                       │
│      │                               │─▶ Write reg 8575 =100 │
│      │                               │                       │
│      │─ econ_rule_1_voltage = 58 ───▶│                       │
│      │                               │─▶ Write reg 8574 =580 │
│      │                               │   (×10 scaling)       │
│      │                               │                       │
│      │─ econ_rule_1_power = 3000 ───▶│                       │
│      │                               │─▶ Write reg 8576=3000 │
│      │                               │   (watts, direct)     │
│      │                               │                       │
│      │─ econ_rule_1_start_day ──────▶│                       │
│      │                               │─▶ Write reg 8571      │
│      │─ econ_rule_1_stop_day ───────▶│                       │
│      │                               │─▶ Write reg 8572      │
│                                                              │
│  For discharging: same registers, but                        │
│    reg 8568 = 2, SOC = discharge_min (20),                   │
│    voltage = discharge_min_voltage (50 → 500 scaled)         │
│                                                              │
│  For idle: reg 8568 = 0 (other regs not written)             │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

#### TREX-25 / TREX-50 (High Voltage)

The TREX-25/50 models do not have a single enable register. Instead, charging and discharging require different combinations of system mode, grid charge enable, and sell enable registers:

```
┌──────────────────────────────────────────────────────────────┐
│         _transition_to_state("charging")                     │
│         TREX-25 / TREX-50                                    │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  Coordinator                    TypeSpecificHandler           │
│      │                               │                       │
│      │─ econ_rule_1_enable = 1 ─────▶│                       │
│      │                               │─▶ reg 8713 = 1        │
│      │                               │   (grid_charge_enable)│
│      │                               │peak_shaving_enable = 1│
│      │                               │                       │
│      │─ econ_rule_1_soc = 100 ──────▶│                       │
│      │                               │─▶ reg 8718 = 100      │
│      │                               │                       │
│      │─ econ_rule_1_voltage = 58 ───▶│                       │
│      │                               │─▶ reg 8717 = 580      │
│      │                               │   (×10 scaling)       │
│      │                               │                       │
│      │─ econ_rule_1_power = 3000 ───▶│                       │
│      │                               │─▶ reg 8719 = 3        │
│      │                               │   (÷1000 → kW)       │
│      │                               │─▶ reg 8521 = 3        │
│      │                               │   (grid_peak_shaving) │
│      │                               │                       │
│      │─ start_day / stop_day ───────▶│                       │
│      │                               │─▶ (ignored, not used  │
│      │                               │    on TREX-25/50)     │
│                                                              │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│         _transition_to_state("discharging")                  │
│         TREX-25 / TREX-50                                    │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  Coordinator                    TypeSpecificHandler           │
│      │                               │                       │
│      │─ econ_rule_1_enable = 2 ─────▶│                       │
│      │                               │─▶ reg 8713 = 1        │
│      │                               │   (grid_charge_enable)│
│      │                               │─▶ reg 8521 = 0        │
│      │                               │   (peak_shaving = 0)  │
│      │                               │peak_shaving_enable = 0│
│      │                               │                       │
│      │─ econ_rule_1_soc = 20 ───────▶│                       │
│      │                               │─▶ reg 8718 = 20       │
│      │                               │                       │
│      │─ econ_rule_1_voltage = 50 ───▶│                       │
│      │                               │─▶ reg 8717 = 500      │
│      │                               │   (min discharge V)   │
│      │                               │                       │
│      │─ econ_rule_1_power = 3000 ───▶│                       │
│      │                               │─▶ reg 8719 = 3 (kW)   │
│      │                               │if peak_shaving_enable:│
│      │                               │    ─▶ reg 8521 = 3    │
│      │                               │   (peak_shaving power)│
│                                                              │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│         _transition_to_state("idle")                         │
│         TREX-25 / TREX-50                                    │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  Coordinator                    TypeSpecificHandler           │
│      │                               │                       │
│      │─ econ_rule_1_enable = 0 ─────▶│                       │
│      │                               │─▶ reg 8713 = 0        │
│      │                               │   (grid_charge OFF)   │
│      │                               │─▶ reg 8521 = 0        │
│      │                               │   (peak_shaving OFF)  │
│      │                               │                       │
│      │  (SOC, voltage, power, dates not written for idle)    │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

#### Key Differences Between Models

| Aspect | TREX-5 / TREX-10 | TREX-25 / TREX-50 |
|---|---|---|
| **Enable register** | Single `econ_rule_1_enable` @ 8568 (0/1/2) | `econ_rule_1_grid_charge_enable` @ 8713 + mode registers |
| **Power unit** | Watts (direct) | Kilowatts (÷1000 conversion) |
| **Power registers** | `econ_rule_1_power` only | `econ_rule_1_power` + `grid_peak_shaving_power` |
| **Date registers** | Written (start_day / stop_day) | Ignored (not applicable) |
| **Voltage range** | 48–60 V (dynamic) | 48–500 V |
| **Battery SOC source** | Single battery register | min(bat1_soc, bat2_soc) — conservative |

### Safe Power Management

When active, each update cycle:

1. Read the highest grid current across all phases
2. Compare against **Max Amperage Per Phase** setting:
   - **> 95%**: Reduce power level by 2 kW (emergency)
   - **> 80%**: Reduce power level by 1 kW (caution)
   - **< 70%**: Recover power level by 1 kW (up to user's Power Level)
   - **0 or no current**: Jump directly to user's Power Level
3. Write `econ_rule_1_power` register when the level changes

```
Grid current ─────────────────────────────────────────────────▶

 0A          70%            80%           95%          max
 │            │              │             │            │
 │  JUMP TO   │   RECOVER    │   HOLD      │  REDUCE    │
 │  user      │   +1 kW/     │   (no       │  -1 kW     │
 │  power     │   cycle      │   change)   │            │
 │  level     │              │             │  >95%:     │
 │            │              │             │  -2 kW     │
```

### Informational Sensors (Always Active)

These sensors update regardless of EMS state and provide insight:

| Sensor | Description |
|---|---|
| **Available Slots** | Number of remaining time slots at or below the current price threshold |
| **Available Energy Capacity** | How much energy (kWh) those slots could provide |
| **Charge Likelihood** | Whether the battery target will likely be met: `on_track` / `tight` / `at_risk` / `insufficient` |
| **Schedule Status** | Current optimizer state: `manual` / `active` / `waiting` / `off` / `no_action_needed` |
| **Weekly Avg Consumption** | 7-day rolling average daily consumption (kWh). Persisted to disk — survives reboots. |

### Midnight Reset

At midnight each day:

1. Record today's energy consumption (from override entity or inverter registers) for the rolling average
2. Calculate yesterday's deficit (how far short of the charge target)
3. Carry the deficit forward to the next day's energy target (capped by battery headroom)
4. Reset the energy state to idle
5. Begin a new scheduling cycle

### Consumption Tracking & Persistence

The 7-day consumption rolling average is **persisted to disk** using Home Assistant's `Store` helper (saved to `.storage/`). On reboot or update:

1. The stored history (up to 7 days) is loaded from disk
2. The weekly average is immediately recalculated
3. No data is lost — you do NOT need to wait another week

The average works with as few as 1 day of data (divides by actual number of entries, not always 7). It builds up over the first week from a 1-day to a 7-day rolling average.

**Data priority for daily consumption:**
1. Consumption Override Entity (P1 meter / utility meter) — most accurate
2. Inverter daily energy registers (daily_energy_consumed, daily_load_energy, etc.)
3. Falls back to `Daily Consumption Estimate` setting if neither source provides data
