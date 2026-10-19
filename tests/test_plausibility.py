"""Issue #220: impossible readings must never reach a total_increasing sensor.

Every scenario here is replayed through ``lts_sum``, a model of Home Assistant's
long-term statistics for a total_increasing sensor: a value below 0.9 x the
previous one is a meter RESET, after which the new value is counted in full.
That rule is why one wrong reading inflates a statistic by a whole period's
total — and why the assertions are about the SUM, not just the state.
"""

from __future__ import annotations

import pytest

from tests.conftest import load_component

pl = load_component("plausibility")

T10_KW = 10.0          # T-REX-10 rating
POLL = 10.0            # seconds


def lts_sum(states):
    """Home Assistant's total_increasing sum over a sequence of states."""
    total, prev = 0.0, None
    for s in states:
        if s is None:                     # unknown/unavailable are skipped
            continue
        if prev is not None:
            total += s if s < 0.9 * prev else s - prev
        prev = s
    return total


def _map(**regs):
    return {k: {"unit": u, "state_class": "total_increasing", "device_class": "energy"}
            for k, u in regs.items()}


def _run(key, unit, readings, kw=T10_KW, start=0.0):
    """Feed one reading per poll; return what would be published."""
    guard = pl.PlausibilityGuard(_map(**{key: unit}), kw)
    out = []
    for i, value in enumerate(readings):
        data = {} if value is None else {key: value}
        guard.apply(data, start + i * POLL)
        out.append(data.get(key))
    return out, guard


def test_period_words_classify_every_models_counters():
    assert pl.is_period_counter("pv_generated_energy_day")
    assert pl.is_period_counter("total_load_consumption_energy_day")   # "total" + day
    assert pl.is_period_counter("pv1_month_gen_energy")
    assert pl.is_period_counter("grid_year_cost_energy")
    assert not pl.is_period_counter("pv_generated_energy_total")
    assert not pl.is_period_counter("load_total_cost_energy")
    assert not pl.is_period_counter("microinverter_total_energy")


def test_the_day_rollover_glitch_is_not_counted():
    """pv_generated_energy_day read 34,309 Wh (yesterday's PV) for one poll at
    04:05 and 0 again at the next — booked as +34 kWh and a reset, every day."""
    readings = [0, 0, 34309, 0, 0, 0]
    published, guard = _run("pv_generated_energy_day", "Wh", readings)
    assert published == [0, 0, 0, 0, 0, 0]
    assert lts_sum(published) == 0
    assert lts_sum(readings) == 34309            # what HA counted before
    assert guard.rejected == {"pv_generated_energy_day": 1}


def test_the_month_glitch_is_not_counted():
    """On the first days of a month the month counter returned the previous
    month's total for one poll (+1,256.2 kWh)."""
    readings = [120_000, 120_010, 1_256_200, 120_030, 120_040]
    published, _ = _run("pv_generated_energy_month", "Wh", readings)
    assert published == [120_000, 120_010, 120_010, 120_030, 120_040]
    assert lts_sum(published) == 40


def test_a_real_period_reset_is_accepted():
    readings = [34_000, 34_020, 0, 15, 40]       # midnight: the day starts again
    published, guard = _run("battery_charged_energy_day", "Wh", readings)
    assert published == readings
    assert not guard.rejected
    assert lts_sum(published) == 20 + 40


def test_a_lifetime_total_never_reads_zero_or_falls():
    """A 0 (or a garbage low) on a lifetime total is a reset to HA — the
    2025 install booked 95 such drop/bounce pairs of up to 11,964 kWh."""
    readings = [19_366_742, 0, 19_366_750, 5_000, 19_366_760]
    published, guard = _run("pv_generated_energy_total", "Wh", readings)
    assert published == [19_366_742, 19_366_742, 19_366_750, 19_366_750, 19_366_760]
    assert lts_sum(published) == 18
    assert guard.rejected["pv_generated_energy_total"] == 2


def test_a_garbage_jump_on_a_year_counter_is_rejected():
    """lineload_consumption_energy_inquiry_year +3,858,109 kWh in one poll."""
    readings = [10_270_168, 10_270_170, 3_868_379_000, 10_270_175]
    published, _ = _run("line_load_consumption_energy_year", "Wh", readings)
    assert published[2] == 10_270_170
    assert lts_sum(published) == 7


def test_a_new_level_that_holds_is_accepted():
    """A counter cleared on the inverter (or a bad first reading) must not be
    frozen for ever: a level that holds, consistently, for CONFIRM_POLLS polls
    is the truth."""
    readings = [9_000_000] + [500, 510, 520, 530]
    published, _ = _run("pv_generated_energy_total", "Wh", readings)
    assert published[1:3] == [9_000_000, 9_000_000]
    assert published[3] == 520 and published[4] == 530


def test_real_accrual_is_never_rejected():
    """10 kW of PV for 10 s, and an hour without polls at full power."""
    out, guard = _run("pv_generated_energy_day", "Wh", [1000, 1028, 1056])
    assert out == [1000, 1028, 1056] and not guard.rejected
    guard = pl.PlausibilityGuard(_map(pv_generated_energy_day="Wh"), T10_KW)
    a = {"pv_generated_energy_day": 1000}
    guard.apply(a, 0.0)
    b = {"pv_generated_energy_day": 1000 + 10_000}   # +10 kWh after an hour away
    guard.apply(b, 3600.0)
    assert b["pv_generated_energy_day"] == 11_000


def test_kwh_registers_use_their_own_unit():
    readings = [12.5, 12.6, 350.0, 12.7]          # T-REX-25/50 / IVGM: 0.1 kWh
    published, _ = _run("pv1_day_energy", "kWh", readings, kw=25.0)
    assert published == [12.5, 12.6, 12.6, 12.7]


@pytest.mark.parametrize("value", [1.25e8, 4_294_967_260, -3.9e9])
def test_absurd_power_is_dropped(value):
    """total_ac_input_power 1.25e8 W, loadpower_lineside 3.9e9 W."""
    reg = {"total_ac_input_power": {"unit": "W", "device_class": "power"}}
    guard = pl.PlausibilityGuard(reg, T10_KW)
    good = {"total_ac_input_power": 8430}
    guard.apply(good, 0.0)
    bad = {"total_ac_input_power": value}
    guard.apply(bad, 10.0)
    assert bad["total_ac_input_power"] == 8430                  # bridged briefly
    stale = {"total_ac_input_power": value}
    guard.apply(stale, 10.0 + pl.POWER_HOLD_S + 1)
    assert "total_ac_input_power" not in stale                  # then unknown


def test_real_power_passes_including_negative():
    reg = {"bat_power_conversion": {"unit": "W", "device_class": "power"},
           "pv1_power": {"unit": "kW", "device_class": "power"}}
    guard = pl.PlausibilityGuard(reg, T10_KW)
    data = {"bat_power_conversion": -1186, "pv1_power": 6.65}
    guard.apply(data, 0.0)
    assert data == {"bat_power_conversion": -1186, "pv1_power": 6.65}
