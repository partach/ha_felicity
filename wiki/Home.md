# HA-Felicity (Modbus)

Felicity inverter Home Assistant integration for easy setup and use of the device via Modbus. Includes a full Energy Management System to buy and sell electricity at the best moments, with dynamic power management to safeguard against overcurrent.

## Features

- No YAML configuration required
- Full Energy Management System (EMS) with automatic charge/discharge scheduling based on electricity prices
- **Exact cost planner** (default engine) — buys only when that is cheaper, after losses, than the grid energy it replaces later
- Two-day optimization — uses tomorrow's prices when available
- Solar-first approach — never buys what the sun will deliver for free
- Consumption profile learned from your own Home Assistant history (7 days, per hour)
- Flexible load control — schedule up to 3 loads (EV charger, boiler, pool pump) during cheap/solar hours
- EV Boost override — one-press +1h button to force EV charging
- Dynamic power management — per-phase overcurrent protection that only steps in when a phase is over your limit
- Negative price strategies — profit from negative electricity prices
- Manual slot overrides — click slots in the EMS card to force a charge or sale
- Serial and TCP Modbus support (including RS485-to-Ethernet gateways)
- Multiple hubs supported for multiple inverters
- EMS dashboard card with interactive price chart, SOC trajectory, and live slider preview
- Download diagnostics — a full register dump with built-in sanity checks

## Supported Inverters

| Model | Control | Notes |
|---|---|---|
| T-REX-5K-P1G01 / IVGM5048 | Full EMS | Single phase |
| T-REX-6KLP1G01 / IVGM6048 | Full EMS | Same registers as the 5K, 6 kW rating |
| T-REX-10K-P3G01 / IVGM10048 | Full EMS | Three phase |
| T-REX-25KHP3G01 | Full EMS | |
| T-REX-50KHP3G01 | Full EMS | |
| IVGM-8KLP1G1 / 15KLP3G1 / 20KLP3G1 | Full EMS (**provisional**) | Power scales and rule encoding confirmed from customer register dumps; selling not yet proven. Read [IVGM support gaps](https://github.com/partach/ha_felicity/blob/main/docs/IVGM_SUPPORT_GAPS.md) before enabling the EMS. |
| IVAM6048P1G1 | **Monitoring only** | Its protocol has no control registers; the EMS stays off and nothing is ever written. |

## Quick Links

- [Energy and Power Management](Energy-and-Power-management) — Overview of all management features
- [Energy Management Basics](Energy-Management-Basics) — How the scheduling algorithm works
- [Power Management Basics](Power-Management-Basics) — Overcurrent protection and load shedding
- [TREX10 Energy Management](TREX10-Energy-Management) — TREX-5/6/10K register details
- [TREX10 Power Management](TREX10-Power-Management) — TREX-5/6/10K power register details
- [TREX25/50 Energy Management](TREX25-50-Energy-Management) — TREX-25/50K (and IVGM) register details
- [TREX25/50 Power Management](TREX25-50-Power-Management) — TREX-25/50K (and IVGM) power register details
- [Diagnostics and Troubleshooting](Diagnostics-and-Troubleshooting) — Register dump, slow links, what the card's warnings mean
- [Waveshare RS485 TO POE ETH (B)](Waveshare-RS485_TO_POE_ETH_%28B%29) — Setting up the Waveshare RS485-to-Ethernet gateway

## Installation

1. **Via HACS** (recommended): Search for "ha_felicity" or use the HACS repository link.
2. **Manual**: HACS → Custom repositories → Repo: `partach/ha_felicity`, Type: Integration.

After installation, reboot HA and add the integration via Devices & Services.

HACS installs **released versions** (tags). A fix merged on GitHub reaches you only once a new release is published.

## Configuration

All EMS settings are available as configuration entities on the device page. Entity assignments (Nordpool, Solar Forecast, EV charger, flexible loads, consumption meter) are configured via the integration options flow (gear icon).

See the [README](https://github.com/partach/ha_felicity/blob/main/README.md) for full installation and configuration details.

See [FLEXIBLE_LOADS.md](https://github.com/partach/ha_felicity/blob/main/FLEXIBLE_LOADS.md) for the flexible load control setup guide.

## Support

- [Discussions](https://github.com/partach/ha_felicity/discussions)
- [Issues](https://github.com/partach/ha_felicity/issues) — please attach a **Download diagnostics** file (see [Diagnostics and Troubleshooting](Diagnostics-and-Troubleshooting))
- [Changelog](https://github.com/partach/ha_felicity/blob/main/CHANGELOG.md)
