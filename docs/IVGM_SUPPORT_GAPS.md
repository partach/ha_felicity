# IVGM family — what is NOT yet known

Status of `IVGM-8KLP1G1` and `IVGM-20KLP3G1`: **provisional**. The register maps
in `custom_components/ha_felicity/ivgm.py` are derived from one document —

> *Inverter Communication Protocol — Series RS485*, Guangzhou Felicity Solar
> Technology Co., Ltd. (广州菲利斯太阳能科技有限公司), v01, 2025‑03‑13, author
> "Moliting". PDF written 2025‑03‑28. Modbus RTU, 9600 8N1, slave 1.

Nothing here has been checked against hardware. This file lists every open
question, **dangerous ones first**, so the unknowns are visible instead of being
discovered on a live inverter.

**Until items 0–4 are answered, treat both models as read-only**: select the
model to get sensors, but leave `grid_mode = off` so the EMS never writes.
`grid_mode` defaults to `off`, so a fresh install is already in that state — the
owner has to opt in before any register is written.

### The addresses rule

**An IVGM register address must come from the IVGM document.** It is never
borrowed from a TREX map on the grounds that the families look similar — they
are different product lines and the layouts genuinely differ: `10minovptime` is
0x2205 here but 0x2206 on TREX‑25/50, and the telemetry block is offset by one
versus TREX‑5/10. A borrowed address does not fail loudly; it reads (or writes)
a neighbouring register and yields a plausible wrong number.

This is enforced, not just intended: `tests/data/ivgm_documented_registers.json`
is a frozen transcript of every address the document defines, and
`tests/test_model_coverage.py` fails if any IVGM register uses an address — or
carries a name — that isn't in it. All 365 addresses in the shipped map are
document-sourced; zero are borrowed.

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
| **3‑phase map** | **Inferred.** The document never mentions a 20K. We enable the registers the 8K is told not to support. Reasonable, unverified. |
| **Control path** | Documented: `ECO_TimeOfUse` (0x2207) + `ECOn_GridChargeEnable` (0x2209…), i.e. the **TREX‑25/50 shape**. |
| **Power units** | Documented as **W** — unlike TREX‑25/50, which use kW at the same addresses. |

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
| `ECO1_StartTime`/`StopTime` (0x220B/C) | packed `HH<<8 \| MM` | ❌ |
| `ECO_EffectiveWeek` (0x2208) | bit0=Sunday…bit6=Saturday, 0x7F = all | ❌ |

These are **cross-family assumptions of exactly the kind this project has
decided not to make** (see "The addresses rule" below). They are unavoidable —
the integration has to write *something* — but they are assumptions, not facts,
and a wrong `system_mode` value could put the inverter into an export mode the
owner did not ask for.

**To resolve:** set each mode from the inverter's own display and read the
register back. One pass over Work Mode's three positions and the ECO1 enable
settles the whole table.

## 1. Power unit — ANSWERED by hardware: 0.01 kW, not watts

**RESOLVED for the family (customer report, Sept 2026).** A 20K read `bat1_power`
(0x1131) as raw **156** where the true power was **1560 W** — 0.01 kW per count.
The document's "W" column is wrong. Both models' telemetry power registers are
now `index 9` (signed, /100) + `kW` + precision 2, and **neither IVGM is in
`WATT_POWER_MODELS`**.

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

**Still open: the SETTING register.** The measurement was of a *telemetry*
register. `ECO1_Power` (0x220F) is *written*, and nobody has measured it. Both
models are treated as kW there too, because the two error directions are not
symmetric:

| If we write | and the register is | result |
|---|---|---|
| W | kW | **1000× too much power** — dangerous |
| kW | W | 1000× too little — undercharges, harmless |

So kW is the side to be wrong on. Bounded further by `grid_mode` defaulting to
**off** — nothing is written until the owner opts in.

**To confirm:** on either model, set a known charge power (say 3 kW) from the
display and read 0x220F. `300` ⇒ 0.01 kW (as now assumed), `3000` ⇒ watts,
`3` ⇒ whole kW.

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

## 5. Unverified: does the 20K share this register map at all?

The whole 3‑phase map rests on the inference in the table above. Specifically
unverified for the 20K:

- extra registers the 20K may have that this 8K document omits — note
  TREX‑25/50 have **23** registers absent here (`econ_rule_N_sell_enable` ×6,
  `zero_export_mode_selection`, smart‑load timing, `serial_number_part_2..5`);
- whether `10minovptime` shifts: it is **0x2205** here but **0x2206** on
  TREX‑25/50 — proof the maps are *not* identical, so other one‑off shifts are
  possible;
- battery count. `L` in `IVGM-20KLP3G1` is read here as low‑voltage battery, and
  the family map has Bat1 + Bat2, but the 20K's actual count is unconfirmed.

**To resolve:** read `Device TypeID` (0xF800) and `Device SubTypeID` (0xF801) on
both models. The integration does not currently use them; they would be the
cleanest way to *detect* the model rather than have the user pick it.

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

## Checklist to promote IVGM from provisional to supported

1. [ ] Confirm the control-register enum values from the display (item 0).
2. [ ] Read 0xF800/0xF801 on both models; record the IDs here.
2. [ ] Confirm `ECO1_Power` unit on the **20K** (item 1).
3. [ ] Confirm 0x115A carries current on the 8K (item 4).
4. [ ] Establish how the IVGM enables export, without touching 0x21FF (item 2).
5. [ ] Confirm `ECO1_StartTime` packing.
6. [ ] Run on `basic` for a full day, read-only, and compare sensors to the
       inverter's display before enabling `grid_mode`.
7. [ ] Add an IVGM scenario to `tools/scenarios.py` once the power unit is
       settled, so the EMS behaviour is pinned like every other model's.
