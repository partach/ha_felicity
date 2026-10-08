# IVGM family — what is NOT yet known

Status of `IVGM-8KLP1G1`, `IVGM-15KLP3G1` and `IVGM-20KLP3G1`: **provisional**.
The 15K and 20K are register-identical and share one map, differing only in
`INVERTER_MAX_POWER_KW`. The maps in `custom_components/ha_felicity/ivgm.py` are
derived from one document —

> *Inverter Communication Protocol — Series RS485*, Guangzhou Felicity Solar
> Technology Co., Ltd. (广州菲利斯太阳能科技有限公司), v01, 2025‑03‑13, author
> "Moliting". PDF written 2025‑03‑28. Modbus RTU, 9600 8N1, slave 1.

Two customer dumps (Sept 2026, a 20K and a 15K) have now checked **part** of this
against hardware: the power scale, the `HH<<8|MM` time packing and the weekday
mask are confirmed, and three scaling bugs were found and fixed. Everything below
that is still marked open is still open — in particular **nothing has been
WRITTEN to an IVGM yet**, so every control assumption remains untested.

This file lists every open question, **dangerous ones first**, so the unknowns are
visible instead of being discovered on a live inverter.

**Until items 0–4 are answered, treat both models as read-only**: select the
model to get sensors, but leave `grid_mode = off` so the EMS never writes.
`grid_mode` defaults to `off`, so a fresh install is already in that state — the
owner has to opt in before any register is written.

⚠️ **Until Oct 2026 `grid_mode = off` did NOT fully prevent writes.** On every
HA start the coordinator "transitioned" from its initial `None` state to idle and
wrote it. On an IVGM that is: `system_mode` (0x2144 *Work Mode*) = 2,
`zero_export_to_ct_sell_enable` (0x2146) = 0, `grid_peak_shaving_enable` (0x2148)
= 1, `econ_rule_1_power` (0x220F) = 0, `econ_rule_1_grid_charge_enable` (0x2209)
= 0, `grid_peak_shaving_power` (0x2149) = 0 — the Work Mode value 2 being an
**unverified TREX-25/50 meaning** (item 0). It only ran once price data was
available. Fixed; if an IVGM ran an earlier version, check those six settings on
the display.

### The addresses rule

**An IVGM register address must come from the IVGM document.** It is never
borrowed from a TREX map on the grounds that the families look similar — they
are different product lines and the layouts genuinely differ: `10minovptime` is
0x2205 here but 0x2206 on TREX‑25/50, and the telemetry block is offset by one
versus TREX‑5/10. A borrowed address does not fail loudly; it reads (or writes)
a neighbouring register and yields a plausible wrong number.

This is enforced, not just intended: `custom_components/ha_felicity/ivgm_documented_registers.json`
is a frozen transcript of every address the document defines, and
`tests/test_model_coverage.py` fails if any IVGM register uses an address — or
carries a name — that isn't in it. All 363 addresses in the shipped map are
document-sourced; zero are borrowed.

The name check is the one that catches a mis-transcribed address, because a typo
usually still lands on a *valid* address. It asserts **containment**: our name
must appear within the document's text for that address, normalised. That lets us
trim the document's noise while still failing if a name points at a different
register — so when two names shipped with "(8K donot 0.1KWh support)" baked into
the HA entity name and were cleaned up, the guard correctly stayed green. Only the
`name` was changed; the dict **key** becomes the entity's `unique_id`, so renaming
a key would orphan every existing entity.

Key *names* are shared with the TREX maps on purpose (`eco_timeofuse`,
`econ_rule_1_power`, …) because the handlers look registers up by key. The key
is the interface; the **address is per-model data** and comes from that model's
own document.

---

## How confident are we, and why

The document is **8K‑scoped** — its tables are headed "3.2 **8K** Analog Quantity
Information" and "4.1 **8K** Setting Quantity Information" — but it documents the
*family* map and annotates 41 registers "(8K donot support)": phase C, PV3/PV4
and battery 2. Those registers exist precisely because a larger model uses them.

| | Evidence |
|---|---|
| **8K map** | Documented directly. High confidence. |
| **3‑phase map** | **Inferred**, but now partly corroborated: two 3‑phase units (a 20K and a 15K) return sensible values across the map. The document still never mentions either model. |
| **Control path** | Documented: `ECO_TimeOfUse` (0x2207) + `ECOn_GridChargeEnable` (0x2209…), i.e. the **TREX‑25/50 shape**. Never yet exercised — no IVGM has been written to. |
| **Power units** | Documented as **W** throughout. **Half wrong**: the 4xxx telemetry is really 0.01 kW (measured), the 8xxx setpoints really are W (confirmed). See item 1. |
| **Time / weekday packing** | ✅ **Confirmed on hardware** — see item 0. |

---

## 0. ⚠️ The document defines no enum VALUES for any control register (DANGEROUS)

The settings section gives address, word size and unit — and **nothing else**.
There is not a single value table in it. So for every register we actually
*write*, the meaning of the value is assumed from TREX‑25/50:

| Register | We assume | Documented? |
|---|---|---|
| `system_mode` (0x2144, "Work Mode") | 0 Selling / 1 Zero Export To Load / 2 Zero Export To CT | ❌ |
| `eco_timeofuse` (0x2207) | 1 = Economic mode on | ❌ |
| `ECO1_GridChargeEnable` (0x2209) | 1 = charge from grid | ❌ |
| `ECO1_StartTime`/`StopTime` (0x220B/C) | packed `HH<<8 \| MM` | ✅ **CONFIRMED** (below) |
| `ECO_EffectiveWeek` (0x2208) | bit0=Sunday…bit6=Saturday, 0x7F = all | ✅ **CONFIRMED** (below) |

These are **cross-family assumptions of exactly the kind this project has
decided not to make** (see "The addresses rule" below). They are unavoidable —
the integration has to write *something* — but they are assumptions, not facts,
and a wrong `system_mode` value could put the inverter into an export mode the
owner did not ask for.

**Two of them are now settled** by a 15K dump (Sept 2026). Its six ECO windows
decode under `HH<<8 | MM` to a perfect contiguous day, which cannot be
coincidence:

| Rule | Raw start → stop | Decoded |
|---|---|---|
| ECO1 | 0 → 2048 | 00:00 → 08:00 |
| ECO2 | 2048 → 3072 | 08:00 → 12:00 |
| ECO3 | 3072 → 3584 | 12:00 → 14:00 |
| ECO4 | 3584 → 4608 | 14:00 → 18:00 |
| ECO5 | 4608 → 5376 | 18:00 → 21:00 |
| ECO6 | 5376 → 0 | 21:00 → 00:00 |

And `ECO_EffectiveWeek` read **127** = 0x7F = all seven days, as assumed.

**Still open: `system_mode` and the enable values.** The same unit read
`eco_timeofuse` = 1 with all six rules showing active and an operational mode of
"Zero Export To Load", which is consistent with our assumed table but does not
prove it — reading a value the owner set from the display is not the same as
knowing what each value means.

**To resolve the rest:** set each mode from the inverter's own display and read
the register back. One pass over Work Mode's three positions and the ECO1 enable
settles what remains.

## 1. Power unit — ANSWERED by hardware, and it is DIFFERENT per block

**RESOLVED (two customer reports, Sept 2026).** The document says "W" everywhere.
It is right about the settings and wrong about the telemetry.

**4xxx telemetry = 0.01 kW per count.** A 20K read `bat1_power` (0x1131) as raw
**156** where the true power was **1560 W**. A 15K confirmed it twice from physics
in a single dump: `bat1_power` 80 against 53.6 V × 15.1 A = 809 W, and `pv1_power`
146 against 361.8 V × 4.0 A = 1447 W. Both ratios are 10. Telemetry power is now
`index 9` (signed, /100) + `kW` + precision 2, and **no IVGM is in
`WATT_POWER_MODELS`**.

**8xxx setpoints = plain watts.** The same 15K dump settles this the other way,
and a factory default equal to the nameplate is the tell:

| Setpoint | Raw | As W | As 0.01 kW |
|---|---|---|---|
| `grid_peak_shaving_power` | 15000 | **15.00 kW = its exact rating** | 150 kW ✗ |
| `ECO1…6_Power` | 7500 | 7.50 kW | 75 kW ✗ |
| `gen_input_rate_power` | 7500 | 7.50 kW | 75 kW ✗ |
| `max_pv_input_power` | 4850 | 4.85 kW | 48.5 kW ✗ |

So the conversion is gated on the address (`_IVGM_SETTING_BLOCK_START = 8192`) and
**every IVGM IS in `SETPOINT_WATT_MODELS`**. The first cut of this fix converted
both blocks and made the setpoints 10× wrong in both directions — displaying
75 kW, and writing a 5 kW charge command as raw 500 where the register wants 5000.
Caught by the second dump before release.

A second report corroborates it from another direction: an **IVGM-50K** driven by
the T-REX-50 map read every power sensor 10× high, and `-2` (not the documented
`-1`) was confirmed against nameplate capacity, the day-energy registers, and an
external meter. So across the range: small models in W, large in 0.01 kW —
T-REX-5/10 vs T-REX-25/50, and IVGM-50K.

**The 8K follows the 20K**, and this is *not* the cross-model inference the
T-REX-25 freeze forbids. There, the two models legitimately diverge: the 25's
firmware was altered and its scaling is field-proven, so borrowing the 50's
number would overwrite a measurement with a guess. Here **no IVGM has ever been
field-tested** and both maps are generated from ONE document — the same "W"
column produced both entries — so the measurement is evidence that the *source*
is wrong, and a wrong source does not stop at whichever model happened to be
plugged in first. The correction is applied to `_REGISTERS_IVGM_FAMILY`, so it
cannot reach one model and not the other.

**Still to confirm on an 8K**, and the reason it is safe to wait: if the 8K
really does report watts, this makes its readings 1000× low — instantly obvious
and harmless — whereas leaving it in W when it is 0.01 kW asks the inverter for
1000× too much. If an 8K is ever measured and disagrees, split the family in the
same commit as the measurement.

**Still open: whether WRITING behaves like READING.** Every number above was
*read*. `ECO1_Power` (0x220F) is the one we *write*, and no IVGM has ever been
written to. The read value (7500 = 7.5 kW as watts) is strong evidence the write
unit is watts too — a register almost always reads back in the unit it accepts —
but "almost always" is not "always".

Bounded by `grid_mode` defaulting to **off**: nothing is written until the owner
opts in.

**To confirm:** on any IVGM, set a known charge power (say 3 kW) from the display
and read 0x220F. `3000` ⇒ watts (as now assumed), `300` ⇒ 0.01 kW, `3` ⇒ whole kW.

Enforced in code by `const.POWER_UNIT_BY_MODEL` (every model declares its unit;
`WATT_POWER_MODELS` is derived from it) and asserted by
`tests/test_power_scaling.py`.

## 2. ⚠️ The discharge path writes a register the IVGM does not define (DANGEROUS)

The TREX‑25/50 discharge path writes `econ_rule_1_sell_enable` at **0x21FF**. The
IVGM document does not define 0x21FF–0x2204 at all — and in the IVGM map those
addresses sit **inside the grid under‑frequency protection block**
(0x21FA `Grid1 under frequency Value`, 0x21FB `Grid1 under frequency Time`,
0x21FC/0x21FD Grid2, 0x21FE `Grid3 under frequency Value`).

Writing a 0/1 there is **not a harmless no‑op** — it could alter a grid
protection threshold.

Blocked in code: `IVGM_UNSUPPORTED_WRITES` + `type_specific._write_if_defined()`,
pinned by `test_ivgm_never_writes_registers_its_protocol_does_not_define`.

**Consequence:** with the sell‑enable flag unavailable, **`to_grid` / `both`
(selling) is unproven on IVGM.** How the IVGM is told to export is unknown —
possibly via `system_mode` (0x2144 "Work Mode": Selling / Zero Export To Load /
Zero Export To CT) plus `Max Sell Power` (0x2147, W) alone.

**To resolve:** enable selling from the inverter's display and diff the 0x21xx /
0x2144–0x2149 block before and after.

## 3. ⚠️ The IVGM must use the *less stable* control path

There is **no** TREX‑5/10‑style control on this family: `operating_mode` (0x2103)
and the tri‑state `econ_rule_1_enable` (0x2178) are **absent** from the document.
The IVGM has only the `ECO_TimeOfUse` + per‑rule `GridChargeEnable` scheme.

The maintainer reports the **TREX‑10 write path is the stable one and TREX‑25 is
less stable** — so IVGM is forced onto the shape with the weaker track record.
Any flakiness seen on TREX‑25 should be expected here too, and fixes to that path
now affect four models rather than two.

## 4. ⚠️ Phase count of the 8K: `P1` says one, the register map implies two

The model code `IVGM-8KLP1G1` reads as **P1 = single phase**, but the document
marks only the **C** phase unsupported — leaving `Grid A`/`Grid B`,
`Home Load A`/`B`, `Gen A`/`B` all present. Either the 8K is split‑phase (L1/L2),
or it is genuinely single‑phase and the document simply never annotated B.

This matters for **safe power management**, which takes `max(|phase A|, |phase B|,
|phase C|)`: if B is unpopulated it reads 0 and is harmless, but if B is a real
second live conductor it must be included — as it currently is.

**To resolve:** read 0x115A (`Outside CT B Current`) on a loaded 8K. Persistent
0 ⇒ single phase.

## 5. Partly verified: does the 3‑phase map hold?

Two 3‑phase units (a 20K and a 15K) now return sensible values right across the
map — voltages, currents, frequencies, energies and the ECO block all read
plausibly and cross-check against each other. That is real corroboration of the
inference, but it is not proof of completeness. Still unverified:

- extra registers the 20K may have that this 8K document omits — note
  TREX‑25/50 have **23** registers absent here (`econ_rule_N_sell_enable` ×6,
  `zero_export_mode_selection`, smart‑load timing, `serial_number_part_2..5`);
- whether `10minovptime` shifts: it is **0x2205** here but **0x2206** on
  TREX‑25/50 — proof the maps are *not* identical, so other one‑off shifts are
  possible;
- battery count. `L` in `IVGM-20KLP3G1` is read here as low‑voltage battery, and
  the family map has Bat1 + Bat2, but the actual count is unconfirmed. The 15K
  dump reported Bat2 voltage/SOC/power all 0.0, consistent with a single battery
  installed — which does not tell us whether the model supports two.

**To resolve:** read `Device TypeID` (0xF800) and `Device SubTypeID` (0xF801) on
each model. The integration does not currently use them; they would be the
cleanest way to *detect* the model rather than have the user pick it. **First
data point:** the 15K reports TypeID **84**, SubTypeID **1052**. Two more models'
values would be enough to build a detection table.

## 5b. RESOLVED: three more scaling bugs the 15K dump exposed

Not gaps any more, recorded so the reasoning is not lost.

- **Temperatures were raw, not /10.** `environment_temperature` read **410 °C**,
  boost 329, inverter 349, BMS cells 210/200. Now `index 8` (signed /10) +
  precision 1 → 41.0 / 32.9 / 34.9 / 21.0 / 20.0. The map contradicted itself:
  `lead_acid_tempe` in the same block was already `index 8` and read correctly.
  Signed, not unsigned — ambient temperature goes below zero. The owner had built
  "Temperatur korrigiert" template sensors dividing by 10 by hand, which is the
  only reason this surfaced.
- **`bms_total_voltage` (0x120D / 4621) is 0.01 V per count**, not 0.1 — it read
  **536.0 V** on a 48 V pack whose `bat1_voltage` read 53.6 V. Only this one
  register: the neighbouring BMS charge/discharge voltage *limits* read 57.6 and
  48.0 V correctly at `index 1` and must not be swept along with it.
- **Two display names leaked the document's annotation** ("PV4 Day Gen
  Energy(8K donot 0.1KWh support)"). Names fixed; keys left alone, since the key
  is the `unique_id` and renaming it orphans existing entities.

**Still unexplained, low stakes:** `ATS Start Signal` read **65535**, which looks
like an unsigned read of −1 (`index 0` where `3` may be right). Nobody depends on
it; noted rather than guessed at.

## 6. Telemetry block is offset by one vs TREX‑5/10

The IVGM has `WorkMode` @ **0x1100** and `State1` @ 0x1101; the TREX‑5/10 map has
`setting_data_sn` @ 0x1100 and `working_mode` @ 0x1101. `MODEL_REGISTRY`
therefore sets `default_first_reg = 4352` (0x1100) for IVGM instead of 4353.

Unverified: whether the IVGM `WorkMode` enum matches the TREX‑5/10 `working_mode`
enum (Power On / Standby / Bypass / Off‑grid / Fault / Line / PV Charge). It is
exposed as a plain sensor, **not** an enum sensor, until the values are known —
an enum sensor with a wrong map renders "unavailable" rather than the truth.

## 7. Unmapped functionality (present in the document, unused by us)

Documented, in the register map, but nothing in the integration reads or acts on
them yet:

| Area | Registers | Why it might matter |
|---|---|---|
| Generator control | 0x2126 `Gen Start Signal`, 0x211F `Gen Force Run`, 0x2237 `Gen Mode`, 0x2238–0x223A | The IVGM is a hybrid with a real generator port; the EMS models only grid + PV. |
| Smart load | 0x223B–0x223F, 0x2247, 0x2248 | The inverter has its own load‑shedding by SOC/voltage, which could fight the integration's flexible‑load overlay. |
| Micro‑inverter / AC couple | 0x2240–0x2246, 0x118B–0x118D | Relevant to the generator‑port‑solar workaround (CLAUDE.md issue #6). |
| BMS block | 0x1200–0x121F | Per‑cell voltages/temperatures, charge/discharge current limits — richer than what SOH tracking currently infers from throughput. |
| Grid protection | 0x21B0–0x2205, 0x224A–0x2259 | Read‑only interest; **do not write** (see item 2). |
| ATS | 0x122E `ATS Start Signal` | Unknown relevance. |

## 8. Things the document does not state at all

- **Whether writes need a specific unlock/sequence.** The TREX‑25/50 path writes
  `system_mode` before `eco_timeofuse`; whether IVGM needs the same order, or
  any order, is untested.
- **Register write limits** — no min/max/step for any setting register, so the
  integration's own clamps are the only protection.
- **Whether unlisted addresses are safe to read.** `build_groups()` batches
  consecutive addresses; if a gap address faults, a whole group read could fail.
  Start on the `basic` register set, which polls the fewest registers.
- **Rule 1 time‑window semantics.** `ECO1_StartTime`/`StopTime` are assumed to be
  the packed `HH<<8 | MM` used elsewhere. Unverified.
- **Model auto-detection.** Not attempted; the user selects the model at setup.

---

## How to send evidence

Settings → Devices & services → Felicity → ⋮ → **Download diagnostics** gives a
JSON file with a fresh, read-only read of every documented address (raw words +
decoded values + P-vs-V×I checks). Without HA: `python tools/ivgm_dump.py --host
<ip>`. Take a photo of the inverter display at the same moment — the display is
the only source for the enum values in item 0. Screenshots of HA alone are not
enough: they show decoded numbers, which hide the scale being checked.

## Checklist to promote IVGM from provisional to supported

1. [ ] Confirm `system_mode` and the enable enum values from the display (item 0).
2. [~] Read 0xF800/0xF801 on each model; record the IDs here. *(15K: TypeID 84,
       SubTypeID 1052 — need the 8K and 20K.)*
3. [ ] Confirm the `ECO1_Power` **write** unit by setting a known power from the
       display and reading 0x220F back (item 1). Reads say watts; writes untested.
4. [ ] Confirm 0x115A carries current on the 8K (item 4).
5. [ ] Establish how the IVGM enables export, without touching 0x21FF (item 2).
6. [x] ~~Confirm `ECO1_StartTime` packing.~~ **Done** — `HH<<8 | MM` confirmed on
       a 15K (item 0).
7. [ ] Measure an **8K**'s telemetry power scale. It currently inherits the
       3-phase 0.01 kW correction on the reasoning that the document (not one
       model) is what was wrong.
6. [ ] Run on `basic` for a full day, read-only, and compare sensors to the
       inverter's display before enabling `grid_mode`.
7. [ ] Add an IVGM scenario to `tools/scenarios.py` once the power unit is
       settled, so the EMS behaviour is pinned like every other model's.
