"""Read-only register dump — shared by diagnostics.py and tools/ivgm_dump.py.

Used to check a register map against real hardware: read every address, keep
the RAW words, decode each mapped register the way the coordinator does, and
run the physics cross-checks that found every IVGM scaling bug so far.

The raw words are the important part.  They let the map be re-checked later
(by a person or a test) without access to the inverter.

    STRICTLY READ-ONLY.  The only Modbus call here is function 3 (read holding
    registers), passed in by the caller.  Nothing in this module can write.

HA-free and import-free on purpose, so the standalone tool can load it by file
path without Home Assistant installed.
"""

from __future__ import annotations

import json
import os
import time

DUMP_FORMAT_VERSION = 1

#: Frozen transcript of the IVGM protocol document — every address it defines.
#: Also the source of truth for tests/test_model_coverage.py's provenance rule.
IVGM_DOCUMENT_PATH = os.path.join(os.path.dirname(__file__),
                                  "ivgm_documented_registers.json")

#: Addresses in the transcript that are PDF-extraction artifacts, not
#: registers: 0xAAAA is the prose example of a write frame, 0x122F is the
#: "4. Communication frame format" section heading that follows the last
#: telemetry row.
NOT_REGISTERS = {0xAAAA, 0x122F}

#: Largest single read.  Well under the Modbus limit of 125 — some dongles
#: struggle with large frames.
MAX_CHUNK = 60

#: When a batch fails and we retry it register by register, give up on that
#: batch after this many consecutive failures (a dead link would otherwise
#: cost one timeout per address).
_MAX_CONSECUTIVE_FAILURES = 3


# ---------------------------------------------------------------------------
# What to read
# ---------------------------------------------------------------------------

def load_ivgm_documented_addresses() -> dict[int, str]:
    """{address: document text} for every register the IVGM document defines."""
    with open(IVGM_DOCUMENT_PATH, encoding="utf-8") as fh:
        raw = json.load(fh)["addresses"]
    return {int(k, 16): text for k, text in raw.items()
            if int(k, 16) not in NOT_REGISTERS}


def map_addresses(register_map: dict) -> set[int]:
    """Every word address the map occupies (32-bit registers take two)."""
    return {
        info["address"] + i
        for info in register_map.values() if "address" in info
        for i in range(info.get("size", 1))
    }


def plan_reads(addresses, max_chunk: int = MAX_CHUNK) -> list[tuple[int, int]]:
    """Contiguous runs of `addresses` as (start, count), split at max_chunk.

    Only the given addresses are read — never a gap between them, because an
    undefined address can fault the whole batch.
    """
    reads = []
    start = prev = None
    for addr in sorted(addresses):
        if start is None:
            start = prev = addr
        elif addr == prev + 1 and addr - start < max_chunk:
            prev = addr
        else:
            reads.append((start, prev - start + 1))
            start = prev = addr
    if start is not None:
        reads.append((start, prev - start + 1))
    return reads


# ---------------------------------------------------------------------------
# Reading.  `read(address, count) -> list[int]` raises on any failure.
# ---------------------------------------------------------------------------

def _record_batch(raw, start, words):
    for i, word in enumerate(words):
        raw[start + i] = word


def read_all(read, reads, budget_s: float = 120.0):
    """Synchronous variant (standalone tool)."""
    raw, failed = {}, {}
    deadline = time.monotonic() + budget_s
    for start, count in reads:
        if time.monotonic() > deadline:
            _mark_skipped(failed, start, count, "time budget exhausted")
            continue
        try:
            _record_batch(raw, start, read(start, count))
            continue
        except Exception as err:
            batch_error = str(err) or type(err).__name__
        streak = 0
        for addr in range(start, start + count):
            if streak >= _MAX_CONSECUTIVE_FAILURES:
                _mark_skipped(failed, addr, start + count - addr, batch_error)
                break
            try:
                raw[addr] = read(addr, 1)[0]
                streak = 0
            except Exception as err:
                failed[f"0x{addr:04X}"] = str(err) or batch_error
                streak += 1
    return raw, failed


async def async_read_all(read, reads, budget_s: float = 60.0):
    """Async variant (diagnostics, using the integration's own client)."""
    raw, failed = {}, {}
    deadline = time.monotonic() + budget_s
    for start, count in reads:
        if time.monotonic() > deadline:
            _mark_skipped(failed, start, count, "time budget exhausted")
            continue
        try:
            _record_batch(raw, start, await read(start, count))
            continue
        except Exception as err:
            batch_error = str(err) or type(err).__name__
        streak = 0
        for addr in range(start, start + count):
            if streak >= _MAX_CONSECUTIVE_FAILURES:
                _mark_skipped(failed, addr, start + count - addr, batch_error)
                break
            try:
                raw[addr] = (await read(addr, 1))[0]
                streak = 0
            except Exception as err:
                failed[f"0x{addr:04X}"] = str(err) or batch_error
                streak += 1
    return raw, failed


def _mark_skipped(failed, start, count, reason):
    for addr in range(start, start + count):
        failed.setdefault(f"0x{addr:04X}", f"skipped: {reason}")


# ---------------------------------------------------------------------------
# Decoding — mirrors coordinator._async_update_data / _apply_scaling.
# tests/test_register_dump.py checks this agrees with the coordinator, so the
# two cannot drift apart silently.
# ---------------------------------------------------------------------------

def _signed(raw: int, size: int) -> int:
    bits = 16 * size
    return raw - (1 << bits) if raw >= (1 << (bits - 1)) else raw


def apply_scaling(raw: int, index: int, size: int = 1):
    if index == 1:
        return raw / 10.0
    if index == 2:
        return raw / 100.0
    if index == 3:
        return _signed(raw, size)
    if index == 4:
        return raw / 1000.0
    if index == 8:
        return _signed(raw, size) / 10.0
    if index == 9:
        return _signed(raw, size) / 100.0
    return raw


def combine_words(words: list[int], endian: str = "big") -> int:
    if endian != "big":
        words = list(reversed(words))
    raw = 0
    for word in words:
        raw = (raw << 16) | word
    return raw


def decode(register_map: dict, raw_by_addr: dict) -> dict:
    """{key: {address, name, unit, index, size, raw, value}} for the map."""
    decoded = {}
    entries = [(k, v) for k, v in register_map.items() if "address" in v]
    for key, info in sorted(entries, key=lambda kv: kv[1]["address"]):
        addr, size = info["address"], info.get("size", 1)
        index = info.get("index", 0)
        words = [raw_by_addr.get(addr + i) for i in range(size)]
        entry = {"address": f"0x{addr:04X}", "name": info.get("name"),
                 "unit": info.get("unit"), "index": index, "size": size,
                 "raw": None, "value": None}
        if all(w is not None for w in words):
            raw = combine_words(words, info.get("endian", "big"))
            if size == 4 and index == 3:
                raw = _signed(raw, 4)
            value = apply_scaling(raw, index, size)
            if isinstance(value, float):
                value = round(value, info.get("precision", 0))
            entry["raw"], entry["value"] = raw, value
        decoded[key] = entry
    return decoded


# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------

#: (label, voltage key, current key, power key).  P should be close to V x I;
#: a ratio near 10 or 1000 is a scale bug, not noise.
_PHYSICS = (
    ("Bat1", "bat1_voltage", "bat1_current", "bat1_power"),
    ("PV1", "pv1_voltage", "pv1_current", "pv1_power"),
    ("PV2", "pv2_voltage", "pv2_current", "pv2_power"),
)


def _value(decoded, key):
    return (decoded.get(key) or {}).get("value")


def _hhmm(raw):
    return None if raw is None else f"{raw >> 8:02d}:{raw & 0xFF:02d}"


def physics_checks(decoded: dict) -> list[dict]:
    checks = []
    for label, vk, ik, pk in _PHYSICS:
        v, i, p = _value(decoded, vk), _value(decoded, ik), _value(decoded, pk)
        if None in (v, i, p):
            continue
        vi_w = abs(v * i)
        if vi_w < 50:
            checks.append({"check": label, "result": "skipped (too little current)"})
            continue
        p_w = abs(p) * (1000 if (decoded[pk]["unit"] == "kW") else 1)
        ratio = p_w / vi_w
        checks.append({"check": label, "v_x_i_w": round(vi_w), "reported_w": round(p_w),
                       "ratio": round(ratio, 2),
                       "result": "ok" if 0.7 <= ratio <= 1.3 else "SCALE SUSPECT"})
    return checks


def eco_windows(decoded: dict) -> dict:
    out = {}
    for n in range(1, 7):
        start = (decoded.get(f"econ_rule_{n}_start_time") or {}).get("raw")
        stop = (decoded.get(f"econ_rule_{n}_stop_time") or {}).get("raw")
        if start is not None or stop is not None:
            out[f"ECO{n}"] = f"{_hhmm(start)} -> {_hhmm(stop)}"
    return out


_SUMMARY_KEYS = (
    "device_type_id", "device_sub_type_id",
    "bat1_soc", "bms_total_soc", "bat1_voltage", "bms_total_voltage",
    "bat1_current", "bat1_power", "bat2_soc",
    "pv1_power", "pv2_power", "total_grid_power", "total_load_power",
    "total_generator_power",
    "inverter_temperature", "boost_temperature", "environment_temperature",
    "bms_maximum_cell_temperature",
    "system_mode", "operating_mode", "eco_timeofuse", "eco_effectiveweek",
    "econ_rule_1_enable", "econ_rule_1_grid_charge_enable",
    "econ_rule_1_voltage", "econ_rule_1_soc", "econ_rule_1_power",
)


def summary_lines(dump: dict) -> list[str]:
    decoded = dump["decoded"]
    lines = [(f"Read {dump['addresses_read']} addresses, "
              f"{len(dump['addresses_failed'])} failed.")]
    for key in _SUMMARY_KEYS:
        e = decoded.get(key)
        if e:
            lines.append(f"  {e['name'][:34]:<34} {e['value']!s:>10} "
                         f"{e['unit'] or '':<4} (raw {e['raw']})")
    for name, window in dump["eco_windows"].items():
        lines.append(f"  {name} window{'':<26} {window}")
    for c in dump["physics_checks"]:
        if "ratio" in c:
            lines.append(f"  {c['check']:<5} V x I = {c['v_x_i_w']} W, reported "
                         f"{c['reported_w']} W, ratio {c['ratio']} -> {c['result']}")
        else:
            lines.append(f"  {c['check']:<5} {c['result']}")
    return lines


# ---------------------------------------------------------------------------
# The dump document
# ---------------------------------------------------------------------------

def build_dump(*, model: str, register_map: dict, raw: dict, failed: dict,
               documented: dict | None = None, meta: dict | None = None) -> dict:
    decoded = decode(register_map, raw)
    mapped = {info["address"] for info in register_map.values() if "address" in info}
    dump = {
        "format_version": DUMP_FORMAT_VERSION,
        **(meta or {}),
        "model": model,
        "addresses_read": sum(1 for v in raw.values() if v is not None),
        "addresses_failed": failed,
        "physics_checks": physics_checks(decoded),
        "eco_windows": eco_windows(decoded),
        "decoded": decoded,
        "raw": {f"0x{a:04X}": raw[a] for a in sorted(raw)},
    }
    if documented:
        dump["unmapped_documented"] = {
            f"0x{a:04X}": {"raw": raw.get(a), "document": documented[a][:160]}
            for a in sorted(documented) if a not in mapped
        }
    return dump
