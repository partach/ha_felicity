# IVAM6048P1G1 — monitoring only, and what would change that

Status: **provisional, monitoring only.**  The map in
`custom_components/ha_felicity/ivam.py` comes from one document:

> *IVAM High-Frequency Inverter External Communication Protocol — RS485*,
> Guangzhou Felicity Solar Technology Co., Ltd. (广州菲利斯太阳能科技有限公司),
> v01, 2026‑07‑06.  Modbus RTU, **9600 8N1**, slave address 1–31 (31 is the
> broadcast address).  Frozen as `ivam_documented_registers.json`.

## Why monitoring only

The document has exactly one register table, and it is **read-only telemetry**
(section 3.1: PV, battery, grid, load, generator, smart load, energy counters,
alarm and fault codes).  It defines **no setting registers** — no operating or
economic mode, no time-of-use rule, no charge/discharge power limit.  Those are
what the EMS drives on every other model, so on the IVAM:

- every write is refused at the bottom of the write path
  (`TypeSpecificHandler.async_write_registers`), logged once;
- the coordinator never attempts a state transition and reports self-use;
- safe power does not throttle (there is no power register to lower);
- `grid_mode` is forced to `off` at setup, the Grid Mode select offers only
  `off`, and the strategy presets are not offered.

Sensors, the energy-flow card and the diagnostics dump all work.  Flexible
loads do not run — they are scheduled by the EMS, which is off.

⚠️ **The telemetry addresses are the same as the IVGM's and T-REX-25/50's**
(battery SOC 0x1138, PV1 power 0x1128, grid power 0x1173 …).  That makes it
tempting to assume the IVAM also has their setting block (0x2103 / 0x2144 /
0x2207 / 0x2209 …).  **Do not.**  An address the IVAM document does not define
may be something else on this firmware; on the IVGM, the T-REX-25/50 sell-enable
addresses turned out to sit inside the grid under-frequency protection block.
`tests/test_model_coverage.py` fails if a control register appears in the IVAM
map, and `tests/test_ivam_support.py` fails on any address not in the document.

## What would make control possible

1. **Ask Felicity for the settings section** of the IVAM protocol (the
   time-of-use / economic rules, operating mode and their value tables).  With a
   documented address and encoding per register, the IVAM can join one of the
   two control paths (`OPERATING_MODE_MODELS` or `ECO_TIMEOFUSE_MODELS`).
2. Or, read-only: a dump of the candidate setting addresses taken next to a
   photo of the inverter's own settings screen would show whether the IVGM
   layout is present.  That is evidence for a decision, not a licence to write.

## Open questions in the telemetry (check with a diagnostics dump)

Settings → Devices & services → Felicity → ⋮ → **Download diagnostics** reads
every address in the document (including the unmapped ones below) and runs the
physics checks: `P / (V × I)` for the battery, PV1, PV2, grid and load.

| # | Question | Why it matters |
|---|---|---|
| 1 | **Is power really in W?** The document says "Rate 0, W". The IVGM document said the same and its telemetry was 0.01 kW per count (10× off). | Every power sensor and the card. A ratio near 10 or 0.1 in the dump's physics checks settles it. |
| 2 | Sign of **grid power** (0x1173): import positive? | Card direction of the grid flow. Not documented (battery current/power ARE: negative = discharging). |
| 3 | **SccHs temperature** "Rate 0 °C" — raw degrees, or tenths like the IVGM's temperatures turned out to be? | A reading of ~300 means tenths. |
| 4 | The "**inquiry – Day / Month / Year**" registers at 0x1260–0x1269 and 0x1294–0x1298 sit beside the "This day / month / year" ones. What period do they answer? | Not mapped until known; the dump reads them. |
| 5 | Alarm and fault code values — "see the model's instruction manual". | Shown as raw numbers. |
| 6 | 30 small reads per poll at 9600 baud (the document's registers are scattered). | Should be ~1–2 s; `poll_stats` in the diagnostics shows the real number. |

## Setting it up

Choose **IVAM6048P1G1 (monitoring only, 9600 baud, provisional)** in the setup
dialog and set the serial baud rate to **9600** (the T-REX default is 2400).
