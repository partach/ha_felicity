"""The Felicity integration."""
import os
import shutil
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient
from homeassistant.helpers import entity_registry as er


from .const import (
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_CONNECTION_TYPE,
    CONF_HOST,
    CONF_INVERTER_MODEL,
    CONF_PARITY,
    CONF_PORT,
    CONF_REGISTER_SET,
    CONF_SERIAL_PORT,
    CONF_SLAVE_ID,
    CONF_STOPBITS,
    CONNECTION_TYPE_SERIAL,
    DEFAULT_BAUDRATE,
    DEFAULT_BYTESIZE,
    DEFAULT_INVERTER_MODEL,
    DEFAULT_PARITY,
    DEFAULT_REGISTER_SET,
    DEFAULT_STOPBITS,
    DOMAIN,
    MODEL_DATA,
)
from .coordinator import HA_FelicityCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR,Platform.NUMBER,Platform.SELECT]

async def async_install_frontend_resource(hass: HomeAssistant):
    """Ensure the frontend JS file is copied to the www/community folder."""
    
    def install():
        # Source path: custom_components/ha_felicity/frontend/ha_felicity.js
        source_path = hass.config.path("custom_components", DOMAIN, "frontend", "ha_felicity.js")
        
        # Target path: www/community/ha_felicity/
        target_dir = hass.config.path("www", "community", DOMAIN)
        target_path = os.path.join(target_dir, "ha_felicity.js")

        try:
            # 1. Ensure the destination directory exists
            if not os.path.exists(target_dir):
                _LOGGER.debug("Creating directory: %s", target_dir)
                os.makedirs(target_dir, exist_ok=True)

            # 2. Check if source exists and copy
            if os.path.exists(source_path):
                # Using copy2 to preserve metadata (timestamps)
                shutil.copy2(source_path, target_path)
                _LOGGER.info("Updated frontend resource: %s", target_path)
            else:
                _LOGGER.warning("Frontend source file missing at %s", source_path)
                
        except Exception as err:
            _LOGGER.error("Failed to install frontend resource: %s", err)

    # Offload the blocking file operations to the executor thread
    await hass.async_add_executor_job(install)

async def async_register_card(hass: HomeAssistant, entry: ConfigEntry):
    """Register the custom card as a Lovelace resource."""
    lovelace_data = hass.data.get("lovelace")
    if not lovelace_data:
        _LOGGER.debug("Unable to get lovelace data (new api 2026.2)")
        return  # YAML mode or Lovelace not loaded

    resources = lovelace_data.resources
    if not resources:
        _LOGGER.debug("Unable to get resources (new api 2026.2)")
        return  # YAML mode or not loaded

    if not resources.loaded:
        await resources.async_load()

    card_url = f"/hacsfiles/{DOMAIN}/{DOMAIN}.js"
    # Or local: f"/local/custom_cards/{DOMAIN}-card.js"

    # Check if already registered
    for item in resources.async_items():
        if item["url"] == card_url:
            _LOGGER.debug("Card already registered: %s", card_url)
            return  # already there

    await resources.async_create_item({
        "res_type": "module",
        "url": card_url,
    })
    _LOGGER.debug("Card registered: %s", card_url)


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options change — reload only if register set changed."""
    # Get the coordinator to access previous register set if needed
    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    if not coordinator:
        return

    # Previous register set (you can store it on coordinator if needed)
    # But simpler: just reload if register_set changed (we can't know old, but reload is safe)
    # Or better: always reload on register_set change, refresh otherwise
    # Since we can't easily get "old" value here, safest is:
    if entry.options.get(CONF_REGISTER_SET) != getattr(coordinator, "_last_register_set", None):
        _LOGGER.debug("Register set changed — reloading integration")
        coordinator._last_register_set = entry.options.get(CONF_REGISTER_SET)
        await hass.config_entries.async_reload(entry.entry_id)
    else:
        _LOGGER.debug("Other options changed — refreshing data")
        await coordinator.async_request_refresh()
        # Update stored last set
        coordinator._last_register_set = entry.options.get(CONF_REGISTER_SET)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Felicity from a config entry."""
    config = entry.data
    connection_type = config.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_SERIAL)
    
    # Select model data (fallback to default model)
    model = entry.data.get(CONF_INVERTER_MODEL, DEFAULT_INVERTER_MODEL)
    model_data = MODEL_DATA.get(model, MODEL_DATA[DEFAULT_INVERTER_MODEL])

    #add nordpool integration
    nordpool_entity = entry.options.get("nordpool_entity")
    # === Ensure options have defaults (migration-safe) ===
    updated_options = dict(entry.options)
    updated_options.setdefault("price_threshold_level", 5)
    updated_options.setdefault("battery_charge_max_level", 100)
    updated_options.setdefault("battery_discharge_min_level", 20)
    updated_options.setdefault("grid_mode", "off")
    updated_options.setdefault("power_level", 5)
    updated_options.setdefault("safe_max_power", 0)
    updated_options.setdefault("voltage_level", 58)
    updated_options.setdefault(CONF_REGISTER_SET, DEFAULT_REGISTER_SET)
    updated_options.setdefault("update_interval", 10)

    if updated_options != entry.options:
        hass.config_entries.async_update_entry(entry, options=updated_options)
    # === Now read from (possibly updated) options ===
    register_set_key = entry.options.get(CONF_REGISTER_SET, DEFAULT_REGISTER_SET)
    nordpool_entity = entry.options.get("nordpool_entity")
    nordpool_override = entry.options.get("nordpool_override")
    update_interval = entry.options.get("update_interval", 10)
    
    # Select register set from options (filtered on model's registers)
    if register_set_key not in model_data["sets"]:
        _LOGGER.warning("Invalid register set '%s', falling back to full", register_set_key)
        selected_registers = model_data["registers"]
    else:
        selected_registers = model_data["sets"].get(register_set_key, model_data["registers"])
    
    _LOGGER.debug("Current entry.options: %s", entry.options)
    _LOGGER.debug("Selected register_set_key: %s", register_set_key)
    _LOGGER.debug("Number of selected registers: %d", len(selected_registers))
    
    # === SAFETY NET: Auto-include missing group keys (prevents update failed) ===
    all_group_keys = {key for group in model_data["groups"] for key in group["keys"]}
    missing = all_group_keys - selected_registers.keys()
    if missing:
        _LOGGER.debug("Auto-adding missing group keys: %s", missing)
        for key in missing:
            if key in model_data["registers"]:
                selected_registers[key] = model_data["registers"][key]
    # ===========================================================================

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
        hass=hass,
        client=hub.client,
        slave_id=config[CONF_SLAVE_ID],
        register_map=selected_registers,
        groups=model_data["groups"],
        config_entry=entry,
        nordpool_entity=nordpool_entity,
        nordpool_override=nordpool_override,
        update_interval=update_interval,
        
    )
    # Store config and hub_key for unload cleanup
    coordinator.config = config
    coordinator.hub_key = hub_key
    coordinator._last_register_set = register_set_key

    # First data refresh
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Forward to sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if "services_setup" not in hass.data[DOMAIN]:
        await async_setup_services(hass)
        hass.data[DOMAIN]["services_setup"] = True

    await async_install_frontend_resource(hass)
    await async_register_card(hass,entry)
    return True

async def async_setup_services(hass: HomeAssistant) -> None:
    async def handle_write_register(call: ServiceCall):
        entity_ids = call.data.get("entity_id")
        if not entity_ids:
            _LOGGER.error("write_register: entity_id required")
            return
    
        # Handle single string or list
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
    
        key = call.data["key"]
        value = call.data["value"]
    
        for entity_id in entity_ids:
            entity_registry = er.async_get(hass)
            ent = entity_registry.async_get(entity_id)
            if not ent or ent.config_entry_id not in hass.data[DOMAIN]:
                _LOGGER.error("No Felicity config entry for entity %s", entity_id)
                continue
    
            coordinator = hass.data[DOMAIN][ent.config_entry_id]
            success = await coordinator.async_write_register(key, value)
            if success:
                _LOGGER.info("Wrote %s = %s to %s", key, value, entity_id)
                await coordinator.async_request_refresh()
            else:
                _LOGGER.error("Failed to write %s = %s to %s", key, value, entity_id)
    
    hass.services.async_register(DOMAIN, "write_register", handle_write_register)
    
async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator = hass.data[DOMAIN].pop(entry.entry_id, None)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    if coordinator:
        hub_key = coordinator.hub_key
        # Close hub only if no other entries use it
        remaining_entries = [
            e for e in hass.config_entries.async_entries(DOMAIN)
            if e.entry_id != entry.entry_id
        ]
        if not any(
            hass.data[DOMAIN].get(e.entry_id).hub_key == hub_key
            for e in remaining_entries
            if hass.data[DOMAIN].get(e.entry_id)
        ):
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
        """Initialize the serial hub."""
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
                    self.client.close()
                except Exception as err:
                    _LOGGER.exception("Unexpected error closing Felicity connection for serial: %s", err)
            self.client = None

class FelicityTcpHub:
    """Manages a single TCP connection shared across meters."""

    def __init__(self, hass: HomeAssistant, host: str, port: int):
        """Initialize the TCP hub."""
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
                    self.client.close()
                except Exception as err:
                    _LOGGER.exception("Unexpected error closing Felicity connection for tcp: %s", err)
            self.client = None
