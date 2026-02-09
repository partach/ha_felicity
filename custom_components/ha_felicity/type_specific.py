import logging
from typing import Any
from .const import INVERTER_MODEL_TREX_FIVE, INVERTER_MODEL_TREX_TEN, INVERTER_MODEL_TREX_FIFTY
_LOGGER = logging.getLogger(__name__)

class TypeSpecificHandler:
    def __init__(
        self, 
        client: Any, 
        slave_id: int, 
        inverter_model: str,
        register_map: dict,
    ):
        self._inverter_model = inverter_model
        self.client = client
        self.slave_id = slave_id
        self.register_map = register_map

    def determine_battery_voltage(self, data: dict) -> int | float | None:
        if self._inverter_model in (INVERTER_MODEL_TREX_FIVE, INVERTER_MODEL_TREX_TEN):
            voltage = data.get("battery_voltage")
            if voltage is not None:
                return voltage
            _LOGGER.debug("battery_voltage missing on 5/10K model")
            return None
      
        elif self._inverter_model == INVERTER_MODEL_TREX_FIFTY:
            bat1 = data.get("bat1_voltage")
            bat2 = data.get("bat2_voltage")
      
            if bat1 is not None:
                return bat1 
            elif bat2 is not None:
                return bat2 
            else:
                _LOGGER.debug("Neither bat1_voltage nor bat2_voltage available")
                return None
    
    def determine_rule_power(self, data: dict) -> int | None:
        power = data.get("econ_rule_1_power")
        if power is not None:
            if self._inverter_model in (INVERTER_MODEL_TREX_FIVE, INVERTER_MODEL_TREX_TEN):
                return round(power / 1000) # these use Watts
            elif self._inverter_model == INVERTER_MODEL_TREX_FIFTY:
                return round(power) # these use kW
        _LOGGER.debug("econ_rule_1_power not found / is None")
        return None

    def determine_operational_mode(self, data: dict) -> str | None:
        """
          for trex-10   0: General mode (self-generation and self-use, priority to load power supply)
                        1: Backup mode (grid-connected battery does not discharge, PV is charged first)
                        2: Economic mode (time-of-use electricity price/scheduled charging and discharging)
          for trex-50   System Mode (0 Selling Mode, 1 Zero Export To Load, 2 Zero Export To CT)
                        Zero Export To Load Sell Enable (0 Disabled, 1 Enabled)
                        Zero Export To CT Sell Enable (0 Disabled, 1 Enabled)
                        Zero-export mode selection (0 CT, 1 Meter)
        """
        if self._inverter_model in (INVERTER_MODEL_TREX_FIVE, INVERTER_MODEL_TREX_TEN):
            mode = data.get("operating_mode", "?")
            return mode # should be textual as it is a select
        elif self._inverter_model == INVERTER_MODEL_TREX_FIFTY:
            mode = data.get("system_mode", "?")
            loadToSell = data.get("zero_export_to_load_sell_enable", "?")
            CTtoSell = data.get("zero_export_to_ct_sell_enable", "?")
            modeSelection = data.get("zero_export_mode_selection", "?")
            return f"{mode} (LtS:{loadToSell},CTtS:{CTtoSell},Sel:{modeSelection})"
        _LOGGER.debug("Unable to determine operational mode")
        return None

    def determine_max_amperage(self, data: dict) -> float:
        if self._inverter_model in (INVERTER_MODEL_TREX_FIVE, INVERTER_MODEL_TREX_TEN):
            phase_1 = data.get("ac_input_current", 0.0)
            phase_2 = data.get("ac_input_current_l2", 0.0)
            phase_3 = data.get("ac_input_current_l3", 0.0)
            return max(phase_1, phase_2, phase_3)
        elif self._inverter_model == INVERTER_MODEL_TREX_FIFTY:
            phase_1 = data.get("phase_a_ct_current", 0.0)
            phase_2 = data.get("phase_b_ct_current", 0.0)
            phase_3 = data.get("phase_c_ct_current", 0.0)
            return max(phase_1, phase_2, phase_3)

        _LOGGER.debug("max current not found / is None")
        return None
        
    def determine_battery_soc(self, data: dict) -> int | float | None:
        """
        Determine the representative battery SOC based on model.
        For 10K: single battery SOC.
        For 50K:
          - Both available → return the minimum (most conservative)
          - Only one available → return that one
          - Neither → return None
        """
        if self._inverter_model in (INVERTER_MODEL_TREX_FIVE, INVERTER_MODEL_TREX_TEN):
            soc = data.get("battery_capacity")
            if soc is not None:
                return soc
            _LOGGER.debug("battery_capacity missing on 10K model")
            return None
        
        elif self._inverter_model == INVERTER_MODEL_TREX_FIFTY:
            bat1 = data.get("bat1_soc")
            bat2 = data.get("bat2_soc")
        
            # Case 1: Both batteries report a value → return the minimum
            if bat1 is not None and bat2 is not None and bat2 != 0:
                min_soc = min(bat1, bat2)
                _LOGGER.debug("Dual battery SOC: bat1=%.1f%%, bat2=%.1f%% → using minimum %.1f%%",
                             bat1, bat2, min_soc)
                return min_soc
        
            # Case 2: Only one battery has a value → use that one
            if bat1 is not None:
                _LOGGER.debug("Only bat1_soc available: %.1f%% (bat2 missing)", bat1)
                return bat1
        
            if bat2 is not None:
                _LOGGER.debug("Only bat2_soc available: %.1f%% (bat1 missing)", bat2)
                return bat2
        
            # Case 3: Neither has a value
            _LOGGER.debug("Neither bat1_soc nor bat2_soc available on 50K model")
            return None
    
        _LOGGER.warning("Unsupported model for battery SOC: %s", self._inverter_model)
        return None
              
    async def write_type_specific_register(self, register_name: str, value: int) -> None:
        """
        Model-specific write behavior.
        If a handler exists and handles it → done.
        Otherwise → fallback to standard async_write_register.
        """
        handlers = {
            "econ_rule_1_enable":    self._handle_econ_rule_1_enable,
            "econ_rule_1_start_day": self._handle_rule_1_start_day,
            "econ_rule_1_stop_day":  self._handle_rule_1_stop_day,
            "econ_rule_1_power" : self._handle_econ_rule_1_power,
            "operating_mode" :  self._handle_operating_mode,
        }
    
        handler = handlers.get(register_name)
        if handler:
            handled = await handler(value)
            if handled:
                _LOGGER.debug("Special handling applied for %s (model %s)", register_name, self._inverter_model)
                return
    
        # Fallback: normal write
        _LOGGER.debug("Using standard write for %s (no special handler or not handled)", register_name)
        await self.async_write_register(register_name, value)
    
      
    async def _handle_rule_1_start_day(self, value: int) -> bool:
        """Handle writing start day register (model-specific)."""
        if self._inverter_model in (INVERTER_MODEL_TREX_FIVE, INVERTER_MODEL_TREX_TEN):
            await self.async_write_register("econ_rule_1_start_day", value)
            return True
    
        elif self._inverter_model == INVERTER_MODEL_TREX_FIFTY:
            # Register not used / not present → silently ignore (no error)
            _LOGGER.debug("Ignoring econ_rule_1_start_day on 50K model (not applicable)")
            return True
    
        return False  # unknown model → fallback
    
    async def _handle_operating_mode(self, value: int) -> bool:
        """
          {"charging": 1, "discharging": 2, "idle": 0}
          Handle writing start operating mode register (model-specific).
          for trex-10   0: General mode (self-generation and self-use, priority to load power supply)
                        1: Backup mode (grid-connected battery does not discharge, PV is charged first)
                        2: Economic mode (time-of-use electricity price/scheduled charging and discharging)
          for trex-50   System Mode (0 Selling Mode, 1 Zero Export To Load, 2 Zero Export To CT)
                        Zero Export To Load Sell Enable (0 Disabled, 1 Enabled)
                        Zero Export To CT Sell Enable (0 Disabled, 1 Enabled)
                        Zero-export mode selection (0 CT, 1 Meter)
        """
        if self._inverter_model in (INVERTER_MODEL_TREX_FIVE, INVERTER_MODEL_TREX_TEN):
            if value == 0: # assume idle
                await self.async_write_register("operating_mode", 0)
            elif value in (1,2): # assume Economic mode, enabled to_grid or from_grid 
                await self.async_write_register("operating_mode", 2) # skip back-up mode for now
            else:
              _LOGGER.warning("Operating mode unknown for TREX10 series, not changing registers")
            return True
    
        elif self._inverter_model == INVERTER_MODEL_TREX_FIFTY:
            if value == 0: # assume idle, we dont control but we need to set things back if we did (but defaults no know atm)
    #            await self.async_write_register("econ_rule_1_grid_charge_enable", 0) # already happens in coordinator
                await self.async_write_register("system_mode", 2) # default no sell
            elif value == 1: # Charge and we want to control
                await self.async_write_register("system_mode", 2) # allows charge
                if self.register_map.get("eco_timeofuse",0) == 0:
                    _LOGGER.debug("Econ rule not enabled. Enabling directly via integration!")
                await self.async_write_register("eco_timeofuse", 1) # enable use of rule set
            elif value == 2: # discharge to grid and we want to control
                if self.register_map.get("eco_timeofuse",0) == 0:
                    _LOGGER.debug("Econ rule not enabled. Enabling directly via integration!")
                await self.async_write_register("zero_export_to_ct_sell_enable", 1) # Provide back to grid if needed. (to_grid or from_grid is enabled)
                await self.async_write_register("system_mode", 0)
                await self.async_write_register("eco_timeofuse", 1) # enable use of rule set
            else:    
              _LOGGER.warning("Operating mode unknown for TREX50 series, not changing registers")
            return True
    
        return False  # unknown model → fallback

    async def _handle_econ_rule_1_enable(self, value: int) -> bool:
        """
           Handle economic rule 1 enable (model-specific mapping).
           0 is off, 1 is charging, 2 is discharging
        """
        
        if self._inverter_model in (INVERTER_MODEL_TREX_FIVE, INVERTER_MODEL_TREX_TEN):
            await self.async_write_register("econ_rule_1_enable", value) # same setup as in trex-10
            return True
      
        elif self._inverter_model == INVERTER_MODEL_TREX_FIFTY:
            if value == 1:   # charging → 
                await self.async_write_register("econ_rule_1_grid_charge_enable", 1) # needs to be enabled to charge or discharge
            elif value == 2: # discharging → 
                await self.async_write_register("econ_rule_1_grid_charge_enable", 1) # needs to be enabled to charge or discharge
            else:            # idle / unknown → disable both
                await self.async_write_register("econ_rule_1_grid_charge_enable", 0)
      
            return True
      
        return False
    
    
    async def _handle_rule_1_stop_day(self, value: int) -> bool:
        """Handle writing stop day register (model-specific)."""
        if self._inverter_model in (INVERTER_MODEL_TREX_FIVE, INVERTER_MODEL_TREX_TEN):
            await self.async_write_register("econ_rule_1_stop_day", value)
            return True
    
        elif self._inverter_model == INVERTER_MODEL_TREX_FIFTY:
            _LOGGER.debug("Ignoring econ_rule_1_stop_day on 50K model (not applicable)")
            return True
    
        return False
        
    async def _handle_econ_rule_1_power(self, value: int) -> bool:
        """
          Handle writing stop power register (model-specific).
          for trex-10: in watts (also used in coordinator)
          for trex-50: in kW and Zero-export Power (assumed rule is leading and this value is independent)
        """
        if self._inverter_model in (INVERTER_MODEL_TREX_FIVE, INVERTER_MODEL_TREX_TEN):
            await self.async_write_register("econ_rule_1_power", value)
            return True
      
        elif self._inverter_model == INVERTER_MODEL_TREX_FIFTY:
            await self.async_write_register("econ_rule_1_power", int(round(value / 1000.0))) # for trex fifty it is in kW
            # in testing it seemed that this register also needs to be set to the same amount to enable charging at least. Not sure for selling...
            await self.async_write_register("grid_peak_shaving_power", int(round(value / 1000.0))) # for trex fifty it is in kW
            return True
      
        return False   
    
        
    async def async_write_register(self, key: str, value: int) -> bool:
        """Write to a register, handling size and endianness."""
        if key not in self.register_map:
            _LOGGER.error("Attempt to write unknown register key: %s with value", key, value)
            return False
    
        info = self.register_map[key]
        address = info["address"]
        size = info.get("size", 1)
        endian = info.get("endian", "big")
        index = info.get("index", 0)
    #     type = info.get("type", "") # Not yet use this, later maybe we also make sure we use the type specifics here like time8bit, etc.

        if index in (1, 8):    # /10 fields
            value = int(round(value * 10.0))   # 12.3 → 123
        elif index in (2, 9):  # /100 fields (including power factor 0.01)
            value = int(round(value * 100.0))  # 0.95 → 95
        elif index == 3:       # signed – usually no scaling needed, but cast to int
            value = int(value)
        # index 0 or other → no scaling
        
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
                device_id=int(self.slave_id)
            )
            if result.isError():
                _LOGGER.error("Write registers error at %s: %s", start_address, result)
                return False
            _LOGGER.debug("Successfully wrote registers at %s: %s", start_address, values)
            return True
        except Exception as err:
            _LOGGER.error("Exception writing registers at %s: %s", start_address, err)
            return False
