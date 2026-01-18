import logging
from .const import INVERTER_MODEL_TREX_TEN, INVERTER_MODEL_TREX_FIFTY
_LOGGER = logging.getLogger(__name__)

async def _write_type_specific_register(self, register_name: str, value: int) -> None:
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
          # Add more here as needed:
          # "econ_rule_1_voltage":   self._handle_rule_1_voltage,
          # "econ_rule_1_power":     self._handle_rule_1_power,
          # etc.
      }
  
      handler = handlers.get(register_name)
      if handler:
          handled = await handler(value)
          if handled:
              _LOGGER.debug("Special handling applied for %s (model %s)", register_name, self.inverter_model)
              return
  
      # Fallback: normal write
      _LOGGER.debug("Using standard write for %s (no special handler or not handled)", register_name)
      await self.async_write_register(register_name, value)
  
  
  async def _handle_rule_1_start_day(self, value: int) -> bool:
      """Handle writing start day register (model-specific)."""
      if self.inverter_model == INVERTER_MODEL_TREX_TEN:
          await self.async_write_register("econ_rule_1_start_day", value)
          return True
  
      elif self.inverter_model == INVERTER_MODEL_TREX_FIFTY:
          # Register not used / not present → silently ignore (no error)
          _LOGGER.debug("Ignoring econ_rule_1_start_day on 50K model (not applicable)")
          return True
  
      return False  # unknown model → fallback
  
  
  async def _handle_rule_1_stop_day(self, value: int) -> bool:
      """Handle writing stop day register (model-specific)."""
      if self.inverter_model == INVERTER_MODEL_TREX_TEN:
          await self.async_write_register("econ_rule_1_stop_day", value)
          return True
  
      elif self.inverter_model == INVERTER_MODEL_TREX_FIFTY:
          _LOGGER.debug("Ignoring econ_rule_1_stop_day on 50K model (not applicable)")
          return True
  
      return False
      
   async def _handle_econ_rule_1_power(self, value: int) -> bool:
      """Handle writing stop day register (model-specific)."""
      if self.inverter_model == INVERTER_MODEL_TREX_TEN:
          await self.async_write_register("econ_rule_1_power", value)
          return True
  
      elif self.inverter_model == INVERTER_MODEL_TREX_FIFTY:
          await self.async_write_register("econ_rule_1_power", int(round(value / 1000.0))) # for trex fifty it is in kW
          return True
  
      return False   
  
  async def _handle_econ_rule_1_enable(self, value: int) -> bool:
      """Handle economic rule 1 enable (model-specific mapping)."""
      if self.inverter_model == INVERTER_MODEL_TREX_TEN:
          await self.async_write_register("econ_rule_1_enable", value)
          return True
  
      elif self.inverter_model == INVERTER_MODEL_TREX_FIFTY:
          if value == 1:   # charging → prefer grid
              await self.async_write_register("econ_rule_1_grid_charge_enable", 1)
             # await self.async_write_register("econ_rule_1_gen_charge_enable", 0)
          elif value == 2: # discharging → prefer generator
              await self.async_write_register("econ_rule_1_grid_charge_enable", 0)
             # await self.async_write_register("econ_rule_1_gen_charge_enable", 1)
          else:            # idle / unknown → disable both
              await self.async_write_register("econ_rule_1_grid_charge_enable", 0)
             # await self.async_write_register("econ_rule_1_gen_charge_enable", 0)
  
          return True
    
        return False
