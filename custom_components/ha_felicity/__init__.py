"""The Felicity integration."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient

from .const import (
    CONF_REGISTER_SET,
    DEFAULT_REGISTER_SET,
    _REGISTER_SETS,
    DOMAIN,
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_CONNECTION_TYPE,
    CONF_HOST,
    CONF_PARITY,
    CONF_PORT,
    CONF_SERIAL_PORT,
    CONF_SLAVE_ID,
    CONF_STOPBITS,
    CONNECTION_TYPE_SERIAL,
    DEFAULT_BAUDRATE,
    DEFAULT_BYTESIZE,
    DEFAULT_PARITY,
    DEFAULT_STOPBITS,
 #   CONF_INVERTER_MODEL,
    MODEL_DATA,
    _REGISTERS,
)
from .coordinator import HA_FelicityCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Felicity from a config entry."""
    config = entry.data
    connection_type = config.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_SERIAL)
    

    register_set_key = entry.options.get(CONF_REGISTER_SET, DEFAULT_REGISTER_SET)
    
    # Select model data (fallback to default model)
    model = entry.data.get(CONF_INVERTER_MODEL, DEFAULT_INVERTER_MODEL)
    model_data = MODEL_DATA.get(model, MODEL_DATA[DEFAULT_INVERTER_MODEL])
    
    # Select register set from options (filtered on model's registers)
    register_set_key = entry.options.get(CONF_REGISTER_SET, DEFAULT_REGISTER_SET)
    selected_registers = model_data["sets"].get(register_set_key, model_data["registers"])

    # Get or create shared hub for this connection
    hubs = hass.data.setdefault(DOMAIN, {}).setdefault("hubs", {})
    
    if connection_type == CONNECTION_TYPE_SERIAL:
        port = config[CONF_SERIAL_PORT]
        baudrate = config.get(CONF_BAUDRATE, DEFAULT_BAUDRATE)
        parity = config.get(CONF_PARITY, DEFAULT_PARITY)
        stopbits = config.get(CONF_STOPBITS, DEFAULT_STOPBITS)
        bytesize = config.get(CONF_BYTESIZE, DEFAULT_BYTESIZE)
        hub_key = f"serial_{port}_{baudrate}_{parity}_{stopbits}_{bytesize}"
        
        if hub_key not in hubs:
            hubs[hub_key] = FelicitySerialHub(hass, port, baudrate, parity, stopbits, bytesize)
    else:  # TCP
        host = config[CONF_HOST]
        port = config[CONF_PORT]
        hub_key = f"tcp_{host}_{port}"
        
        if hub_key not in hubs:
            hubs[hub_key] = FelicityTcpHub(hass, host, port)

    hub = hubs[hub_key]

    # Create coordinator with shared client and selected registers
    coordinator = HA_FelicityCoordinator(
        hass,
        hub.client,
        config[CONF_SLAVE_ID],
        selected_registers,
        groups=model_data["groups"]
    )
    # Store config and hub_key for unload cleanup
    coordinator.config = config
    coordinator.hub_key = hub_key
    entry.async_on_unload(entry.add_update_listener(update_listener))

    # First data refresh
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Forward to sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    coordinator = hass.data[DOMAIN].pop(entry.entry_id)
    hub_key = coordinator.hub_key

    # Check if any other active entries still use this hub
    remaining = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != entry.entry_id
    ]
    
    # Check if the hub is still used by other entries
    hub_still_used = False
    for other_entry in remaining:
        other_coordinator = hass.data[DOMAIN].get(other_entry.entry_id)
        if other_coordinator and getattr(other_coordinator, "hub_key", None) == hub_key:
            hub_still_used = True
            break

    if not hub_still_used:
        hub = hass.data[DOMAIN]["hubs"].pop(hub_key, None)
        if hub:
            await hub.close()

    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


class FelicitySerialHub:
    """Manages a single serial connection shared across meters."""

    def __init__(
        self,
        hass: HomeAssistant,
        port: str,
        baudrate: int,
        parity: str,
        stopbits: int,
        bytesize: int,
    ):
        self.hass = hass
        self.port = port
        self.baudrate = baudrate
        self.parity = parity
        self.stopbits = stopbits
        self.bytesize = bytesize
        self.client = AsyncModbusSerialClient(
            port=port,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            bytesize=bytesize,
            timeout=5,
        )

    async def close(self):
        """Close the connection safely."""
        if self.client is not None:
            if self.client.connected:
                try:
                    await self.client.close()
                except Exception as err:
                    _LOGGER.exception("Unexpected error closing Felicity connection for serial: %s", err)


class FelicityTcpHub:
    """Manages a single TCP connection shared across meters."""

    def __init__(self, hass: HomeAssistant, host: str, port: int):
        self.hass = hass
        self.host = host
        self.port = port
        self.client = AsyncModbusTcpClient(
            host=host,
            port=port,
            timeout=5,
        )

    async def close(self):
        """Close the connection safely."""
        if self.client is not None:
            if self.client.connected:
                try:
                    await self.client.close()
                except Exception as err:
                    _LOGGER.exception("Unexpected error closing Felicity connection for tcp: %s", err)
