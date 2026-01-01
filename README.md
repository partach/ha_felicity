# HA-Felicity (Modbus)
[![Home Assistant](https://img.shields.io/badge/Home_Assistant-00A1DF?style=flat-square&logo=home-assistant&logoColor=white)](https://www.home-assistant.io)
[![HACS](https://img.shields.io/badge/HACS-Default-41BDF5?style=flat-square)](https://hacs.xyz)
[![HACS Action](https://img.shields.io/github/actions/workflow/status/partach/ha_felicity/validate-hacs.yml?label=HACS%20Action&style=flat-square)](https://github.com/partach/ha_felicity/actions)
[![Installs](https://img.shields.io/github/downloads/partach/ha_felicity/total?color=28A745&label=Installs&style=flat-square)](https://github.com/partach/ha_felicity/releases)
[![License](https://img.shields.io/github/license/partach/ha_felicity?color=ffca28&style=flat-square)](https://github.com/partach/ha_felicity/blob/main/LICENSE)
[![HACS validated](https://img.shields.io/badge/HACS-validated-41BDF5?style=flat-square)](https://github.com/hacs/integration)

Felicity inverter home assistant integration for easy setup and use of the device (via Modbus).

For this integration to work you need to have a wired modbus connection to your inverter either [via this USB dongle](https://www.amazon.nl/Industrial-Converter-Lightningproof-Resettable-Protection/dp/B0B87YJLJQ?source=ps-sl-shoppingads-lpcontext&ref_=fplfs&psc=1&smid=A2FQD9ZIAONBLW) or via something [like this](https://www.kiwi-electronics.com/nl/rs485-to-rj45-ethernet-tcp-ip-to-serial-rail-mount-support-20109?country=NL&utm_term=20109&gad_source=1&gad_campaignid=19763718639&gbraid=0AAAAADuMvucKntnrNZrVkZAHDgps81zYC&gclid=Cj0KCQiAx8PKBhD1ARIsAKsmGbeFZaWC_S38eFyu1NtZ0SP4zyLWwMWG70BRz6Ur1nmBymMCxvSR1_kaAmR9EALw_wcB).
Currently supports one type: T-REX-10KLP3G01 (and others with similar register setup)


<p align="center">
<img src="https://github.com/partach/ha_felicity/blob/main/T-REX-10KLP3G01.png" width="200" style="vertical-align: middle; margin: 0 10px;"/>
<img src="https://github.com/partach/ha_felicity/blob/main/HA-felicity%20card.png" width="400" style="vertical-align: middle; margin: 0 10px;"/>
<br><em>Inverter that is supported and card included</em>
</p>
<p align="center">
  <img src="https://github.com/partach/ha_felicity/blob/main/HA-felicity%20hub.png" width="600"/>
  <br><em>Home assistant hub view</em>
</p>

## Features
- No need for any yaml configuration!
- Includes support for dynamic load/offload to grid!!
- Serial and TCP Modbus support
- USB/Serial port selection via dropdown
- Customizable communication settings
- Customizable registers (basic, basic plus, full). No need to clutter your entities with unwanted registers
- Hassle free use of the device
- Combined registers into meaningfull data (no raw unusable values)
- Multiple hubs supported, ability to add multiple inverters.
- configurable refresh speeds for modbus
- Optimized modbus loading
- Automations possible, read and write on modbus!
- Very easy and straight forward!

## Installation
Options:
1. Install via HACS (is coming in the near future)
2. Install manually:
   * The integration: In UI go to `HACS`--> `custom repositories` --> `Repo`: partach/ha_felicity, `Type`: Integration
   * After HA reboot (Needed for new integrations): choose 'add integration' (in devices and services) and choose `ha_felicity` in the list.
     
Let the install config of the integration guide you as it asks you for the needed data.

## Serial and TCP
It supports modbus USB dongle and TCP Modbus connection

## Configuration options
<p align="center">
  <img src="https://github.com/partach/ha_felicity/blob/main/HA-felicity%20config1.png" width="300"/>
  <br>
  <em>Select Serial or TCP and select model + update refresh interval</em>
</p>
<p align="center">
  <img src="https://github.com/partach/ha_felicity/blob/main/HA-felicity%20config2.png" width="300"/>
  <br>
  <em>Serial Example, choose the settings you need (2400 baud is sadly default for the TRex)</em>
</p>

## Controls
Via the device you can directly control many settings run-time. Be carefull with some of these setting as the affect the behavior of the device.
If you don't know what a register does, don't touch it :)
The integration is to be used on own risk.

<p align="center">
  <img src="https://github.com/partach/ha_felicity/blob/main/HA-felicity%20config3.png" width="600"/>
  <br>
  <em>Runtime Settings</em>
</p>

## Setting Dynamic Energy managment
Note1: 
 * the integration uses internal **Econ Rule 1** for this. 
 * Rule 1 will be activated and controlled by the integration. Make sure the settings in there are inline with the intended use!
   **Weekdays, Time Start and Time Stop will not be set by the integration**. The user has to set those to a default usefull for them.
 * The integration wil set: The date on Today (if not idle), Voltage depending on charge (58) and discharge (50) but can be overwritten, SOC on configured setting (max battery / min battery, see below)

Note2: The Operating mode **must be set (by user) to Economic mode**. The Energy management feature will not engage in any other mode (Like General).

During setup or with config setting (gear symbol in hub/device overview) you can add a 'Monetary' Home Assistant Device.
Examples are the Nordpool integration or Tibber. Look at the Nordpool integration details on how to set that up (not covered here).
During first setup or during run-time configuration (device gear symbol) it will display a list of installed Monetary integrations to chose from.
Currently Nordpool and Tibber (via Norpool override field in config) are tested to work.

<p align="center">
  <img src="https://github.com/partach/ha_felicity/blob/main/HA-felicity%20config4.png" width="300"/>
  <br>
  <em>Dynamic Energy Management Settings</em>
</p>
The operation is pretty straightforward. (Maybe further version will support more algorithms)
Use `Price Threshold Level (1-10)` To set the desired price point level. (It can take about 10 sec. for the integration to calculate that into a **Base-Threshold-Price**)
Based on settings the unit will either engage when The Actual Current Price is above Base-Threshold-Price or below.

Example: Max price = 0.30 Euro, Min Price = 0.20 Euro and Avergage Price = 0.25 Euro (collected via Nordpool or Tibber)
When setting the `Price Threshold Level to 5` the Base-Threshold-Price will be 0.25.

**The Grid Mode setting**:
 * If `Grid Mode` <em>(From-grid, To-Grid, Off)</em> is set to From-grid it will allow use of grid power when actual price is <=0.25 Euro
 * If `Grid Mode` <em>(From-grid, To-Grid, Off)</em> is set to To-grid it will allow Battery power to go to grid power when actual price is >=0.25 Euro
**Additional variables** are `Battery Charge Max Level` and `Battery Charge Min Level`.
 * In `From Grid mode` it will stop when `Actual Battery Capacity` reaches `Battery Charge Max Level`
 * In `To Grid mode` it will stop when `Actual Battery Capacity` reaches `Battery Charge Min Level`

IMPORTANT: The integration is depedent on the Monetary Integration to contiously supply the data.

## Power management
The integration also supports power management. After instalation, via controls, you can set the maximum amperage of your home electricity setup.
For example if you have a maximum of 16A per group, set the value to 16A. The integration will then make sure the battery loading will be dialed back if the amperage becomes to high.
(by decreasing the user requested power level, controlled via rule 1 via the integration).
It will keep monitorning this and will increase the the battery loading to requested power levels if the amperage becomes lower.

## Installing the card
After installation of the integration you need to first reboot HA.
The card will be automatically registered by the integration on start up.
To use the card in your dashboard, go to you dashboard, edit, choose `Add card`.
Choose `Manual`
Add first line: `type: custom:felicity-inverter-card`
Then choose the `visual editor` to continue.
From the `Device` dropdown chose your felicity inverter install.

Advanced settings.
If you want to override items in the card you can add the following yaml code:
```
type: custom:felicity-inverter-card
device_id: <some big hex nr translation of your device which was set in visual mode> 
overrides:
  total_ac_active_power: sensor.<your house usage in watts sensor>
  pv_input_power: sensor.<your total pv in watts sensor>
  ac_input_power: sensor.<your house total grid sensor>
```
`the override declarations only have to be used if there is more electricity generated or used then felicity is aware off`

## Discussion 
See [here](https://github.com/partach/ha_felicity/discussions)

## Changelog
See [CHANGELOG.md](https://github.com/partach/ha_felicity/blob/main/CHANGELOG.md)

## Issues
Report at GitHub [Issues](https://github.com/partach/ha_felicity/issues)

## Support development
If you like it and find it usefull, or want to support this and future developments, it would be greatly appreciated :)

[![Donate](https://img.shields.io/badge/Donate-PayPal-green.svg?style=flat-square)](https://paypal.me/therealbean)

