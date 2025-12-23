# HA-Felicity (Modbus)
[![Home Assistant](https://img.shields.io/badge/Home_Assistant-00A1DF?style=flat-square&logo=home-assistant&logoColor=white)](https://www.home-assistant.io)
[![HACS](https://img.shields.io/badge/HACS-Default-41BDF5?style=flat-square)](https://hacs.xyz)
[![HACS Action](https://img.shields.io/github/actions/workflow/status/partach/ha_felicity/validate-hacs.yml?label=HACS%20Action&style=flat-square)](https://github.com/partach/ha_felicity/actions)
[![Installs](https://img.shields.io/github/downloads/partach/ha_felicity/total?color=28A745&label=Installs&style=flat-square)](https://github.com/partach/ha_felicity/releases)
[![License](https://img.shields.io/github/license/partach/ha_felicity?color=ffca28&style=flat-square)](https://github.com/partach/ha_felicity/blob/main/LICENSE)
[![HACS validated](https://img.shields.io/badge/HACS-validated-41BDF5?style=flat-square)](https://github.com/hacs/integration)

Felicity inverter home assistant integration for easy setup and use of the device (via Modbus)

Currently supports one type: T-REX-10KLP3G01


<p align="center">
  <img src="https://github.com/partach/ha_felicity/blob/main/T-REX-10KLP3G01.png" width="200"/>
  <br>
  <em>Inverter that is supported</em>
</p>
<p align="center">
  <img src="https://github.com/partach/ha_felicity/blob/main/HA-felicity%20hub.png" width="600"/>
  <br>
  <em>Home assistant hub view</em>
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
It supports modbus USB dongle and TCP connection

## Configuration options
<p align="center">
  <img src="https://github.com/partach/ha_felicity/blob/main/HA-felicity%20config1.png" width="300"/>
  <br>
  <em>Select Serial or TCP and select model + update refresh interval</em>
</p>
<p align="center">
  <img src="https://github.com/partach/ha_felicity/blob/main/HA-felicity%20config2.png" width="300"/>
  <br>
  <em>Serial Example, choose the settings you need</em>
</p>

## Controls
Via the device you can directly control many settings run-time. Be carefull with some of these setting as the affect the behavior of the device.
<p align="center">
  <img src="https://github.com/partach/ha_felicity/blob/main/HA-felicity%20config3.png" width="600"/>
  <br>
  <em>Runtime Settings</em>
</p>

## Setting Dynamic Energy managment
Note: the integration uses internal **schema 1** for this. So this schema will be overwritten and controlled by the integration.
During setup or with config setting (gear symbol in hub/device overview) you can add a 'Monetary' Home Assistant Device.
Examples are Nordpool and Tibber. Look at these integration the details how to setup.
During config it will display a list of installed Monetary integrations to chose from.
Currently Nordpool and Tibber are tested to work.

<p align="center">
  <img src="https://github.com/partach/ha_felicity/blob/main/HA-felicity%20config4.png" width="300"/>
  <br>
  <em>Dynamic Energy Management Settings</em>
</p>
The operation is pretty straightforward. (Maybe further version will support more algorithms)
Use `Price Threshold Level (1-10)` To set the desired price point level. (It can take about 10 sec. for the integration to calculate that into a **Base-Threshold-Price**)
Based on settings the unit will either engage when The Actual Current Price is above Base-Threshold-Price or below.

Example: Max price = 0.30 Euro, Min Price = 0.20 Euro and Avergage Price = 0.25 Euro
When setting the `Price Threshold Level to 5` the Base-Threshold-Price will be 0.25.

The Grid Mode setting:
 * If `Grid Mode` <em>(From-grid, To-Grid, Off)</em> is set to From-grid it will allow use of grid power when actual price is <=0.25 Euro
 * If `Grid Mode` <em>(From-grid, To-Grid, Off)</em> is set to To-grid it will allow Battery power to go to grid power when actual price is >=0.25 Euro
Additional variables are `Battery Charge Max Level` and `Battery Charge Min Level`.
 * In `From Grid mode` it will stop when `Actual Battery Capacity` reaches `Battery Charge Max Level`
 * In `To Grid mode` it will stop when `Actual Battery Capacity` reaches `Battery Charge Min Level`

IMPORTANT: The integration is depedent on the Monetary Integration to contiously supply the data.

## Discussion 
See [here](https://github.com/partach/ha_felicity/discussions)

## Changelog
See [CHANGELOG.md](https://github.com/partach/ha_felicity/blob/main/CHANGELOG.md)

## Issues
Report at GitHub [Issues](https://github.com/partach/ha_felicity/issues)

## Support development
If you like it and find it usefull, or want to support this and future developments, it would be greatly appreciated :)

[![Donate](https://img.shields.io/badge/Donate-PayPal-green.svg?style=flat-square)](https://paypal.me/therealbean)

