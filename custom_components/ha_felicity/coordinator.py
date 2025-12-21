"""Data update coordinator for Felicity with proper async handling."""

import logging
from datetime import timedelta
from typing import Dict

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pymodbus.client import AsyncModbusSerialClient
from pymodbus.exceptions import ModbusException, ConnectionException

_LOGGER = logging.getLogger(__name__)

class HA_FelicityCoordinator(DataUpdateCoordinator):
    """Felicity Solar Inverter Data Update Coordinator."""

    def __init__(
        self, 
        hass: HomeAssistant, 
        client: AsyncModbusSerialClient, 
        slave_id: int, 
        register_map: dict, 
        groups: list
    ):
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Felicity",
            update_interval=timedelta(seconds=10),
        )
        self.client = client
        self.slave_id = slave_id
        self.register_map = register_map
        self._address_groups = groups
        self.connected = False
        
    def _apply_scaling(self, raw: int, index: int, size: int = 1) -> int | float:
        """Apply scaling based on index and size."""
        if index == 1:  # /10 – only for size=1
            if size != 1:
                _LOGGER.warning("Index 1 (/10) used with size=%d – applying anyway", size)
            return raw / 10.0
        elif index == 2:  # /100 – only for size=1
            if size != 1:
                _LOGGER.warning("Index 2 (/100) used with size=%d – applying anyway", size)
            return raw / 100.0
        elif index == 3:  # signed
            if size == 1 and raw >= 0x8000:
                return raw - 0x10000
            elif size == 2 and raw >= 0x80000000:
                return raw - 0x100000000
            elif size == 4 and raw >= 0x8000000000000000:
                return raw - 0x10000000000000000
            return raw
        elif index == 8:  # /10 (and signed possible)
            # First make signed if needed
            if size == 1 and raw >= 0x8000:
                raw -= 0x10000
            elif size == 2 and raw >= 0x80000000:
                raw -= 0x100000000
            elif size == 4 and raw >= 0x8000000000000000:
                raw -= 0x10000000000000000
            return raw / 10.0
            return raw / 10.0
        else:
            return raw  # index 0,4,5,6,7 – raw
        
    def _group_addresses(self, reg_map: dict) -> Dict[int, list]:
        """Group consecutive register addresses to minimize requests."""
        # Note: This method returns a Dict, but _async_update_data expects a list of dicts 
        # with "start", "count", and "keys". Ensure input 'groups' matches that structure.
        addresses = sorted([(info["address"], key) for key, info in reg_map.items()])
        groups = {}
        current_start = None
        current_keys = []

        for addr, key in addresses:
            if current_start is None:
                current_start = addr
                current_keys = [key]
            elif addr == current_start + len(current_keys) * 1: 
                # NOTE: Assuming 1 register per key here based on logic? 
                # If floats take 2 registers, the logic in this helper might need adjustment 
                # to look at the 'size' of the previous key.
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
        """Connect to the Modbus client if not already connected."""
        if not self.connected:
            try:
                await self.client.connect()
                self.connected = self.client.connected
            except Exception as err:
                _LOGGER.error("Failed to connect to Felicity: %s", err)
                return False
        return self.connected
            
    async def async_write_register(self, key: str, value: int) -> bool:
        """Write to register, handling size."""
        if key not in self.register_map:
            _LOGGER.error("Unknown key %s", key)
            return False

        info = self.register_map[key]
        address = info["address"]
        size = info.get("size", 1)
        endian = info.get("endian", "big")

        if size == 1:
            reg_vals = [value]
        elif size == 2:
            high = (value >> 16) & 0xFFFF
            low = value & 0xFFFF
            reg_vals = [high, low] if endian == "big" else [low, high]
        elif size == 4:
            hh = (value >> 48) & 0xFFFF
            hl = (value >> 32) & 0xFFFF
            lh = (value >> 16) & 0xFFFF
            ll = value & 0xFFFF
            if endian == "big":
                reg_vals = [hh, hl, lh, ll]
            else:
                reg_vals = [ll, lh, hl, hh]
        else:
            _LOGGER.error("Unsupported size %d for write to %s", size, key)
            return False

        return await self.async_write_registers(address, reg_vals)

    async def async_write_registers(self, start_address: int, values: list[int]) -> bool:
        """Write multiple registers."""
        try:
            result = await self.client.write_registers(
                address=start_address, 
                values=values, 
                device_id=self.slave_id
            )
            if result.isError():
                _LOGGER.error("Write registers error at %s: %s", start_address, result)
                return False
            _LOGGER.debug("Successfully wrote registers at %s: %s", start_address, values)
            return True
        except Exception as err:
            _LOGGER.error("Exception writing registers at %s: %s", start_address, err)
            return False
            
    async def _async_update_data(self) -> dict:
        """Fetch data from Modbus."""
        if not await self._async_connect():
            raise UpdateFailed("Failed to connect to Felicity")
    
        new_data = {}
        try:
            for group in self._address_groups:
                start_addr = group["start"]
                count = group["count"]
                
                result = await self.client.read_holding_registers(
                    address=start_addr, count=count, device_id=self.slave_id
                )
                
                if result.isError():
                    _LOGGER.warning("Modbus read error at %s (skipping group): %s", start_addr, result)
                    continue  # Skip bad group – don't fail whole update
    
                registers = result.registers
                pos = 0
                for key in group["keys"]:
                    info = self.register_map[key]
                    size = info.get("size", 1)  # 1, 2, or 4 registers
                    endian = info.get("endian", "big")  # "big" or "little"
                    index = info.get("index", 0)
                    precision = info.get("precision", 0)
    
                    if pos + size > len(registers):
                        _LOGGER.warning(
                            "Not enough registers for %s (need %d, have %d from pos %d)", 
                            key, size, len(registers) - pos, pos
                        )
                        break
    
                    reg_vals = registers[pos:pos + size]
                    pos += size
    
                    # Unpack to raw integer
                    raw = 0
                    if size == 1:
                        raw = reg_vals[0]
                    elif size == 2:
                        high, low = reg_vals
                        if endian == "big":
                            raw = (high << 16) | low
                        else:
                            raw = (low << 16) | high
                    elif size == 4:
                        if endian == "big":
                            raw = (reg_vals[0] << 48) | (reg_vals[1] << 32) | (reg_vals[2] << 16) | reg_vals[3]
                        else:
                            raw = (reg_vals[3] << 48) | (reg_vals[2] << 32) | (reg_vals[1] << 16) | reg_vals[0]
                    else:
                        _LOGGER.warning("Unsupported size %d for %s", size, key)
                        continue
    
                    # Apply scaling
                    value = self._apply_scaling(raw, index, size)
    
                    # Round if float
                    if isinstance(value, float):
                        value = round(value, precision)
    
                    new_data[key] = value
    
            return new_data

        except ConnectionException as err:
            self.connected = False
            await self.client.close()
            raise UpdateFailed(f"Connection lost: {err}")
        except ModbusException as err:
            raise UpdateFailed(f"Modbus error: {err}")
        except Exception as err:
            _LOGGER.error("Unexpected error during Felicity update: %s", err)
            raise UpdateFailed(f"Update failed: {err}")
