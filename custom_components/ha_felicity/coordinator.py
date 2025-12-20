"""Data update coordinator for Felicity with proper async handling."""

import logging
import struct
from datetime import timedelta
from typing import Dict

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException, ConnectionException

_LOGGER = logging.getLogger(__name__)
from .const import _REGISTER_GROUPS

class HA_FelicityCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, client: AsyncModbusSerialClient, slave_id: int, register_map: dict):
        super().__init__(
            hass,
            _LOGGER,
            name="Felicity",
            update_interval=timedelta(seconds=10),
        )
        self.client = client  # ← Shared client
        self.slave_id = slave_id
        self.register_map = register_map
        self._address_groups = _REGISTER_GROUPS  # NEW: Use our predefined groups (instead of _group_addresses)
        
    @staticmethod
    def _apply_scaling(value: int, index: int) -> int | float:
        """Apply scaling based on index from register dict."""
        if index == 1:  # /10
            return value / 10.0
        if index == 2:  # /100
            return value / 100.0
        if index == 3:  # signed 16-bit
            if value >= 0x8000:
                return value - 0x10000
            return value
        # Add more if needed later (e.g. index 4 for high/low – but that's combined)
        return value  # default: raw
        
    def _group_addresses(self, reg_map: dict) -> Dict[int, list]:
        """Group consecutive register addresses to minimize requests."""
        addresses = sorted([(info["address"], key) for key, info in reg_map.items()])
        groups = {}
        current_start = None
        current_keys = []

        for addr, key in addresses:
            if current_start is None:
                current_start = addr
                current_keys = [key]
            elif addr == current_start + len(current_keys) * 2:  # Consecutive (2 regs per float)
                current_keys.append(key)
            else:
                # Save previous group
                groups[current_start] = current_keys
                current_start = addr
                current_keys = [key]

        # Save last group
        if current_start is not None:
            groups[current_start] = current_keys

        return groups

    async def _async_connect(self) -> bool:
        """Connect to the device."""
        try:
            if not self.client.connected:
                await self.client.connect()
            return self.client.connected
        except Exception as err:
            _LOGGER.debug("Failed to connect to Felicity: %s", err)
            return False

async def _async_update_data(self) -> dict:
        if not await self._async_connect():
            raise UpdateFailed("Failed to connect to Felicity")

        new_data = {}
        try:
            for group in self._address_groups:  # NEW: Loop over predefined groups
                start_addr = group["start"]
                count = group["count"]
                result = await self.client.read_input_registers(address=start_addr, count=count, device_id=self.slave_id)
                if result.isError():
                    raise ModbusException(f"Read error at {start_addr}: {result}")

                registers = result.registers
                keys = group["keys"]
                for i, key in enumerate(keys):
                    info = self.register_map[key]
                    index = info.get("index", 0)
                    precision = info.get("precision", 0)
                    # Assume most are floats (your original logic) – fallback for raw/single
                    if index in [1, 2, 3] and len(registers) >= (i * 2 + 2):  # Float path
                        reg_offset = i * 2
                        reg1 = registers[reg_offset]
                        reg2 = registers[reg_offset + 1]
                        raw = struct.pack(">HH", reg1, reg2)
                        value = struct.unpack(">f", raw)[0]
                        if value != value:  # NaN
                            value = None
                        else:
                            value = round(value, precision)
                    else:
                        # Raw/single-word (faults, modes, etc.)
                        reg_offset = i
                        value = registers[reg_offset]
                        value = self._apply_scaling(value, index)
                        if isinstance(value, float):
                            value = round(value, precision)
                    new_data[key] = value

            return new_data

        except ConnectionException as err:
            await self.client.close()
            raise UpdateFailed(f"Connection lost: {err}")
        except ModbusException as err:
            raise UpdateFailed(f"Modbus error: {err}")
        except Exception as err:
            _LOGGER.error("Unexpected error during Felicity update: %s", err)
            raise UpdateFailed(f"Update failed: {err}")

        finally:
            # Keep connection open for next poll (async client handles it well)
            pass
