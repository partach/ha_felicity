## Changelog

### [1.3.8] - Power-register scaling fixes, provisional IVGM support

⚠️ **T-REX-50 owners — read this.** Every telemetry power register was scaled
÷10 where the inverter actually uses ÷100, so all power readings were **10×
too high** (a 6.65 kWp PV string reporting ~50 kW). Confirmed against nameplate
capacity, the day-energy registers and an external sub-meter. Root cause is
Felicity's own protocol document: its "Multiple" column says -1 for every kW/kVA
row and the device uses -2.
**Consequence of the fix:** your recorded long-term statistics for these sensors
were logged at the old scale, so history before this update reads 10× high and
the graphs will show a step down at the upgrade. Energy (kWh) sensors were never
affected. If you want clean graphs, clear the statistics for the affected power
sensors in Developer Tools → Statistics.
The nine *setpoint* registers (export limit, peak shaving, rule power) are
deliberately left unchanged — nobody has measured them, and guessing a write
scale is how you ask an inverter for ten times the power you meant.

**T-REX-25 is untouched** and stays exactly as shipped. Its firmware was altered
and its scaling is proven in the field, so it legitimately differs from the 50.

**New: provisional support for the IVGM family** (IVGM-8KLP1G1, IVGM-20KLP3G1),
selectable at setup and labelled "(provisional)". Built entirely from the IVGM
protocol document — no address borrowed from a T-REX map. **Nothing is
hardware-validated yet**: see `docs/IVGM_SUPPORT_GAPS.md` before enabling
`grid_mode`, and note that selling (`to_grid` / `both`) is unproven because the
IVGM protocol does not define the register the discharge path needs.
A customer measurement showed IVGM power registers are 0.01 kW per count, not
the watts the document claims (raw 156 = 1560 W); both models now use that scale.

**Fixes**
- Economic-mode self-heal was silently disabled on the default register set for
  T-REX-25/50 — the exact models where an inverter dropping to General mode
  leaves the battery inert. The watchdog registers are now always polled, and
  the heal re-applies the full Rule 1 state, not just the operating mode.
- `working_mode` (4353) is the inverter's running-status report, not a settable
  mode. It was a writable select that always snapped back ("whatever I choose,
  it switches back to Line"); it is now a read-only sensor. The old
  `select.*_working_mode` entity shows as orphaned and can be deleted.
- The EMS no longer tops the battery off with grid energy that tomorrow's
  forecast already covers — buying at 0.30 €/kWh to reach 100% the day before
  47 kWh of sun.
- Equal-priced charge slots are now placed so they don't fill the battery right
  before a solar peak, which used to spill free PV.

**MILP**
- The engine now reports *why* it fell back to greedy (hover the engine chip in
  the card, or run `tools/check_milp.py`), instead of one warning that scrolled
  out of the log.
- Pinned `pulp<4.0`: PuLP 4.0 removes the bundled CBC solver, which would have
  broken MILP for every user the day it shipped.

**Internal**
- CI now runs the test suite (354 tests) and the EMS scenario simulator, not
  just the linter.

### [1.0.0] - EMS stable
- Finally we arrived to a point we call it the 1.0 release!
- EMS tested and stable
  
### [0.9.8] - EMS hardening and improvements
- EMS card improvements
- EMS looks now at cheapest slot for multiple days (if available)
- EMS history shown in EMS card (when did we charge or sell)
- EMS shows plans for tomorrow (if price data for tomorrow is available)
  
### [0.9.7] - EMS card and EMS improvements
- NEW! extra card showing EMS status and predications
- Improved EMS functionality and documentation. See EMS.md document on github

### [0.9.5] - EMS extension and other bits
- Extended EMS functionality. See EMS.md document on github
- Total EMS can now be switched off completely or run on automatic, including Safe Power management. See EMS.md
- Added possibility to add PV forecasting entity to help with automatic EMS
- Few small bugs like preventing to write economic power rule
- Register updates for TREX25k (PV on/off in rules set and Sell on/off in rules set)
- Basics now seem to work for TREX25k/50k
- Extra writable register on user request for TREX5k/10k
- Extended automatic handling of high-voltage / low-voltage systems

### [0.9.0] - Stable tested release
- Full rule setting via integration. Date and Time can now be set via integration for rules!
- NEED TO DELETE YOUR DEVICE AND RE-ADD IT to ensure clean up of all older sensors (in case some sensors report unavailable after a few minutes)
- Added few extra diagnostics
- Card updates:
  -  Added max current bar
  -  Added battery max / min integration setting visualisation
  -  Added configuration options to switch of parts of the card
  -  Status bar at the bottom
- register tweaks and small bug fixes
- OPEN: Firmware of TREX25k/50k is not mature. built-in automations do not fully work yet. Needs firmware update
  
### [0.8.9] - Inverter type handling, card updpate
- Card update for adding generator (TREX 25-50k)
- register tweaks
- small bug fixes

### [0.8.4] - Fix inverter selection bug
- Solving bug in inverter selection
  
### [0.8.3] - Support for multiple inverter types
- Issue with writing rule sliders fixed
- Issue with TREX50 write rule handled (not fixed, still missing requirements)

### [0.8.2] - Support for multiple inverter types
- TREX 5 series added
  
### [0.8.0] - Support for multiple inverter types
- TREX 50 series added

### [0.7.2] - Dynamic Power Management on dashboard
- Dynamic Power Management visible on card

### [0.7.0] - Dynamic Power Management
- Dynamic Power Management build in

### [0.6.7] - HV Battery support
- High Voltage Battery system support
   
### [0.6.5] - Documentation and card improvement.
- pymodbus version dependency bumped to 3.10
   
### [0.6.4] - Documentation and card improvement.
- Documentation (Readme) update
  
### [0.6.3] - Fix for no price entity selected
- Fix for Allowing None Price entity

### [0.6.2] - Card improvements
- small graphical card improvement to help understand price strategy

### [0.6.0] - Persistence fix
- Issue fixed to correctly maintain all settings
- Card resource registration fix
- Small register fix (typo)
- Added few registers (super set)
  
### [0.5.6] - First install fix
- Issue fixed for first time users
  
### [0.5.3] - Card included
- Added energy flow card, see Readme for install instruction
- Register improvements

### [0.5.2] - Minor fixes
- Several registry fixes (wrong in datasheet)
- Storage of settings

### [0.5.1] - Dynamic Energy Management update
- Norpool override to also use i.e. Tibber
- Extra Configuration options for Dynamic Energy Management
- minor fixes
  
### [0.5.0] - Dynamic Energy Management
- Includes active (dynamic) energy management
- Ability to connect to Nordpool which wil be use energy price information
- Energy data displayed within own entities in the integration
- Easy manipulation of settings run-time
- Huge update on device overview
  - Writable settings directly on the entities
  - Logic automatically translate to write toward needed registry content
  - Sections (Diagnostics, Sensor, Configuration)
    

### [0.4.0] - Official version
- Tested and verified working on Modbus serial
- Includes writable registers via HA service calls (automation possible)
