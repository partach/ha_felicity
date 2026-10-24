# Power Management Basics

## Overview

The HA_Felicity integration includes power management ("Safe Power") to prevent electrical overload by monitoring the grid current on every phase. It lowers the battery's grid charge/discharge power first, and steps down or switches off flexible loads (EV charger, boiler, …) only when that is not enough.

## Configuration

| Setting | Range | Default | Description |
|---|---|---|---|
| **Safe Power Management** | auto / on / off | auto | `auto`: active when grid_mode is not `off`. `on`: always active. `off`: disabled. |
| **Max Amperage Per Phase** | 10-63 A | 16 | The current a phase may carry. Set this to match your main breaker rating. |
| **Power Level** | 1 - max kW (model) | 5 | The Rule 1 power the battery normally charges/discharges at. |
| **EV Charger Grid Phase** | unknown / L1 / L2 / L3 | unknown | The phase a one-phase EV charger (load 1) is on. `unknown` = assume the busiest phase. |

## The Rule

**The Rule 1 power stays at your Power Level. It is written only when a phase is over Max Amperage Per Phase, and goes back to the Power Level as soon as that fits again.**

| Grid Condition | Response |
|---|---|
| **A phase over the limit**, battery charging/discharging above 1 kW | Cut the battery power **in one step** so the busiest phase lands at 90 % of the limit |
| **A phase over 95 %**, battery already at its 1 kW minimum (or idle) | Step the EV charger current down, or switch off a flexible load |
| Battery below the Power Level and **the full Power Level fits again** (busiest phase + the restored power stays ≤ 90 %) | Back to the Power Level in one write |
| Battery idle and below the Power Level | Back to the Power Level (the limit has no effect while idle) |
| Within **30 s of a cut** | Nothing — the inverter is still ramping down, so the current it reports is from before the cut |
| Anything else | Nothing is written |

### How big is a cut?

The battery's power is spread over the inverter's phases, so 1 kW of battery changes each phase by `1000 / (230 V × phases)` A — about 1.45 A on a three-phase inverter, 4.35 A on a single-phase one. The cut is the excess current (above 90 % of the limit) converted to kW, rounded up, never below 1 kW.

Example: 18 A limit, three-phase inverter, battery charging at 8 kW, L1 reads 21 A. Excess over 16.2 A = 4.8 A → 3.3 kW → cut 4 kW → the battery charges at 4 kW and L1 settles near 15 A.

### Why wait 30 s after a cut?

The inverter ramps to a new limit. The next reading still shows the old, higher power — acting on it cut a second time (8 → 3 → 1 kW where 3 kW was enough), and then the low readings raised it again: a see-saw of hundreds of writes a day. Since October 2026 one cut is made and given time to take effect.

## Priority Chain — battery first

| Priority | Action | Detail |
|---|---|---|
| **1st** | Battery power reduction | Lower the Rule 1 power in one step (above). |
| **2nd** | EV charger current step-down | Only when the battery is at its 1 kW minimum (or idle) and a phase is still over 95 %. One current step per poll (e.g. 16A → 13A). |
| **3rd** | Binary load shedding | Switch off a flexible load — highest shed-priority number first (3 before 2 before 1). One load per poll. |

### Why this order?

The Rule 1 power is the inverter's own lever and tells it exactly how hard to charge from the grid; your loads are yours. So the battery gives way first.

### A shed load stays off for a while

A load switched off by Safe Power is **held off for at least 5 minutes**. After that it comes back only when it fits: the current on **its phase** plus the load's own current stays under 95 % of the limit. Its phase is the **EV Charger Grid Phase** setting for a one-phase load 1, otherwise the busiest phase.

### Recovery

Recovery runs in reverse: a shed load that fits comes back first, then the battery returns to its Power Level once that fits.

**Consequence on a tight connection**: during a charge slot with the EV charging, the battery is cut first, then the EV is shed; the battery then returns to its Power Level, and the EV waits until there is room for it (typically when the charge ends).

### EV Boost Protection

During an active EV Boost (one-press +1h override), Safe Power can still step down the EV charger current to protect the grid, but it will **never switch the charger off**. You explicitly asked for urgent charging.

## Power Scaling Logic

```
EVERY POLL
  │
  ▼
Within 30 s of a cut? ──yes──▶ wait (reading is stale)
  │ no
  ▼
A phase over the limit and battery charging/discharging above 1 kW?
  │ yes ──▶ cut battery in one step to 90 % of the limit
  │ no
  ▼
A phase over 95 % and battery at 1 kW / idle?
  │ yes ──▶ step EV current down, or shed a load
  │ no
  ▼
Battery below Power Level?
  │ yes ──▶ a shed load that fits returns first;
  │         otherwise back to Power Level if it fits (or the battery is idle)
  │ no
  ▼
Nothing written
```

## Example Scenario

**Setup**: 18A max per phase, 8 kW Power Level, three-phase inverter (1 kW of battery ≈ 1.45 A per phase), EV charger (one-phase, 16 A) on L2 as load 1 with *EV Charger Grid Phase* = L2, house load ~0.8 A on L2.

| Moment | L2 | Action |
|---|---|---|
| Charge slot starts at 8 kW, EV charging | 16 + 0.8 + 11.6 ≈ 28.4 A | Over 18 A → cut the battery 8 → 1 kW in one write |
| 30 s later | 16 + 0.8 + 1.45 ≈ 18.3 A | Over 95 % (17.1 A) with the battery at its minimum → shed the EV charger |
| Next poll | 0.8 + 1.45 ≈ 2.3 A | 8 kW fits again (2.3 + 7 × 1.45 ≈ 12.4 A ≤ 16.2 A) → back to 8 kW |
| During the charge | 0.8 + 11.6 ≈ 12.4 A | The EV does not fit (12.4 + 16 > 17.1 A) — it waits |
| Charge ends (≥ 5 min after the shed) | ≈ 0.8 A | 0.8 + 16 ≤ 17.1 A → the EV comes back |

With more house load on L2 than ~1 A, a 16 A charger does not fit on an 18 A limit at all while the battery charges — it returns once the charge ends and the phase has room.

## Coordination with Energy Management

Power management runs alongside the energy plan:

- The **plan** always assumes your Power Level — a temporary cut does not make the planner book extra slots. If a cut lasts, the battery falls behind its plan and the next re-plan books what is missing.
- **From Grid**: a cut slows charging. **To Grid**: a cut slows selling.
- **Off mode**: Safe Power is still active if set to `on`.

## Implementation Details

- [TREX10 Power Management](TREX10-Power-Management) — Register details for TREX-5/6/10K
- [TREX25/50 Power Management](TREX25-50-Power-Management) — Register details for TREX-25/50K and IVGM
