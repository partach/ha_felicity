# HA-Felicity (Modbus)

Felicity inverter Home Assistant integration for easy setup and use of the device via Modbus. Includes a full Energy Management System to buy and sell electricity at the best moments, with dynamic power management to safeguard against overcurrent.

## Features

- No YAML configuration required
- Full Energy Management System (EMS) with automatic charge/discharge scheduling based on electricity prices
- Two-day optimization — uses tomorrow's prices when available
- Solar-first approach — dynamic reserve target charges only what's needed overnight
- Flexible load control — schedule up to 3 loads (EV charger, boiler, pool pump) during cheap/solar hours
- EV Boost override — one-press +1h button to force EV charging
- Dynamic power management — automatic overcurrent protection with per-phase monitoring
- Negative price strategies — profit from negative electricity prices
- Serial and TCP Modbus support
- Customizable registers (basic, basic plus, full)
- Multiple hubs supported for multiple inverters
- EMS dashboard card with interactive price chart, SOC trajectory, and live slider preview

## Quick Links

- [Energy and Power Management](Energy-and-Power-management) — Overview of all management features
- [Energy Management Basics](Energy-Management-Basics) — How the scheduling algorithm works
- [Power Management Basics](Power-Management-Basics) — Overcurrent protection and load shedding
- [TREX10 Energy Management](TREX10-Energy-Management) — TREX-5/10K register details
- [TREX10 Power Management](TREX10-Power-Management) — TREX-5/10K power register details
- [TREX25/50 Energy Management](TREX25-50-Energy-Management) — TREX-25/50K register details
- [TREX25/50 Power Management](TREX25-50-Power-Management) — TREX-25/50K power register details

## Installation

1. **Via HACS** (recommended): Search for "ha_felicity" or use the HACS repository link.
2. **Manual**: HACS → Custom repositories → Repo: `partach/ha_felicity`, Type: Integration.

After installation, reboot HA and add the integration via Devices & Services.

## Configuration

All EMS settings are available as configuration entities on the device page. Entity assignments (Nordpool, Solar Forecast, EV charger, flexible loads) are configured via the integration options flow (gear icon).

See the [README](https://github.com/partach/ha_felicity/blob/main/README.md) for full installation and configuration details.

See [FLEXIBLE_LOADS.md](https://github.com/partach/ha_felicity/blob/main/FLEXIBLE_LOADS.md) for the flexible load control setup guide.

## Support

- [Discussions](https://github.com/partach/ha_felicity/discussions)
- [Issues](https://github.com/partach/ha_felicity/issues)
- [Changelog](https://github.com/partach/ha_felicity/blob/main/CHANGELOG.md)
