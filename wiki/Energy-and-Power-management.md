# Energy and Power Management

## Overview

The HA_Felicity integration provides intelligent energy and power management for home battery and grid systems using Felicity TREX inverters. It combines price-aware scheduling with overcurrent protection and flexible load control.

## Table of Contents

### Energy Management
- [Energy Management Basics](Energy-Management-Basics) — Algorithm overview: modes, scheduling, reserve target, advanced settings
- [TREX10 Energy Management](TREX10-Energy-Management) — Register-level details for TREX-5/10K models
- [TREX25/50 Energy Management](TREX25-50-Energy-Management) — Register-level details for TREX-25/50K models

### Power Management
- [Power Management Basics](Power-Management-Basics) — Overcurrent protection, load shedding, EV current stepping
- [TREX10 Power Management](TREX10-Power-Management) — Power register details for TREX-5/10K models
- [TREX25/50 Power Management](TREX25-50-Power-Management) — Power register details for TREX-25/50K models

## Quick Start

The integration provides automated energy management with four operational modes:

| Mode | Description |
|---|---|
| **Off** | No active energy management. |
| **From Grid** | Charge battery from grid when prices are low. |
| **To Grid** | Sell battery energy to grid when prices are high. |
| **Both** | Buy cheap + sell expensive with profitability filter. |

Two price modes are available:
- **Manual**: A price threshold (level 1-10) determines when to charge/sell.
- **Auto**: The optimizer selects the cheapest charge slots and most expensive sell slots automatically, using two-day look-ahead when tomorrow's prices are available.

## Key Concepts

### Reserve Target
The EMS does not fill the battery to 100%. It calculates a **reserve target** — just enough to survive overnight based on your consumption and sunset/sunrise times. This keeps room for solar to fill the battery during the day.

### Solar-First
Grid charging is a last resort. The algorithm checks whether PV production will naturally fill the battery before scheduling any grid charging.

### Flexible Loads
Up to 3 controllable loads (EV charger, boiler, pool pump) can be scheduled during cheap/negative-price/PV-surplus hours. See [FLEXIBLE_LOADS.md](https://github.com/partach/ha_felicity/blob/main/FLEXIBLE_LOADS.md).

### Safe Power
Monitors grid current per phase. Reduces loads and battery power automatically to prevent overcurrent. Priority chain: EV charger current step-down → binary load shedding → battery power reduction.
