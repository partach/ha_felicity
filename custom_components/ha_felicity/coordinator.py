"""Data update coordinator for SDM630 with proper async handling."""

import logging
import struct
from datetime import timedelta
from typing import Dict

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException, ConnectionException

_LOGGER = logging.getLogger(__name__)


class HA_SDM630Coordinator(DataUpdateCoordinator):
    def __init__(self, hass, client: AsyncModbusSerialClient, slave_id: int, register_map: dict):
        super().__init__(
            hass,
            _LOGGER,
            name="SDM630",
            update_interval=timedelta(seconds=10),
        )
        self.client = client  # ← Shared client
        self.slave_id = slave_id
        self.register_map = register_map
        self._address_groups = self._group_addresses(register_map)  # Use passed map

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
            _LOGGER.debug("Failed to connect to SDM630: %s", err)
            return False

    async def _async_update_data(self) -> dict:
        """Fetch all data in batched async reads."""
        if not await self._async_connect():
            raise UpdateFailed("Failed to connect to SDM630")

        new_data = {}

        try:
            for start_addr, keys in self._address_groups.items():
                count = len(keys) * 2  # 2 registers per float
                result = await self.client.read_input_registers(address=start_addr,count=count,device_id=self.slave_id)

                if result.isError():
                    raise ModbusException(f"Read error at {start_addr}: {result}")

                registers = result.registers

                for i, key in enumerate(keys):
                    
                    reg_offset = i * 2
                    reg1 = registers[reg_offset]
                    reg2 = registers[reg_offset + 1]

                    # Convert to big-endian float
                    raw = struct.pack(">HH", reg1, reg2)
                    value = struct.unpack(">f", raw)[0]

                    # Handle NaN/invalid values
                    if value is None or (value != value):  # NaN check
                        value = None
                    else:
                        precision = self.register_map[key].get("precision", 2)
                        value = round(value, precision)

                    new_data[key] = value

            return new_data

        except ConnectionException as err:
            # Force reconnect next time
            await self.client.close()
            raise UpdateFailed(f"Connection lost: {err}")

        except ModbusException as err:
            raise UpdateFailed(f"Modbus error: {err}")

        except Exception as err:
            _LOGGER.error("Unexpected error during SDM630 update: %s", err)
            raise UpdateFailed(f"Update failed: {err}")

        finally:
            # Keep connection open for next poll (async client handles it well)
            pass
