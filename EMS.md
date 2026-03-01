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

### Step 2 — Set Grid Mode to `from_grid` or `to_grid`

Use the **Grid Mode** selector entity on your dashboard:

- `from_grid` — Charge battery from the grid during cheap price slots
- `to_grid` — Sell battery energy back to the grid during expensive price slots

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
| **Grid Mode** | `off` / `from_grid` / `to_grid` | `off` | Main EMS switch. `from_grid` = charge from grid, `to_grid` = sell to grid. |
| **Price Mode** | `manual` / `auto` | `manual` | `manual` = user sets price level, `auto` = optimizer picks best slots. |
| **Safe Power Management** | `auto` / `on` / `off` | `auto` | Controls amperage-based power limiting. `auto` = active only when Grid Mode is on. `on` = always active. `off` = never active (for external EMS). |

### Number Entities (Dashboard)

| Entity | Range | Default | Description |
|---|---|---|---|
| **Power Level** | 1–10 | 5 | Charge/discharge power in kW. Written to `econ_rule_1_power`. |
| **Price Threshold Level** | 1–10 | 5 | Where the price threshold sits between min and max price. Only used in manual mode. 1 = only cheapest, 10 = almost all slots. |
| **Battery Charge Max Level** | 0–100% | 100 | Target SOC for charging. Charging stops when SOC reaches this level. |
| **Battery Discharge Min Level** | 0–100% | 20 | Minimum SOC for discharging. Discharging stops when SOC drops to this level. |
| **Battery Capacity** | 1–100 kWh | 10 | Total usable battery capacity. Used by the schedule optimizer for energy calculations. |
| **Efficiency Factor** | 0.70–1.00 | 0.90 | Round-trip charge/discharge efficiency. Accounts for conversion losses. |
| **Daily Consumption Estimate** | 0–100 kWh | 10 | Fallback daily consumption estimate. Replaced by 7-day rolling average when consumption data is available. |

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

### Manual Mode (`price_mode = manual`)

Each update cycle (~10 seconds):

1. Read current price from Nordpool entity
2. Calculate price threshold from min/avg/max prices using the user's **Price Threshold Level** (1–10)
3. Compare current price to threshold:
   - `from_grid`: If current price **<** threshold AND battery SOC **<** charge max → **charge**
   - `to_grid`: If current price **>** threshold AND battery SOC **>** discharge min → **discharge**
   - Otherwise → **idle**
4. Write economic rule registers on state change

### Auto Mode (`price_mode = auto`)

Each update cycle:

1. Retrieve all price slots for today (supports 15-min, 30-min, or hourly granularity)
2. Retrieve PV forecast (if configured)
3. Calculate energy deficit: `target_kwh - current_kwh - net_pv + yesterday_carryover` (capped by battery headroom)
4. **from_grid**: Select cheapest N slots to fill the deficit. Negative-price slots are always included (you get paid to charge).
5. **to_grid**: Select most expensive N slots for sellable energy. Negative-price slots are excluded (never sell at a loss).
6. If the current time slot is in the scheduled set → activate charge/discharge
7. The price threshold is automatically set to the boundary price of selected slots

### Safe Power Management

When active, each update cycle:

1. Read the highest grid current across all phases
2. Compare against **Max Amperage Per Phase** setting:
   - **> 95%**: Reduce power level by 2 (emergency)
   - **> 80%**: Reduce power level by 1 (caution)
   - **< 70%**: Recover power level by 1 (up to user's Power Level)
   - **0 or no current**: Jump directly to user's Power Level
3. Write `econ_rule_1_power` register when the level changes

### Informational Sensors (Always Active)

These sensors update regardless of EMS state and provide insight:

| Sensor | Description |
|---|---|
| **Available Slots** | Number of remaining time slots at or below the current price threshold |
| **Available Energy Capacity** | How much energy (kWh) those slots could provide |
| **Charge Likelihood** | Whether the battery target will likely be met: `on_track` / `tight` / `at_risk` / `insufficient` |
| **Schedule Status** | Current optimizer state: `manual` / `active` / `waiting` / `off` / `no_action_needed` |
| **Weekly Avg Consumption** | 7-day rolling average daily consumption (kWh) |

### Midnight Reset

At midnight each day:

1. Record today's energy consumption (from override entity or inverter registers) for the rolling average
2. Calculate yesterday's deficit (how far short of the charge target)
3. Carry the deficit forward to the next day's energy target (capped by battery headroom)
4. Reset the energy state to idle
5. Begin a new scheduling cycle
