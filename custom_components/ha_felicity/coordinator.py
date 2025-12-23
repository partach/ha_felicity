"""Data update coordinator for Felicity with proper async handling."""

import logging
from datetime import timedelta
from typing import Dict
from datetime import datetime
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
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
        groups: list,
        config_entry=ConfigEntry,
        nordpool_entity: str | None = None,
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
        self.nordpool_entity = nordpool_entity
        # runtime setting (if used) 
        self._current_energy_state = None
        self._last_state_change = None
        self.current_price = None
        self.max_price = None
        self.min_price = None
        self.avg_price = None
        self.price_threshold = None
        self.config_entry = config_entry
        
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
            
    def _determine_energy_state(
        self, 
        grid_mode: str, 
        current_price: float, 
        threshold: float, 
        battery_soc: float | None,
        battery_discharge_min: float,
        battery_charge_max: float
    ) -> str:
        """Determine what energy state we should be in."""
        
        # Safety check
        if battery_soc is None:
            return "idle"
        
        # Charging logic
        if (grid_mode == "from_grid") and (current_price < threshold) and (battery_soc <= battery_charge_max):
            return "charging"
        
        # Discharging logic
        elif (grid_mode == "to_grid") and (current_price > threshold) and (battery_soc >= battery_discharge_min):
            return "discharging"
        
        # Default to idle
        return "idle"
    
    async def _transition_to_state(
        self,
        new_state: str,
        price_threshold_level: float,
        battery_charge_max: float,
        battery_discharge_min: float,
        battery_soc: float | None,
        grid_mode: str
    ) -> None:
        """Execute state transition with register writes."""

        # Get current operating mode from select entity
        operating_mode_entity = f"select.{self.config_entry.title.lower().replace(' ', '_')}_operating_mode"
        operating_mode_state = self.hass.states.get(operating_mode_entity)
        current_operating_mode = operating_mode_state.state if operating_mode_state else "unknown"
        
        # Get current econ_rule_1_enable from number entity
        # rule1_entity = f"number.{self.config_entry.title.lower().replace(' ', '_')}_economic_mode_rule_1_enable"
        # rule1_state = self.hass.states.get(rule1_entity)
        # rule1_enabled = float(rule1_state.state) if rule1_state and rule1_state.state not in ("unavailable", "unknown") else 0
        now = datetime.now()
        valid_month = now.month
        valid_day = now.day
        date_16bit = (valid_month * 256) + valid_day
        if new_state == "charging":
            _LOGGER.info(
                "🔋 STARTING CHARGE CYCLE | Price threshold level: %s | "
                "Max battery: %s%% | Current battery: %s%% | Grid-Mode: %s | Operating-Mode: %s",
                price_threshold_level, battery_charge_max, battery_soc, grid_mode, current_operating_mode
            )
            await self.async_write_register("econ_rule_1_enable", 1) # 1 is schema to charge
            await self.async_write_register("econ_rule_1_soc", int(battery_charge_max))
            await self.async_write_register("econ_rule_1_start_day", date_16bit)
            await self.async_write_register("econ_rule_1_stop_day", date_16bit)
            await self.async_write_register("econ_rule_1_voltage", 580)
        
        elif new_state == "discharging":
            _LOGGER.info(
                "⚡ STARTING DISCHARGE CYCLE | Price threshold level: %s | "
                "Min battery: %s%% | Current battery: %s%% |  Grid-Mode: %s | Operating-Mode: %s",
                price_threshold_level, battery_discharge_min, battery_soc, grid_mode, current_operating_mode
            )
            # Uncomment when ready:
            await self.async_write_register("econ_rule_1_enable", 2) # 2 is schema to discharge
            await self.async_write_register("econ_rule_1_soc", int(battery_discharge_min))
            await self.async_write_register("econ_rule_1_start_day", date_16bit)
            await self.async_write_register("econ_rule_1_stop_day", date_16bit)
            await self.async_write_register("econ_rule_1_voltage", 500)
            
        elif new_state == "idle":
            _LOGGER.info(
                "🛑 STOPPING CHARGE/DISCHARGE CYCLE | Grid-Mode: %s | Operating-Mode: %s| Previous state: %s",
                grid_mode, current_operating_mode, self._current_energy_state)
            await self.async_write_register("econ_rule_1_enable", 0) # 0 is Disable
            # dont bother with other settings, will not be used anyhow.
    
    def get_energy_state_info(self) -> dict:
        """Get current energy management state info (useful for debugging sensor)."""
        return {
            "current_state": self._current_energy_state,
            "last_change": self._last_state_change.isoformat() if self._last_state_change else None,
            "current_price": self.current_price,
            "price_threshold": self.price_threshold,
        }

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

            # === Load user settings & Nordpool every update ===
            price_threshold_level = getattr(self, "price_threshold_level", 5)
            battery_charge_max = getattr(self, "battery_charge_max_level", 100)
            battery_discharge_min = getattr(self, "battery_discharge_min_level", 20)
            grid_mode = getattr(self, "grid_mode", "off") 

            # Update Nordpool price
            self.max_price = getattr(self, "max_price", None)
            self.min_price = getattr(self, "min_price", None)
            self.avg_price = getattr(self, "avg_price", None)
            self.price_threshold = getattr(self, "price_threshold", None)
            if self.nordpool_entity:
                price_state = self.hass.states.get(self.nordpool_entity)
                if price_state and price_state.state not in ("unavailable", "unknown"):
                    try:
                        self.current_price = float(price_state.state)
                        setattr(self, "current_price", self.current_price)
                        attrs = price_state.attributes

                        # Define possible attribute names for each value (add more if needed)
                        max_names = ["max", "max_price", "Max price", "max price", "Max"]  # Priority order
                        min_names = ["min", "min_price", "Min price", "min price", "Min"]
                        avg_names = ["average", "average_price", "avg_price", "Avg price", "Average", "Avg", "avg"]
                        def get_attr(attrs, names, default=None):
                            for name in names:
                                value = attrs.get(name)
                                if value is not None:
                                    return value
                            return default

                        self.max_price = get_attr(attrs, max_names)
                        self.min_price = get_attr(attrs, min_names)
                        self.avg_price = get_attr(attrs, avg_names)
                        
                        if self.avg_price is not None: # we need to know the baseline value to determine the logic

                            if price_threshold_level <= 5:
                                # Scale between Min (level 1) and Avg (level 5)
                                # Level 1 -> 0% progress, Level 5 -> 100% progress between Min and Avg
                                ratio = (price_threshold_level - 1) / 4.0  # (1-1)/4=0, (5-1)/4=1
                                self.price_threshold = self.min_price + (self.avg_price - self.min_price) * ratio
                            else:
                                # Scale between Avg (level 5) and Max (level 10)
                                # Level 5 -> 0% progress, Level 10 -> 100% progress between Avg and Max
                                ratio = (price_threshold_level - 5) / 5.0  # (5-5)/5=0, (10-5)/5=1
                                self.price_threshold = self.avg_price + (self.max_price - self.avg_price) * ratio
                            
                            setattr(self, "price_threshold", self.price_threshold)
                            # === DYNAMIC PRICE LOGIC ===
                            battery_soc = new_data.get("battery_capacity")
                             # Determine desired state
                            desired_state = self._determine_energy_state(
                                grid_mode=grid_mode,
                                current_price=self.current_price,
                                threshold=self.price_threshold,
                                battery_soc=battery_soc,
                                battery_discharge_min=battery_discharge_min,
                                battery_charge_max=battery_charge_max
                            )
                            
                            # Only act if state changed
                            if desired_state != self._current_energy_state:
                                await self._transition_to_state(
                                    desired_state,
                                    price_threshold_level=price_threshold_level,
                                    battery_charge_max=battery_charge_max,
                                    battery_discharge_min=battery_discharge_min,
                                    battery_soc=battery_soc,
                                    grid_mode=grid_mode
                                )
                                self._current_energy_state = desired_state
                                self._last_state_change = datetime.now()                    

                    except ValueError:
                        self.current_price = None
                        self.price_threshold = None
                else:
                    self.current_price = None
            else:
                self.current_price = None  
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
