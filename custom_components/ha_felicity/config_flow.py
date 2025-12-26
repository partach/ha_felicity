"""Config flow for Felicity integration."""

import logging
from typing import Any

import serial.tools.list_ports
import voluptuous as vol
from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.core import callback
from homeassistant.components.sensor import SensorDeviceClass

from .const import (
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
    CONNECTION_TYPE_TCP,
    DEFAULT_BAUDRATE,
    DEFAULT_BYTESIZE,
    DEFAULT_PARITY,
    DEFAULT_SLAVE_ID,
    DEFAULT_STOPBITS,
    DEFAULT_TCP_PORT,
    CONF_REGISTER_SET,
    DEFAULT_REGISTER_SET,
    REGISTER_SET_BASIC,
    REGISTER_SET_BASIC_PLUS,
    REGISTER_SET_FULL,
    INVERTER_MODEL_IVGM,
    CONF_INVERTER_MODEL,
    DEFAULT_INVERTER_MODEL,
    DEFAULT_FIRST_REG,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class HA_FelicityConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Felicity."""

    VERSION = 1

    def __init__(self):
        """Initialize the config flow."""
        self._connection_type = None
        self._user_input = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        """Get the options flow for this handler."""
        return FelicityOptionsFlowHandler(config_entry)
        
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle connection type selection."""
        current_nordpool = None
        # Only access config_entry if reconfiguring (it exists)
        if getattr(self, "config_entry", None):
            current_nordpool = self.config_entry.options.get("nordpool_entity")
        if user_input is not None:
            self._connection_type = user_input[CONF_CONNECTION_TYPE]
            self._user_input = user_input
            # Store initial common options to pass forward if needed, 
            # though usually we just collect them in the final step.
            if self._connection_type == CONNECTION_TYPE_SERIAL:
                return await self.async_step_serial()
            else:
                return await self.async_step_tcp()
        data_schema = vol.Schema(
            {
                vol.Required(CONF_CONNECTION_TYPE, default=CONNECTION_TYPE_SERIAL): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=CONNECTION_TYPE_SERIAL, label="Serial (RS485)"),
                            selector.SelectOptionDict(value=CONNECTION_TYPE_TCP, label="TCP/IP (Modbus TCP)"),
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_INVERTER_MODEL, default=DEFAULT_INVERTER_MODEL): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=INVERTER_MODEL_IVGM, label="T-REX-10KLP3G01"),
                            # Future models go here
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional("update_interval", default=10): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
                vol.Optional(CONF_REGISTER_SET, default=DEFAULT_REGISTER_SET): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=REGISTER_SET_BASIC, label="Basic"),
                            selector.SelectOptionDict(value=REGISTER_SET_BASIC_PLUS, label="Basic+"),
                            selector.SelectOptionDict(value=REGISTER_SET_FULL, label="Full"),
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional("nordpool_entity", default=current_nordpool): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="sensor",
                        device_class=SensorDeviceClass.MONETARY,  # Nordpool uses monetary class
                        multiple=False,
                    )
                ),

            }
        )

        return self.async_show_form(step_id="user", data_schema=data_schema)

    async def async_step_serial(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle serial connection configuration."""
        errors = {}

        # Discover serial ports
        ports = await self.hass.async_add_executor_job(serial.tools.list_ports.comports)
        port_options = [
            selector.SelectOptionDict(
                value=port.device,
                label=(
                    f"{port.device} - {port.description or 'Unknown device'}"
                    + (f" ({port.manufacturer})" if port.manufacturer else "")
                ),
            )
            for port in ports if port.device
        ]
        port_options.sort(key=lambda x: x["value"])

        data_schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="Felicity Inverter"): str,
                vol.Required(CONF_SERIAL_PORT): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=port_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_SLAVE_ID, default=DEFAULT_SLAVE_ID): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=247)
                ),
                vol.Required(CONF_BAUDRATE, default=DEFAULT_BAUDRATE): vol.In(
                    [2400, 4800, 9600, 19200, 38400]
                ),
                vol.Required(CONF_PARITY, default=DEFAULT_PARITY): vol.In(
                    ["N", "E", "O"]
                ),
                vol.Required(CONF_STOPBITS, default=DEFAULT_STOPBITS): vol.In(
                    [1, 2]
                ),
                vol.Required(CONF_BYTESIZE, default=DEFAULT_BYTESIZE): vol.In(
                    [7, 8]
                ),
            }
        )

        if user_input is not None:
            try:
                # Merge data from previous step if we stored it, or just rely on defaults/hidden fields if simpler.
                # Here we reconstruct the full config.
                final_data = {
                    CONF_CONNECTION_TYPE: CONNECTION_TYPE_SERIAL,
                    CONF_NAME: user_input[CONF_NAME],
                    CONF_SERIAL_PORT: user_input[CONF_SERIAL_PORT],
                    CONF_SLAVE_ID: user_input[CONF_SLAVE_ID],
                    CONF_BAUDRATE: user_input[CONF_BAUDRATE],
                    CONF_PARITY: user_input[CONF_PARITY],
                    CONF_STOPBITS: user_input[CONF_STOPBITS],
                    CONF_BYTESIZE: user_input[CONF_BYTESIZE],
                    # Defaults for things not asked in this step but required
                    "update_interval": 10,
                    CONF_INVERTER_MODEL: DEFAULT_INVERTER_MODEL,
                    CONF_REGISTER_SET: DEFAULT_REGISTER_SET,
                }
                
                await self._async_test_serial_connection(final_data)

                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data=final_data,
                    options={
                        "price_threshold_level": 5,
                        "battery_charge_max_level": 100,
                        "battery_discharge_min_level": 20,
                        "grid_mode": "off",
                        CONF_REGISTER_SET: self._user_input.get(CONF_REGISTER_SET, DEFAULT_REGISTER_SET),
                        "update_interval": self._user_input.get("update_interval", 10),
                        "nordpool_entity": self._user_input.get("nordpool_entity"),
                    }
                )

            except ConnectionError:
                errors["base"] = "cannot_connect"
            except ModbusException:
                errors["base"] = "read_error"
            except ValueError:
                errors["base"] = "read_error"
            except Exception as err:
                errors["base"] = "unknown"
                _LOGGER.exception("Unexpected error during Felicity serial setup: %s", err)

        return self.async_show_form(step_id="serial", data_schema=data_schema, errors=errors)

    async def async_step_tcp(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle TCP connection configuration."""
        errors = {}

        data_schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="Felicity Inverter"): str,
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_TCP_PORT): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
                vol.Required(CONF_SLAVE_ID, default=DEFAULT_SLAVE_ID): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=247)
                ),
            }
        )

        if user_input is not None:
            try:
                final_data = {
                    CONF_CONNECTION_TYPE: CONNECTION_TYPE_TCP,
                    CONF_NAME: user_input[CONF_NAME],
                    CONF_HOST: user_input[CONF_HOST],
                    CONF_PORT: user_input[CONF_PORT],
                    CONF_SLAVE_ID: user_input[CONF_SLAVE_ID],
                    "update_interval": 10,
                    CONF_INVERTER_MODEL: DEFAULT_INVERTER_MODEL,
                    CONF_REGISTER_SET: DEFAULT_REGISTER_SET,
                }

                await self._async_test_tcp_connection(final_data)

                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data=final_data,
                    options={
                        "price_threshold_level": 5,
                        "battery_charge_max_level": 100,
                        "battery_discharge_min_level": 20,
                        "grid_mode": "off",
                        CONF_REGISTER_SET: self._user_input.get(CONF_REGISTER_SET, DEFAULT_REGISTER_SET),
                        "update_interval": self._user_input.get("update_interval", 10),
                        "nordpool_entity": self._user_input.get("nordpool_entity"),
                    }
                )

            except ConnectionError:
                errors["base"] = "cannot_connect"
            except ModbusException:
                errors["base"] = "read_error"
            except ValueError:
                errors["base"] = "read_error"
            except Exception as err:
                errors["base"] = "unknown"
                _LOGGER.exception("Unexpected error during Felicity TCP setup: %s", err)

        return self.async_show_form(step_id="tcp", data_schema=data_schema, errors=errors)

    async def _async_test_serial_connection(self, data: dict[str, Any]) -> None:
        """Test serial connection to the Felicity meter."""
        client = None
        try:
            client = AsyncModbusSerialClient(
                port=data[CONF_SERIAL_PORT],
                baudrate=data[CONF_BAUDRATE],
                parity=data.get(CONF_PARITY, DEFAULT_PARITY),
                stopbits=data.get(CONF_STOPBITS, DEFAULT_STOPBITS),
                bytesize=data.get(CONF_BYTESIZE, DEFAULT_BYTESIZE),
                timeout=5,
            )
            
            await client.connect()
            if not client.connected:
                raise ConnectionError("Failed to open serial port")
    
            result = await client.read_holding_registers(
                address=DEFAULT_FIRST_REG, count=1, device_id=data[CONF_SLAVE_ID]
            )
            
            if result.isError():
                raise ModbusException(f"Modbus read error: {result}")
                
            if len(result.registers) != 1:
                raise ValueError("Invalid response: expected 1 register")
    
        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception as err:
                    _LOGGER.debug("Error closing Modbus Serial client: %s", err)

    async def _async_test_tcp_connection(self, data: dict[str, Any]) -> None:
        """Test TCP connection to the Felicity meter."""
        client = None
        try:
            client = AsyncModbusTcpClient(
                host=data[CONF_HOST],
                port=data[CONF_PORT],
                timeout=5,
            )
    
            await client.connect()
            if not client.connected:
                raise ConnectionError(f"Failed to connect to {data[CONF_HOST]}:{data[CONF_PORT]}")
    
            result = await client.read_holding_registers(
                address=DEFAULT_FIRST_REG, count=1, device_id=data[CONF_SLAVE_ID]
            )
    
            if result.isError():
                raise ModbusException(f"Modbus read error: {result}")
    
            if len(result.registers) != 1:
                # Note: Testing 1 register here for consistency with serial test
                raise ValueError("Invalid response: expected 1 register")
    
        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception as err:
                    _LOGGER.debug("Error closing Modbus TCP client: %s", err)


class FelicityOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for the integration."""

    def __init__(self, config_entry: ConfigEntry):
        """Initialize options flow."""
     #   self.config_entry = config_entry  # HA config fails if this is not removed!

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current_config = {**self.config_entry.data, **self.config_entry.options}
        # Get current values from options (with defaults)
        current_register_set = current_config.get(CONF_REGISTER_SET, DEFAULT_REGISTER_SET)
        current_interval = current_config.get("update_interval", 10)
        current_nordpool = current_config.get("nordpool_entity")
        nordpool_override = current_config.get("nordpool_override")
        
        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_REGISTER_SET,
                    default=current_register_set,
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value=REGISTER_SET_BASIC,
                                label="Basic (Essential sensors, fastest, low overhead)",
                            ),
                            selector.SelectOptionDict(
                                value=REGISTER_SET_BASIC_PLUS,
                                label="Basic+ (adds VA, PF, neutral, if you need it)",
                            ),
                            selector.SelectOptionDict(
                                value=REGISTER_SET_FULL,
                                label="Full (All registers, slowest, heavy)",
                            ),
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    "update_interval",
                    default=current_interval,
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=5, max=300),  # 5 seconds to 5 minutes
                ),
                vol.Optional("nordpool_entity", default=current_nordpool): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="sensor",
                        device_class=SensorDeviceClass.MONETARY,  # Nordpool uses monetary class
                        multiple=False,
                    )
                ),
                vol.Optional("nordpool_override", default=""): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT,  # or PASSWORD, EMAIL, etc.
                        multiline=False,  # True for textarea
                    )
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=data_schema)
