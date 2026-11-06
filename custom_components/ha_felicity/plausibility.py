"""Reject readings that cannot be true before anything publishes them (issue #220).

Home Assistant's long-term statistics treat a ``total_increasing`` sensor that
drops by more than 10 % as a meter RESET: the sum keeps what it had and the next
value is counted again in full.  So one wrong reading on an energy counter is
not a blip, it is the counter's whole value added to the statistic a second
time — on a year counter, a year of energy.  Field data from a T-REX-10
(Dec 2025 – Oct 2026): day/month/year sums inflated 3–8.5×, and lifetime totals
recounted at restarts.

Two kinds of wrong reading reach the poll:

* the inverter's own rollover glitch — at its period change it answers the
  PREVIOUS period's total for one poll, then 0 (day counters: 34 kWh for one
  poll at ~04:05, every day; month counters on the first days of a month);
* a reply decoded against the wrong request (a late reply after a silent
  retry) — a lifetime total landing in a year/month block, 0 in a total.

Neither can be told from the value alone, but both are physically impossible:
energy cannot arrive faster than the hardware can move it.  So:

* an energy counter may rise by at most ``rate × elapsed`` (+ a small slack);
* a lifetime total never falls (it may read 0 only if it never counted); a period counter may fall only to a value it
  could have reached since a reset (that IS its reset);
* a 0 with nothing to compare it with yet is not taken as the starting point
  until it has held for ``CONFIRM_POLLS`` polls (issue #214: an IVAM answered 0
  in some polls and its real total in others);
* a period counter's fall must hold too: ``RESET_HOLD_IN_WINDOW_S`` near its
  own reset time (``in_reset_window``), ``RESET_CONFIRM_S`` at any other time.  A
  reading back at the old level in between ends it — that was a glitch, and
  publishing it would make Home Assistant count the whole period again;
* a rejected reading keeps the last good value — unless the new level holds
  for ``CONFIRM_POLLS`` consecutive polls, consistent with itself, in which
  case it is the truth (a re-baseline, e.g. a counter cleared on the inverter);
* a power reading beyond ``POWER_BOUND_FACTOR`` × the inverter's rating is
  dropped (garbage such as 1.25e8 W), keeping the last good value briefly.

Nothing here decides which model has which glitch: the bounds come from the
register's own unit and the inverter's declared rating, so it applies to every
model without assuming anything about its firmware.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

#: Energy may accrue at most this many times the inverter's rating.  Generous on
#: purpose — PV arrays are oversized and grid passthrough feeds the house beyond
#: the inverter — while a glitch is a whole period's total in one poll.
ENERGY_RATE_FACTOR = 5.0
#: Fixed allowance on top of rate × elapsed (kWh): register resolution and poll
#: jitter must never trip the guard.
ENERGY_SLACK_KWH = 0.5
#: A rejected level is accepted once it has held, consistently, this many polls.
CONFIRM_POLLS = 3
#: How long a period counter's fall must hold before it is taken as its reset:
#: near its reset time (``in_reset_window``) and at any other time (a wrong
#: inverter clock, a counter cleared in the app).  Always CONFIRM_POLLS polls
#: at least.  A real reset holds; a glitch 0 is followed by the old level.
RESET_HOLD_IN_WINDOW_S = 60.0
RESET_CONFIRM_S = 900.0
#: Power readings beyond this multiple of the rating are not measurements.
POWER_BOUND_FACTOR = 10.0
#: How long a dropped power reading may be bridged with the last good value.
POWER_HOLD_S = 60.0

_ENERGY_UNIT_PER_KWH = {"Wh": 1000.0, "kWh": 1.0, "MWh": 0.001}
_POWER_UNIT_PER_KW = {"W": 1000.0, "kW": 1.0, "VA": 1000.0, "kVA": 1.0}
_PERIOD_WORDS = {"day", "month", "year"}


def is_period_counter(key: str) -> bool:
    """A day/month/year counter resets; anything else counting energy is lifetime."""
    return bool(_PERIOD_WORDS & set(key.lower().split("_")))


def _period_of(key: str) -> str | None:
    words = set(key.lower().split("_"))
    for period in ("day", "month", "year"):
        if period in words:
            return period
    return None


def in_reset_window(key: str, now: float) -> bool:
    """Is ``now`` (epoch s) near the time this period counter resets?

    22:00-03:00 local for a day counter (the T-REX-10's day resets at 01:00 in
    summer: its clock ignores DST), the same hours around the 1st for a month
    counter, around New Year for a year counter.  Taken generously because the
    inverter's own clock decides, not Home Assistant's.
    """
    period = _period_of(key)
    if period is None:
        return False
    # Local time, as the coordinator's slots and midnight rollover use it.
    shifted = datetime.fromtimestamp(now + 2 * 3600, tz=UTC).astimezone()   # 22:00 -> 00:00
    if shifted.hour >= 5:
        return False
    if period == "day":
        return True
    if period == "month":
        return shifted.day == 1
    return shifted.day == 1 and shifted.month == 1


@dataclass
class _Track:
    value: float
    ts: float
    pending: list[tuple[float, float]] = field(default_factory=list)


class PlausibilityGuard:
    """Per-coordinator filter applied to each poll's decoded values."""

    def __init__(self, register_map: dict, inverter_max_kw: float) -> None:
        self._map = register_map
        self._rate_kw = max(inverter_max_kw, 1.0) * ENERGY_RATE_FACTOR
        self._power_bound_kw = max(inverter_max_kw, 1.0) * POWER_BOUND_FACTOR
        self._counters: dict[str, _Track] = {}
        self._power: dict[str, tuple[float, float]] = {}
        #: Consecutive 0 readings of a counter with no baseline yet.
        self._zero_streak: dict[str, int] = {}
        #: Non-zero readings of a counter whose first readings were 0: they
        #: must agree for CONFIRM_POLLS polls before one becomes the baseline.
        self._start_candidates: dict[str, list[tuple[float, float]]] = {}
        #: {key: count} of readings rejected since start — for diagnostics.
        self.rejected: dict[str, int] = {}
        #: {key: {"rejected": value, "kept": value}} — the last rejection, so a
        #: diagnostics download shows what the inverter actually sent (#214).
        self.last_rejected: dict[str, dict] = {}

    # -- public --------------------------------------------------------------
    def apply(self, data: dict, now: float) -> None:
        """Filter ``data`` in place."""
        for key in list(data):
            info = self._map.get(key)
            value = data[key]
            if info is None or not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            if info.get("state_class") == "total_increasing":
                factor = _ENERGY_UNIT_PER_KWH.get(info.get("unit"))
                if factor is not None:
                    self._counter(data, key, float(value), now, factor)
            elif info.get("device_class") in ("power", "apparent_power"):
                factor = _POWER_UNIT_PER_KW.get(info.get("unit"))
                if factor is not None:
                    self._power_reading(data, key, float(value), now, factor)

    # -- energy counters -----------------------------------------------------
    def _allowed(self, dt_s: float, factor: float) -> float:
        return (self._rate_kw * max(dt_s, 0.0) / 3600.0 + ENERGY_SLACK_KWH) * factor

    def _plausible(self, prev: float, prev_ts: float, new: float, now: float,
                   factor: float) -> bool:
        """A rise the hardware could deliver.  A fall is never plausible at
        once: a period counter's reset is confirmed (``_counter``)."""
        return prev <= new <= prev + self._allowed(now - prev_ts, factor)

    def _reset_confirmed(self, key, track, now) -> bool:
        """Has a period counter's fall held long enough to be its reset?"""
        if len(track.pending) < CONFIRM_POLLS:
            return False
        since = track.pending[0][1]
        hold = RESET_HOLD_IN_WINDOW_S if in_reset_window(key, since) else RESET_CONFIRM_S
        return now - since >= hold

    def _start(self, data, key, value, now, factor) -> None:
        """The first readings of a counter: pick a baseline worth trusting.

        A 0 to start from is the #220 garbage (a lifetime total), a counter
        answering 0 in some polls (#214, an IVAM), or the truth (nothing on a
        micro-inverter port, #237; a day counter after its reset).  So 0 is the
        baseline once it has held CONFIRM_POLLS polls.  A non-zero first reading
        is taken at once — unless zeros came first: then the start is ambiguous
        (the T-REX-10 rollover glitch is one poll of the previous total between
        zeros), and it must agree with itself for CONFIRM_POLLS polls too.
        """
        if value <= 0:
            self._start_candidates.pop(key, None)
            streak = self._zero_streak.get(key, 0) + 1
            if streak < CONFIRM_POLLS:
                self._zero_streak[key] = streak
                self._reject(data, key, None)
                return
        elif key in self._zero_streak:
            cands = self._start_candidates.setdefault(key, [])
            if cands:
                last_value, last_ts = cands[-1]
                if not (last_value <= value <= last_value + self._allowed(now - last_ts, factor)):
                    cands.clear()
            cands.append((value, now))
            if len(cands) < CONFIRM_POLLS:
                self._reject(data, key, None)
                return
        self._zero_streak.pop(key, None)
        self._start_candidates.pop(key, None)
        self._counters[key] = _Track(value, now)

    def _counter(self, data, key, value, now, factor) -> None:
        period = is_period_counter(key)
        track = self._counters.get(key)
        if track is None:
            self._start(data, key, value, now, factor)
            return
        if self._plausible(track.value, track.ts, value, now, factor):
            track.value, track.ts, track.pending = value, now, []
            return
        # Implausible against the last good value.  Accept it only once the
        # new level has held consistently: CONFIRM_POLLS polls for a jump up
        # (a re-baseline), and for a period counter's fall the reset rule.
        if track.pending:
            last_value, last_ts = track.pending[-1]
            if not (last_value <= value <= last_value + self._allowed(now - last_ts, factor)):
                track.pending = []
        falling = value < track.value
        if falling and not period and value <= 0:
            track.pending = []        # a lifetime total never falls back to 0
        else:
            track.pending.append((value, now))
        if falling and period:
            accept = self._reset_confirmed(key, track, now)
        else:
            accept = len(track.pending) >= CONFIRM_POLLS
        if accept:
            track.value, track.ts, track.pending = value, now, []
            return
        self._reject(data, key, track.value)

    # -- power ---------------------------------------------------------------
    def _power_reading(self, data, key, value, now, factor) -> None:
        if abs(value) <= self._power_bound_kw * factor:
            self._power[key] = (value, now)
            return
        last = self._power.get(key)
        self._reject(data, key, last[0] if last and now - last[1] <= POWER_HOLD_S else None)

    def _reject(self, data, key, keep) -> None:
        self.rejected[key] = self.rejected.get(key, 0) + 1
        self.last_rejected[key] = {"rejected": data.get(key), "kept": keep}
        if keep is None:
            data.pop(key, None)
        else:
            data[key] = keep
