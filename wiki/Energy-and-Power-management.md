# Energy and Power Management

## Overview

The HA_Felicity integration provides intelligent energy and power management for home battery and grid systems using Felicity inverters. It combines price-aware scheduling with overcurrent protection and flexible load control.

## Table of Contents

### Energy Management
- [Energy Management Basics](Energy-Management-Basics) — Algorithm overview: modes, engines, reserve, advanced settings
- [TREX10 Energy Management](TREX10-Energy-Management) — Register-level details for TREX-5/6/10K models
- [TREX25/50 Energy Management](TREX25-50-Energy-Management) — Register-level details for TREX-25/50K (and IVGM) models

### Power Management
- [Power Management Basics](Power-Management-Basics) — Overcurrent protection, load shedding, EV current stepping
- [TREX10 Power Management](TREX10-Power-Management) — Power register details for TREX-5/6/10K models
- [TREX25/50 Power Management](TREX25-50-Power-Management) — Power register details for TREX-25/50K (and IVGM) models

## Quick Start

Pick a **Strategy** on the EMS card; it sets the two knobs below for you:

| Strategy | Grid Mode | Optimization Priority |
|---|---|---|
| Save Money | from_grid | cost |
| Self-Sufficiency | from_grid | self_consumption |
| Battery Care | from_grid | longevity |
| Trader | both | cost |
| Custom | (unchanged) | (unchanged) |

A strategy changes only those two settings; your reserve, cycle cost, arbitrage delta and negative-price choices are kept.

The four grid modes:

| Mode | Description |
|---|---|
| **Off** | No energy management, and **nothing is written to the inverter**. |
| **From Grid** | Charge the battery from the grid when that saves money. |
| **To Grid** | Sell battery energy to the grid when prices are high. |
| **Both** | Buy cheap + sell expensive, with a profitability filter. |

Two price modes are available:
- **Manual**: A price threshold (level 1-10) determines when to charge/sell.
- **Auto**: The scheduler plans the whole horizon (today, plus tomorrow once its prices are out) with the selected engine — greedy (default) or MILP.

## Key Concepts

### Survive the night, in the cheapest slots
The EMS keeps the battery above Min SOC and through the night (the **reserve target**: Min SOC + what the night needs) and buys that energy in the cheapest known slots. It does not fill the battery for its own sake; beyond the reserve it buys only for Self-Sufficiency's top-off or on a day clearly cheaper than this week's average.

### At Min SOC the house runs on the grid
That is the inverter's own behaviour at its minimum. The EMS never forces a charge at the current price because the battery is low; the gap is bought back in the cheapest slots.

### Solar-First
Energy the sun will deliver has no value to buy. The plan uses the PV forecast (scaled by how well today's forecast is holding up) and does not buy what PV will fill.

### Flexible Loads
Up to 3 controllable loads (EV charger, boiler, pool pump) can be scheduled during cheap/negative-price/PV-surplus hours. See [FLEXIBLE_LOADS.md](https://github.com/partach/ha_felicity/blob/main/FLEXIBLE_LOADS.md).

### Safe Power
Monitors grid current per phase. The Rule 1 power stays at your **Power Level** and is lowered only when a phase is over **Max Amperage Per Phase**. Priority chain: battery power first, then EV charger current step-down, then load shedding. See [Power Management Basics](Power-Management-Basics).
