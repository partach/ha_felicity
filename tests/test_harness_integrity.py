"""Guards on the TEST HARNESS itself.

The bug these exist for was invisible: `tests/test_coordinator.py` stubbed
`const` with a hand-typed two-name replica, production grew more constants, the
stub could no longer satisfy `coordinator.py`'s import, and the entire file
stopped collecting.  pytest reported a collection error — but the rest of the
suite still said "passed", so coordinator coverage silently went to ZERO and
stayed there.

A harness that can quietly stop testing things is worse than no harness, so
these tests check the harness, not the EMS.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys

# Read the harness's REGISTERED state rather than importing conftest: this is
# exactly what `coordinator.py` and friends see when they do `from .const
# import ...`, so asserting on it tests the thing that actually matters.
const = sys.modules["custom_components.ha_felicity.const"]
_PKG_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "custom_components", "ha_felicity")
)


def _load(name: str):
    """Load a component module by file path (mirrors conftest.load_component)."""
    dotted = f"custom_components.ha_felicity.{name}"
    if dotted in sys.modules:
        return sys.modules[dotted]
    spec = importlib.util.spec_from_file_location(
        dotted, os.path.join(_PKG_ROOT, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "custom_components.ha_felicity"
    sys.modules[dotted] = module
    spec.loader.exec_module(module)
    return module


def _const_imports(filename: str) -> list[str]:
    """Names a component module imports via `from .const import ...`."""
    path = os.path.join(_PKG_ROOT, filename)
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "const" and node.level == 1:
            names.extend(alias.name for alias in node.names)
    return names


def test_const_is_the_real_module_not_a_stub():
    """The harness must load production `const.py`, never a replica.

    A replica is a hand-maintained duplicate of production code: it drifts the
    moment someone adds a constant, and the failure mode is a whole test file
    silently disappearing from the run.
    """
    assert hasattr(const, "__file__"), (
        "const has no __file__ — it is a stub/MagicMock, not the real module"
    )
    assert os.path.normpath(const.__file__) == os.path.join(
        _PKG_ROOT, "const.py"
    ), f"const loaded from an unexpected path: {const.__file__}"
    # A stub would satisfy a couple of names; the real thing carries the lot.
    assert len(const.MODEL_REGISTRY) >= 4, "MODEL_REGISTRY looks stubbed"


def test_every_const_name_the_components_import_actually_exists():
    """Static check across the whole component: no module imports a constant
    that `const.py` does not define.

    This is what would have caught the original drift on the FIRST commit that
    added `CONF_INVERTER_MODEL` to coordinator.py, instead of after the import
    error had been masking lost coverage for weeks.  It also catches a genuine
    production typo (importing a name that was renamed or never added).
    """
    modules = sorted(
        f for f in os.listdir(_PKG_ROOT)
        if f.endswith(".py") and f not in ("const.py", "__init__.py")
    )
    missing: list[str] = []
    checked = 0
    for filename in modules:
        for name in _const_imports(filename):
            checked += 1
            if not hasattr(const, name):
                missing.append(f"{filename} imports const.{name} — not defined")
    assert checked > 0, "found no `from .const import` statements — scan is broken"
    assert not missing, "\n".join(missing)


def test_component_modules_are_importable_by_the_harness():
    """Every HA-free module must load for real through the harness loader.

    These are the modules the EMS tests depend on being genuine rather than
    mocked; if one grows a Home Assistant import, this fails loudly instead of
    the suite quietly testing a stub.
    """
    for name in ("const", "ems", "milp", "trex_five", "trex_ten",
                 "trex_twenty_five", "trex_fifty"):
        module = _load(name)
        assert getattr(module, "__file__", None), f"{name} did not load as a real module"
