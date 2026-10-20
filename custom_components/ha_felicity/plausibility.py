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
* a lifetime total never falls; a period counter may fall only to a value it
  could have reached since a reset (that IS its reset);
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

#: Energy may accrue at most this many times the inverter's rating.  Generous on
#: purpose — PV arrays are oversized and grid passthrough feeds the house beyond
#: the inverter — while a glitch is a whole period's total in one poll.
ENERGY_RATE_FACTOR = 5.0
#: Fixed allowance on top of rate × elapsed (kWh): register resolution and poll
#: jitter must never trip the guard.
ENERGY_SLACK_KWH = 0.5
#: A rejected level is accepted once it has held, consistently, this many polls.
CONFIRM_POLLS = 3
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
        #: {key: count} of readings rejected since start — for diagnostics.
        self.rejected: dict[str, int] = {}

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
                   factor: float, period: bool) -> bool:
        allowed = self._allowed(now - prev_ts, factor)
        if new >= prev:
            return new - prev <= allowed
        # A fall: only a period counter may fall, and only to what it could
        # have counted since its reset.
        return period and new <= allowed

    def _counter(self, data, key, value, now, factor) -> None:
        period = is_period_counter(key)
        track = self._counters.get(key)
        if track is None:
            if not period and value <= 0:
                self._reject(data, key, None)      # a lifetime total is never 0
                return
            self._counters[key] = _Track(value, now)
            return
        if self._plausible(track.value, track.ts, value, now, factor, period):
            track.value, track.ts, track.pending = value, now, []
            return
        # Implausible against the last good value.  Accept it only once the
        # new level has held consistently for CONFIRM_POLLS polls.
        if track.pending:
            last_value, last_ts = track.pending[-1]
            if not (last_value <= value <= last_value + self._allowed(now - last_ts, factor)):
                track.pending = []
        if not period and value <= 0:
            track.pending = []
        else:
            track.pending.append((value, now))
        if len(track.pending) >= CONFIRM_POLLS:
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
        if keep is None:
            data.pop(key, None)
        else:
            data[key] = keep
