"""Diagnostics: a full, read-only register dump of the inverter.

Settings → Devices & services → Felicity → ⋮ → **Download diagnostics**
returns one JSON file containing a fresh read of every register the model's
map defines (for the IVGM family: every address its protocol document
defines), with the RAW words, the decoded values, and physics cross-checks.
That file is what to share when checking a register map against hardware.

Read-only: only function 3 (read holding registers) goes over the bus.  It
uses the integration's own Modbus connection, so the gateway needs no second
client slot and Home Assistant need not be stopped.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import register_dump
from .const import CONF_HOST, CONF_INVERTER_MODEL, DOMAIN, IVGM_MODELS, MODEL_REGISTRY

_TO_REDACT = {CONF_HOST}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    # The coordinator holds the normalised model (legacy names are mapped in
    # async_setup_entry), so prefer it over the raw entry data.
    model = getattr(coordinator, "inverter_model", None) or entry.data.get(CONF_INVERTER_MODEL)

    result: dict[str, Any] = {
        "integration_version": getattr(coordinator, "integration_version", None),
        "entry_data": async_redact_data(dict(entry.data), _TO_REDACT),
        "entry_options": dict(entry.options),
        "inverter_model": model,
    }
    if coordinator is None:
        result["register_dump"] = {"error": "integration not loaded"}
        return result

    result["last_poll"] = dict(coordinator.data or {})
    result["milp_status"] = getattr(coordinator, "milp_status", None)
    result["register_dump"] = await _async_dump(hass, coordinator, model)
    return result


async def _async_dump(hass: HomeAssistant, coordinator, model: str | None) -> dict:
    # The FULL model map, not the selected register set — the point is to
    # check every register we might ever read.
    register_map = MODEL_REGISTRY.get(model, {}).get("registers") or coordinator.register_map
    documented = None
    if model in IVGM_MODELS:
        documented = await hass.async_add_executor_job(
            register_dump.load_ivgm_documented_addresses)

    addresses = register_dump.map_addresses(register_map)
    if documented:
        addresses |= set(documented)

    client, slave = coordinator.client, coordinator.slave_id

    async def read(address: int, count: int) -> list[int]:
        if not client.connected:
            await client.connect()
        response = await client.read_holding_registers(
            address=address, count=count, device_id=slave)
        if response.isError():
            raise OSError(str(response))
        return response.registers

    started = dt_util.now()
    raw, failed = await register_dump.async_read_all(
        read, register_dump.plan_reads(addresses))
    return register_dump.build_dump(
        model=model or "unknown", register_map=register_map, raw=raw, failed=failed,
        documented=documented,
        meta={"source": "Home Assistant diagnostics",
              "captured_at": started.isoformat(timespec="seconds"),
              "duration_s": round((dt_util.now() - started).total_seconds(), 1),
              "slave": slave},
    )
