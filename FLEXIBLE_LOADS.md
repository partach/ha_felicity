# Flexible Load Control — Setup & Operation Guide

The integration can switch up to **3 loads** for you — an EV charger, a boiler,
a pool pump — in the hours where electricity is cheap, negative, or covered by
your own solar.  This guide covers what to assign, every setting, and when the
integration switches a load on or off.

> Flexible loads are scheduled by the EMS, so they only run while the EMS is on
> (Grid Mode not `off`).  Monitor-only models (IVAM6048P1G1, IVGM100600) have no
> EMS and therefore no flexible loads.

---

## Quick overview

| | Load 1 (EV charger) | Load 2 | Load 3 |
|---|---|---|---|
| On/off control | `switch` / `input_boolean` / `light` | same | same |
| Current control | optional (`number` / `select` / `input_number` / `input_select`) | — | — |
| EV Charge Strategy, EV Boost | yes | — | — |
| Typical use | EV charger | boiler / water heater | pool pump / other |

Load 1 becomes an **EV charger** as soon as it has a current entity: it then
gets the EV Charge Strategy, current stepping (with Current Steps) and the EV
Boost buttons.  Loads 2 and 3 are on/off only.

---

## Step 1 — Assign the entities (integration options)

> **Settings → Devices & services → Felicity → ⚙ Configure**

At the bottom of the options form:

| Option | Accepts | Purpose |
|---|---|---|
| **EV Charger / Load 1 — Switch Entity** | `switch`, `input_boolean`, `light` | Turns load 1 on and off. |
| **EV Charger / Load 1 — Current Entity** | `number`, `select`, `input_number`, `input_select` | *(Optional)* Sets the charger's current in amps. Makes load 1 an EV charger. |
| **Flexible Load 2 — Switch Entity** | `switch`, `input_boolean`, `light` | Turns load 2 on and off. |
| **Flexible Load 3 — Switch Entity** | `switch`, `input_boolean`, `light` | Turns load 3 on and off. |

A load's other settings stay unavailable until its switch entity is assigned;
the EV settings (phases, voltage, startup current, current steps) until the
current entity is.  Saving the options form without touching a picker keeps
what was assigned; only clearing a picker removes it.

Examples (names differ per installation — check the device page):

- **Easee**: `switch.easee_charger`, `number.easee_charger_dynamic_charger_limit`
- **Wallbox**: `switch.wallbox_portal_charging`, `number.wallbox_portal_maximum_charging_current`
- **OpenEVSE**: `switch.openevse`, `number.openevse_max_current`
- **Shelly / smart plug (boiler)**: `switch.shelly_plug_s`

---

## Step 2 — Configure the loads (device page)

### Every load (1, 2, 3)

| Entity | Range | Default | What it does |
|---|---|---|---|
| **… Enabled** | off / on | off | The EMS only switches the load while this is `on`. While it is `on`, the EMS owns the switch: a load you start by hand is switched off again in a slot it is not scheduled for. |
| **… Name** | text | — | Shown on the EMS card ("Boiler", "Pool pump"). |
| **… Rated Power** | 0.5 – 25 kW | 3.7 (load 1) / 2.0 | The load's draw. Decides whether the sun covers it, and how much current it adds when it comes back after being shed. For a stepped EV charger the draw is computed from current × voltage × phases instead. |
| **… Shed Priority (1=last 3=first)** | 1 – 3 | 1 / 2 / 3 | Order in which loads are switched off when a phase is overloaded: **3 first, 1 last**. |

### EV charger (load 1, needs the current entity)

| Entity | Range | Default | What it does |
|---|---|---|---|
| **EV Charger / Load 1 Current Steps** | text | — | The currents your charger supports, comma-separated: `6,10,13,16,20,25` (units are fine: `6 A, 10 A`). Enables current stepping. Without it the charger runs on/off at its own setting. |
| **EV Charger Number of Phases** | 1 – 3 | 1 | Power = amps × voltage × phases (16 A × 230 V × 1 = 3.7 kW; × 3 = 11 kW). |
| **EV Charger Grid Voltage** | 110 – 400 V | 230 | Used in the same formula. |
| **EV Charger Startup Current** | 6 – 32 A | 16 | Current set when the charger is switched on. Rounded down to the nearest step (11 A with `6,10,13,16` → 10 A). |
| **EV Charger Grid Phase** | unknown / L1 / L2 / L3 | unknown | The phase a 1-phase charger is on. After safe power switched it off, it may come back when *its own* phase has room. `unknown` waits for room on the busiest phase (safe, but on a small connection it can keep the charger off). |
| **EV Charge Strategy** | smart / solar_only / cheap_only / always_on | smart | When the charger may run — see below. |

---

## When does a load run?

The plan is made every time the EMS re-plans (each slot, and whenever prices,
forecast or SOC change); the switches are set every poll (10 s).  A load that is
**enabled** runs in a slot that is any of:

| Condition | Meaning |
|---|---|
| **Battery charge slot** | The EMS is buying grid energy for the battery here — the cheapest moments it found. |
| **Cheap, and the grid feeds the house** | The price is at or below the dearest slot the battery charges in, *and* the grid feeds the house there (battery charging, or planned at its minimum). In any other slot the battery would feed the load in self-use, so the cheap price would never be paid. If the battery buys nothing today, no slot counts as cheap. |
| **Negative price** | You are paid to consume. |
| **Solar surplus that covers the load** | Forecast PV (scaled by how well today's sun matches the forecast) minus the house's hourly consumption profile is at least the load's power — for a stepped charger, its lowest step. A charger running on sun alone gets the highest step the surplus covers. |

Outside those slots the load is switched **off**.

Loads are an **overlay**: they don't change the battery plan, and the battery
comes first.  They are not included in the battery's SOC line either.

### EV Charge Strategy (load 1)

| Strategy | The charger runs in |
|---|---|
| **smart** (default) | every condition above |
| **solar_only** | solar-surplus slots only |
| **cheap_only** | cheap (as defined above) and negative-price slots |
| **always_on** | every slot — safe power still steps the current down when a phase is overloaded |

Loads 2 and 3 always use *smart*.

---

## Grid current protection (safe power)

With **Safe Power Management** on (or `auto` with the EMS on), the integration
watches the current on each phase against **Max Amperage Per Phase**:

| Order | Action |
|---|---|
| **1st — battery** | When a phase is over the limit while the battery charges or discharges, the battery's power is cut **in one step** to the level that brings the busiest phase back to 90 % of the limit (never below 1 kW). |
| **2nd — EV current** | If the battery is at its 1 kW minimum (or idle) and a phase is still above 95 %, the EV charger is stepped down one current step per poll. |
| **3rd — switch loads off** | Still above 95 %: loads are switched off one per poll, Shed Priority 3 first, 1 last. |

A switched-off load stays off for at least **5 minutes**, and then comes back
only when its own current fits (current on its phase + its own amps ≤ 95 % of
the limit); otherwise it checks again every minute.  A load that fits comes
back before the battery's power is raised.  The battery's power goes back to
your **Power Level** in one step as soon as that fits again.  While the battery
is idle, its power setting is not touched.

---

## EV Boost (load 1)

| Button | What it does |
|---|---|
| **EV Boost +1h** | Forces the charger on at its highest current step for an hour. Presses add up (3 presses = 3 hours). |
| **EV Boost Cancel** | Ends the boost at once. |

The buttons exist only when load 1 has a current entity.  During a boost safe
power may still step the current down, but never switches the charger off.
Loads 2 and 3 keep their schedule.  The boost ends on its own; its end time
survives a Home Assistant restart.  The card shows a cyan countdown banner and a
**Boost Xh Ym** chip.

---

## What the EMS card shows

- A **cyan strip** under the price bars where a load is scheduled, and a
  "loads" legend entry.
- **"N/M loads active"** in the stats row.
- The **Flexible Loads panel**: one row per load with its on/off state, live
  power and a fill bar (for the EV charger the real draw, with
  `A · φ · V`), a **BOOST** chip during a boost, and a shed-priority badge
  ("Sheds 1st / 2nd / last"); the header shows the total live power.

For automations: `flex_load_schedule`, `flex_load_states` and
`flex_load_configs` are attributes of the `schedule_status` sensor.

After a Home Assistant restart each load's on/off state is read from its switch,
so a charger left running is still known to be on.

---

## Example setups

**EV charger only** — assign `switch.wallbox_portal_charging` and
`number.wallbox_portal_maximum_charging_current`; set Enabled `on`, Name
`EV`, Phases `1`, Voltage `230`, Startup Current `32`, Current Steps
`6,10,16,20,25,32`, and the Grid Phase it is wired to.

**EV + boiler** — assign both switches and the EV current entity; boiler Shed
Priority `2`, EV `1`: on an overloaded phase the battery is cut first, then the
EV current stepped down, then the boiler switched off before the EV.

**EV + boiler + pool pump** — priorities EV 1, boiler 2, pool pump 3: the pool
pump goes first, the EV last.

---

*Behaviour reference for developers: `docs/EMS_LOGIC.md` §10 and CLAUDE.md
("Flexible Load Control", Known Issues 8h / 8i / 8j / 10).*
