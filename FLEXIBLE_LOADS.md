# Flexible Load Control — Setup & Operation Guide

The Felicity integration can manage up to **3 controllable loads** — for example an EV charger, a boiler, and a pool pump.
Loads are steered automatically during the cheapest and most solar-rich hours, exactly like the battery schedule.
This document explains every entity you need to provide, how to configure each load, and when and how the integration will switch them.

---

## Quick overview

| | Load 1 (EV Charger) | Load 2 | Load 3 |
|---|---|---|---|
| On/off control | switch / input_boolean / light | same | same |
| Current stepping | optional (number / select) | — | — |
| Typical use | EV charger | Boiler / water heater | Pool pump / other |

Load 1 has extra EV-charger support: variable current control, phase/voltage settings, and the EV Boost override button.
Loads 2 and 3 are binary (on/off only).

---

## Step 1 — Assign the entities (Integration Options)

Entity assignments are done in the integration's **options flow** — the same place where you set up Nordpool and the solar-forecast entity.

> **Settings → Devices & Services → Felicity Inverter → gear icon (⚙) → Configure**

You will see the following entity selectors at the bottom of the options form:

| Option | Domain filter | Purpose |
|---|---|---|
| **EV Charger / Load 1 — Switch Entity** | `switch`, `input_boolean`, `light` | The entity that turns the load on or off. |
| **EV Charger / Load 1 — Current Entity** | `number`, `select`, `input_number`, `input_select` | *(Optional)* The entity that controls the EV charger's charging current (in Amps). Only needed if your charger supports variable current. |
| **Flexible Load 2 — Switch Entity** | `switch`, `input_boolean`, `light` | The entity that turns load 2 on or off. |
| **Flexible Load 3 — Switch Entity** | `switch`, `input_boolean`, `light` | The entity that turns load 3 on or off. |

**Important:** Until you assign a switch entity to a load, all other settings for that load (power, priority, name, enabled toggle) remain greyed out / unavailable. Similarly, the EV-specific settings (phases, voltage, default current, current steps) only appear once the current entity is assigned.

### Which entities to use?

Most smart plugs, relays, and EV charger integrations in Home Assistant expose a `switch.*` entity.
For example:

- **Easee charger**: `switch.easee_charger` (on/off), `number.easee_charger_dynamic_charger_limit` or `select.easee_charger_current` (current)
- **Shelly relay**: `switch.shelly_plug_s`
- **Wallbox**: `switch.wallbox_portal_charging`, `number.wallbox_portal_maximum_charging_current`
- **OpenEVSE**: `switch.openevse`, `number.openevse_max_current`
- **Smart Life plug (boiler)**: `switch.smart_life_boiler`

The exact entity names depend on your installation. Check your device page in Home Assistant.

---

## Step 2 — Configure load settings (Device Page)

After assigning a switch entity, these configuration entities become available on the device page:

### All loads (1, 2, 3)

| Entity | Type | Range | Default | Description |
|---|---|---|---|---|
| **Enabled** | Select | off / on | off | Master switch for EMS-controlled scheduling. When `off`, the integration will not turn the load on or off. |
| **Name** | Text | — | *(empty)* | Friendly name shown in the EMS card (e.g., "Boiler", "Pool Pump"). |
| **Power** | Number | 0.5 – 25 kW | 3.7 (load 1) / 2.0 (load 2-3) | Rated power of the load. Used in safe-power calculations. |
| **Shed Priority** | Number | 1 – 3 | 1 / 2 / 3 | Order in which loads are shed during overcurrent. **Higher number = shed first.** Load 1 (EV) defaults to 1 so it is shed last. |

### EV charger extras (Load 1 only — requires current entity)

| Entity | Type | Range | Default | Description |
|---|---|---|---|---|
| **Current Steps** | Text | — | *(empty)* | Comma-separated list of available charging currents your charger supports, in Amps. Example: `6,10,13,16,20,25`. The integration uses this list to step the current up or down. |
| **Phases** | Number | 1 – 3 | 1 | Number of phases the charger uses. Together with voltage, this determines the power at each current step: `power = amps × voltage × phases / 1000`. |
| **Voltage** | Number | 110 – 400 V | 230 | Supply voltage at the charger. |
| **Default Current** | Number | 6 – 32 A | 16 | The current the integration sets when it turns the charger on during a scheduled slot. |

---

## When does the integration turn loads on?

Every 10 seconds the integration evaluates the current time slot. A load that is **enabled** and has a **switch entity** assigned will be turned **on** when the current slot matches **any** of these conditions:

| Condition | Example |
|---|---|
| **Price is below the threshold** | Electricity costs €0.05/kWh and your threshold is at level 5 (= avg price €0.12/kWh). |
| **Price is negative** | The grid pays you to consume (common midday in solar-heavy markets). |
| **Solar surplus** | Your PV forecast for this hour exceeds your consumption for this hour — free energy. |
| **Battery charge slot** | The EMS already identified this slot as cheap enough to charge the battery — equally good for your loads. |

When the slot ends (or none of the above conditions are met), the integration turns the load **off**.

### Scheduling is an overlay

Load scheduling is calculated **after** the battery schedule. Loads do not affect the battery plan — they are additive consumption. The battery will charge in the same slots regardless of whether loads are on.

This means: if electricity is cheap, both the battery charges **and** your loads run. Maximum benefit from the same cheap window.

---

## How does current stepping work? (EV charger)

When the integration turns on the EV charger, it sets the current to **Default Current** via the assigned current entity.

If your current entity is a `number.*`, the integration calls `number.set_value`.
If it is a `select.*`, the integration calls `select.select_option`.

During safe-power events (see below), the integration steps the current down using the **Current Steps** list: it finds the next lower step that reduces power enough.

Example with current steps `6,10,13,16,20,25` and default current 16A:
- Normal operation: charger runs at 16A.
- Grid current is 85% of max → charger is stepped down to 13A.
- Grid current still high → charger is stepped down to 10A.
- Grid current normalises → charger is stepped back up to 16A.

---

## Safe Power Protection — Load Shedding Priority

When the grid current on any phase exceeds the configured **Max Amperage Per Phase**, the integration reduces load in this order:

| Priority | Action | Detail |
|---|---|---|
| **1st** | EV charger current step-down | Reduce one current step at a time (e.g., 16A → 13A → 10A). Only one step per 10-second cycle to avoid oscillation. |
| **2nd** | Binary load shedding | Turn off active loads in shed-priority order — **highest priority number first**. Load with priority 3 is shed before priority 2, which is shed before priority 1. One load per cycle. |
| **3rd** | Battery power reduction | As a last resort, the battery charge/discharge power is reduced by 1–2 kW. |

Recovery works in reverse: when current drops below 70% of max, loads are restored one per cycle in reverse priority order, and battery power is increased back toward the user's Power Level setting.

---

## EV Boost Override

For situations where you need to charge your car urgently — regardless of price or schedule:

| Button | What it does |
|---|---|
| **EV Boost +1h** | Adds 1 hour to the boost timer. Presses **stack**: pressing 3 times gives 3 hours. |
| **EV Boost Cancel** | Immediately cancels any active boost. |

These buttons are only available when the **current entity** is assigned (Load 1).

### What happens during a boost?

1. The EV charger is **forced on at maximum current** (the highest value in your current steps list).
2. The normal schedule is bypassed for the charger — it stays on regardless of price.
3. Safe Power Management **can still step down the current** if the grid is overloaded, but it will **never fully turn off the charger** during a boost.
4. Other loads (2 and 3) continue to follow their normal schedule.
5. The boost expires automatically when the timer runs out.

The EMS card shows a **cyan banner** with a countdown: _"EV Boost active — 1h 45m remaining"_.

---

## What the EMS card shows

When at least one load is configured:

- **Cyan strip** at the bottom of the price bars for slots where loads are scheduled.
- **"loads"** entry in the chart legend.
- **"N/M loads active"** in the stats row (e.g., "2/3 loads active" means 2 out of 3 configured loads are currently on).
- **EV Boost banner** (when active) with countdown timer above the chart.

Load schedule data (`flex_load_schedule`, `flex_load_states`, `flex_load_configs`) is available in the `schedule_status` sensor attributes for use in automations.

---

## Example setups

### EV charger only

1. Options flow: assign `switch.wallbox_charging` and `number.wallbox_max_current`.
2. Device page: set Enabled → `on`, Name → `EV Charger`, Power → `7.4`, Phases → `1`, Voltage → `230`, Default Current → `32`, Current Steps → `6,10,16,20,25,32`.
3. The charger will run during cheap/negative/solar-surplus hours and step current during overcurrent.

### EV charger + boiler

1. Options flow: assign switch entities for both loads. Assign the current entity for load 1.
2. Device page: configure both loads as above.
3. Set the boiler's Shed Priority to `2` and the EV charger to `1`. During overcurrent, the boiler sheds first, protecting the EV charge session.

### Three loads (EV + boiler + pool pump)

1. Options flow: assign switch entities for all three.
2. Device page: enable all three, set priorities: EV=1, boiler=2, pool pump=3.
3. During overcurrent, pool pump sheds first, then boiler, then EV current steps down — battery power reduction is the last resort.
