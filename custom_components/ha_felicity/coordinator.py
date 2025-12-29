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
        nordpool_override: str | None = None,
        update_interval: int = 10,
    ):
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Felicity",
            update_interval=timedelta(seconds=update_interval),
        )
        self.client = client
        self.slave_id = slave_id
        self.register_map = register_map
        self._address_groups = groups
        self.config_entry = config_entry
        self._last_register_set: str | None = None
        
        # Nordpool: override wins over entity
        self.nordpool_entity = nordpool_override or nordpool_entity
        
        # Runtime state
        self.connected = False
        self._current_energy_state: str | None = None
        self._last_state_change: datetime | None = None
        self._current_day: int | None = None

        # Price tracking
        self.current_price: float | None = None
        self.max_price: float | None = None
        self.min_price: float | None = None
        self.avg_price: float | None = None
        self.price_threshold: float | None = None

        
    def _apply_scaling(self, raw: int, index: int, size: int = 1) -> int | float:
        """Apply scaling based on index and size."""
        if index == 1:  # /10 – only for size=1
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
        elif index == 8: # /10 (and signed possible)
            # First make signed if needed
            if size == 1 and raw >= 0x8000:
                raw -= 0x10000
            elif size == 2 and raw >= 0x80000000:
                raw -= 0x100000000
            elif size == 4 and raw >= 0x8000000000000000:
                raw -= 0x10000000000000000
            return raw / 10.0
        else:
            return raw  # index 0,4,5,6,7 – raw

    #obsolete, think about removing.
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
        """Write to a register, handling size and endianness."""
        if key not in self.register_map:
            _LOGGER.error("Attempt to write unknown register key: %s", key)
            return False

        info = self.register_map[key]
        address = info["address"]
        size = info.get("size", 1)
        endian = info.get("endian", "big")

        if size == 1:
            values = [value]
        elif size == 2:
            high = (value >> 16) & 0xFFFF
            low = value & 0xFFFF
            values = [high, low] if endian == "big" else [low, high]
        elif size == 4:
            values = []
            for i in range(3, -1, -1):
                values.append((value >> (i * 16)) & 0xFFFF)
            if endian == "little":
                values.reverse()
        else:
            _LOGGER.error("Unsupported register size %d for key %s", size, key)
            return False

        return await self.async_write_registers(address, values)

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
            
    def _determine_energy_state(self, battery_soc: float | None) -> str:
        """Determine desired energy management state."""
        opts = self.config_entry.options

        grid_mode = opts.get("grid_mode", "off")
        if grid_mode == "off":
            _LOGGER.info("grid_mode is off, returning idle")
            return "idle"

        if battery_soc is None:
            _LOGGER.info("Battery SOC state unknown, returning idle")
            return "idle"

        if self.current_price is None or self.price_threshold is None:
            _LOGGER.info("current price or price threshold is unknown, returning idle")
            return "idle"
            
        charge_max = opts.get("battery_charge_max_level", 100)
        discharge_min = opts.get("battery_discharge_min_level", 20)
        
        if grid_mode == "from_grid" and self.current_price < self.price_threshold and battery_soc <= charge_max:
            return "charging"
        if grid_mode == "to_grid" and self.current_price > self.price_threshold and battery_soc >= discharge_min:
            return "discharging"
        
        return "idle"

# Get current operating mode from select entity if needed
# operating_mode_entity = f"select.{self.config_entry.title.lower().replace(' ', '_')}_operating_mode"
# operating_mode_state = self.hass.states.get(operating_mode_entity)
# current_operating_mode = operating_mode_state.state if operating_mode_state else "unknown"
    
    async def _transition_to_state(self, new_state: str) -> None:
        """Apply state change via economic rule 1."""
        opts = self.config_entry.options
        now = datetime.now()
        date_16bit = (now.month << 8) | now.day

        power_level = opts.get("power_level", 5)
        voltage_level = opts.get("voltage_level", 58) # safe but how will it go with high voltage systems?
        soc_limit = (
            opts.get("battery_charge_max_level", 100)
            if new_state == "charging"
            else opts.get("battery_discharge_min_level", 20)
        )

        enable_value = {"charging": 1, "discharging": 2, "idle": 0}[new_state]

        _LOGGER.info(
            "Energy state → %s | Price: %.4f | Threshold: %.4f | SOC limit: %d%%",
            new_state.upper(),
            self.current_price or 0,
            self.price_threshold or 0,
            soc_limit,
        )

        await self.async_write_register("econ_rule_1_enable", enable_value)
        if new_state != "idle":
            await self.async_write_register("econ_rule_1_soc", int(soc_limit))
            await self.async_write_register("econ_rule_1_start_day", date_16bit)
            await self.async_write_register("econ_rule_1_stop_day", date_16bit)
            await self.async_write_register("econ_rule_1_voltage", int(voltage_level * 10))
            await self.async_write_register("econ_rule_1_power", int(round(power_level * 1000,0)))
    
    def get_energy_state_info(self) -> dict:
        """Get current energy management state info (useful for debugging sensor)."""
        info = {
            "current_state": self._current_energy_state,
            "last_change": self._last_state_change.isoformat() if self._last_state_change else None,
            "current_price": self.current_price,
            "price_threshold": self.price_threshold,
            "max_price": self.max_price,
            "min_price": self.min_price,
            "avg_price": self.avg_price,
        }

        # Add kWh for all Wh registers
        for key, value in self.data.items():
            info_key = self.register_map.get(key, {})
            if info_key.get("unit") == "Wh" and value is not None:
                info[f"{key}_kwh"] = round(value / 1000.0, 3)
        return info

    async def _async_update_data(self) -> dict:
        """Fetch latest data from inverter."""
        if not await self._async_connect():
            raise UpdateFailed("Cannot connect to Felicity inverter")

        new_data = {}

        try:
            for group in self._address_groups:
                start_addr = group["start"]
                count = group["count"]

                result = await self.client.read_holding_registers(
                    address=start_addr,
                    count=count,
                    device_id=self.slave_id,
                )

                if result.isError():
                    _LOGGER.warning("Read error at address %d, skipping group", start_addr)
                    continue

                registers = result.registers
                pos = 0
                for key in group["keys"]:
                    info = self.register_map[key]
                    size = info.get("size", 1)
                    endian = info.get("endian", "big")
                    index = info.get("index", 0)
                    precision = info.get("precision", 0)

                    if pos + size > len(registers):
                        _LOGGER.warning("Insufficient registers for %s", key)
                        break

                    reg_slice = registers[pos:pos + size]
                    pos += size
                    # Reconstruct raw value
                    raw = 0
                    if size == 1:
                        raw = reg_slice[0]
                    elif size == 2:
                        if endian == "big":
                            raw = (reg_slice[0] << 16) | reg_slice[1]
                        else:
                            raw = (reg_slice[1] << 16) | reg_slice[0]
                    elif size == 4:
                        if endian == "big":
                            raw = (reg_slice[0] << 48) | (reg_slice[1] << 32) | (reg_slice[2] << 16) | reg_slice[3]
                        else:
                            raw = (reg_slice[3] << 48) | (reg_slice[2] << 32) | (reg_slice[1] << 16) | reg_slice[0]
                        if index == 3 and raw >= (1 << 63):
                            raw -= (1 << 64)
                    else:
                        _LOGGER.warning("Unsupported register size %d for key %s", size, key)
                        continue

                    value = self._apply_scaling(raw, index, size)
                    if isinstance(value, float):
                        value = round(value, precision)

                    new_data[key] = value
            # dynamically check which battery system we have.
            raw_system_voltage = new_data.get("battery_voltage")
            if raw_system_voltage is not None:
                new_data["battery_nominal_voltage"] = raw_system_voltage

            # === Nordpool price update & dynamic logic ===
            if self.nordpool_entity:
                try: #when nordpool is disabled or uninstalled during runtime
                  price_state = self.hass.states.get(self.nordpool_entity)
                except Exception:
                    _LOGGER.exception("Felicity coordinator error, nordpool or override no longer available!")
                    self.nordpool_entity = None
                    self.current_price = self.min_price = self.avg_price = self.max_price = self.price_threshold = None
                    return new_data # return with what we do have
                if price_state and price_state.state not in ("unknown", "unavailable", "none"):
                    try:
                        self.current_price = float(price_state.state)
                        attrs = price_state.attributes

                        def get_attr(names):
                            for name in names:
                                val = attrs.get(name)
                                if val is not None:
                                    return val
                            return None

                        self.max_price = get_attr(["max", "max_price", "Max price", "max price"])
                        self.min_price = get_attr(["min", "min_price", "Min price", "min price"])
                        self.avg_price = get_attr(["average", "average_price", "avg_price", "Avg price", "avg"])

                        if self.avg_price is not None and self.min_price is not None and self.max_price is not None:
                            level = self.config_entry.options.get("price_threshold_level", 5)
                            if level <= 5:
                                ratio = (level - 1) / 4.0
                                self.price_threshold = self.min_price + (self.avg_price - self.min_price) * ratio
                            else:
                                ratio = (level - 5) / 5.0
                                self.price_threshold = self.avg_price + (self.max_price - self.avg_price) * ratio

                            # Midnight reset
                            now = datetime.now()
                            if self._current_day != now.day:
                                _LOGGER.info("New day detected — resetting energy state")
                                self._current_energy_state = "idle"
                                self._current_day = now.day

                            # Determine and apply new state
                            battery_soc = new_data.get("battery_capacity")
                            desired_state = self._determine_energy_state(battery_soc)

                            if desired_state != self._current_energy_state:
                                await self._transition_to_state(desired_state)
                                self._current_energy_state = desired_state
                                self._last_state_change = now
                        else:
                            _LOGGER.debug(
                                "Cannot calculate price threshold: missing data (min=%s, avg=%s, max=%s)",
                                self.min_price, self.avg_price, self.max_price
                            )
                            self.price_threshold = None

                    except ValueError:
                        self.current_price = None
                        self.price_threshold = None
            else:
                self.current_price = None
                self.price_threshold = None

            return new_data

        except ConnectionException as err:
            self.connected = False
            await self.client.close()
            raise UpdateFailed(f"Connection lost: {err}")
        except ModbusException as err:
            raise UpdateFailed(f"Modbus error: {err}")
        except Exception as err:
            _LOGGER.exception("Unexpected error in Felicity coordinator update")
            raise UpdateFailed(f"Unexpected update error: {err}")
