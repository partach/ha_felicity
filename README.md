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
  <img src="https://github.com/partach/ha_sdm630/blob/main/HA-SDM630%20hub.png" width="600"/>
  <br>
  <em>Integration after installation</em>
</p>

## Features
- No need for any yaml configuration!
- Serial and TCP Modbus support
- USB/Serial port selection via dropdown
- Customizable communication settings
- Customizable registers (basic, basic plus, full). No need to clutter your entities with unwanted registers
- Hassle free use of the device
- Combined registers into meaningfull data
- Multiple hubs supported, ability to add multiple inverters.
- Very easy and straight forward!

## Installation
Options:
1. Install via HACS (is coming in the near future)
2. Install manually:
   * The integration: In UI go to `HACS`--> `custom repositories` --> `Repo`: partach/ha_felicity, `Type`: Integration
   * After HA reboot (Needed for new integrations): choose 'add integration' (in devices and services) and choose `ha_felicity` in the list.
     
Let the install config of the integration guide you as it asks you for the needed data.

## Configuration options
<p align="center">
  <img src="https://github.com/partach/ha_sdm630/blob/main/HA-SDM630%20config1.png" width="300"/>
  <br>
  <em>Select Serial or TCP</em>
</p>
<p align="center">
  <img src="https://github.com/partach/ha_sdm630/blob/main/HA-SDM630%20config2.png" width="300"/>
  <br>
  <em>Serial Example, choose the settings you need</em>
</p>

## Discussion 
See [here](https://github.com/partach/ha_felicity/discussions)

## Changelog
See [CHANGELOG.md](https://github.com/partach/ha_felicity/blob/main/CHANGELOG.md)

## Issues
Report at GitHub [Issues](https://github.com/partach/ha_felicity/issues)

## Support development
If you like it and find it usefull or want to support this and future developments it would be greatly appreciated :)

[![Donate](https://img.shields.io/badge/Donate-PayPal-green.svg?style=flat-square)](https://paypal.me/therealbean)

