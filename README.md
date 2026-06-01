# HA-Felicity (Modbus)
[![Home Assistant](https://img.shields.io/badge/Home_Assistant-00A1DF?style=flat-square&logo=home-assistant&logoColor=white)](https://www.home-assistant.io)
[![HACS](https://img.shields.io/badge/HACS-Default-41BDF5?style=flat-square)](https://hacs.xyz)
[![HACS Action](https://img.shields.io/github/actions/workflow/status/partach/ha_felicity/validate-hacs.yml?label=HACS%20Action&style=flat-square)](https://github.com/partach/ha_felicity/actions)
[![License](https://img.shields.io/github/license/partach/ha_felicity?color=ffca28&style=flat-square)](https://github.com/partach/ha_felicity/blob/main/LICENSE)
[![HACS validated](https://img.shields.io/badge/HACS-validated-41BDF5?style=flat-square)](https://github.com/hacs/integration)

Felicity inverter home assistant integration for easy setup and use of the device (via [Modbus](https://www.se.com/us/en/faqs/FA168406/)).
Additionally includes a full Energy Management System to Buy and Sell electricity on the best moments and guards maximum power use to save guard against overcurrent.
With the right settings the software makes sure you pay the best energy prices or use no grid if not needed.

For this integration to work you need to have a wired modbus connection to your inverter either [via this USB dongle](https://www.amazon.nl/Industrial-Converter-Lightningproof-Resettable-Protection/dp/B0B87YJLJQ?source=ps-sl-shoppingads-lpcontext&ref_=fplfs&psc=1&smid=A2FQD9ZIAONBLW) or via something [like this](https://www.kiwi-electronics.com/nl/rs485-to-rj45-ethernet-tcp-ip-to-serial-rail-mount-support-20109?country=NL&utm_term=20109&gad_source=1&gad_campaignid=19763718639&gbraid=0AAAAADuMvucKntnrNZrVkZAHDgps81zYC&gclid=Cj0KCQiAx8PKBhD1ARIsAKsmGbeFZaWC_S38eFyu1NtZ0SP4zyLWwMWG70BRz6Ur1nmBymMCxvSR1_kaAmR9EALw_wcB).
Currently supports IVGM / TREX types: 
- The T-REX 5 and 10K series with H or L (High / Low Voltage batteries) with 1 or 3 Phases (P1 or P3).<br>
Letters are in the type indication like  T-REX-**10**K**H**P**3**G01
- The T-REX 25K and 50K range is now released with v0.8.0 (has complete new register setup).
- Others with exactly similar register configurations as above types, just be aware you chose the right configuration matching your type.



<p align="center">
<img src="https://github.com/partach/ha_felicity/blob/main/pictures/T-REX-10KLP3G01.png" width="200" style="vertical-align: middle; margin: 0 10px;"/>
<img src="https://github.com/partach/ha_felicity/blob/main/pictures/HA-felicity-card.gif" width="400" style="vertical-align: middle; margin: 0 10px;"/>
<img src="https://github.com/partach/ha_felicity/blob/main/pictures/HA-felicity-ems-card.png" width="290" style="vertical-align: middle; margin: 0 10px;"/>
<br><em>Felicity inverter and lovelace cards included</em>
</p>
<p align="center">
  <img src="https://github.com/partach/ha_felicity/blob/main/pictures/HA-felicity%20hub.png" width="600"/>
  <br><em>Home assistant hub view</em>
</p>

## Features
- No need for any yaml configuration!
- Full Energy Management System (EMS) with automatic charge/discharge scheduling based on electricity prices
- Two-day optimization — uses tomorrow's prices when available for smarter scheduling
- Solar-first approach — dynamic reserve target charges only what's needed for overnight
- Flexible load control — schedule up to 3 loads (EV charger, boiler, pool pump) during cheap/solar hours
- EV Boost override — one-press +1h button to force EV charging when you need to leave soon
- Dynamic power management — automatic overcurrent protection with per-phase monitoring
- Negative price strategies — profit from negative electricity prices with configurable charge/discharge behavior
- Serial and TCP Modbus support
- USB/Serial port selection via dropdown
- Customizable communication settings
- Customizable registers (basic, basic plus, full). No need to clutter your entities with unwanted registers
- Combined registers into meaningful data (no raw unusable values)
- Multiple hubs supported, ability to add multiple inverters
- Configurable refresh speeds for modbus
- Optimized modbus loading
- Automations possible, read and write on modbus!
- EMS dashboard card with interactive price chart, SOC trajectory, and live slider preview

## Installation
Options:
1. Install via HACS
   * This integration including the card <a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=partach&repository=ha_felicity"><img src="https://my.home-assistant.io/badges/hacs_repository.svg" alt="Open in HACS" width="150" height="75"></a>
    * After HA reboot (Needed for new integrations): choose 'add integration' (in devices and services) and choose `ha_felicity` in the list.
2. Install manually:
   * The integration: In UI go to `HACS`--> `custom repositories` --> `Repo`: partach/ha_felicity, `Type`: Integration
   * After HA reboot (Needed for new integrations): choose 'add integration' (in devices and services) and choose `ha_felicity` in the list.
     
Let the install config of the integration guide you as it asks you for the needed data.

## Serial and TCP (Wired and Wireless)
It supports modbus USB dongle and TCP [Modbus](https://www.se.com/us/en/faqs/FA168406/) connections.
The 3 possible ways are explained in the picture below. At the moment the last part always requires a RS485 connection to the inverter.
<p align="center">
  <img src="https://github.com/partach/ha_felicity/blob/main/pictures/HA-felicity-connect.png" width="600"/>
  <img src="https://github.com/partach/ha_felicity/blob/main/pictures/modbus_location_trex10k.png" width="400"/>
  <br><em>Ways to connect the inverter and TREX10k modbus location</em>
</p>
NOTE: when using the USR-D164 wifi module you need to put Pack Interval to 100 (20 causes packet loss)

## Installation options
The T-REX 5 and 10K series with HP or HL (High / Low Voltage batteries) with 1 or 3 Phases (P1 or P3) can be selected with selecting
**T-REX-10K-P3G01** (All use same register setup).
The T-REX 25K and 50K range can be select by choosing option **T-REX-50KHP3G01**.
Below are the install configuration options. When installing, make sure your select **2400 baud** as communication speed for the **T-REX-5/10** series.
The **T-REX-25/50** series supports 9600 baud according documentation. If it doesnt work at first good to check if a different baud rate helps.
You can immediately select your monetary (Nordpool) integration but this can also be done later. (Later you can even override Nordpool with for example TIBBER).
This can be done via configuration when the intallation is succesfull (device found). Configuration is set in the hub/device view via the gear icon.
<p align="center">
  <img src="https://github.com/partach/ha_felicity/blob/main/pictures/HA-felicity%20config5.png" width="300"/>
  <br>
  <em>Select Serial or TCP and select model + update refresh interval</em>
</p>
<p align="center">
  <img src="https://github.com/partach/ha_felicity/blob/main/pictures/HA-felicity%20config2.png" width="300"/>
  <br>
  <em>Serial Example, choose the settings you need (2400 baud is sadly default for the TRex 5-10k series)</em>
</p>

## Configuration
After successfull install the integration can be configured at any time with a few settings. See picture on top for location of the gear icon
- Update interval (the frequency of refresh of data). For the T-REX 5-10k models keep it on 10 sec minimum due to small baud rate.
- Setting up Nordpool (For energy prices). Use the HACS version, NOT the default version. (HACS version has 15 min slot information). You need to setup Nordpool for your energy supplier, see the web for examples.
- Solar Forecast for today and tomorrow. Install an integration that predicts solar power. It should support a Today and Tomorrow sensor showing total expected amount (you need to configure the solar forecast right).
- Monetary override. If you use Nordpool (recommended) leave this empty! Nordpool is supported by default but also other monetary integrations as Tibber. The format is that it needs a sensor with attributes about min, max, avg price
If you want use Tibber enter in the override fied: `sensor.tibber_electricity_price` where electricity_price is the sensor with attributes (avg, min, max) and 'tibber' how you named the integration.
The Felicity integration looks for a variaty of avg_price like fields as attributes and if it finds in the the override sensor, uses that as needed price information. If no information is found, 
the price information remains unavailable. 

## Controls
Via the device you can directly control many settings run-time. Be carefull with some of these setting as the affect the behavior of the device.
If you don't know what a register does, don't touch it :)
The integration is to be used at own risk.

<p align="center">
  <img src="https://github.com/partach/ha_felicity/blob/main/pictures/HA-felicity%20config3.png" width="600"/>
  <br>
  <em>Runtime Settings</em>
</p>

## Dynamic Energy Management (EMS)

The integration includes a full Energy Management System that optimizes battery charge and discharge based on electricity prices, solar forecasts, and consumption patterns. It automatically selects the cheapest hours to charge and the most expensive hours to sell, with two-day look-ahead when tomorrow's prices are available.

**Important notes:**
 * The integration uses internal **Econ Rule 1** for this.
 * The Operating mode **must be set (by user) to Economic mode**. The EMS will not engage in any other mode (like General).
 * Settings affected differ per model selected during installation (5/10K series or 25/50K series).

During setup or via configuration (gear symbol in hub/device overview) you add a Monetary integration.
Nordpool (HACS version, not the default) and Tibber are tested to work. See their respective documentation for setup.

### Core Settings

All EMS settings are available as configuration entities on the device. You can find them in the device page under "Configuration".

| Setting | Range | Default | Description |
|---|---|---|---|
| **Grid Mode** | off / from_grid / to_grid / both | off | Main EMS switch. Controls whether the integration buys from grid, sells to grid, both, or is disabled. |
| **Price Mode** | manual / auto | manual | In `manual` mode, the price threshold is set by the Price Threshold Level slider. In `auto` mode, the threshold is calculated automatically based on price distribution. |
| **Price Threshold Level** | 1 - 10 | 5 | Sets the price level at which the EMS decides to charge or discharge. Level 5 corresponds to the average price. Lower values = only charge at very cheap prices. |
| **Power Level** | 1 - max kW (model dependent) | 5 | The charge/discharge power limit in kW. The EMS will not exceed this power when charging or discharging the battery. |
| **Battery Charge Max Level** | 30 - 100 % | 100 | Maximum SOC the EMS will charge to. Useful for battery longevity (e.g., set to 80%). |
| **Battery Discharge Min Level** | 10 - 70 % | 20 | Minimum SOC floor. The EMS will not discharge below this level. |

**Grid Mode explained:**
 * **from_grid** — Charges the battery from the grid when the price is below the threshold.
 * **to_grid** — Discharges the battery to the grid when the price is above the threshold.
 * **both** — Does both: charges at cheap hours, sells at expensive hours. Includes a profitability filter to ensure every sold kWh covers the round-trip losses.
 * **off** — EMS is disabled. The inverter operates normally without grid scheduling.

### Battery & Efficiency Settings

| Setting | Range | Default | Description |
|---|---|---|---|
| **Battery Capacity** | 1 - 200 kWh | 10 | Your usable battery capacity. The integration automatically adjusts this for battery health (SOH) over time. |
| **Battery Efficiency Factor** | 0.70 - 1.00 | 0.90 | Single-direction efficiency. Round-trip efficiency is this value squared (e.g., 0.90 = 81% round-trip). Used to calculate whether selling energy is profitable after losses. |
| **Daily Consumption Estimate** | 0 - 120 kWh | 10 | Fallback daily consumption estimate. If the integration has 7+ days of history, it uses the actual rolling average instead. |

### Reserve Target

| Setting | Range | Default | Description |
|---|---|---|---|
| **Reserve Target** | 0 - 100 % | 0 | Controls how much battery reserve to keep for overnight. **0 = dynamic** (recommended): the EMS automatically calculates how much energy is needed to survive the night based on your consumption pattern and sunset/sunrise times. Any value > 0 sets a fixed minimum SOC floor. |

The dynamic reserve target is the key to the EMS's solar-first approach: it charges only what is needed to get through the night, leaving room for solar to fill the battery during the day.

### Advanced Optimization Settings

| Setting | Range | Default | Description |
|---|---|---|---|
| **Optimization Priority** | cost / longevity / self_consumption | cost | **cost**: minimize grid spend. **longevity**: adds a minimum cycle cost (0.05 €/kWh) to protect battery life. **self_consumption**: increases the overnight reserve by 25% to keep more solar energy for self-use. |
| **Arbitrage Price Delta** | 0 - 0.50 €/kWh | 0 | When the price spread (max - min) exceeds this value, the EMS charges to full capacity instead of just the reserve target. Set to 0 to disable. Useful in markets with large price swings. |
| **Battery Cycle Cost** | 0 - 0.50 €/kWh | 0 | Estimated cost per kWh of battery wear. Added to the minimum sell price in the profitability filter. If you know your battery cost per cycle, enter it here to prevent unprofitable trading. |

### Negative Price Settings

These settings control behavior during negative electricity prices (when you get paid to consume energy).

| Setting | Options | Default | Description |
|---|---|---|---|
| **Block Export on Negative Price** | on / off | on | When `on`, prevents the EMS from scheduling battery discharge (sell) during negative price hours. Selling at negative prices means you pay the grid to take your energy. |
| **Charge to Full on Negative Price** | off / on | off | When `on`, schedules charging at every negative-price slot regardless of the reserve target. You get paid to consume grid energy, so filling the battery is profitable. Trade-off: may cause PV curtailment if the battery is full when solar peaks. |
| **Discharge to Make Room for Negative Price** | off / on | off | When `on`, pre-emptively discharges the battery at the most expensive positive-price slots before a negative-price window. This creates headroom so PV + grid charging during the negative-price window can fill the battery. |

### Inverter Rule 1 Settings

The EMS controls the inverter via Economic Rule 1 registers. By default the integration does not modify the Rule 1 time window or weekday settings on the inverter — you manage those yourself. If you want the integration to handle them automatically:

| Setting | Options | Default | Description |
|---|---|---|---|
| **Rule 1 Time Window** | manual / auto | manual | `auto` sets the Rule 1 start/stop time to 00:00–23:59 (full day). If left on `manual`, make sure your inverter's Rule 1 time window covers the hours the EMS needs to operate, or actions will be silently ignored. |
| **Rule 1 Weekday** | manual / auto | manual | `auto` enables Rule 1 for all 7 days. If left on `manual`, make sure all needed weekdays are enabled on the inverter. |

If the EMS detects that scheduled actions fall outside the inverter's Rule 1 window, a warning banner appears on the EMS card.

### Voltage Settings

| Setting | Range | Default | Description |
|---|---|---|---|
| **Voltage Level** | 48 - 60 V (LV) / 300 - 448 V (HV) | 58 | Charge voltage setpoint written to the inverter during charging. Automatically adjusts range based on detected battery system. |
| **Discharge Min Voltage** | 48 - 55 V (LV) / 300 - 448 V (HV) | 50 | Discharge voltage floor written to the inverter during discharging. |

## Dynamic Power Management

The integration monitors grid current per phase and automatically adjusts inverter power to prevent overcurrent.

| Setting | Range | Default | Description |
|---|---|---|---|
| **Safe Power Management** | auto / on / off | auto | `auto`: activates when grid_mode is not `off`. `on`: always active. `off`: disabled. |
| **Max Amperage Per Phase** | 10 - 63 A | 16 | Your home's maximum amperage per phase. Set this to match your main breaker rating. |

How it works:
- **> 95% of max amperage**: Emergency — reduces power by 2 kW immediately.
- **> 80% of max amperage**: Caution — reduces power by 1 kW.
- **< 70% of max amperage**: Recovery — gradually restores power back to your Power Level setting.
- When flexible loads are configured, the priority chain is: EV charger current step-down first, then binary load shedding, then battery power reduction as last resort.

## Flexible Load Control

The integration can manage up to 3 controllable loads (EV charger, boiler, pool pump, etc.). Loads are automatically scheduled during cheap, negative-price, and PV-surplus hours — the same slots the EMS identifies as optimal for the battery.

### Configuring Loads

Each load has the following settings available as configuration entities on the device:

| Setting | Load 1 (EV) | Load 2-3 | Description |
|---|---|---|---|
| **Enabled** (select: off/on) | yes | yes | Enable or disable the load slot. |
| **Name** (text) | yes | yes | A friendly name for the load (e.g., "Boiler", "Pool Pump"). |
| **Switch Entity** (text) | yes | yes | The Home Assistant entity ID of the switch that controls this load (e.g., `switch.ev_charger`). |
| **Power** (number: 0.5-25 kW) | yes (default 3.7) | yes (default 2.0) | Rated power of the load. Used for safe power calculations. |
| **Shed Priority** (number: 1-3) | yes (default 1) | yes (default 2/3) | Priority for load shedding during overcurrent. Higher number = shed first. |

**Load 1** has additional EV charger settings:

| Setting | Range | Default | Description |
|---|---|---|---|
| **Current Entity** (text) | — | — | Entity ID for setting the EV charger current (e.g., `number.ev_charger_current`). |
| **Current Steps** (text) | — | — | Comma-separated list of available current steps in Amps (e.g., `6,10,13,16,20,25`). |
| **Phases** (number) | 1 - 3 | 1 | Number of phases the EV charger uses. |
| **Voltage** (number) | 110 - 400 V | 230 | Voltage of the EV charger connection. |
| **Default Current** (number) | 6 - 32 A | 16 | Default charging current when the load is activated. |

### How Load Scheduling Works

Loads are scheduled as an overlay on the battery schedule — they activate during:
- **Cheap price slots** (price below threshold)
- **Negative price slots** (you get paid to consume)
- **PV surplus slots** (hourly solar > hourly consumption)
- **Battery charge slots** (already identified as cheap)

The loads don't affect the battery schedule itself. They are additive consumption.

In the EMS card, scheduled loads appear as a cyan strip at the bottom of the price bars. The stats row shows how many loads are active.

### EV Boost Override

For situations where you need to charge your car urgently (e.g., you need to leave soon), the integration provides an **EV Boost** button.

- **EV Boost +1h** — Each press adds 1 hour to the boost timer. Presses stack, so pressing 3 times gives you a 3-hour boost.
- **EV Boost Cancel** — Immediately cancels any active boost.

During a boost, the EV charger is forced on at maximum current regardless of the EMS schedule. The Safe Power Management system can still step down the current if grid amperage is too high, but it will not fully shut off the EV charger.

The EMS card shows a cyan banner with a countdown when a boost is active.

## Using the cards
After installation of the integration you need to first reboot HA.
The cards will be automatically installed and registered by the integration on start up.
To use the card in your dashboard, go to you dashboard, edit, choose `Add card`.
They can be found at the bottom of the list.
If they are not visible you can choose `Manual` as card type.
Add first line: `type: custom:felicity-inverter-card` for the inverter card and `type: custom:felicity-ems-card` for the EMS card.
Then choose the `visual editor` to continue.
From the `Device` dropdown chose your felicity inverter integration installed.
<p align="center">
  <img src="https://github.com/partach/ha_felicity/blob/main/pictures/HA-felicity-card-expl.png" width="600"/>
  <img src="https://github.com/partach/ha_felicity/blob/main/pictures/ems_ui_explanation.png" width="600"/>
  <br><em>Card usage explained</em>
</p>

## Advanced settings

If you want to override items in the card you can add the below items in your code.
DISCLAIMER: It only overrides the visual values, not the actual values in the integration.
So use with some caution as it can paint a different picture then you maybe intend.

```
type: custom:felicity-inverter-card
device_id: <some big hex nr translation of your device which was set in visual mode> 
overrides:
  loadpower_lineside: sensor.<your house usage in watts sensor>
  total_ac_output_active_power: sensor.<your backup usage in watts sensor>
  total_pv_power: sensor.<your total pv in watts sensor>
  total_ac_input_power: sensor.<your house total grid sensor>
  battery_power: sensor.<your house total battery sensor>
  total_generator_active_power: sensor.<your generator sensor>
  battery_voltage: sensor.<your battery voltage sensor>
  battery_current: sensor.<your battery current sensor>
  battery_capacity: sensor.<your battery capacity sensor>
  battery_discharge_depth_on_grid_bms: sensor.<your battery min dept charge setting>
  current_price: sensor.<>
  today_min_price: sensor.<>
  today_avg_price: sensor.<>
  today_max_price: sensor.<>
  price_threshold_level: sensor.<>
  power_level: sensor.<>
  safe_max_power: sensor.<>
```

The override declarations can for example be used if there is more power generated or used then felicity is aware off.

## Discussion 
See [here](https://github.com/partach/ha_felicity/discussions)

## Changelog
See [CHANGELOG.md](https://github.com/partach/ha_felicity/blob/main/CHANGELOG.md)

## Issues
Report at GitHub [Issues](https://github.com/partach/ha_felicity/issues)

## Support development
If you like it and find it usefull, or want to support this and future developments, it would be greatly appreciated :)

[![Donate](https://img.shields.io/badge/Donate-PayPal-green.svg?style=flat-square)](https://paypal.me/therealbean)

