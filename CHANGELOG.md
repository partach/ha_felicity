## Changelog

### [Unreleased] - Register dump in diagnostics

- **MILP now runs on Home Assistant OS.** pulp's built-in CBC solver cannot
  start in Home Assistant OS (it is built for a different system library), and
  that switched MILP off for the whole session. Now a solver that won't start
  is skipped for the next one, and while the engine is MILP the integration
  installs the HiGHS solver itself at startup (again after every Home
  Assistant update). After updating: select MILP and restart Home Assistant
  once; hover the engine chip — it should say HiGHS.
- **No forced charging when the battery is at Min SOC.** At its minimum the
  inverter runs the house on the grid; the EMS no longer charges right away
  at whatever the price is because the battery is low (that bought the
  evening peak and switched the charge on and off every few minutes). The
  missing energy is bought back in the cheapest slots. Both engines.
- **The cheapest day of the week fills the battery.** The integration
  remembers the average price of the last 7 days (rebuilt from Home
  Assistant's statistics at startup). Energy for the coming days is bought
  only when it is clearly cheaper than that after the battery's losses — so
  a cheap Sunday fills the battery (and the car follows in those slots), an
  ordinary day buys only what it needs. Both engines, Save Money /
  Self-Sufficiency / Battery Care.
- Two scheduler engines: Greedy (default) and MILP.
- **Self-Sufficiency: the reserve by sunrise instead of by midnight.** The
  battery must still reach the morning with energy, but it is now charged in
  the cheapest slots before sunrise — including the night after midnight —
  instead of at a dear evening. Example (a real evening): 0.43 until
  midnight, 0.25 at night — it used to charge at 0.43; now the house runs on
  the grid until midnight and the battery charges at 0.25 for the morning.
- **The SOC "dip" at 04:00 is gone.** The consumption profile counted a
  whole day of use in the 04:00 hour, every day (the inverter's day counter
  briefly reports yesterday's total at ~04:05, and Home Assistant's
  statistics count that). That made the night look like 50+ kWh, the 7-day
  average twice the real one, and drove expensive evening buying. The
  profile now uses the counter's end-of-hour values; existing installs heal
  on the next restart.
- **…and this time also for the days already stored.** The first fix only
  replaced a stored day when Home Assistant's statistics covered it, so the
  old polluted days stayed (card: "Avg 96 kWh/d", "battery runs empty at
  04:15"). Stored days are now cleaned on load: an hour no house can draw is
  dropped and the day's total recalculated. The log says, once per start,
  which days were rebuilt and which were not.
- **New model: IVAM6048P1G1 (6 kW) — monitoring only, provisional.** Sensors
  and the energy-flow card work; the EMS does not. Felicity's protocol document
  for this inverter defines only read-only measurements and no settings, so the
  integration never writes to it and keeps the EMS off. Choose 9600 baud. What
  is still unverified (the power scale above all) is listed in
  `docs/IVAM_SUPPORT_GAPS.md`; a *Download diagnostics* file settles it.

- **Energy statistics no longer inflate** (issue #220, all models). The
  inverter's day/month/year counters briefly report the previous period's
  total around their reset, and occasional wrong readings slipped through;
  Home Assistant counted each one as extra energy (sums 3–8.5× too high).
  Impossible readings are now rejected before they are published — and before
  the EMS uses them — and a Modbus reply that doesn't match its request is
  discarded. Existing statistics stay as they are; correct them once in
  Developer tools → Statistics if you want.
- **No more "unknown" after a restart.** Sensors show their value straight
  away, and energy totals keep their last value until the inverter answers.

- **T-REX-10: corrected readings, checked against a real T-REX-10.** (The
  T-REX-5/6K is unchanged until one is measured.)
  "Linepowerconversion", "Batpowerconversion" and "Loadpowerconversion" showed
  a tenth of the real power (e.g. 843 W instead of 8430 W); "Battery Capacity
  secondary" showed 9 % instead of 94 %; the six firmware/hardware version
  sensors read the wrong registers (65535, 5.23 …) and now show the real
  versions. Long-term statistics of the three power sensors will show a step
  at the update. Download diagnostics now also checks T-REX-5/6/10 readings against
  each other (V × I against power, and registers that report the same thing).

- **Download diagnostics works on slow or unreliable connections.** On a
  flaky RS485 link the register read behind it could run for many minutes, and
  the browser gave up with a network error. It now finishes within about half a
  minute — registers it had no time for are listed as "not read" in the file —
  and answers at once while the inverter is not responding.

- **IVGM5048 / IVGM6048 / IVGM10048 owners: pick the T-REX-5K / 6K / 10K.**
  Felicity's protocol document for these inverters has exactly the T-REX-5/10
  register layout; the setup list now shows both names. (The IVGM 8K/15K/20K
  are a different layout and keep their own entries.) The T-REX-5/6/10 register
  maps are now checked against that document — every documented register
  matched.
- **New select values**: Operating Mode "Oil engine mode" and Economic Rule
  "ECO mode" (value 3), which the inverter can report but the integration
  showed as unknown.
- **T-REX-10: Economic Rule value 3 is now labelled "ECO mode (AC Couple)"**
  — the Felicity app calls the same setting "AC Couple" / "Para AC"
  (discussion #230). Only the name changed; an automation that selects the
  option by its text "ECO mode" needs the new text. T-REX-5/6K keep "ECO mode".
- **T-REX-6K and IVGM inverters now recover when they drop out of Economic
  mode** during a charge or discharge (app, power blip). The recovery check
  only covered the T-REX-5/10/25/50 before.
- *Download diagnostics* on T-REX-5/6/10 reads every register in Felicity's
  document, for checking open scale questions.

- **The `ha_felicity.write_register` service works.** It failed with an
  error on every call in every release that had it (it called a method that
  had been moved). It now writes through the same path as the register's own
  entity: give the value in the register's units, as the entity shows it
  (e.g. `56` for 56 V), whole numbers only. Refused on monitoring-only models.

- **Your real consumption pattern is used at last.** The hour-by-hour
  consumption profile behind the overnight reserve and the battery forecast
  was always flat (every hour the same), because the history it was read from
  was never parsed. It now comes from Home Assistant's long-term statistics
  for your consumption sensor, in local time, and the last 7 days are rebuilt
  as soon as the integration starts — no week of waiting. The daily average is
  recorded for the right day too.
- **More solar forecasts give an hourly curve.** Open-Meteo Solar Forecast
  (`wh_period`, today and tomorrow entities) and Solcast (`detailedHourly` /
  `detailedForecast`) are now read; before, only Forecast.Solar was, and the
  others were spread over a guessed 06:00–20:00 curve. Forecasts published in
  UTC land in the right hour. When a forecast gives only a daily total, the
  guessed curve now runs from today's sunrise to sunset (from `sun.sun`).
- **The EMS card says when it is guessing.** An amber chip appears when the
  solar curve is estimated or the consumption profile is flat, with a tooltip
  saying why; `schedule_status` has a new `input_sources` attribute listing
  what each input comes from.
- **The EV charger no longer "charges on solar" from your battery.** A slot
  only counts as solar for a load when the forecast surplus (after your
  household's own use at that hour) covers the load's minimum power, and a
  charger with current steps is set to the step the sun can cover. A load is
  also no longer switched on in a "cheap" slot the battery isn't charging in —
  there the battery, not the grid, would have fed it. Reported: 35 % of an
  EV's energy over ten days came from the battery.
- **Consumption-deviation correction works without an hourly forecast.** It
  was silently switched off whenever the solar curve had to be estimated.

- **Download diagnostics now contains a full register dump.** Settings → Devices
  & services → Felicity → ⋮ → Download diagnostics reads every register of your
  model (read-only) and saves raw + decoded values with sanity checks. Share that
  file when reporting wrong sensor values — it is far more useful than
  screenshots. A standalone `tools/ivgm_dump.py` does the same without HA.
- **Grid current protection works again with a flexible load (EV charger)
  running.** Safe power switched the charger off and therefore skipped cutting
  the battery power — but the charger was switched straight back on in the
  same cycle, so nothing was reduced and the current stayed over the limit
  (reported: 23–28 A on an 18 A setting). A shed load now stays off until
  there is room for it.
- **Grid current protection lowers the battery power first.** Flexible loads
  (EV charger, boiler…) are only stepped down or switched off when the battery
  is already at its minimum and the current is still too high. When the
  current drops, a switched-off load comes back before the battery power is
  raised again.
- **An unreliable inverter link no longer loses registers.** On a link that
  drops some replies (e.g. a T-REX-5 behind an RS485 gateway at 2400 baud), a
  register that has answered once is never removed from polling, misses only
  count when they follow each other, and a long read that keeps failing is
  polled in shorter pieces (down to 9 registers). Before, occasional misses
  added up over time and could remove the economic-rule registers the EMS
  uses to control a T-REX-5/6.
- **Self-Sufficiency no longer buys for a need that isn't there.** With little
  sun forecast for tomorrow the plan booked many charge slots "to cover 0.0 kWh
  deficit", including tonight's expensive evening and tomorrow's night. It
  overstated tomorrow's need (the evening was counted twice and the flat daily
  average was used instead of your consumption profile), and it planned every
  slot at the reduced power the grid-current protection had set for that
  moment. It now plans at your Power Level and computes tomorrow's need from
  your profile.
- **Flexible loads after a restart.** The integration now reads each load's
  real on/off state after a restart (a charger left running is no longer shown
  as off or left on unscheduled), and an EV Boost survives a restart.
- **New setting: EV Charger Grid Phase (L1/L2/L3).** When the grid-current
  protection has switched the charger off, it comes back as soon as its own
  phase has room. Left on "unknown" it waits for the busiest phase, as before.
- **Clearer power indicator.** The "Active power" chip in the EMS card is now
  "Grid charge limit" while charging and "Discharge limit" while discharging
  (hover for details), and the inverter card's bar reads "Eco Rule Power
  (Max)". It shows the power in Economic Rule 1, which limits charging from
  the grid only — solar still charges the battery on top of it.
- **The battery power is cut in one step.** When the grid current exceeds the
  limit, the battery power now drops straight to the level that brings the
  current back under it, instead of 2 kW every 10 seconds. It is raised back
  1 kW at a time once the current has dropped.
- **Cheap charging today is no longer postponed to a barely cheaper
  tomorrow.** Once tomorrow's prices were published, today's charging moved to
  tomorrow as soon as tomorrow was even a fraction of a cent cheaper — and the
  house then bought the evening peak and the night from the grid instead.
  Today's slot is now kept when it is cheaper (after battery losses) than what
  the house would pay from the grid tonight, earliest first, so the battery
  fills as soon as possible.
- **Selling starts when the peak starts.** With several evening slots at the
  same top price, the plan kept sliding later and sold only in the last hour
  (the rest at the night price), switching off and on mid-slot. A cheap midday
  charging window could drift the same way until it was missed. Fixed.
- **No more on/off every minute when a big load runs during a sale.** When the
  house imports while selling, discharge now pauses for 5 minutes before
  retrying, instead of toggling every poll.
- **Remaining solar today no longer counts tomorrow's sun.** With a
  Forecast.Solar entity the EMS thought about twice the real remaining PV was
  still coming, and bought too little on a disappointing day.
- **Manual overrides survive a restart.** Overrides set in the evening for the
  night were moved onto the wrong day by any Home Assistant restart before
  midnight (e.g. installing an update) and then cleared — so the night charge
  never happened. Overrides now remember the day they were set on.
- **Overrides run from their first slot, even when the battery can't take all
  of it.** They were trimmed to the last slots, and the running slot was
  switched off and on every few minutes. The inverter's own SOC limit ends the
  charge when the battery is full.
- **No more register writes every poll with a full battery.** In a charge slot
  the inverter now simply holds at max SOC instead of being switched between
  idle and charging every poll.
- **No false "Self-heal: inverter dropped out of Economic mode" warning** after
  every charge/discharge start.
- **T-REX-5/6/10: a charge or discharge running at midnight keeps going.** The
  rule's date is now moved to the new day; it used to stay on yesterday's.
- **Manual slot overrides are executed again.** Overrides could be silently
  discarded when the plan projected a full battery later in the day (they were
  always the first slots dropped); now the EMS's own slots give way instead.
  Overrides also work in manual price mode, where they were ignored.
- **The card's solid SOC line only shows measured SOC.** After a restart it used
  to fill missing history with the plan, so a planned charge looked done.
- **IVGM: economic-rule settings are now controls.** For each of the six ECO
  rules: start/stop time, voltage, SOC, power and grid/generator charge enable,
  plus ECO_TimeOfUse. Work mode and the weekday mask stay read-only until their
  values are confirmed.
- **A read that never gets an answer no longer slows every poll.** When one
  block keeps timing out while the rest of the inverter answers, it is split up
  automatically; a single register that never answers is skipped, and a whole
  block the inverter doesn't have is skipped after three polls. Slow polls
  are now logged with the reason, and Download diagnostics shows poll timing.
- **An unresponsive inverter no longer hangs Home Assistant's startup.** When
  reads keep timing out, the integration now stops after 3, pauses polling (30 s,
  growing to 5 min) and retries, instead of spending ~20 s on every register
  group. Entities show unavailable meanwhile and recover by themselves.
- **With the EMS off (`grid_mode` = off) the integration no longer writes to
  the inverter.** Previously every Home Assistant start wrote an "idle" state
  once price data arrived — on T-REX-5/10 this switched the inverter to General
  mode, on T-REX-25/50 and IVGM it changed work mode, zero-export, peak-shaving
  and rule-1 settings. If you run with the EMS off, check those settings on the
  inverter after updating.
- **IVGM:** removed a second bogus register (0x122F, "4. Communication frame
  format…").
- **IVGM:** removed a bogus register at address 0xAAAA (an artifact of reading
  the protocol PDF) that was polled on the "full" register set and showed up as
  a sensor with a very long name. That entity can be deleted.

### [1.3.10] - IVGM setpoint scale, sensor fixes, two new models

⚠️ **IVGM owners on 1.3.9 should update.** 1.3.9 corrected the IVGM's live power
readings but applied the same correction to its *settings* registers, which are
genuinely in watts. On 1.3.9 an IVGM therefore showed rule power as 75 kW instead
of 7.5 kW, and — the part that matters — wrote charge-power commands at **a tenth
of the intended value**. Fixed here. (Only relevant if you had turned `grid_mode`
on; it ships off.)

**New models**
- **T-REX-6KLP1G01** — electrically a T-REX-5 with a higher rating. Same
  registers, same control path; only the 6 kW cap differs.
- **IVGM-15KLP3G1** — register-identical to the IVGM-20K, capped at 15 kW.
  Still labelled "(provisional)" like the rest of the IVGM family.

  Pick the right one: the rating caps the EMS's charge planning and the Power
  Level slider, so a 15K configured as a 20K plans around 5 kW it hasn't got. The
  model is still chosen at setup only — to change it, remove and re-add the
  integration.

**IVGM — three more sensor fixes from the same customer report**
- Temperatures read 10× high: ambient showed **410 °C**, inverter 349, boost 329,
  BMS cells 210/200. Now 41.0 / 34.9 / 32.9 / 21.0 / 20.0 °C.
- BMS Total Voltage showed **536.0 V** on a 48 V pack; now 53.60 V.
- Two sensors carried the protocol document's "(8K donot 0.1KWh support)" note in
  their displayed name. Names cleaned up; entity IDs unchanged.

**IVGM — the dashboard cards now work.** The IVGM registers are named from its own
protocol document, but the energy-flow card finds sensors by name. On an IVGM it
showed nothing for grid, load and generator power and no battery SOC — and, worse,
it picked plausible-looking wrong entities for battery voltage (a 54 V SmartLoad
*setpoint*) and battery current (a charge *limit* reading 0.0 A). The IVGM maps
now publish the same aggregate sensors every other model has, and both cards
resolve ambiguous names deterministically. The EMS card was never affected — it
reads only EMS-computed entities.

**Confirmed on IVGM hardware for the first time** (previously assumed from the
T-REX-25/50): the economic-rule time encoding and the weekday mask. A 15K's six
rule windows decode to a clean 00:00→08:00→12:00→14:00→18:00→21:00→00:00 day.
Selling is still unproven on the IVGM — see `docs/IVGM_SUPPORT_GAPS.md`.

**Internal**
- Telemetry and setpoint power units are now separate per-model declarations,
  because on the IVGM they genuinely differ. One list could not serve both.
- CI runs the test suite (550 tests) and the EMS scenario simulator, not just
  the linter.

### [1.3.9] - Power-register scaling fixes

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

**IVGM — live power readings were 10× too low.** The telemetry power registers
are 0.01 kW per count, not the watts the protocol document claims. Measured on a
20K (raw 156 = 1560 W) and later confirmed twice on a 15K from physics. All IVGM
models use that scale — they are generated from the same document and no 8K has
been field-tested, so the measurement says the document's unit column is wrong
rather than that one model is special.

*(This release also applied that correction to the IVGM's settings registers,
which was wrong — see 1.3.10.)*

**Internal**
- Every model declares its power unit explicitly instead of being absent from a
  list, so adding a model can no longer pick one up by accident.

### [1.3.8] - Provisional IVGM family support

**New: IVGM-8KLP1G1 and IVGM-20KLP3G1**, selectable at setup and labelled
"(provisional)". Built entirely from the IVGM protocol document — no address
borrowed from a T-REX map, enforced by a frozen copy of the document's address
list. **Nothing is hardware-validated**: read `docs/IVGM_SUPPORT_GAPS.md` before
enabling `grid_mode`. Selling (`to_grid` / `both`) is unproven, because the IVGM
protocol does not define the register the discharge path needs.

### [1.3.7] - EMS and reliability fixes

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
- MILP reports *why* it fell back to greedy (hover the engine chip in the card,
  or run `tools/check_milp.py`) instead of one warning that scrolled out of the
  log, and `pulp` is pinned below 4.0 — PuLP 4.0 removes the bundled CBC solver
  and would have broken MILP for every user the day it shipped.

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
