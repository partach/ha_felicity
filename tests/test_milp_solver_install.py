"""The MILP engine installs its own LP solver (HiGHS) on Home Assistant OS.

Maintainer's diagnostics, 26 Oct 2026 (Home Assistant OS 18.3, Python 3.14,
x86_64): MILP "disabled — CBC solver binary unavailable".  pulp's bundled CBC
is a glibc program; in Home Assistant's Alpine (musl) container it cannot
start.  `pulp[cbc]` (cbcbox) ships glibc builds only; HiGHS (`highspy`) has
musl wheels and runs in-process.  A package installed by hand is gone after
the next Home Assistant update (the container is replaced), so the
integration installs it itself — through Home Assistant's own requirement
installer, at setup, only while the engine is MILP, and never fatally.
"""
from __future__ import annotations

import asyncio
import importlib.util as ilu
import pathlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

_PKG = pathlib.Path(__file__).parent.parent / "custom_components" / "ha_felicity"


def _load_init():
    for missing in ("homeassistant.components.frontend", "homeassistant.components.http"):
        sys.modules.setdefault(missing, MagicMock())
    spec = ilu.spec_from_file_location("custom_components.ha_felicity._solver_init",
                                       _PKG / "__init__.py")
    module = ilu.module_from_spec(spec)
    module.__package__ = "custom_components.ha_felicity"
    spec.loader.exec_module(module)
    return module


INIT = _load_init()


def _hass():
    hass = MagicMock()
    hass.async_add_executor_job = lambda fn, *a: asyncio.sleep(0, result=fn(*a))
    return hass


@pytest.fixture
def requirements(monkeypatch):
    mod = types.ModuleType("homeassistant.requirements")
    mod.async_process_requirements = AsyncMock()
    monkeypatch.setitem(sys.modules, "homeassistant.requirements", mod)
    return mod


def _highspy(monkeypatch, present: bool):
    real = ilu.find_spec
    monkeypatch.setattr(ilu, "find_spec",
                        lambda name, *a: (object() if present else None)
                        if name == "highspy" else real(name, *a))


@pytest.mark.asyncio
async def test_a_missing_solver_is_installed_through_home_assistant(monkeypatch, requirements):
    _highspy(monkeypatch, present=False)
    assert await INIT.async_ensure_lp_solver(_hass()) is True
    args = requirements.async_process_requirements.await_args.args
    assert args[2] == [INIT.HIGHS_REQUIREMENT] and "highspy" in INIT.HIGHS_REQUIREMENT


@pytest.mark.asyncio
async def test_an_installed_solver_is_left_alone(monkeypatch, requirements):
    _highspy(monkeypatch, present=True)
    assert await INIT.async_ensure_lp_solver(_hass()) is True
    requirements.async_process_requirements.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_install_never_stops_the_integration(monkeypatch, requirements, caplog):
    _highspy(monkeypatch, present=False)
    requirements.async_process_requirements.side_effect = RuntimeError("no wheel for armv7")
    assert await INIT.async_ensure_lp_solver(_hass()) is False
    assert any("greedy runs instead" in r.getMessage() for r in caplog.records)


def test_highspy_is_not_a_manifest_requirement():
    """A manifest requirement that cannot install stops the integration from
    loading at all — for a solver only the opt-in MILP engine uses."""
    import json
    manifest = json.loads((_PKG / "manifest.json").read_text())
    assert not any("highs" in r.lower() for r in manifest["requirements"])
