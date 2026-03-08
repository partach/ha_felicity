# Energy Management System (EMS) — ha_felicity

This document describes the full EMS architecture, algorithms, and lessons learned. It is intended as a reference for building a standalone `ha_ems` component.

---

## Architecture Overview

The EMS is embedded within a Felicity inverter integration but the scheduling logic is inverter-agnostic. It consists of:

| Layer | File | Responsibility |
|---|---|---|
| **Coordinator** | `coordinator.py` | 10-second update loop: reads prices, PV forecast, battery SOC; runs schedule optimizer; determines energy state; writes inverter registers |
| **Sensors** | `sensor.py` | Exposes coordinator data as HA entities (price, schedule status, charge likelihood, etc.) |
| **Selectors/Numbers** | `select.py`, `number.py` | Dashboard controls (Grid Mode, Price Mode, Power Level, SOC limits, etc.) |
| **EMS Card** | `frontend/ha_felicity_ems.js` | LitElement card with canvas chart, client-side simulation, interactive controls |
| **Type Handler** | `type_specific_handler.py` | Translates logical commands (charge/discharge/idle) into model-specific Modbus writes |

### Data Flow

```
Nordpool Entity ──┐
PV Forecast    ──┤
Battery SOC    ──┼──▶ Coordinator ──▶ Schedule Optimizer ──▶ Energy State
Consumption    ──┤       │                                       │
Grid Current   ──┘       │                                       ▼
                         │                              _transition_to_state()
                         │                              Write inverter registers
                         ▼
                   Sensor entities ──▶ EMS Card (frontend)
                         │                  │
                         │           Client-side simulation
                         │           (mirrors coordinator logic
                         │            for live preview)
                         ▼
                   HA History API ──▶ Past slot coloring
```

---

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

Go to **Settings -> Integrations -> Felicity Inverter -> Configure** and set:

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

Use the **Power Level** number entity (1-10) to set how many kW the inverter should use for charging/discharging. This value is written to the inverter's `econ_rule_1_power` register.

### Step 4 *(optional)* — Choose Price Mode

Use the **Price Mode** selector entity:

- `manual` — You control the price threshold via the **Price Threshold Level** slider (1-10). Level 1 = cheapest prices only, level 10 = almost always active.
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
| **Power Level** | 1-10 | 5 | Charge/discharge power in kW. Written to `econ_rule_1_power`. |
| **Price Threshold Level** | 1-10 | 5 | Where the price threshold sits between min and max price. Only used in manual mode. 1 = only cheapest, 10 = almost all slots. |
| **Voltage Level** | 48-60 V *(48V)* / 300-448 V *(HV)* | 58 | Max battery voltage during **charging**. The inverter stops charging when battery voltage reaches this level. Range auto-adjusts based on battery system voltage. |
| **Discharge Min Voltage** | 48-60 V *(48V)* / 300-448 V *(HV)* | 50 | Min battery voltage during **discharging**. The inverter stops discharging when battery voltage drops to this level. Range auto-adjusts based on battery system voltage. |
| **Battery Charge Max Level** | 30-100% | 100 | Target SOC for charging. Charging stops when SOC reaches this level. |
| **Battery Discharge Min Level** | 10-70% | 20 | Minimum SOC for discharging. Discharging stops when SOC drops to this level. |
| **Battery Capacity** | 1-100 kWh | 10 | Total usable battery capacity. Used by the schedule optimizer for energy calculations. |
| **Efficiency Factor** | 0.70-1.00 | 0.90 | Round-trip charge/discharge efficiency. Accounts for conversion losses. |
| **Daily Consumption Estimate** | 0-100 kWh | 10 | Fallback daily consumption estimate. Replaced by 7-day rolling average when consumption data is available. |
| **Max Amperage Per Phase** | 10-63 A | 16 | Grid current safety limit. Used by Safe Power Management to prevent breaker trips. |

### Configuration Options (Settings -> Configure)

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

Every ~10 seconds the coordinator runs an update cycle:

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
│  │ (1-10)           │  │ (select cheapest /   │              │
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
│  Manual mode also uses hysteresis (5% of price spread)       │
│  to prevent oscillation near threshold.                      │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

---

## Schedule Optimizer (Auto Mode)

### Core Algorithm

The schedule optimizer runs every update cycle and determines which time slots should be used for charging or discharging. The key concept is **solar-first**: grid energy is only purchased when solar cannot cover the overnight reserve.

### Step-by-Step: `from_grid` Mode

```
1. Calculate self-consumption reserve (sunset → sunrise):
   - sunset_hour = last hour with PV forecast > 0.1 kWh (default: 19)
   - sunrise_hour = first hour with PV forecast > 0.1 kWh (default: 7)
   - overnight_hours = (24 - sunset) + sunrise
   - reserve_kwh = consumption_per_hour × overnight_hours

2. Calculate reserve target:
   - min_kwh = discharge_min% × battery_capacity
   - reserve_target = max(min_kwh, reserve_kwh)
   NOTE: target is reserve, NOT charge_max. We don't try to fill
   the battery to 100% — only enough to survive overnight.

3. Calculate battery shortfall:
   - battery_shortfall = max(0, reserve_target - current_kwh)

4. Calculate net PV surplus (see "PV Surplus Model" below):
   - Only counts hours where PV production > house consumption
   - Scaled by PV confidence factor (actual vs forecast)

5. Calculate grid energy deficit:
   - base_deficit = max(0, battery_shortfall - net_pv)
   - Add yesterday's deficit carryover (if any)

6. Select charge slots:
   a. Always include negative-price slots (paid to charge)
   b. Sort remaining by price ascending
   c. Pick cheapest N slots to cover remaining deficit
   d. threshold = highest price among selected slots
```

### Step-by-Step: `to_grid` Mode

```
1. Calculate reserve floor:
   - min_kwh = discharge_min% × battery_capacity
   - reserve_kwh = self-consumption reserve
   - reserve_floor = max(min_kwh, reserve_kwh)

2. Calculate sellable energy:
   - sellable = max(0, current_kwh - reserve_floor) × efficiency
   NOTE: never sell below the reserve floor

3. Select discharge slots:
   a. Exclude negative-price slots (never sell at a loss)
   b. Sort remaining by price descending
   c. Pick most expensive N slots to sell all sellable energy
```

### Step-by-Step: `both` Mode (Self-Sufficiency-First Arbitrage)

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

### Key Design Decision: Target Reserve, Not charge_max

In all modes (`from_grid`, `to_grid`, `both`), the charge target is the **overnight reserve** (enough to survive until tomorrow's solar), NOT charge_max (filling the battery to 100%). This means:

- On sunny days with good forecast: deficit is 0, no grid charging needed
- On cloudy days: deficit increases, more cheap grid slots selected
- charge_max is only used as a SOC cap during actual charging (inverter stops at charge_max), not as a scheduling target

---

## PV Surplus Model

### Hourly vs Flat Calculation

The deficit calculation uses per-hour solar production data (from Forecast.Solar `wh_hours` or Solcast `detailedHourly`) to accurately determine how much solar surplus is available to charge the battery.

**Why this matters:** A flat calculation like `PV_total - consumption_total` is misleading:

```
Example day: consumption = 2 kWh/hour (flat), PV varies by hour

Hour:   06  07  08  09  10  11  12  13  14  15  16  17  18  19
PV:     0   1   2   4   6   7   7   6   4   2   1   0   0   0   = 40 kWh
Load:   2   2   2   2   2   2   2   2   2   2   2   2   2   2   = 28 kWh
                                                                    (14h × 2)

Flat model:  net_pv = 40 - 28 = 12 kWh surplus

Hourly model: surplus per hour (only positive values):
Hour:   06  07  08  09  10  11  12  13  14  15  16  17  18  19
Diff:   -2  -1   0  +2  +4  +5  +5  +4  +2   0  -1  -2  -2  -2
Surplus: 0   0   0   2   4   5   5   4   2   0   0   0   0   0  = 22 kWh

The hourly model yields MORE surplus because it correctly recognizes
that solar peak hours produce enough to charge the battery, even
though evening hours have no sun.
```

When hourly PV data is unavailable, the system falls back to the flat model.

### PV Confidence Factor (Actual vs Forecast Scaling)

**Problem discovered:** On cloudy days the PV forecast can be wildly optimistic (e.g., forecast says 24.7 kWh but actual production at midday is 0 kWh). The scheduler trusts the forecast surplus and schedules too few grid charge slots, leaving the battery well below the min SOC target.

**Solution:** Scale the forecast by a confidence factor based on actual-vs-expected production:

```
pv_confidence = actual_produced_so_far / forecast_expected_by_now

Example at 13:00:
  - Forecast expected by now: 12 kWh (sum of hourly forecast for hours 0-12)
  - Actual PV today: 0 kWh
  - Confidence: 0.0 → floored to 0.1

  Without confidence: net_pv = 10 kWh → deficit = 0 → no grid charging
  With confidence:    net_pv = 1 kWh  → deficit = 8 kWh → 4 charge slots

Rules:
  - Only activates when >1 kWh was expected by now (avoids early-morning noise)
  - Floored at 0.1 (never completely ignores forecast — weather can improve)
  - Capped at 1.0 (if actual exceeds forecast, don't over-estimate)
  - Sunny days: confidence ≈ 1.0, no change
  - Cloudy days: confidence drops, more grid slots scheduled
```

This is critical for reliable operation. Without it, a single cloudy day can leave the battery dangerously low.

### Generator-Port Solar (PV via Micro-Inverter / Gen Port)

**Problem:** Some TREX-25/50 installations have solar panels connected via the generator/micro-inverter port instead of the dedicated PV inputs. In these setups:

- PV registers (`pv1-4_day_energy`) always read **0 kWh**
- The inverter doesn't know the generator-port power is solar
- The confidence factor permanently drops to **0.1** (floor), because `actual / expected = 0 / X = 0`
- The scheduler over-estimates the energy deficit and over-schedules grid charging
- Likelihood shows "tight" or "at_risk" when solar is actually producing fine

**Detection:** The inverter has a `genmode` register (address 8759) with options: `Generator`, `Smart Load`, `Micro Inv`. When set to `Micro Inv`, the generator port is being used for solar micro-inverters. Additionally, if PV registers read ~0 but `generator_day_cost_energy` > 0, solar is clearly flowing through the generator port.

**Solution:** The `pv_actual_today_kwh` property now falls back to `generator_day_cost_energy` when:
1. PV string registers read near-zero (< 0.1 kWh), AND
2. `generator_day_cost_energy` > 0

This ensures the PV confidence factor works correctly even when solar enters via the generator port. The generator energy register (address 4586, 0.1 kWh precision) tracks daily energy just like PV day energy would.

**Available generator registers (TREX-25/50):**

| Register | Address | Description |
|---|---|---|
| `generator_day_cost_energy` | 4586 | Daily energy through gen port (kWh) — **used as PV actual fallback** |
| `total_generator_power` | 4498 | Current total power through gen port (kW) |
| `phase_a/b/c_generator_active_power` | 4464-4466 | Per-phase gen power (kW) |
| `genmode` | 8759 | Port mode: Generator / Smart Load / Micro Inv |

**For ha_ems:** This is an important edge case. Any generic EMS must handle the scenario where the inverter's PV measurement point doesn't cover all solar sources. Consider:
- A config option to specify alternative PV actual entities (e.g., a separate energy meter on the solar array)
- Auto-detection when PV reads 0 but other power sources show solar-like patterns (daytime-only, follows irradiance curve)
- A flag to disable the confidence factor entirely if no reliable PV actual measurement exists

---

## Manual Mode (`price_mode = manual`)

Each update cycle (~10 seconds):

1. Read current price from Nordpool entity
2. Calculate price threshold from min/avg/max prices using the user's **Price Threshold Level** (1-10)
3. Apply **hysteresis band** around the threshold to prevent oscillation:
   - Margin = 5% of price spread (max_price - min_price)
   - To **enter** charging: price must drop below `threshold - margin`
   - To **enter** discharging: price must rise above `threshold + margin`
   - To **stay** in current state: price only needs to remain past `threshold` (no margin)
   - Dead zone between `threshold +/- margin` -> remains in current state or idle
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

---

## Safe Power Management

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

---

## EMS Card (Frontend)

### Overview

The EMS card is a LitElement-based HA Lovelace card (`ha_felicity_ems.js`) that provides a complete dashboard for monitoring and controlling the EMS. It includes a canvas-based price chart, interactive controls, and a client-side schedule simulation for live preview.

### Card Layout

```
┌─────────────────────────────────────────────────────────┐
│  [████████░░] 65% / 60 kWh    CHARGING  ACTIVE          │
├─────────────────────────────────────────────────────────┤
│  PRICE           THRESHOLD           LIKELIHOOD          │
│  0.220 €/kWh     0.253 €/kWh         on_track           │
├─────────────────────────────────────────────────────────┤
│  Today's Schedule              [Today] [Tomorrow]        │
│  ┌─────────────────────────────────────────────────┐    │
│  │  Canvas chart: price bars per slot              │    │
│  │  - Green bars = charge slots                    │    │
│  │  - Orange bars = discharge slots                │    │
│  │  - Grey bars = idle                             │    │
│  │  - Red dashed line = price threshold            │    │
│  │  - White border = current slot                  │    │
│  │  - Past: dim colors from actual HA history      │    │
│  └─────────────────────────────────────────────────┘    │
│  ⚡ 4 charge  📡 0 sell  ↓ 9.0 kWh planned  🔋 19.3 res│
├─────────────────────────────────────────────────────────┤
│  PV Today    Remaining    Forecast Today    Tomorrow     │
│  22.8 kWh    9.5 kWh      39.6 kWh         37.7 kWh    │
├─────────────────────────────────────────────────────────┤
│  Grid Mode   Price Mode   Max SOC   Min SOC             │
│  [from_grid] [auto     ]  [100%  ]  [35%  ]             │
│                                                          │
│  Power 7.5 kW            Price Level 5/10                │
│  ═══════●════            ═══════════●══════              │
│                                                          │
│  Safe: 7.5 kW                       Est: 38.5 kWh/d    │
└─────────────────────────────────────────────────────────┘
```

### Battery Indicator

- 10-segment visual bar in the header
- Color-coded: green (>50%), orange (20-50%), red (<20%)
- Shows SOC % and total capacity

### Price Chart (Canvas)

The chart displays one bar per price slot (supports 15-min, 30-min, or hourly granularity).

**Bar coloring for Today view:**

| Slot Type | Color | Description |
|---|---|---|
| Past + actually charged | Dim green (0.3 alpha) | From HA energy_state history |
| Past + actually discharged | Dim orange (0.3 alpha) | From HA energy_state history |
| Past + idle/no action | Dim grey (0.2 alpha) | No significant charge/discharge |
| Current slot (charge) | Bright green + white border | Active now |
| Current slot (discharge) | Bright orange + white border | Active now |
| Current slot (idle) | Light blue + white border | Active now |
| Future charge | Green (#4CAF50) | Scheduled to charge |
| Future discharge | Orange (#FF9800) | Scheduled to sell |
| Future negative price | Blue (#2196F3) | Will charge (paid to take energy) |
| Future idle | Grey (0.4 alpha) | No action planned |

**Tomorrow view** uses slightly softer (0.6 alpha) versions of the same colors.

**Chart elements:**
- Red dashed threshold line with value label
- Zero line when negative prices exist
- Hour markers on x-axis (adaptive spacing)
- Min/max price labels on y-axis

### Past Slot History from HA

The card fetches the `energy_state` entity's history via the HA REST API (`history/period/...`) to determine what actually happened in past slots. This is **throttled to once per 60 seconds**.

For each past slot:
1. Fetch all state changes that overlap the slot's time window
2. Calculate time-weighted duration of each state (charging, discharging, idle)
3. Mark the slot if charging or discharging exceeded 10% of slot duration
4. Color the bar accordingly (dim green for charged, dim orange for discharged)

This provides visual feedback on what the system actually did vs. what was planned.

### Client-Side Simulation

The card includes a full JavaScript reimplementation of the coordinator's schedule optimizer. This enables **live preview** — as the user drags sliders or changes dropdowns, the chart updates instantly without waiting for HA state updates.

**Mirrored logic:**
- Solar-first reserve targeting (same as coordinator)
- Negative price handling
- Round-trip profitability filter for `both` mode
- All three grid modes (from_grid, to_grid, both)

**Simulation parameters** come from the `schedule_status` entity attributes:
- `sim_params.battery_capacity_kwh`
- `sim_params.battery_soc_pct`
- `sim_params.battery_charge_max_pct`
- `sim_params.battery_discharge_min_pct`
- `sim_params.efficiency`
- `sim_params.net_pv_kwh` (already confidence-adjusted by coordinator)
- `sim_params.consumption_est_kwh`
- `self_consumption_reserve`
- `yesterday_deficit_kwh`
- `slot_granularity_min`

**Override parameters** (local to card, used during slider drag):
- `powerKw` — from power slider
- `priceLevel` — from price level slider
- `chargeMax` / `dischargeMin` — from SOC dropdowns
- `gridMode` — from grid mode dropdown

**Tomorrow simulation** differs: assumes battery starts at discharge_min % (worst-case overnight), uses tomorrow's forecast PV minus daily consumption estimate, and all slots are treated as future.

### Interactive Controls

**Dropdowns (4-column grid):**
- Grid Mode: off / from_grid / to_grid / both
- Price Mode: manual / auto
- Max SOC: 100% down to 30% in 5% steps
- Min SOC: 10% up to 70% in 5% steps

**Sliders (2-column grid):**
- Power Level: 1-10 kW in 0.5 kW steps
- Price Threshold Level: 1-10

Sliders use a **preview + commit** pattern:
1. On drag: update local override, re-run simulation, redraw chart (instant)
2. On release: send value to HA via service call
3. After 2 seconds: clear local override to sync with actual HA state

### Price Data Sources (with Fallback)

1. **Primary**: `schedule_status` entity attribute `slot_schedule` / `slot_schedule_tomorrow`
2. **Fallback**: Read directly from Nordpool entity attributes (`today`, `prices_today`, `raw_today` for today; `tomorrow`, `prices_tomorrow`, `raw_tomorrow` for tomorrow)

### Entity Resolution

The card resolves entity IDs from a device_id. It uses `hass.entities` to find all entities belonging to the configured device, then matches by suffix (e.g., `_energy_state`, `_current_price`). A regex fallback handles entity IDs with extra words inserted (e.g., `sensor.xxx_pv_generated_energy_inquiry_day` matches key `pv_generated_energy_day`).

### PV Display

Shows four PV values:
- **PV Today**: Actual production from inverter registers (TREX-5/10: `pv_generated_energy_day` in Wh; TREX-25/50: sum of `pv1-4_day_energy` in kWh)
- **Remaining**: Estimated remaining forecast for rest of day
- **Forecast Today**: Total forecast for today
- **Tomorrow**: Forecast for tomorrow

When `pv_actual_today_kwh` is not available as a schedule_status attribute, the card falls back to reading the entity directly.

---

## Inverter Control

### Register Mapping

When the EMS decides on a state change, `_transition_to_state()` writes the economic rule registers:

| Register | Charging | Discharging | Idle |
|---|---|---|---|
| `econ_rule_1_enable` | 1 | 2 | 0 |
| `econ_rule_1_soc` | Battery Charge Max Level | Battery Discharge Min Level | *(not written)* |
| `econ_rule_1_voltage` | Voltage Level *(max)* | Discharge Min Voltage *(min)* | *(not written)* |
| `econ_rule_1_power` | Safe Max Power (watts) | Safe Max Power (watts) | *(not written)* |
| `econ_rule_1_start_day` | today | today | *(not written)* |
| `econ_rule_1_stop_day` | today | today | *(not written)* |

### Model-Specific Differences

| Aspect | TREX-5 / TREX-10 | TREX-25 / TREX-50 |
|---|---|---|
| **Enable register** | Single `econ_rule_1_enable` @ 8568 (0/1/2) | `econ_rule_1_grid_charge_enable` @ 8713 + mode registers |
| **Power unit** | Watts (direct) | Kilowatts (divide by 1000) |
| **Power registers** | `econ_rule_1_power` only | `econ_rule_1_power` + `grid_peak_shaving_power` |
| **Voltage scaling** | x10 (58V -> 580) | x10 (58V -> 580) |
| **Date registers** | Written (start_day / stop_day) | Ignored (not applicable) |
| **Voltage range** | 48-60 V (dynamic) | 48-500 V |
| **Battery SOC source** | Single battery register | min(bat1_soc, bat2_soc) — conservative |

### TREX-5/10 Charging Example

```
econ_rule_1_enable = 1       → reg 8568 = 1
econ_rule_1_soc = 100        → reg 8575 = 100
econ_rule_1_voltage = 58     → reg 8574 = 580  (×10)
econ_rule_1_power = 3000     → reg 8576 = 3000 (watts)
econ_rule_1_start_day        → reg 8571
econ_rule_1_stop_day         → reg 8572
```

### TREX-25/50 Charging Example

```
econ_rule_1_enable = 1       → reg 8713 = 1 (grid_charge_enable)
                                peak_shaving_enable = 1
econ_rule_1_soc = 100        → reg 8718 = 100
econ_rule_1_voltage = 58     → reg 8717 = 580  (×10)
econ_rule_1_power = 3000     → reg 8719 = 3   (kW, ÷1000)
                                reg 8521 = 3   (grid_peak_shaving)
```

### TREX-25/50 Discharging

```
econ_rule_1_enable = 2       → reg 8713 = 1 (grid_charge_enable)
                                reg 8521 = 0 (peak_shaving = 0)
                                peak_shaving_enable = 0
econ_rule_1_soc = 20         → reg 8718 = 20
econ_rule_1_voltage = 50     → reg 8717 = 500
econ_rule_1_power = 3000     → reg 8719 = 3 (kW)
```

### TREX-25/50 Idle

```
econ_rule_1_enable = 0       → reg 8713 = 0 (grid_charge OFF)
                                reg 8521 = 0 (peak_shaving OFF)
(SOC, voltage, power not written for idle)
```

---

## Informational Sensors (Always Active)

These sensors update regardless of EMS state:

| Sensor | Description |
|---|---|
| **Current Price** | Current electricity price from Nordpool entity |
| **Min Price** | Lowest price today |
| **Max Price** | Highest price today |
| **Price Threshold** | Calculated threshold (manual: from level, auto: from optimizer) |
| **Available Slots** | Number of remaining time slots at or below the current price threshold |
| **Available Energy Capacity** | How much energy (kWh) those slots could provide |
| **Charge Likelihood** | Whether the battery target will likely be met: `on_track` / `tight` / `at_risk` / `insufficient` / `nothing_to_sell` |
| **Schedule Status** | Current optimizer state: `manual` / `active` / `waiting` / `off` / `no_action_needed` |
| **Energy State** | Current inverter state: `charging` / `discharging` / `idle` / `unknown` |
| **PV Forecast Today** | Today's solar production forecast (kWh) |
| **PV Forecast Remaining** | Estimated remaining solar for rest of day |
| **PV Forecast Tomorrow** | Tomorrow's solar forecast |
| **Safe Max Power** | Current power level after Safe Power Management adjustment |
| **Weekly Avg Consumption** | 7-day rolling average daily consumption (kWh). Persisted to disk. |

### Schedule Status Attributes

The `schedule_status` sensor carries rich attributes used by the EMS card:

```python
{
    "slot_schedule": [...],           # Today's price slots with actions
    "slot_schedule_tomorrow": [...],  # Tomorrow's price slots
    "slot_granularity_min": 60,       # Minutes per slot
    "scheduled_charge_slots": 4,
    "scheduled_discharge_slots": 0,
    "grid_energy_planned_kwh": 9.0,
    "self_consumption_reserve": 19.3,
    "yesterday_deficit_kwh": 0.0,
    "pv_actual_today_kwh": 22.8,
    "sim_params": {                   # For client-side simulation
        "battery_capacity_kwh": 60,
        "battery_soc_pct": 85,
        "battery_charge_max_pct": 100,
        "battery_discharge_min_pct": 35,
        "efficiency": 0.90,
        "net_pv_kwh": 5.2,           # Already confidence-adjusted
        "consumption_est_kwh": 38.5
    }
}
```

---

## Midnight Reset & Consumption Tracking

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

The average works with as few as 1 day of data (divides by actual number of entries, not always 7).

**Data priority for daily consumption:**
1. Consumption Override Entity (P1 meter / utility meter) — most accurate
2. Inverter daily energy registers (daily_energy_consumed, daily_load_energy, etc.)
3. Falls back to `Daily Consumption Estimate` setting if neither source provides data

---

## Lessons Learned & Bug Fixes

### 1. PV Forecast Trust Problem

**Issue:** The scheduler assumed PV forecast was accurate and subtracted predicted solar surplus from the grid energy deficit. On cloudy days (0 kWh actual vs 24.7 kWh forecast), this resulted in zero grid charging, leaving the battery far below min SOC.

**Fix:** PV confidence factor scales forecast by `actual / expected_by_now`. See "PV Confidence Factor" section above.

### 2. `both` Mode Tried to Fill Battery to 100%

**Issue:** Original `both` mode used `charge_max` (e.g., 100%) as the target, causing the scheduler to buy grid energy to fill the battery even when solar would handle it.

**Fix:** Changed target to overnight reserve in all modes. Grid charging only covers the gap between current battery level, expected solar, and overnight needs.

### 3. Entity ID Mismatch for Different Inverter Models

**Issue:** TREX-25/50 entity IDs include extra words (e.g., `sensor.xxx_pv_generated_energy_inquiry_day` for key `pv_generated_energy_day`). The card's exact suffix match failed.

**Fix:** Added a regex fallback in `_getEntityId()` that matches key parts in order with optional extra words between them.

### 4. Battery SOC Source Differs by Model

**Issue:** TREX-25/50 has separate `bat1_soc` / `bat2_soc` registers; the coordinator was reading the wrong register for SOC.

**Fix:** Store resolved SOC on coordinator as `self.battery_soc`, using `min(bat1_soc, bat2_soc)` for TREX-25/50 (conservative approach).

### 5. Safe Power Unit Mismatch

**Issue:** `safe_max_power` was in watts (e.g., 7500) but the energy-per-slot calculation treated it as kW, resulting in 7500 kWh/slot instead of 7.5 kWh/slot. Every slot looked like it could charge the entire battery, so all slots were marked as charge slots.

**Fix:** Divide by 1000 to convert watts to kW before energy calculations.

### 6. Hysteresis Prevents State Oscillation

**Design:** A 5% margin of the price spread prevents rapid switching between charging/discharging when the price hovers near the threshold. Once in a state, the system stays until the price clearly crosses the threshold.

### 7. SOC >= 95% Skip

**Design:** When battery is nearly full (>= 95%), skip charge scheduling entirely to avoid unnecessary grid purchases for the last few percent.

### 8. Negative Price Handling

**Design:** Negative prices mean you get paid to consume energy. The scheduler always includes negative-price slots for charging (free energy + payment). It never discharges during negative prices (selling at a loss).

### 9. Yesterday's Deficit Carryover

**Design:** If the battery didn't reach its target yesterday, the deficit carries forward to today's energy target (capped by physical battery headroom). This prevents persistent under-charging across days.

---

## Price Slot Granularity

The EMS automatically handles different price slot granularities:

| Slots/Day | Granularity | Common Source |
|---|---|---|
| 24 | 60 min (hourly) | Nordpool hourly |
| 48 | 30 min | Some markets |
| 96 | 15 min | Intraday markets |

The granularity is detected from the price array length and used throughout: slot energy calculations, chart rendering, current slot detection, and history mapping.
