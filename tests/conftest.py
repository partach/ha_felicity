"""Shared bootstrap for the Home-Assistant-free unit tests.

WHY THIS FILE EXISTS
--------------------
`ems.py`, `milp.py`, `const.py` and the `trex_*.py` register maps are pure
Python — they import nothing from Home Assistant — so they can be unit-tested
without installing HA.  `coordinator.py` and `select.py` DO import HA, so the
tests stub those modules and then load the component by file path.

That stubbing used to be copy-pasted into every test module, including a
**hand-typed replica of `const.py`**:

    _const_mod = types.ModuleType("custom_components.ha_felicity.const")
    _const_mod.DOMAIN = "ha_felicity"
    _const_mod.INVERTER_MODEL_TREX_TEN = "TREX-10"

That replica is a duplicate of production code maintained by hand, so it drifts
the moment anyone adds a constant.  It did: `coordinator.py` grew imports for
CONF_INVERTER_MODEL, DEFAULT_INVERTER_MODEL, INVERTER_MAX_POWER_KW and three
more model ids, the two-name stub could no longer satisfy them, and

    ImportError: cannot import name 'CONF_INVERTER_MODEL' from
    'custom_components.ha_felicity.const' (unknown location)

killed collection of the WHOLE file — silently zeroing out coordinator test
coverage while the suite still reported "passed" for everything else.

The fix is to stop duplicating: `const.py` is HA-free, so load the REAL one by
file path (the same spec-loader trick `test_ems.py` uses for `ems.py`).  It can
never drift from itself.  A constant added to production is simply present.

WHAT THIS FILE DOES NOT DO
--------------------------
It deliberately does NOT define the fake HA base classes (`SelectEntity`,
`CoordinatorEntity`, `DataUpdateCoordinator`, …).  Each test module shapes those
differently — test_coordinator wants a `DataUpdateCoordinator` with a permissive
`__init__`, test_select wants a `CoordinatorEntity` that records its
coordinator — so they stay local to the module that needs them.  Everything here
uses `setdefault`, so a test module can still override any stub.
"""

from __future__ import annotations

import importlib.util as _ilu
import os
import sys
import types
from unittest.mock import MagicMock

_PKG_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "custom_components", "ha_felicity")
)

# --- 1. Stub the third-party modules the component imports -------------------
# Only modules that are NOT installed in the test environment.  `pymodbus.client`
# is included even though no test currently reaches it: `__init__.py` and
# `config_flow.py` already import `AsyncModbusSerialClient`/`AsyncModbusTcpClient`
# from it, so any test that loads one of those (or a future module split out of
# coordinator.py) would otherwise fail on an import nobody thought to stub.
for _mod in (
    "homeassistant",
    "homeassistant.components",
    "homeassistant.components.select",
    "homeassistant.components.sensor",
    "homeassistant.config_entries",
    "homeassistant.const",
    "homeassistant.core",
    "homeassistant.helpers",
    "homeassistant.helpers.entity",
    "homeassistant.helpers.entity_registry",
    "homeassistant.helpers.storage",
    "homeassistant.helpers.update_coordinator",
    "pymodbus",
    "pymodbus.client",
    "pymodbus.exceptions",
):
    sys.modules.setdefault(_mod, MagicMock())


# --- 2. Register the component as a real package -----------------------------
# `__path__` lets Python resolve the relative imports inside the component
# (`from .trex_ten import ...`) straight from the source tree.
sys.modules.setdefault("custom_components", types.ModuleType("custom_components"))
if "custom_components.ha_felicity" not in sys.modules:
    _pkg = types.ModuleType("custom_components.ha_felicity")
    _pkg.__path__ = [_PKG_ROOT]
    _pkg.__package__ = "custom_components.ha_felicity"
    sys.modules["custom_components.ha_felicity"] = _pkg


def load_component(name: str):
    """Load `custom_components/ha_felicity/<name>.py` for real, by file path.

    Registers it in `sys.modules` under its full dotted name first, so the
    module's own relative imports (and anything importing it later) resolve to
    this instance rather than re-executing it.
    """
    dotted = f"custom_components.ha_felicity.{name}"
    if dotted in sys.modules:
        return sys.modules[dotted]
    spec = _ilu.spec_from_file_location(dotted, os.path.join(_PKG_ROOT, f"{name}.py"))
    module = _ilu.module_from_spec(spec)
    module.__package__ = "custom_components.ha_felicity"
    sys.modules[dotted] = module
    spec.loader.exec_module(module)
    return module


# --- 3. Load the REAL const module (never a hand-typed replica) --------------
# const.py pulls in the four trex_*.py register maps; all five are HA-free, so
# this is a genuine import of production code, not a fixture.
const = load_component("const")
