"""Issue #220: impossible readings must never reach a total_increasing sensor.

Every scenario here is replayed through ``lts_sum``, a model of Home Assistant's
long-term statistics for a total_increasing sensor: a value below 0.9 x the
previous one is a meter RESET, after which the new value is counted in full.
That rule is why one wrong reading inflates a statistic by a whole period's
total — and why the assertions are about the SUM, not just the state.
"""

from __future__ import annotations

import time

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


def _local(*args) -> float:
    """Epoch seconds of a LOCAL wall-clock time (the reset window is local)."""
    return time.mktime((*args, *(0,) * (6 - len(args)), 0, 0, -1))


NOON = _local(2026, 11, 6, 12, 0)          # far from any reset time
NIGHT = _local(2026, 11, 6, 23, 30)        # a day counter's reset window


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
    # A start of zeros is held back until 0 has held (the sensor keeps its
    # restored value meanwhile); the glitch is never published.
    assert published == [None, None, None, 0, 0, 0]
    assert lts_sum(published) == 0
    assert lts_sum(readings) == 34309            # what HA counted before
    guard = pl.PlausibilityGuard(_map(pv_generated_energy_day="Wh"), T10_KW)
    out = []
    for i, value in enumerate([0, 0, 0, 0, 34309, 0, 0]):   # 0 held, then the glitch
        data = {"pv_generated_energy_day": value}
        guard.apply(data, NOON + i * POLL)
        out.append(data.get("pv_generated_energy_day"))
    assert out == [None, None, 0, 0, 0, 0, 0]


def test_the_month_glitch_is_not_counted():
    """On the first days of a month the month counter returned the previous
    month's total for one poll (+1,256.2 kWh)."""
    readings = [120_000, 120_010, 1_256_200, 120_030, 120_040]
    published, _ = _run("pv_generated_energy_month", "Wh", readings)
    assert published == [120_000, 120_010, 120_010, 120_030, 120_040]
    assert lts_sum(published) == 40


def test_a_real_period_reset_is_accepted():
    """Midnight: the day starts again and stays low.  It is published once it
    has held RESET_HOLD_IN_WINDOW_S; HA's sum is the same as if at once."""
    readings = [34_000, 34_020] + [0, 0, 5, 10, 15, 20, 25, 30, 35]
    published, _ = _run("battery_charged_energy_day", "Wh", readings,
                        start=_local(2026, 11, 6, 23, 59, 40))
    assert published[2] == 34_020                # not at once
    assert published[-1] == 35                   # after a minute: the reset
    assert lts_sum(published) == 20 + 35
    assert lts_sum(published) == lts_sum(readings)


def test_a_period_counter_answering_0_in_some_polls_is_not_reset():
    """Issue #214 (IVAM6048P1G1): the day/month/year load counters answered
    0 in some polls and the true value (2.6 / 8.0 / 81.2 kWh) in others.  A 0
    was accepted as the day's reset and the true value then counted again —
    the #220 inflation through another door.  Away from the reset time, and
    near it too, a 0 the old level follows is never published."""
    true = [2.6 + 0.01 * i for i in range(40)]
    readings = [0.0 if i % 5 in (2, 3) else v for i, v in enumerate(true)]
    for start in (NOON, NIGHT):
        published, guard = _run("load_day_cost_energy", "kWh", readings, kw=6.0, start=start)
        assert 0.0 not in published, (start, published)
        assert lts_sum(published) == pytest.approx(true[-1] - true[0])
        assert guard.last_rejected["load_day_cost_energy"]["rejected"] == 0.0


def test_a_start_of_zeros_does_not_hide_the_true_value():
    """The same IVAM after a restart: zeros first, then the true value mixed
    with zeros.  Before, 0 became the baseline and 2.6 was rejected as a jump
    for ever — the sensor read 0.0 while the register said 2.6."""
    readings = [0.0, 2.6, 0.0, 2.6, 2.6, 2.6, 0.0, 2.61, 2.61]
    published, _ = _run("load_day_cost_energy", "kWh", readings, kw=6.0, start=NOON)
    assert published[-1] == 2.61
    assert 0.0 not in published


def test_a_fall_away_from_the_reset_time_needs_a_long_hold():
    """A wrong inverter clock, or a counter cleared in the app: a fall that
    holds RESET_CONFIRM_S with nothing back at the old level is accepted."""
    polls = int(pl.RESET_CONFIRM_S // POLL) + 2
    readings = [5.0, 5.0] + [0.0] * polls
    published, _ = _run("load_day_cost_energy", "kWh", readings, kw=6.0, start=NOON)
    assert published[10] == 5.0
    assert published[-1] == 0.0


@pytest.mark.parametrize("key, when, expected", [
    ("load_day_cost_energy", (2026, 11, 6, 23, 0), True),
    ("load_day_cost_energy", (2026, 11, 7, 2, 30), True),
    ("load_day_cost_energy", (2026, 11, 6, 12, 0), False),
    ("load_month_cost_energy", (2026, 11, 30, 23, 0), True),
    ("load_month_cost_energy", (2026, 12, 1, 1, 0), True),
    ("load_month_cost_energy", (2026, 11, 6, 23, 0), False),
    ("load_year_cost_energy", (2026, 12, 31, 23, 30), True),
    ("load_year_cost_energy", (2026, 6, 1, 0, 30), False),
    ("load_total_cost_energy", (2026, 11, 6, 23, 0), False),
])
def test_reset_windows(key, when, expected):
    assert pl.in_reset_window(key, _local(*when)) is expected


def test_a_lifetime_total_never_reads_zero_or_falls():
    """A 0 (or a garbage low) on a lifetime total is a reset to HA — the
    2025 install booked 95 such drop/bounce pairs of up to 11,964 kWh."""
    readings = [19_366_742, 0, 19_366_750, 5_000, 19_366_760]
    published, guard = _run("pv_generated_energy_total", "Wh", readings)
    assert published == [19_366_742, 19_366_742, 19_366_750, 19_366_750, 19_366_760]
    assert lts_sum(published) == 18
    assert guard.rejected["pv_generated_energy_total"] == 2


def test_a_lifetime_total_that_never_counted_may_read_zero():
    """Issue #237: microinverter_total_energy on a T-REX-50 with nothing on the
    micro-inverter port reads 0 for ever, and was rejected 4,943 times — the
    sensor stayed unknown.  0 is the baseline once it has held CONFIRM_POLLS
    polls; the counter then counts up as usual."""
    readings = [0, 0, 0, 0, 0, 0.1]
    published, guard = _run("microinverter_total_energy", "kWh", readings, kw=50.0)
    assert published == [None, None, 0, 0, 0, 0.1]
    assert guard.rejected == {"microinverter_total_energy": pl.CONFIRM_POLLS - 1}


def test_a_zero_before_the_first_real_total_is_still_dropped():
    """The #220 garbage 0 at startup is not taken as the baseline."""
    readings = [0, 19_366_742, 19_366_750, 19_366_760, 19_366_770]
    published, _ = _run("pv_generated_energy_total", "Wh", readings)
    assert published == [None, None, None, 19_366_760, 19_366_770]


def test_a_total_that_has_counted_may_not_fall_back_to_zero():
    readings = [0, 0, 0, 0.5, 0, 0, 0.6]
    published, _ = _run("microinverter_total_energy", "kWh", readings, kw=50.0)
    assert published[3] == 0.5
    assert published[4] == 0.5 and published[5] == 0.5      # the 0s are not published
    assert published[6] == 0.6


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
