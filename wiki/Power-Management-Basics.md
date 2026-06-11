# Power Management Basics

## Overview

The HA_Felicity integration includes power management to prevent electrical overload by monitoring and controlling the current on all three phases of the electrical system. It extends beyond simple battery power reduction to include flexible load shedding and EV charger current stepping.

## Configuration

| Setting | Range | Default | Description |
|---|---|---|---|
| **Safe Power Management** | auto / on / off | auto | `auto`: activates when grid_mode is not `off`. `on`: always active. `off`: disabled. |
| **Max Amperage Per Phase** | 10-63 A | 16 | Your home's maximum amperage per phase. Set this to match your main breaker rating. |
| **Power Level** | 1 - max kW (model) | 5 | Target power for normal operation. The upper limit for power restoration. |

## Response Tiers

The system uses a tiered response based on how close the grid current is to the configured maximum:

| Grid Condition | Response |
|---|---|
| **> 95% of max amperage** | Emergency — reduces power by 2 kW immediately |
| **> 80% of max amperage** | Caution — reduces power by 1 kW |
| **< 70% of max amperage** | Recovery — restores power by 1 kW (up to Power Level) |
| **Current = 0** | Jumps immediately to full Power Level |

## Load Shedding Priority Chain

When flexible loads are configured, the system uses a three-tier priority chain before reducing battery power:

| Priority | Action | Detail |
|---|---|---|
| **1st** | EV charger current step-down | Reduce one current step per cycle (e.g., 25A → 20A → 16A). Configured via the current steps list. |
| **2nd** | Binary load shedding | Turn off active loads by shed priority — highest number first (3 before 2 before 1). One load per cycle. |
| **3rd** | Battery power reduction | Reduce charge/discharge power by 1-2 kW. Last resort. |

### Why this order?

- **EV step-down** is the gentlest intervention — the car still charges, just slower.
- **Load shedding** removes discretionary loads (boiler, pool pump) before touching the battery.
- **Battery reduction** is the last resort because it directly impacts the EMS schedule.

### Recovery

Recovery works in reverse order:
1. Battery power restored first (up to Power Level)
2. Loads turned back on in reverse priority (lowest number first)
3. EV charger current stepped up

### EV Boost Protection

During an active EV Boost (one-press +1h override), the safe power system can still step down the EV charger current to protect the grid, but it will **never fully turn off** the charger. The user explicitly requested urgent charging.

## Power Scaling Logic

```
START (every 10 seconds)
  │
  ▼
Monitor phase currents (3 phases)
  │
  ▼
Any phase > 95% of max? ──yes──▶ Emergency: shed/reduce 2 kW
  │ no
  ▼
Any phase > 80% of max? ──yes──▶ Caution: shed/reduce 1 kW
  │ no                              Priority: EV step-down → load shed → battery
  ▼
All phases < 70% of max?
  │ no ──▶ Hold current levels
  │ yes
  ▼
Current power < target? ──yes──▶ Restore 1 kW (reverse priority)
  │ no
  ▼
Stable at target power
```

## Example Scenario

**Setup**: 16A max per phase, 5 kW power level, EV charger on load 1 (steps: 6,10,13,16,20A), boiler on load 2 (priority 2), pool pump on load 3 (priority 3).

**Event**: EV charger + oven push Phase 2 to 15A (94% of 16A max).

| Cycle | Phase 2 | Action | Result |
|---|---|---|---|
| 1 | 15A (94%) | > 80% → caution: step EV from 16A to 13A | EV drops ~0.7 kW |
| 2 | 14A (88%) | > 80% → shed pool pump (priority 3) | Pool off, ~2 kW removed |
| 3 | 10A (63%) | < 70% → restore pool pump | Pool back on |
| 4 | 13A (81%) | > 80% → re-shed pool pump | Pool off |
| (stable) | 10A | Holds until oven turns off | |
| 5+ | 8A (50%) | < 70% → restore pool, then step EV 13→16A | Full recovery |

## Coordination with Energy Management

Power management operates independently from energy management but coordinates:

- **From Grid mode**: Power reduction affects charging rate
- **To Grid mode**: Power reduction affects discharge rate
- **Both mode**: Affects both directions
- **Off mode**: Power management still active if set to `on`

## Implementation Details

- [TREX10 Power Management](TREX10-Power-Management) — Register details for TREX-5/10K
- [TREX25/50 Power Management](TREX25-50-Power-Management) — Register details for TREX-25/50K
