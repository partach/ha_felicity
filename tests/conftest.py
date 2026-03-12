"""Test configuration: mock homeassistant and related modules."""

import sys
from unittest.mock import MagicMock

# Mock Home Assistant modules so we can import ems.py without HA installed
for mod in [
    "homeassistant",
    "homeassistant.core",
    "homeassistant.config_entries",
    "homeassistant.helpers",
    "homeassistant.helpers.update_coordinator",
    "homeassistant.helpers.storage",
    "pymodbus",
    "pymodbus.exceptions",
]:
    sys.modules.setdefault(mod, MagicMock())
