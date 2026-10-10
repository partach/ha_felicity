"""Coordinator day-replay harness: the REAL coordinator against a fake inverter.

WHY THIS EXISTS
---------------
Almost all of the test suite pins `ems.py`'s pure planning.  The path that turns
a plan into inverter writes — the override merge, `_determine_energy_state`,
`_transition_to_state`, the midnight rollover, `type_specific`'s per-model
register translation — had only resilience tests, and every customer report in
October 2026 landed there (see CLAUDE.md, Known Issues 8e: overrides set for
02:00–05:00 that never charged).

This harness replays hours of wall-clock time through the production code:

* a **fake clock** drives `datetime.now()` / `time.time()` inside the coordinator;
* a **fake inverter** is a Modbus register memory.  The coordinator reads it and
  writes it through the REAL `TypeSpecificHandler`, and the fake inverter turns
  what was written into battery physics — it charges only when the registers
  say so (Economic mode, rule 1 enabled, inside the rule's time window, below
  the rule's SOC), so a plan that never reaches the registers never moves the
  battery;
* a **fake Home Assistant** serves a Nordpool-shaped price entity (tomorrow's
  prices appear at 13:00), persists options, and runs executor jobs inline.

Nothing in the coordinator or type_specific is mocked.  What a test asserts on
is what a customer would see: the register writes, and the state of charge.

The coordinator is loaded as a PRIVATE copy (its own module name) against its
own HA stubs, so this file cannot be disturbed by — or disturb — the module-level
stubbing in test_coordinator.py, which replaces type_specific with a MagicMock.
"""

from __future__ import annotations

import importlib.util as _ilu
import os
import sys
import types
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

from tests.conftest import load_component

const = load_component("const")
ems = load_component("ems")
register_dump = load_component("register_dump")

_PKG_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "custom_components", "ha_felicity"))


# ---------------------------------------------------------------------------
# Loading the production modules as private copies
# ---------------------------------------------------------------------------

class UpdateFailed(Exception):
    """Stand-in for homeassistant.helpers.update_coordinator.UpdateFailed."""


class _DataUpdateCoordinator:
    def __init__(self, hass, logger, *, name, update_interval):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval
        self.data = None


class ConnectionException(Exception):
    pass


class ModbusException(Exception):
    pass


def _load_private(name: str, sys_overrides: dict):
    dotted = f"custom_components.ha_felicity._replay_{name}"
    spec = _ilu.spec_from_file_location(dotted, os.path.join(_PKG_ROOT, f"{name}.py"))
    module = _ilu.module_from_spec(spec)
    module.__package__ = "custom_components.ha_felicity"
    with patch.dict(sys.modules, sys_overrides):
        spec.loader.exec_module(module)
    return module


type_specific = _load_private("type_specific", {})

_duc = types.ModuleType("homeassistant.helpers.update_coordinator")
_duc.DataUpdateCoordinator = _DataUpdateCoordinator
_duc.UpdateFailed = UpdateFailed
_pymodbus_exc = types.ModuleType("pymodbus.exceptions")
_pymodbus_exc.ConnectionException = ConnectionException
_pymodbus_exc.ModbusException = ModbusException

coordinator = _load_private("coordinator", {
    "homeassistant.helpers.update_coordinator": _duc,
    "pymodbus.exceptions": _pymodbus_exc,
    "custom_components.ha_felicity.ems": ems,
    "custom_components.ha_felicity.type_specific": type_specific,
})


# ---------------------------------------------------------------------------
# Fake clock
# ---------------------------------------------------------------------------

class FakeClock:
    def __init__(self, start: datetime):
        self.now = start

    def timestamp(self) -> float:
        return self.now.timestamp()

    def install(self, module) -> None:
        """Point the module's `datetime` and `time` at this clock."""
        clock = self

        class _Datetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.now if tz is None else clock.now.replace(tzinfo=tz)

        module.datetime = _Datetime
        module.time = types.SimpleNamespace(
            time=clock.timestamp, monotonic=clock.timestamp)


# ---------------------------------------------------------------------------
# Fake inverter: register memory + battery physics driven by the registers
# ---------------------------------------------------------------------------

class _Response:
    def __init__(self, registers=None, error=False):
        self.registers = registers or []
        self.retries = 0
        self._error = error

    def isError(self):
        return self._error


@dataclass
class Write:
    at: datetime
    key: str
    address: int
    values: list


def _encode(info: dict, value: float) -> list[int]:
    """Inverse of the coordinator's decode, for seeding register memory."""
    index = info.get("index", 0)
    raw = value * {1: 10, 8: 10, 2: 100, 9: 100, 4: 1000}.get(index, 1)
    raw = round(raw) & ((1 << (16 * info.get("size", 1))) - 1)
    size = info.get("size", 1)
    return [(raw >> (16 * (size - 1 - i))) & 0xFFFF for i in range(size)]


class FakeInverter:
    """A Felicity inverter as seen over Modbus.

    The battery moves according to what the registers say, not what the
    coordinator intended — that gap is what the harness exists to expose.
    Only rule 1 is modelled (it is the only rule the integration drives).
    """

    def __init__(self, model: str, clock: FakeClock, *, capacity_kwh: float,
                 soc_pct: float, efficiency: float = 0.95, self_use_floor_pct: float = 10.0,
                 honour_rule_dates: bool = True):
        self.model = model
        self.clock = clock
        self.register_map = const.MODEL_REGISTRY[model]["registers"]
        self.capacity_kwh = capacity_kwh
        self.soc_pct = soc_pct
        self.efficiency = efficiency
        self.self_use_floor_pct = self_use_floor_pct
        self.honour_rule_dates = honour_rule_dates
        self.memory: dict[int, int] = {}
        self.writes: list[Write] = []
        self.connected = True
        self._by_address = {}
        for key, info in self.register_map.items():
            if "address" in info:
                self._by_address.setdefault(info["address"], key)
        self.grid_import_kwh = 0.0
        self.grid_export_kwh = 0.0
        self.grid_cost = 0.0                  # paid for import
        self.grid_revenue = 0.0               # earned for export (same price)
        self.pv_today_kwh = 0.0
        self._pv_day = clock.now.date()
        self.last_grid_kw = 0.0               # signed: + import, - export
        # Factory-ish defaults: rule 1 all day, every weekday, General mode.
        self.set("econ_rule_1_start_time", 0)
        self.set("econ_rule_1_stop_time", 0)
        if self.eco_path:
            self.set("eco_timeofuse", 0)
            self.set("eco_effectiveweek", 0x7F)
            self.set("system_mode", 2)
        else:
            self.set("operating_mode", 0)
            self.set("econ_rule_1_effective_week", 0x7F)
        self._sync_soc()

    # -- register memory ----------------------------------------------------
    @property
    def eco_path(self) -> bool:
        return self.model in const.ECO_TIMEOFUSE_MODELS

    def set(self, key: str, value: float) -> None:
        info = self.register_map[key]
        for i, word in enumerate(_encode(info, value)):
            self.memory[info["address"] + i] = word

    def get(self, key: str):
        info = self.register_map.get(key)
        if info is None:
            return None
        words = [self.memory.get(info["address"] + i, 0) for i in range(info.get("size", 1))]
        raw = register_dump.combine_words(words, info.get("endian", "big"))
        return register_dump.apply_scaling(raw, info.get("index", 0), info.get("size", 1))

    def _sync_soc(self) -> None:
        for key in ("battery_capacity", "bat1_soc"):
            if key in self.register_map:
                self.set(key, round(self.soc_pct, 1))

    # -- pymodbus client surface -------------------------------------------
    async def connect(self):
        self.connected = True
        return True

    def close(self):
        self.connected = False

    async def read_holding_registers(self, address, count, device_id=1):
        return _Response([self.memory.get(address + i, 0) for i in range(count)])

    async def write_registers(self, address, values, device_id=1):
        for i, word in enumerate(values):
            self.memory[address + i] = int(word) & 0xFFFF
        self.writes.append(Write(self.clock.now, self._by_address.get(address, hex(address)),
                                 address, list(values)))
        return _Response()

    # -- what the inverter does with those registers ------------------------
    def _rule_in_window(self) -> bool:
        now = self.clock.now
        start, stop = int(self.get("econ_rule_1_start_time")), int(self.get("econ_rule_1_stop_time"))
        if start != stop:
            minute = now.hour * 60 + now.minute
            lo, hi = (start >> 8) * 60 + (start & 0xFF), (stop >> 8) * 60 + (stop & 0xFF)
            inside = lo <= minute < hi if lo < hi else (minute >= lo or minute < hi)
            if not inside:
                return False
        week_key = "eco_effectiveweek" if self.eco_path else "econ_rule_1_effective_week"
        if not int(self.get(week_key)) & (1 << (now.isoweekday() % 7)):
            return False
        if self.honour_rule_dates and "econ_rule_1_start_day" in self.register_map:
            d0, d1 = int(self.get("econ_rule_1_start_day")), int(self.get("econ_rule_1_stop_day"))
            if d0 and d1:
                today = (now.month << 8) | now.day
                if not d0 <= today <= d1:
                    return False
        return True

    def action(self) -> str:
        """What the inverter does, from the registers alone.

        'charge' / 'discharge' while rule 1 drives the battery; 'hold' when the
        rule is active but the battery has reached the rule's SOC (the house
        runs on grid, the battery rests); 'self_use' otherwise (General mode,
        rule off or outside its window: the battery covers the house).
        """
        if self.eco_path:
            if self.get("eco_timeofuse") != 1 or not self._rule_in_window():
                return "self_use"
            if self.get("econ_rule_1_grid_charge_enable") == 1:
                return "charge" if self.soc_pct < self.get("econ_rule_1_soc") else "hold"
            selling = (self.get("system_mode") == 0
                       and self.get("zero_export_to_ct_sell_enable") == 1
                       and self.get("econ_rule_1_sell_enable") in (None, 1))
            if selling:
                return "discharge" if self.soc_pct > self.get("econ_rule_1_soc") else "hold"
            return "self_use"
        if self.get("operating_mode") != 2 or not self._rule_in_window():
            return "self_use"
        enable = self.get("econ_rule_1_enable")
        if enable == 1:
            return "charge" if self.soc_pct < self.get("econ_rule_1_soc") else "hold"
        if enable == 2:
            return "discharge" if self.soc_pct > self.get("econ_rule_1_soc") else "hold"
        return "self_use"

    def rule_power_kw(self) -> float:
        power = self.get("econ_rule_1_power") or 0
        unit_w = self.model in const.SETPOINT_WATT_MODELS
        return power / 1000.0 if unit_w else float(power)

    def advance(self, seconds: float, load_kw: float, pv_kw: float, price: float) -> str:
        """Move energy for `seconds` according to the registers.

        PV serves the house first.  A PV surplus charges the battery (up to
        full) and the rest is exported — except while rule 1 discharges, when
        the surplus is exported alongside.  Rule-1 charge adds grid energy up
        to the rule SOC; rule-1 discharge delivers its power to the house and
        exports the rest, down to the rule SOC.  Self-use covers the house
        from the battery down to `self_use_floor_pct`.  Inverter power limits
        are not modelled.
        """
        hours = seconds / 3600.0
        act = self.action()
        cap = self.capacity_kwh
        bat = self.soc_pct / 100.0 * cap
        net = (load_kw - pv_kw) * hours              # > 0: house short, < 0: PV surplus
        imp = exp = 0.0

        def absorb_surplus(bat):
            """PV surplus into the battery, rest exported."""
            if net >= 0:
                return bat, 0.0
            stored = min(-net * self.efficiency, max(0.0, cap - bat))
            return bat + stored, -net - stored / self.efficiency

        if act == "charge":
            limit = self.get("econ_rule_1_soc") / 100.0 * cap
            stored = min(self.rule_power_kw() * hours * self.efficiency, max(0.0, limit - bat))
            bat += stored
            imp += stored / self.efficiency + max(0.0, net)
            bat, exp = absorb_surplus(bat)
        elif act == "discharge":
            floor = self.get("econ_rule_1_soc") / 100.0 * cap
            drawn = min(self.rule_power_kw() * hours, max(0.0, bat - floor))
            bat -= drawn
            balance = drawn * self.efficiency - net     # what is left after the house
            exp, imp = max(0.0, balance), max(0.0, -balance)
        elif act == "hold":
            imp += max(0.0, net)
            bat, exp = absorb_surplus(bat)
        elif net > 0:                                   # self-use, house short
            floor = self.self_use_floor_pct / 100.0 * cap
            drawn = min(net, max(0.0, bat - floor))
            bat -= drawn
            imp += net - drawn
        else:                                           # self-use, PV surplus
            bat, exp = absorb_surplus(bat)

        self.grid_import_kwh += imp
        self.grid_export_kwh += exp
        self.grid_cost += imp * price
        self.grid_revenue += exp * price
        self.last_grid_kw = (imp - exp) / hours if hours else 0.0
        if self.clock.now.date() != self._pv_day:
            self._pv_day, self.pv_today_kwh = self.clock.now.date(), 0.0
        self.pv_today_kwh += pv_kw * hours
        self.soc_pct = max(0.0, min(100.0, bat / cap * 100.0))
        self._sync_soc()
        self._publish_telemetry(pv_kw)
        return act

    def _publish_telemetry(self, pv_kw: float) -> None:
        """What the coordinator reads back: grid power (anti-conflict guard),
        PV power and PV energy today (PV confidence)."""
        if self.eco_path:
            self.set("total_grid_power", round(self.last_grid_kw, 2))       # kW, signed
            self.set("pv1_power", round(pv_kw, 2))                          # kW
            self.set("pv1_day_energy", round(self.pv_today_kwh, 1))         # kWh
        else:
            self.set("total_ac_input_power", round(self.last_grid_kw * 1000))   # W, signed
            self.set("pv_power_conversion", round(pv_kw * 1000))                # W
            self.set("pv_generated_energy_day", round(self.pv_today_kwh * 1000))  # Wh


# ---------------------------------------------------------------------------
# Fake Home Assistant
# ---------------------------------------------------------------------------

class _State:
    def __init__(self, state, attributes):
        self.state = state
        self.attributes = attributes


class FakeConfigEntry:
    def __init__(self, model: str, options: dict):
        self.entry_id = "replay_entry"
        self.title = "Felicity Replay"
        self.data = {const.CONF_INVERTER_MODEL: model}
        self.options = dict(options)


class FakeHass:
    PRICE_ENTITY = "sensor.nordpool_replay"
    FORECAST_ENTITY = "sensor.pv_forecast_today_replay"
    FORECAST_TOMORROW_ENTITY = "sensor.pv_forecast_tomorrow_replay"
    PRICES_PUBLISHED_HOUR = 13

    def __init__(self, clock: FakeClock, prices_for: callable, forecast_kw: callable | None = None):
        self.clock = clock
        self.prices_for = prices_for
        self.forecast_kw = forecast_kw          # datetime -> kW, or None: no forecast
        self.extra_states: dict[str, _State] = {}
        self.services = MagicMock()
        self.config_entries = MagicMock()
        self.config_entries.async_update_entry.side_effect = self._update_entry
        self.states = MagicMock()
        self.states.get.side_effect = self._get_state

    @staticmethod
    def _update_entry(entry, options=None, **_kw):
        if options is not None:
            entry.options = dict(options)
        return True

    async def async_add_executor_job(self, func, *args):
        return func(*args)

    def _get_state(self, entity_id):
        if entity_id == self.PRICE_ENTITY:
            return self._price_state()
        if self.forecast_kw and entity_id == self.FORECAST_ENTITY:
            return self._forecast_state(self.clock.now.date(), with_hours=True)
        if self.forecast_kw and entity_id == self.FORECAST_TOMORROW_ENTITY:
            return self._forecast_state(self.clock.now.date() + timedelta(days=1))
        return self.extra_states.get(entity_id)

    def _forecast_hours(self, d: date) -> dict[datetime, float]:
        """Hourly forecast (kWh in each hour), integrated over 5-minute steps."""
        out = {}
        for h in range(24):
            t0 = datetime(d.year, d.month, d.day, h)  # noqa: DTZ001 - naive local, as the coordinator
            out[t0] = sum(self.forecast_kw(t0 + timedelta(minutes=m)) for m in range(0, 60, 5)) / 12
        return out

    def _forecast_state(self, d: date, with_hours: bool = False) -> _State:
        """Forecast.Solar shape: state = the day's kWh, `wh_hours` per hour."""
        hours = self._forecast_hours(d)
        attrs = {}
        if with_hours:
            both = {**hours, **self._forecast_hours(d + timedelta(days=1))}
            attrs["wh_hours"] = {t.isoformat(): round(kwh * 1000) for t, kwh in both.items() if kwh}
        return _State(str(round(sum(hours.values()), 2)), attrs)

    def _price_state(self) -> _State:
        now = self.clock.now
        today = self.prices_for(now.date())
        tomorrow = (self.prices_for(now.date() + timedelta(days=1))
                    if now.hour >= self.PRICES_PUBLISHED_HOUR else [])
        slot = int((now.hour * 60 + now.minute) / (1440 / len(today)))
        return _State(str(today[slot]), {
            "today": today, "tomorrow": tomorrow,
            "min": min(today), "max": max(today),
            "average": sum(today) / len(today),
        })


class _FakeStore:
    def __init__(self, *args, **kwargs):
        self.saved = None

    async def async_load(self):
        return None

    async def async_save(self, data):
        self.saved = data


# ---------------------------------------------------------------------------
# The replay
# ---------------------------------------------------------------------------

@dataclass
class Tick:
    at: datetime
    soc: float
    state: str | None
    planned: str | None
    inverter: str
    price: float = 0.0
    pv_kw: float = 0.0
    grid_kw: float = 0.0                  # + import, - export


@dataclass
class DayReplay:
    """One installation, replayed tick by tick through the real coordinator."""

    model: str
    start: datetime
    options: dict
    prices_for: callable                     # date -> list[float] (24/48/96 slots)
    capacity_kwh: float = 20.0
    soc_pct: float = 50.0
    load_kw: callable = lambda t: 0.6        # house load, kW, as a function of time
    pv_kw: callable = lambda t: 0.0          # what the panels actually produce
    forecast_kw: callable | None = None      # what the forecast entity says (None: no entity)
    honour_rule_dates: bool = True
    ticks: list[Tick] = field(default_factory=list)

    def __post_init__(self):
        self.clock = FakeClock(self.start)
        self.clock.install(coordinator)
        options = {"battery_capacity_kwh": self.capacity_kwh, **self.options}
        self.entry = FakeConfigEntry(self.model, options)
        self.hass = FakeHass(self.clock, self.prices_for, self.forecast_kw)
        if self.forecast_kw:
            self.entry.options["forecast_entity_tomorrow"] = FakeHass.FORECAST_TOMORROW_ENTITY
        self.inverter = FakeInverter(self.model, self.clock, capacity_kwh=self.capacity_kwh,
                                     soc_pct=self.soc_pct,
                                     honour_rule_dates=self.honour_rule_dates)
        model_config = const.MODEL_REGISTRY[self.model]
        self.coordinator = coordinator.HA_FelicityCoordinator(
            hass=self.hass, client=self.inverter, slave_id=1,
            register_map=model_config["registers"],
            groups=model_config["register_groups"],
            model_combined=model_config["combined"],
            inverter_model=self.model, config_entry=self.entry,
            nordpool_entity=FakeHass.PRICE_ENTITY,
            forecast_entity=FakeHass.FORECAST_ENTITY if self.forecast_kw else None,
        )

    # -- what the card's set_slot_overrides service does --------------------
    def set_overrides(self, *, today: dict | None = None, tomorrow: dict | None = None):
        overrides = {"today": today or {}, "tomorrow": tomorrow or {}}
        self.coordinator.slot_overrides = overrides
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, "slot_overrides": overrides})

    def restore_overrides(self, *, today: dict, tomorrow: dict, set_on: date):
        """Overrides the card set on `set_on`, as found in entry.options when
        HA (re)starts — i.e. persisted earlier, then a restart."""
        overrides = {"today": dict(today), "tomorrow": dict(tomorrow),
                     "date": set_on.isoformat()}
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, "slot_overrides": overrides})
        self.coordinator.slot_overrides = self.entry.options["slot_overrides"]

    def set_option(self, key, value):
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, key: value})

    async def tick(self) -> dict:
        storage = types.ModuleType("homeassistant.helpers.storage")
        storage.Store = _FakeStore
        with patch.dict(sys.modules, {"homeassistant.helpers.storage": storage}):
            data = await self.coordinator._async_update_data()
        self.coordinator.data = data
        return data

    async def run_until(self, end: datetime, step_s: int = 60) -> None:
        while self.clock.now < end:
            await self.tick()
            now = self.clock.now
            slot = self.coordinator._current_slot_index()
            planned = self.coordinator.scheduled_slots.get(slot) if slot is not None else None
            today = self.prices_for(now.date())
            price = today[int((now.hour * 60 + now.minute) / (1440 / len(today)))]
            pv = self.pv_kw(now)
            act = self.inverter.advance(step_s, self.load_kw(now), pv, price)
            self.ticks.append(Tick(now, round(self.inverter.soc_pct, 2),
                                   self.coordinator._current_energy_state, planned, act,
                                   price, pv, round(self.inverter.last_grid_kw, 3)))
            self.clock.now = now + timedelta(seconds=step_s)

    # -- reading the result --------------------------------------------------
    def writes(self, key: str | None = None) -> list[Write]:
        return [w for w in self.inverter.writes if key is None or w.key == key]

    def writes_after(self, key: str, t: datetime) -> list[tuple[datetime, int]]:
        """(time, first word) of each write to `key` strictly after `t` — the
        first tick always writes 'idle' as the coordinator takes control."""
        return [(w.at, w.values[0]) for w in self.writes(key) if w.at > t]

    def rule_driven(self, t0: datetime, t1: datetime) -> bool:
        """Rule 1 was in charge the whole time (charging, or holding full)."""
        actions = self.inverter_actions(t0, t1)
        return bool(actions) and "self_use" not in actions

    def inverter_actions(self, t0: datetime, t1: datetime) -> set[str]:
        return {t.inverter for t in self.ticks if t0 <= t.at < t1}

    def soc_at(self, when: datetime) -> float:
        return next(t.soc for t in self.ticks if t.at >= when)

    def plot(self, path: str, title: str = "") -> None:
        """Chart the replay like tools/ems_simulator.py charts a plan: price
        bars coloured by what the INVERTER did (green charge, orange discharge,
        blue hold, grey self-use), the measured SOC, PV and grid power.
        Needs matplotlib (not a test dependency)."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        colours = {"charge": "#4caf50", "discharge": "#ff9800", "hold": "#64b5f6",
                   "self_use": "#bdbdbd"}
        t = [k.at for k in self.ticks]
        width = (t[1] - t[0]) if len(t) > 1 else timedelta(minutes=1)
        fig, (ax, axg) = plt.subplots(2, 1, figsize=(14, 7), sharex=True,
                                      gridspec_kw={"height_ratios": [3, 1]})
        ax.bar(t, [k.price for k in self.ticks], width=width, align="edge",
               color=[colours[k.inverter] for k in self.ticks], linewidth=0)
        ax.set_ylabel("price")
        ax2 = ax.twinx()
        ax2.plot(t, [k.soc for k in self.ticks], color="#0097a7", lw=2, label="SOC (measured)")
        ax2.set_ylim(0, 105)
        ax2.set_ylabel("SOC %")
        ax2.legend(loc="upper right")
        axg.fill_between(t, [k.pv_kw for k in self.ticks], color="#fff59d", label="PV kW")
        axg.plot(t, [k.grid_kw for k in self.ticks], color="#e53935", lw=1,
                 label="grid kW (+import / -export)")
        axg.axhline(0, color="grey", lw=0.5)
        axg.legend(loc="upper right", fontsize=8)
        inv = self.inverter
        ax.set_title(f"{title or self.model}  ·  import {inv.grid_import_kwh:.1f} kWh "
                     f"(€{inv.grid_cost:.2f})  export {inv.grid_export_kwh:.1f} kWh "
                     f"(€{inv.grid_revenue:.2f})", fontsize=10)
        fig.tight_layout()
        fig.savefig(path, dpi=80)
        plt.close(fig)

    def timeline(self, every_min: int = 15) -> str:
        """Human-readable trace for assertion messages."""
        rows = [f"{t.at:%d %H:%M}  soc {t.soc:5.1f}%  state {t.state!s:<11} "
                f"plan {t.planned!s:<9} inverter {t.inverter}"
                for t in self.ticks if t.at.minute % every_min == 0]
        return "\n".join(rows)


def slot(hour: int, minute: int = 0, slots_per_day: int = 96) -> str:
    """The override key the card uses for a time of day."""
    return str((hour * 60 + minute) * slots_per_day // 1440)


def day(d: date, hour: int = 0, minute: int = 0) -> datetime:
    # Naive on purpose: the coordinator works in naive local time.
    return datetime(d.year, d.month, d.day, hour, minute)  # noqa: DTZ001


def pv_bell(peak_kw: float, sunrise: int = 7, sunset: int = 19):
    """A clear-sky day: sin² curve between sunrise and sunset, every day."""
    import math

    def pv(t: datetime) -> float:
        hour = t.hour + t.minute / 60.0
        if not sunrise < hour < sunset:
            return 0.0
        return peak_kw * math.sin(math.pi * (hour - sunrise) / (sunset - sunrise)) ** 2
    return pv
