# Energy and Power Management

## Overview

The HA_Felicity integration provides intelligent energy and power management for home battery and grid systems using Felicity inverters. It combines price-aware scheduling with overcurrent protection and flexible load control.

## Table of Contents

### Energy Management
- [Energy Management Basics](Energy-Management-Basics) — Algorithm overview: modes, the planner, reserve, advanced settings
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
- **Auto**: The scheduler plans the whole horizon (today, plus tomorrow once its prices are out). In From Grid the **exact planner** works out the cheapest plan; To Grid and Both use the greedy selector.

## Key Concepts

### Buy only what saves money
The planner does not fill the battery for its own sake. It buys a kWh only when that kWh, after charging losses, is cheaper than the grid energy the house would otherwise buy later. A battery that lasts until a cheaper moment or until the sun simply waits.

### Survive the night
After the last day with known prices, the battery is expected to carry the house from midnight to sunrise. A kWh short then is priced at that night's grid price — that is what makes the planner charge ahead of the night when the night is dearer.

### Solar-First
Energy the sun will deliver has no value to buy. The planner sees the PV forecast (scaled by how well today's forecast is holding up) and never buys what PV will fill.

### Flexible Loads
Up to 3 controllable loads (EV charger, boiler, pool pump) can be scheduled during cheap/negative-price/PV-surplus hours. See [FLEXIBLE_LOADS.md](https://github.com/partach/ha_felicity/blob/main/FLEXIBLE_LOADS.md).

### Safe Power
Monitors grid current per phase. The Rule 1 power stays at your **Power Level** and is lowered only when a phase is over **Max Amperage Per Phase**. Priority chain: battery power first, then EV charger current step-down, then load shedding. See [Power Management Basics](Power-Management-Basics).
