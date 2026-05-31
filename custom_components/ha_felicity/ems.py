"""EMS scheduling algorithm — pure functions for energy management.

This module contains the scheduling logic extracted from coordinator.py.
All functions are pure (no HA state, no self.* access) for testability.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

_LOGGER = logging.getLogger(__name__)


@dataclass
class EMSConfig:
    """All configuration needed by the EMS scheduler."""

    grid_mode: str = "off"  # off / from_grid / to_grid / both
    battery_capacity_kwh: float = 10.0
    battery_charge_max_pct: float = 100.0
    battery_discharge_min_pct: float = 20.0
    efficiency: float = 0.90
    safe_power_kw: float = 5.0
    inverter_max_power_kw: float = 10.0
    consumption_est_kwh: float = 10.0
    yesterday_deficit_kwh: float = 0.0
    reserve_target_pct: float = 0.0  # 0 = dynamic (min + overnight), >0 = fixed floor %
    arbitrage_price_delta: float = 0.0  # €/kWh spread threshold for full charge in 'both' mode
    # Battery degradation cost: each cycled kWh has a wear cost.  Used to
    # require a profitable spread before scheduling arbitrage.  Typical LFP
    # range: 0.02-0.05 €/kWh.  0.0 disables (legacy behaviour).
    battery_cycle_cost_eur_kwh: float = 0.0
    # Optimization priority: cost / longevity / self_consumption.
    # cost: minimise grid spend (legacy default).
    # longevity: bias against cycling — higher cycle-cost penalty.
    # self_consumption: maximise PV self-use, reduce grid imports.
    optimization_priority: str = "cost"
    # Disallow grid export during negative-price slots.  Some markets
    # (DE/NL) penalise feed-in at negative prices.  When True, sell slots
    # at p < 0 are blocked even in to_grid / both modes.
    block_export_on_negative_price: bool = True
    # Negative-price strategies (orthogonal to grid_mode).
    # charge_to_full_on_negative_price: extend charging beyond reserve
    # target up to battery_charge_max during negative-price slots, even if
    # PV alone wouldn't fill the battery.  Each grid-charged kWh is revenue
    # (paid to consume); user accepts that some PV may need to be curtailed.
    charge_to_full_on_negative_price: bool = False
    # discharge_to_make_room_for_negative_price: schedule pre-emptive
    # discharges before negative-price PV windows so the battery has room
    # to absorb the PV (avoiding forced grid export at penalty rates).
    # Discharge only happens in positive-price hours.
    discharge_to_make_room_for_negative_price: bool = False
    # NOTE: battery State of Health (SOH) is applied by the coordinator
    # before constructing this config — it scales battery_capacity_kwh
    # by the SOH factor.  ems.py treats the capacity as already-effective.


@dataclass
class EMSState:
    """Current runtime state fed into the scheduler."""

    battery_soc_pct: float | None = None
    slot_prices_today: list[float | None] | None = None
    slot_prices_tomorrow: list[float | None] | None = None
    pv_hourly_kwh: dict[int, float] = field(default_factory=dict)
    pv_forecast_remaining: float | None = None
    pv_forecast_today: float | None = None
    pv_forecast_tomorrow: float | None = None
    pv_actual_today_kwh: float | None = None
    consumption_hourly_kwh: dict[int, float] | None = None  # {hour: avg_kwh} from 7-day profile
    # Previous-tick PV confidence for EMA smoothing.  None on first call.
    previous_pv_confidence: float | None = None
    # Last successful Modbus read timestamp (epoch seconds).  When stale
    # (older than the configured threshold), the algorithm refuses to plan.
    last_modbus_read_ts: float | None = None
    # Fallback PV total for today when forecast.solar is unavailable.
    # Coordinator sets this from the 7-day rolling average of actual daily
    # PV.  Used only when pv_forecast_today is None or 0.
    pv_fallback_today_kwh: float | None = None
    current_hour: int = 12
    current_minute: int = 0


@dataclass
class ScheduleResult:
    """Output of the schedule optimizer."""

    scheduled_slots: dict[int, str] = field(default_factory=dict)
    price_threshold: float | None = None
    grid_energy_planned: float = 0.0
    cheap_slots_remaining: int = 0
    self_consumption_reserve: float = 0.0
    reserve_target_pct: float = 0.0  # computed reserve target as battery %
    tomorrow_planned_slots: int = 0
    tomorrow_planned_kwh: float = 0.0
    tomorrow_precharge: float = 0.0
    status: str = "off"
    soc_trajectory: list[float] = field(default_factory=list)
    tomorrow_scheduled_slots: dict[int, str] = field(default_factory=dict)
    tomorrow_soc_trajectory: list[float] = field(default_factory=list)


@dataclass
class AvailableInfo:
    """Output of the available slots / charge likelihood calculation."""

    available_slots: int = 0
    available_energy_capacity: float = 0.0
    charge_likelihood: str = "no_data"


def _synthesize_pv_hourly(
    pv_forecast_today: float,
    sunrise: int = 6,
    sunset: int = 20,
) -> dict[int, float]:
    """Generate synthetic hourly PV distribution using a solar bell curve.

    Used as a fallback when the forecast entity provides a daily total
    but no per-hour breakdown (wh_hours / detailedHourly missing).
    """
    if pv_forecast_today <= 0:
        return {}

    total_minutes = (sunset - sunrise) * 60
    hourly: dict[int, float] = {}
    for hour in range(sunrise, sunset):
        # fraction of solar day elapsed at start and end of this hour
        start_min = (hour - sunrise) * 60
        end_min = start_min + 60
        f_start = start_min / total_minutes
        f_end = end_min / total_minutes
        # bell-curve fraction produced in this hour
        produced_start = (1 - math.cos(math.pi * f_start)) / 2
        produced_end = (1 - math.cos(math.pi * f_end)) / 2
        hourly[hour] = pv_forecast_today * (produced_end - produced_start)
    return hourly


def calculate_self_consumption_reserve(
    consumption_est: float,
    pv_hourly_kwh: dict[int, float] | None = None,
) -> float:
    """Calculate battery reserve needed for self-consumption overnight.

    Returns reserve in kWh needed from sunset to sunrise.
    """
    consumption_per_hour = consumption_est / 24.0

    sunset_hour = 19
    sunrise_hour = 7
    if pv_hourly_kwh:
        pv_hours = [h for h, kwh in pv_hourly_kwh.items() if kwh > 0.1]
        if pv_hours:
            sunset_hour = max(pv_hours) + 1
            sunrise_hour = min(pv_hours)

    overnight_hours = (24 - sunset_hour) + sunrise_hour
    reserve = consumption_per_hour * overnight_hours

    _LOGGER.debug(
        "Self-consumption reserve: %.2f kWh (sunset=%d:00, sunrise=%d:00, "
        "overnight=%.1fh, consumption=%.1f kWh/day)",
        reserve, sunset_hour, sunrise_hour, overnight_hours, consumption_est,
    )
    return reserve


def _compute_reserve_target(
    config: EMSConfig,
    reserve_kwh: float,
) -> float:
    """Compute the battery reserve target in kWh.

    When reserve_target_pct > 0, uses that as a fixed floor percentage.
    Otherwise falls back to the dynamic calculation: discharge_min + overnight reserve.
    When optimization_priority == "self_consumption", the dynamic reserve is
    boosted to keep more PV-stored energy in the battery for self-use.
    """
    min_kwh = (config.battery_discharge_min_pct / 100.0) * config.battery_capacity_kwh

    if config.reserve_target_pct > 0:
        fixed_floor = (config.reserve_target_pct / 100.0) * config.battery_capacity_kwh
        # Use the higher of fixed floor and discharge minimum
        return min(config.battery_capacity_kwh, max(fixed_floor, min_kwh))

    # Self-consumption priority: hold extra reserve to favour PV self-use
    # over grid exports.  Adds a 25% buffer on top of the overnight reserve.
    multiplier = 1.25 if config.optimization_priority == "self_consumption" else 1.0
    return min(config.battery_capacity_kwh, min_kwh + reserve_kwh * multiplier)


def calculate_net_pv_surplus(
    remaining_slots: list[tuple[int, float]],
    num_slots: int,
    consumption_est: float,
    pv_hourly_kwh: dict[int, float] | None = None,
    pv_forecast_remaining: float | None = None,
    pv_actual_today_kwh: float | None = None,
    pv_forecast_today: float | None = None,
    current_hour: int = 12,
    current_minute: int = 0,
    previous_pv_confidence: float | None = None,
) -> float:
    """Calculate net PV surplus using per-hour solar production vs consumption.

    Uses PV confidence (actual vs forecast) to scale down on cloudy days.
    Falls back to flat model when hourly data is unavailable.
    """
    pv_remaining = pv_forecast_remaining or 0.0

    if not pv_hourly_kwh or not remaining_slots:
        hours_left = len(remaining_slots) * ((24 * 60) / num_slots) / 60.0
        consumption_remaining = (consumption_est / 24.0) * hours_left
        return max(0.0, pv_remaining - consumption_remaining)

    minutes_per_slot = (24 * 60) / num_slots
    consumption_per_hour = consumption_est / 24.0

    # PV confidence factor (sliding window + EMA smoothing across ticks)
    pv_confidence = _calculate_pv_confidence(
        pv_hourly_kwh, pv_actual_today_kwh, current_hour, current_minute,
        previous_confidence=previous_pv_confidence,
    )

    # Sum positive surpluses per hour
    surplus_total = 0.0
    hours_seen: set[int] = set()
    for slot_idx, _ in remaining_slots:
        hour = int((slot_idx * minutes_per_slot) / 60)
        if hour in hours_seen:
            continue
        hours_seen.add(hour)

        pv_kwh = pv_hourly_kwh.get(hour, 0.0) * pv_confidence
        surplus = pv_kwh - consumption_per_hour
        if surplus > 0:
            surplus_total += surplus

    _LOGGER.debug(
        "PV surplus model: hourly_surplus=%.2f kWh, pv_remaining=%.2f kWh, "
        "consumption=%.1f kWh/day, hours_checked=%d, pv_confidence=%.0f%%",
        surplus_total, pv_remaining, consumption_est, len(hours_seen),
        pv_confidence * 100,
    )
    return surplus_total


def _calculate_pv_confidence(
    pv_hourly_kwh: dict[int, float] | None,
    pv_actual_today_kwh: float | None,
    current_hour: int,
    current_minute: int = 0,
    previous_confidence: float | None = None,
    ema_alpha: float = 0.3,
) -> float:
    """Calculate PV production confidence based on actual vs expected output.

    The confidence starts at 1.0 (trust the forecast) and only reduces when
    there is substantial evidence of underperformance.  An "evidence weight"
    controls how much we trust the actual-vs-expected ratio: when only a
    small fraction of the day's total forecast should have been produced,
    the evidence is weak and the confidence stays close to 1.0.

    Uses a sliding-window approach: computes both a cumulative confidence
    (all hours since dawn) and a recent-window confidence (last 3 hours).
    The final confidence is the MAXIMUM of both, allowing recovery when
    weather improves after a cloudy morning.

    Returns a factor between 0.1 and 1.0.  A value of 1.0 means production
    tracks the forecast; lower values indicate a cloudier day than forecast.
    """
    if not pv_hourly_kwh or pv_actual_today_kwh is None:
        return 1.0

    # Total daily forecast and cumulative expected through the current time
    total_forecast = sum(pv_hourly_kwh.values())
    expected_so_far = 0.0
    for hour, kwh in pv_hourly_kwh.items():
        if hour < current_hour:
            expected_so_far += kwh
        elif hour == current_hour:
            expected_so_far += kwh * (current_minute / 60.0)

    if expected_so_far <= 1.0:
        return 1.0

    # Evidence weight: how much of the day's production was expected by now.
    # When only a small fraction has been expected (early morning), we don't
    # have enough data to deviate from the forecast.  The weight ramps
    # linearly from 0 to 1 as expected_so_far goes from 0 to 20% of total.
    evidence_threshold = max(total_forecast * 0.20, 3.0)  # at least 3 kWh
    evidence_weight = min(1.0, expected_so_far / evidence_threshold)

    cumulative_confidence = pv_actual_today_kwh / expected_so_far

    # Sliding window: compare recent production vs recent forecast.
    # If we only have total actual, we estimate recent actual as:
    #   recent_actual = total_actual - expected_before_window
    # This works because if production recovered in the recent window,
    # the surplus over expected_before_window reflects that.
    window_hours = 3
    window_start = max(0, current_hour - window_hours)
    expected_before_window = 0.0
    expected_in_window = 0.0
    for hour, kwh in pv_hourly_kwh.items():
        if hour < window_start:
            expected_before_window += kwh
        elif hour < current_hour:
            expected_in_window += kwh
        elif hour == current_hour:
            expected_in_window += kwh * (current_minute / 60.0)

    window_confidence = cumulative_confidence  # fallback
    if expected_in_window >= 0.5:
        # Estimate actual production in the window by subtracting what
        # we'd expect to have produced before the window started.
        recent_actual = max(0.0, pv_actual_today_kwh - expected_before_window)
        window_confidence = recent_actual / expected_in_window

    raw_confidence = max(cumulative_confidence, window_confidence)

    # Blend: start at 1.0, gradually shift to measured confidence as
    # evidence accumulates.  This prevents over-reacting in early morning
    # when actual production is naturally low.
    confidence = 1.0 * (1.0 - evidence_weight) + raw_confidence * evidence_weight

    # EMA smoothing to prevent oscillation: a single dark/bright hour can
    # otherwise swing the raw confidence dramatically.  Blend the new value
    # with the previous one to enforce hysteresis.
    if previous_confidence is not None:
        smoothed = ema_alpha * confidence + (1.0 - ema_alpha) * previous_confidence
    else:
        smoothed = confidence

    _LOGGER.debug(
        "PV confidence: cumulative=%.2f, window(%dh)=%.2f, raw=%.2f, "
        "evidence_weight=%.2f, instant=%.2f, smoothed=%.2f "
        "(prev=%s, actual=%.1f, expected_total=%.1f, total_forecast=%.1f)",
        cumulative_confidence, window_hours, window_confidence, raw_confidence,
        evidence_weight, confidence, smoothed,
        f"{previous_confidence:.2f}" if previous_confidence is not None else "None",
        pv_actual_today_kwh, expected_so_far, total_forecast,
    )

    return max(0.1, min(1.0, smoothed))


def _project_soc_trajectory(
    remaining: list[tuple[int, float]],
    current_kwh: float,
    consumption_per_slot: float,
    pv_hourly_kwh: dict[int, float] | None,
    minutes_per_slot: float,
    pv_confidence: float = 1.0,
    battery_capacity: float = 100.0,
    consumption_hourly_kwh: dict[int, float] | None = None,
) -> tuple[dict[int, float], float, float]:
    """Project battery SOC through remaining slots (no charge/sell actions).

    When consumption_hourly_kwh is provided, uses per-hour consumption
    instead of the flat consumption_per_slot.  This improves accuracy
    for households with uneven load profiles (e.g., evening peaks).

    Returns:
        (per_slot_projection, min_kwh, max_kwh)
    """
    projection: dict[int, float] = {}
    projected = current_kwh
    min_soc = current_kwh
    max_soc = current_kwh

    for slot_idx, _ in remaining:
        hour = int((slot_idx * minutes_per_slot) / 60)
        pv_kwh = (pv_hourly_kwh or {}).get(hour, 0.0) * pv_confidence
        pv_per_slot = pv_kwh * (minutes_per_slot / 60.0)

        if consumption_hourly_kwh and hour in consumption_hourly_kwh:
            cons_per_slot = consumption_hourly_kwh[hour] * (minutes_per_slot / 60.0)
        else:
            cons_per_slot = consumption_per_slot

        projected = max(0.0, min(battery_capacity, projected + pv_per_slot - cons_per_slot))
        projection[slot_idx] = projected
        min_soc = min(min_soc, projected)
        max_soc = max(max_soc, projected)

    return projection, min_soc, max_soc


def _compute_scheduled_soc_trajectory(
    prices: list[float | None],
    num_slots: int,
    minutes_per_slot: float,
    current_kwh: float,
    current_slot: int,
    scheduled_slots: dict[int, str],
    config: EMSConfig,
    state: EMSState,
) -> list[float]:
    """Compute SOC% trajectory for all slots using the finalized schedule.

    Returns a list of SOC% values (one per slot, from slot 0 to num_slots-1).
    Past slots use current_kwh as placeholder (frontend uses soc_history
    for past slots instead). Future slots simulate forward with PV,
    consumption, and scheduled actions.
    """
    pv_confidence = _calculate_pv_confidence(
        state.pv_hourly_kwh, state.pv_actual_today_kwh,
        state.current_hour, state.current_minute,
        previous_confidence=state.previous_pv_confidence,
    )
    energy_per_slot = config.safe_power_kw * (minutes_per_slot / 60.0)
    min_kwh = (config.battery_discharge_min_pct / 100.0) * config.battery_capacity_kwh
    cap = config.battery_capacity_kwh
    current_pct = max(0.0, min(100.0, (current_kwh / cap) * 100.0)) if cap > 0 else 0.0

    trajectory: list[float] = []
    soc = current_kwh

    for i in range(num_slots):
        if i < current_slot:
            trajectory.append(round(current_pct, 1))
            continue

        if i == current_slot:
            soc = current_kwh

        pct = max(0.0, min(100.0, (soc / cap) * 100.0)) if cap > 0 else 0.0
        trajectory.append(round(pct, 1))

        hour = int((i * minutes_per_slot) / 60)
        pv_kwh = (state.pv_hourly_kwh or {}).get(hour, 0.0) * pv_confidence
        pv_per_slot = pv_kwh * (minutes_per_slot / 60.0)

        if state.consumption_hourly_kwh and hour in state.consumption_hourly_kwh:
            cons = state.consumption_hourly_kwh[hour] * (minutes_per_slot / 60.0)
        else:
            cons = (config.consumption_est_kwh / num_slots)

        delta = pv_per_slot - cons
        action = scheduled_slots.get(i)
        if action == "charge":
            grid_kw = min(config.safe_power_kw,
                          max(0.0, config.inverter_max_power_kw - pv_kwh * pv_confidence))
            delta += grid_kw * (minutes_per_slot / 60.0) * config.efficiency
        elif action == "discharge" and soc > min_kwh:
            delta -= min(energy_per_slot, soc - min_kwh)

        soc = max(min_kwh, min(cap, soc + delta))

    return trajectory


def _validate_schedule_soc(
    remaining: list[tuple[int, float]],
    charge_slots: set[int],
    discharge_slots: set[int],
    current_kwh: float,
    consumption_per_slot: float,
    pv_hourly_kwh: dict[int, float] | None,
    minutes_per_slot: float,
    pv_confidence: float,
    battery_capacity: float,
    min_kwh: float,
    energy_per_slot: float,
    efficiency: float,
    inverter_max_power_kw: float = 0.0,
    safe_power_kw: float = 0.0,
    consumption_hourly_kwh: dict[int, float] | None = None,
    keep_all_negative_charges: bool = False,
) -> tuple[set[int], set[int]]:
    """Validate schedule by simulating SOC at every slot, pruning violations.

    Like the VB Sell macro, this checks that SOC stays within [min_kwh,
    battery_capacity] at every time slot.  If a discharge would cause SOC to
    dip below min, it is removed (least valuable first).  If a charge would
    push SOC above capacity, it is removed (most expensive first).

    When keep_all_negative_charges is True, negative-price charge slots are
    never pruned for overflow (except phantom-charge slots where the battery
    is already at capacity entering the slot — those can't physically execute
    regardless of price).  Used by the charge_to_full_on_negative_price
    strategy: user has opted to charge during all negative slots, accepting
    that some PV may be curtailed.

    Returns pruned (charge_slots, discharge_slots).
    """
    charge_slots = set(charge_slots)
    discharge_slots = set(discharge_slots)

    # Build price lookup from remaining
    price_of: dict[int, float] = {idx: price for idx, price in remaining}

    # Check if PV alone would fill the battery (net surplus > available space).
    # When true, overflow is PV-caused — pruning negative-price charge slots
    # won't prevent it, and the negative-price income is pure profit.
    pv_surplus_total = 0.0
    for slot_idx, _ in remaining:
        hour = int((slot_idx * minutes_per_slot) / 60)
        pv_kwh = (pv_hourly_kwh or {}).get(hour, 0.0) * pv_confidence
        pv_per_slot = pv_kwh * (minutes_per_slot / 60.0)
        if consumption_hourly_kwh and hour in consumption_hourly_kwh:
            cons = consumption_hourly_kwh[hour] * (minutes_per_slot / 60.0)
        else:
            cons = consumption_per_slot
        surplus = pv_per_slot - cons
        if surplus > 0:
            pv_surplus_total += surplus
    # pv_surplus_total is logged when a negative-price slot is kept due
    # to PV-caused overflow.  The actual exemption check is per-slot
    # (violation_pv_caused) below.

    # PV-overflow exemption only applies when the battery has real room
    # to fill.  When current_kwh is already at/near capacity, the exemption
    # would preserve negative-price slots that the inverter can't actually
    # execute (BMS rejects charging a full battery), producing phantom
    # schedule entries.
    pv_fills_battery = (
        current_kwh < battery_capacity * 0.95
        and pv_surplus_total >= (battery_capacity - current_kwh) * 0.9
    )

    max_iterations = len(charge_slots) + len(discharge_slots) + 1

    for _ in range(max_iterations):
        violation_slot: int | None = None
        violation_type: str | None = None  # "low" or "high"
        # Whether the charge action at the violation slot is wasted: when
        # the battery is already at capacity entering a charge slot, the
        # inverter can't physically store any of the grid energy.  Such
        # phantom slots must be dropped regardless of price.
        violation_charge_wasted = False
        discharge_seen = False

        soc = current_kwh
        for slot_idx, _ in remaining:
            hour = int((slot_idx * minutes_per_slot) / 60)
            pv_kwh = (pv_hourly_kwh or {}).get(hour, 0.0) * pv_confidence
            pv_per_slot = pv_kwh * (minutes_per_slot / 60.0)

            if consumption_hourly_kwh and hour in consumption_hourly_kwh:
                cons = consumption_hourly_kwh[hour] * (minutes_per_slot / 60.0)
            else:
                cons = consumption_per_slot
            delta = pv_per_slot - cons
            charge_contribution = 0.0
            if slot_idx in charge_slots:
                if inverter_max_power_kw > 0:
                    grid_kw = min(safe_power_kw or energy_per_slot / (minutes_per_slot / 60.0),
                                  max(0.0, inverter_max_power_kw - pv_kwh * pv_confidence))
                    charge_contribution = grid_kw * (minutes_per_slot / 60.0) * efficiency
                else:
                    charge_contribution = energy_per_slot * efficiency
                delta += charge_contribution
            if slot_idx in discharge_slots:
                delta -= energy_per_slot
                discharge_seen = True

            soc_before = soc
            soc_ideal = soc + delta

            # Low-bound check: SOC dipping below min is always a violation.
            if soc_ideal < min_kwh - 0.01:
                if not discharge_seen:
                    soc = max(0.0, min(battery_capacity, soc_ideal))
                    continue
                violation_slot = slot_idx
                violation_type = "low"
                break
            if soc_ideal > battery_capacity + 0.01:
                # Determine causality.  If PV alone (no charge action)
                # would also overflow at this slot, the natural BMS
                # behaviour is to spill the excess — clamp soc and keep
                # simulating to detect any genuine charge-caused issues
                # at later slots (e.g., a phantom charge during clamped
                # hours that would otherwise be missed).
                soc_no_charge = soc_before + (delta - charge_contribution)
                pv_alone_overflows = soc_no_charge > battery_capacity + 0.01
                slot_price = price_of.get(slot_idx, 0.0)
                # User opted into charge_to_full_on_negative_price: treat
                # phantom negative-price slots as harmless (the inverter
                # may try to charge but BMS will gate it).  Skip phantom
                # marking and just clamp soc, so the slot stays scheduled.
                if (keep_all_negative_charges
                        and charge_contribution > 0
                        and slot_price < 0):
                    soc = battery_capacity
                    continue
                if pv_alone_overflows:
                    # Phantom charge: action present on already-full battery.
                    # Drop regardless of price — the energy can't land.
                    if (charge_contribution > 0
                            and soc_before >= battery_capacity - 0.01):
                        violation_charge_wasted = True
                        violation_slot = slot_idx
                        violation_type = "high"
                        break
                    # PV-only spill: clamp and continue simulating.
                    soc = battery_capacity
                    continue
                # Charge action contributes to / causes overflow.
                if (charge_contribution > 0
                        and soc_before >= battery_capacity - 0.01):
                    violation_charge_wasted = True
                violation_slot = slot_idx
                violation_type = "high"
                break

            # Clamp for next iteration
            soc = max(0.0, min(battery_capacity, soc_ideal))

        if violation_slot is None:
            break  # Schedule is valid

        if violation_type == "low":
            # Remove the least valuable discharge at or before violation
            candidates = [
                s for s in discharge_slots
                if s <= violation_slot
            ]
            if not candidates:
                candidates = list(discharge_slots)
            if not candidates:
                break
            # Remove the one with lowest price (least profitable to sell)
            drop = min(candidates, key=lambda s: price_of.get(s, 0.0))
            discharge_slots.discard(drop)
            _LOGGER.debug(
                "SOC validation: dropped discharge slot %d (price=%.3f) "
                "due to low SOC at slot %d",
                drop, price_of.get(drop, 0.0), violation_slot,
            )
        else:
            # Remove the most expensive charge at or before violation.
            # Prefer dropping non-negative slots first; fall back to
            # negative-price slots only when PV alone wouldn't overflow.
            candidates = [
                s for s in charge_slots
                if s <= violation_slot and price_of.get(s, 0.0) >= 0
            ]
            if not candidates:
                candidates = [
                    s for s in charge_slots
                    if price_of.get(s, 0.0) >= 0
                ]
            if not candidates:
                # Only negative-price slots remain.  If PV alone would
                # fill the battery (and the battery has room to fill),
                # the overflow is PV-caused — pruning negative-price
                # slots won't prevent it, and the income from charging
                # at negative prices is pure profit.
                # EXCEPTION: when the violation slot's charge action is
                # wasted (battery was already at capacity entering it),
                # the negative-price slot is phantom — drop it anyway.
                if pv_fills_battery and not violation_charge_wasted:
                    _LOGGER.debug(
                        "SOC validation: keeping negative-price charge slots — "
                        "PV surplus (%.1f kWh) fills battery anyway",
                        pv_surplus_total,
                    )
                    break
                # User opted into charge_to_full_on_negative_price: keep
                # negative-price slots even when PV alone wouldn't fill
                # the battery.  Some PV may be curtailed but the user has
                # explicitly chosen this trade-off (revenue at p<0 slots).
                if keep_all_negative_charges and not violation_charge_wasted:
                    _LOGGER.debug(
                        "SOC validation: keeping negative-price charge slots "
                        "(charge_to_full_on_negative_price=True)",
                    )
                    break
                candidates = [
                    s for s in charge_slots
                    if s <= violation_slot
                ]
            if not candidates:
                candidates = list(charge_slots)
            if not candidates:
                break
            # Remove the most expensive charge slot
            drop = max(candidates, key=lambda s: price_of.get(s, 0.0))
            charge_slots.discard(drop)
            _LOGGER.debug(
                "SOC validation: dropped charge slot %d (price=%.3f) "
                "due to high SOC at slot %d",
                drop, price_of.get(drop, 0.0), violation_slot,
            )

    return charge_slots, discharge_slots


def _select_discharges_for_pv_headroom(
    remaining: list[tuple[int, float]],
    current_kwh: float,
    scheduled_charge: set[int],
    config: EMSConfig,
    state: EMSState,
    minutes_per_slot: float,
    pv_confidence: float,
    reserve_target: float,
) -> set[int]:
    """Pre-emptively discharge before negative-price PV windows.

    Used by the discharge_to_make_room_for_negative_price strategy.  Walks
    forward through remaining slots simulating SOC.  Whenever a negative-
    price slot with PV surplus would overflow the battery, schedules
    discharge in the most expensive positive-price earlier slots (which
    aren't already charge slots) to create headroom.

    Discharges may temporarily dip SOC below reserve_target during the
    day (the negative-window PV will refill it), but must never violate
    the absolute discharge_min floor, and end-of-day SOC after all
    simulation must remain >= reserve_target so overnight is covered.

    Returns set of slot indices to discharge.
    """
    discharge: set[int] = set()
    if not state.pv_hourly_kwh or not remaining:
        return discharge

    max_battery_kwh = (config.battery_charge_max_pct / 100.0) * config.battery_capacity_kwh
    min_kwh_floor = (config.battery_discharge_min_pct / 100.0) * config.battery_capacity_kwh
    energy_per_slot = config.safe_power_kw * (minutes_per_slot / 60.0)
    cons_per_slot_default = config.consumption_est_kwh / max(1, len(state.slot_prices_today or []))
    if cons_per_slot_default <= 0:
        cons_per_slot_default = config.consumption_est_kwh * (minutes_per_slot / 60.0) / 24.0

    def _slot_cons(hour: int) -> float:
        if state.consumption_hourly_kwh and hour in state.consumption_hourly_kwh:
            return state.consumption_hourly_kwh[hour] * (minutes_per_slot / 60.0)
        return cons_per_slot_default

    def _slot_pv(hour: int) -> float:
        return (state.pv_hourly_kwh or {}).get(hour, 0.0) * pv_confidence * (minutes_per_slot / 60.0)

    def _project_soc(extra_discharge: set[int]) -> tuple[dict[int, float], float, float]:
        """Project SOC entering each slot given current schedule + extra discharges.

        Returns (per_slot_entering, soc_at_end, min_soc_observed).
        """
        soc = current_kwh
        per_slot: dict[int, float] = {}
        min_soc = soc
        for idx, _ in remaining:
            per_slot[idx] = soc
            hour = int((idx * minutes_per_slot) / 60)
            pv = _slot_pv(hour)
            cons = _slot_cons(hour)
            delta = pv - cons
            if idx in scheduled_charge:
                pv_kw_rate = (state.pv_hourly_kwh or {}).get(hour, 0.0) * pv_confidence
                grid_kw = min(config.safe_power_kw,
                              max(0.0, config.inverter_max_power_kw - pv_kw_rate))
                delta += grid_kw * (minutes_per_slot / 60.0) * config.efficiency
            if idx in (discharge | extra_discharge):
                delta -= energy_per_slot
            soc_raw = soc + delta  # before clamp — captures true dip
            min_soc = min(min_soc, soc_raw)
            soc = max(0.0, min(max_battery_kwh, soc_raw))
        return per_slot, soc, min_soc

    # Iterate (bounded) until no more overflow at negative+PV slots is reducible.
    max_passes = len(remaining)
    for _ in range(max_passes):
        soc_in, _, _ = _project_soc(set())
        # Find the FIRST negative-price slot with PV surplus that would overflow.
        target_idx: int | None = None
        for idx, price in remaining:
            if price is None or price >= 0:
                continue
            hour = int((idx * minutes_per_slot) / 60)
            pv = _slot_pv(hour)
            cons = _slot_cons(hour)
            if pv <= cons:
                continue
            # SOC at end of this slot if no discharge added
            soc_end = soc_in[idx] + (pv - cons)
            if idx in scheduled_charge:
                pv_kw_rate = (state.pv_hourly_kwh or {}).get(hour, 0.0) * pv_confidence
                grid_kw = min(config.safe_power_kw,
                              max(0.0, config.inverter_max_power_kw - pv_kw_rate))
                soc_end += grid_kw * (minutes_per_slot / 60.0) * config.efficiency
            if soc_end > max_battery_kwh + 0.01:
                target_idx = idx
                break
        if target_idx is None:
            break

        # Find earlier positive-price slots, not already charge/discharge, that
        # we can discharge in.  Prefer the MOST expensive (highest revenue).
        candidates = [
            (idx, p) for idx, p in remaining
            if idx < target_idx
            and p is not None and p > 0
            and idx not in scheduled_charge
            and idx not in discharge
        ]
        if not candidates:
            break
        candidates.sort(key=lambda x: -x[1])  # most expensive first

        added_this_pass = False
        for cand_idx, _ in candidates:
            # A discharge is acceptable when:
            #  - SOC never drops below the absolute min_kwh floor at any
            #    point in the simulation (hardware safety);
            #  - end-of-day SOC remains >= reserve_target (overnight
            #    self-consumption protection).
            # Temporary dips below reserve_target during the day are OK
            # because the negative-window PV refills the battery.
            _, soc_end_day, soc_min_observed = _project_soc({cand_idx})
            if soc_min_observed < min_kwh_floor - 0.01:
                continue
            if soc_end_day < reserve_target - 0.01:
                continue
            discharge.add(cand_idx)
            added_this_pass = True
            # Did this resolve the overflow at target_idx?
            new_soc, _, _ = _project_soc(set())
            hour = int((target_idx * minutes_per_slot) / 60)
            pv = _slot_pv(hour)
            cons = _slot_cons(hour)
            soc_end = new_soc[target_idx] + (pv - cons)
            if target_idx in scheduled_charge:
                pv_kw_rate = (state.pv_hourly_kwh or {}).get(hour, 0.0) * pv_confidence
                grid_kw = min(config.safe_power_kw,
                              max(0.0, config.inverter_max_power_kw - pv_kw_rate))
                soc_end += grid_kw * (minutes_per_slot / 60.0) * config.efficiency
            if soc_end <= max_battery_kwh + 0.01:
                break
            # else: keep adding more discharge slots
        if not added_this_pass:
            break

    if discharge:
        _LOGGER.info(
            "Make-room discharge: added %d slot(s) before negative-price PV "
            "window(s): %s (current=%.1f kWh, max=%.1f kWh, reserve=%.1f kWh)",
            len(discharge), sorted(discharge), current_kwh, max_battery_kwh,
            reserve_target,
        )
    return discharge


def select_unified_charge_slots(
    remaining_today: list[tuple[int, float]],
    energy_deficit: float,
    effective_per_slot: float,
    battery_capacity: float,
    discharge_min_pct: float,
    consumption_est: float,
    efficiency: float,
    energy_per_slot: float,
    current_kwh: float = 0.0,
    net_pv: float = 0.0,
    charge_max_pct: float = 100.0,
    slot_prices_tomorrow: list[float | None] | None = None,
    pv_forecast_tomorrow: float | None = None,
    pv_hourly_kwh: dict[int, float] | None = None,
    current_hour: int = 12,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]], float]:
    """Select charge slots from a unified today+tomorrow pool.

    Returns:
        (today_selected, tomorrow_selected, tomorrow_charge_kwh)
    """
    today_pool = [(p, 0, i) for i, p in remaining_today]

    tomorrow_pool: list[tuple[float, int, int]] = []
    tomorrow_charge_kwh = 0.0
    if slot_prices_tomorrow:
        tomorrow_pool = [
            (slot_prices_tomorrow[i], 1, i)
            for i in range(len(slot_prices_tomorrow))
            if slot_prices_tomorrow[i] is not None
        ]

    tomorrow_deficit = 0.0
    if tomorrow_pool:
        tomorrow_reserve = calculate_self_consumption_reserve(
            consumption_est, pv_hourly_kwh)
        min_kwh = (discharge_min_pct / 100.0) * battery_capacity
        tomorrow_pv = pv_forecast_tomorrow or 0.0

        # Project battery at midnight
        hours_to_midnight = max(1, 24 - current_hour)
        drain_to_midnight = (consumption_est / 24.0) * hours_to_midnight
        today_charge = len([s for s in today_pool if s[0] <= 0]) * effective_per_slot
        today_charge += energy_deficit
        projected_midnight = max(min_kwh, min(
            battery_capacity,
            current_kwh + net_pv + today_charge - drain_to_midnight
        ))

        tomorrow_reserve_target = min(battery_capacity, min_kwh + tomorrow_reserve)
        daytime_gap = max(0.0, consumption_est - tomorrow_pv)
        # PV surplus beyond consumption will charge the battery during the day,
        # reducing the need for grid charging overnight.
        tomorrow_pv_surplus = max(0.0, tomorrow_pv - consumption_est)
        tomorrow_shortfall = max(0.0,
                                 tomorrow_reserve_target + daytime_gap
                                 - projected_midnight - tomorrow_pv_surplus)
        tomorrow_deficit = tomorrow_shortfall

        _LOGGER.debug(
            "Tomorrow deficit: reserve_target=%.1f, projected_midnight=%.1f, "
            "daytime_gap=%.1f, pv_surplus=%.1f (pv=%.1f, consumption=%.1f), "
            "shortfall=%.1f",
            tomorrow_reserve_target, projected_midnight, daytime_gap,
            tomorrow_pv_surplus, tomorrow_pv, consumption_est,
            tomorrow_shortfall,
        )

    total_deficit = energy_deficit + tomorrow_deficit

    # Combine and sort by price
    combined = today_pool + tomorrow_pool
    negative = [s for s in combined if s[0] < 0]
    non_negative = sorted([s for s in combined if s[0] >= 0], key=lambda x: x[0])

    negative_energy = len(negative) * effective_per_slot
    remaining_deficit = max(0.0, total_deficit - negative_energy)
    needed = math.ceil(remaining_deficit / effective_per_slot) if effective_per_slot > 0 else 0

    selected = negative + non_negative[:needed]

    # Split back into today and tomorrow
    today_selected = [s for s in selected if s[1] == 0]
    tomorrow_selected = [s for s in selected if s[1] == 1]

    # --- Battery headroom constraint ---
    # Subtract net PV surplus: that energy will also fill the battery,
    # so real headroom for grid charging is smaller than raw capacity gap.
    max_battery_kwh = (charge_max_pct / 100.0) * battery_capacity
    pv_fill = max(0.0, net_pv)
    headroom = max(0.0, max_battery_kwh - current_kwh - pv_fill)
    max_today_slots = math.floor(headroom / effective_per_slot) if effective_per_slot > 0 else 0
    today_deficit_slots = math.ceil(energy_deficit / effective_per_slot) if effective_per_slot > 0 and energy_deficit > 0 else 0
    neg_today_count = sum(1 for s in today_selected if s[0] < 0)
    # Allow negative-price slots through: they're profitable (paid to consume).
    # SOC validation downstream will prune any that would cause battery overflow
    # when combined with PV production.
    if pv_fill <= 0:
        max_today_slots = max(max_today_slots, today_deficit_slots, neg_today_count)
    else:
        max_today_slots = max(max_today_slots, neg_today_count)

    if len(today_selected) > max_today_slots:
        today_selected.sort(key=lambda x: x[0])
        excess = today_selected[max_today_slots:]
        today_selected = today_selected[:max_today_slots]
        tomorrow_selected_indices = {s[2] for s in tomorrow_selected}
        available_tmr = sorted(
            [s for s in tomorrow_pool if s[0] >= 0 and s[2] not in tomorrow_selected_indices],
            key=lambda x: x[0],
        )
        replacements = available_tmr[:len(excess)]
        tomorrow_selected.extend(replacements)
        if excess:
            _LOGGER.info(
                "Headroom cap: removed %d today slots (headroom=%.1f kWh, "
                "battery=%.1f/%.1f), replaced with %d tomorrow slots",
                len(excess), headroom, current_kwh, max_battery_kwh,
                len(replacements),
            )

    # --- Safety constraint: ensure battery survives until tomorrow's charging ---
    if tomorrow_selected and tomorrow_pool and slot_prices_tomorrow:
        min_kwh = (discharge_min_pct / 100.0) * battery_capacity
        num_slots_tmr = len(slot_prices_tomorrow)
        minutes_per_slot = (24 * 60) / num_slots_tmr

        earliest_tmr_slot = min(s[2] for s in tomorrow_selected)
        earliest_tmr_hour = int((earliest_tmr_slot * minutes_per_slot) / 60)
        hours_until_tmr_charge = max(1, (24 - current_hour) + earliest_tmr_hour)

        consumption_per_hour = consumption_est / 24.0
        bridge_consumption = consumption_per_hour * hours_until_tmr_charge

        today_charge_energy = len(today_selected) * effective_per_slot
        # Clamp: the inverter stops providing house power once SOC hits
        # min_kwh, so the battery cannot drain below that from consumption
        # alone.  Without this clamp the bridge over-estimates the shortfall
        # and forces expensive today slots when cheap tomorrow slots suffice.
        raw_projected = current_kwh + net_pv + today_charge_energy - bridge_consumption
        projected = max(min_kwh, raw_projected)

        if projected < min_kwh:
            shortfall_kwh = min_kwh - projected
            extra_today_needed = math.ceil(shortfall_kwh / effective_per_slot)

            today_selected_indices = {s[2] for s in today_selected}
            available_today = sorted(
                [s for s in today_pool if s[0] >= 0 and s[2] not in today_selected_indices],
                key=lambda x: x[0],
            )

            tomorrow_by_price = sorted(tomorrow_selected, key=lambda x: -x[0])

            swaps = min(extra_today_needed, len(available_today), len(tomorrow_by_price))
            for j in range(swaps):
                today_selected.append(available_today[j])
                tomorrow_selected.remove(tomorrow_by_price[j])

            _LOGGER.info(
                "Unified safety: swapped %d slots today↔tomorrow "
                "(bridge=%dh, projected=%.1f→%.1f, min=%.1f)",
                swaps, hours_until_tmr_charge, projected,
                projected + swaps * effective_per_slot, min_kwh,
            )

    # Convert to (slot_index, price) tuples
    today_result = [(s[2], s[0]) for s in today_selected]
    tomorrow_result = [(s[2], s[0]) for s in tomorrow_selected]
    tomorrow_charge_kwh = round(len(tomorrow_result) * effective_per_slot, 2)

    _LOGGER.info(
        "Unified slot selection: deficit_today=%.2f, deficit_tomorrow=%.2f, "
        "total=%.2f, today_slots=%d, tomorrow_slots=%d (%.1f kWh), "
        "pool_size=%d+%d",
        energy_deficit, tomorrow_deficit, total_deficit,
        len(today_result), len(tomorrow_result), tomorrow_charge_kwh,
        len(today_pool), len(tomorrow_pool),
    )

    return today_result, tomorrow_result, tomorrow_charge_kwh


def _compute_tomorrow_schedule(
    config: EMSConfig,
    state: EMSState,
    today_result: ScheduleResult,
    today_soc_trajectory: list[float],
) -> tuple[dict[int, str], list[float]]:
    """Compute tomorrow's charge/discharge schedule and SOC trajectory.

    Uses projected midnight SOC from today's trajectory as the starting
    point.  Charge slots come from the unified selection (already stored
    in today_result).  Sell slots are computed fresh using the same
    profitability filter and SOC validation as today.

    Returns (tomorrow_scheduled_slots, tomorrow_soc_trajectory).
    """
    tomorrow_prices = state.slot_prices_tomorrow
    if not tomorrow_prices or config.grid_mode == "off":
        return {}, []

    num_slots = len(tomorrow_prices)
    minutes_per_slot = (24 * 60) / num_slots
    slot_duration_hours = minutes_per_slot / 60.0
    energy_per_slot = config.safe_power_kw * slot_duration_hours
    effective_per_slot = energy_per_slot * config.efficiency
    round_trip_eff = config.efficiency * config.efficiency

    # Projected midnight SOC from today's trajectory (last value)
    min_kwh = (config.battery_discharge_min_pct / 100.0) * config.battery_capacity_kwh
    if today_soc_trajectory:
        midnight_pct = today_soc_trajectory[-1]
        midnight_kwh = max(min_kwh, (midnight_pct / 100.0) * config.battery_capacity_kwh)
    else:
        midnight_kwh = min_kwh

    # Build remaining slots for tomorrow (all are future)
    remaining = [(i, tomorrow_prices[i]) for i in range(num_slots)
                 if tomorrow_prices[i] is not None]
    if not remaining:
        return {}, []

    # Charge slots: from unified selection stored on today_result
    scheduled: dict[int, str] = {}
    charge_indices: set[int] = set()

    # The unified charge selection already picked tomorrow's charge slots.
    # Reconstruct them: they're the cheapest slots up to tomorrow_planned_slots.
    if today_result.tomorrow_planned_slots > 0 and config.grid_mode in ("from_grid", "both"):
        neg = [(i, p) for i, p in remaining if p < 0]
        non_neg = sorted([(i, p) for i, p in remaining if p >= 0], key=lambda x: x[1])
        neg_energy = len(neg) * effective_per_slot
        deficit = today_result.tomorrow_planned_kwh
        non_neg_needed = math.ceil(max(0, deficit - neg_energy) / effective_per_slot) if effective_per_slot > 0 else 0
        charge_slots = neg + non_neg[:non_neg_needed]
        for idx, _ in charge_slots:
            scheduled[idx] = "charge"
            charge_indices.add(idx)

    # Synthesize hourly PV for tomorrow (distribute total across daylight 6-18)
    pv_tomorrow_total = state.pv_forecast_tomorrow or 0.0
    daylight_hours = list(range(6, 18))
    pv_per_daylight_hour = pv_tomorrow_total / len(daylight_hours) if daylight_hours else 0.0
    pv_hourly_tomorrow: dict[int, float] = {}
    for h in daylight_hours:
        pv_hourly_tomorrow[h] = pv_per_daylight_hour

    # Validate tomorrow's charge slots: drop any that would overflow.
    # Without this, a negative or near-zero-price slot picked by the
    # unified selector ends up scheduled even when SOC is already pegged
    # at 100% from PV — a phantom "charge" the inverter can't execute.
    if charge_indices:
        consumption_per_slot_t = config.consumption_est_kwh / num_slots
        validated_charge_t, _ = _validate_schedule_soc(
            remaining, set(charge_indices), set(),
            midnight_kwh, consumption_per_slot_t,
            pv_hourly_tomorrow, minutes_per_slot, 1.0,
            config.battery_capacity_kwh, min_kwh,
            energy_per_slot, config.efficiency,
            consumption_hourly_kwh=state.consumption_hourly_kwh,
            inverter_max_power_kw=config.inverter_max_power_kw,
            safe_power_kw=config.safe_power_kw,
        )
        dropped_t = [i for i in charge_indices if i not in validated_charge_t]
        for idx in dropped_t:
            scheduled.pop(idx, None)
            charge_indices.discard(idx)
        if dropped_t:
            _LOGGER.info(
                "Tomorrow schedule: dropped %d charge slot(s) that would "
                "overflow battery (midnight_kwh=%.1f, capacity=%.1f): %s",
                len(dropped_t), midnight_kwh, config.battery_capacity_kwh,
                sorted(dropped_t),
            )

    # Sell slots (to_grid or both mode)
    if config.grid_mode in ("to_grid", "both"):
        reserve_kwh = calculate_self_consumption_reserve(
            config.consumption_est_kwh, state.pv_hourly_kwh)
        reserve_target = _compute_reserve_target(config, reserve_kwh)

        charge_energy = len(charge_indices) * effective_per_slot
        max_battery_kwh = (config.battery_charge_max_pct / 100.0) * config.battery_capacity_kwh

        # Arbitrage check for tomorrow
        arbitrage_active = False
        if config.arbitrage_price_delta > 0 and remaining:
            prices_vals = [p for _, p in remaining]
            spread = max(prices_vals) - min(prices_vals)
            if spread >= config.arbitrage_price_delta:
                arbitrage_active = True

        if arbitrage_active:
            sellable = max(0.0, max_battery_kwh - reserve_target) * config.efficiency * 0.85
        else:
            peak_kwh = min(max_battery_kwh, midnight_kwh + pv_tomorrow_total + charge_energy)
            sellable = max(0.0, peak_kwh - reserve_target) * config.efficiency * 0.85

        if sellable > 0:
            available = [(i, p) for i, p in remaining
                         if p > 0 and i not in charge_indices]
            if config.grid_mode == "both" and charge_indices:
                max_buy = max(tomorrow_prices[i] for i in charge_indices
                              if tomorrow_prices[i] is not None)
                min_sell = max_buy / round_trip_eff
                available = [(i, p) for i, p in available if p >= min_sell]

            available.sort(key=lambda x: -x[1])
            sell_needed = math.ceil(sellable / energy_per_slot) if energy_per_slot > 0 else 0
            sell_selected = available[:sell_needed]

            # SOC validation — use synthesized PV hourly for tomorrow
            consumption_per_slot = config.consumption_est_kwh / num_slots
            discharge_set = {s[0] for s in sell_selected}
            _, validated_discharge = _validate_schedule_soc(
                remaining, charge_indices, discharge_set,
                midnight_kwh, consumption_per_slot,
                pv_hourly_tomorrow, minutes_per_slot, 1.0,
                config.battery_capacity_kwh, reserve_target,
                energy_per_slot, config.efficiency,
                consumption_hourly_kwh=state.consumption_hourly_kwh,
                inverter_max_power_kw=config.inverter_max_power_kw,
                safe_power_kw=config.safe_power_kw,
            )
            for idx, _ in sell_selected:
                if idx in validated_discharge:
                    scheduled[idx] = "discharge"

    # SOC trajectory for tomorrow
    trajectory: list[float] = []
    cap = config.battery_capacity_kwh
    soc = midnight_kwh

    for i in range(num_slots):
        pct = max(0.0, min(100.0, (soc / cap) * 100.0)) if cap > 0 else 0.0
        trajectory.append(round(pct, 1))

        hour = int((i * minutes_per_slot) / 60)
        pv_kwh_rate = pv_per_daylight_hour if hour in daylight_hours else 0.0
        pv_per_slot = pv_kwh_rate * slot_duration_hours

        if state.consumption_hourly_kwh and hour in state.consumption_hourly_kwh:
            cons = state.consumption_hourly_kwh[hour] * slot_duration_hours
        else:
            cons = config.consumption_est_kwh / num_slots

        delta = pv_per_slot - cons
        action = scheduled.get(i)
        if action == "charge":
            grid_kw = min(config.safe_power_kw,
                          max(0.0, config.inverter_max_power_kw - pv_kwh_rate))
            delta += grid_kw * slot_duration_hours * config.efficiency
        elif action == "discharge" and soc > min_kwh:
            delta -= min(energy_per_slot, soc - min_kwh)

        soc = max(min_kwh, min(cap, soc + delta))

    return scheduled, trajectory


def calculate_schedule(config: EMSConfig, state: EMSState) -> ScheduleResult:
    """Calculate optimal charge/discharge schedule.

    Pure function — all inputs via config and state, no HA dependencies.
    """
    result = ScheduleResult()

    if config.grid_mode == "off" or not state.slot_prices_today:
        result.status = "off" if config.grid_mode == "off" else "no_price_data"
        return result

    prices = state.slot_prices_today
    num_slots = len(prices)
    minutes_per_slot = (24 * 60) / num_slots
    current_slot = int((state.current_hour * 60 + state.current_minute) / minutes_per_slot)
    current_slot = min(current_slot, num_slots - 1)
    slot_duration_hours = minutes_per_slot / 60.0

    remaining = [(i, prices[i]) for i in range(current_slot, num_slots) if prices[i] is not None]

    if not remaining:
        result.status = "day_complete"
        return result

    # When per-hour PV data is unavailable, synthesize from daily forecast
    # so that _project_soc_trajectory can account for solar production.
    # Forecast service downtime fallback: if pv_forecast_today is missing
    # but the coordinator supplied pv_fallback_today_kwh (7-day actual avg),
    # use that instead of treating PV as zero — otherwise the algorithm
    # over-charges from grid on every clear day after a forecast outage.
    forecast_total = state.pv_forecast_today
    forecast_remaining = state.pv_forecast_remaining
    used_fallback = False
    if (not forecast_total or forecast_total <= 0) and state.pv_fallback_today_kwh:
        forecast_total = state.pv_fallback_today_kwh
        # Estimate remaining as: total × (remaining_daylight / total_daylight)
        # using the 6-20 daylight window from _synthesize_pv_hourly.
        daylight_start, daylight_end = 6, 20
        remaining_daylight_min = max(
            0,
            (daylight_end * 60) - max(
                state.current_hour * 60 + state.current_minute,
                daylight_start * 60,
            ),
        )
        total_daylight_min = (daylight_end - daylight_start) * 60
        if total_daylight_min > 0:
            forecast_remaining = forecast_total * (remaining_daylight_min / total_daylight_min)
        used_fallback = True

    if (not state.pv_hourly_kwh
            and forecast_total and forecast_total > 0
            and forecast_remaining and forecast_remaining > 0):
        state = EMSState(
            battery_soc_pct=state.battery_soc_pct,
            slot_prices_today=state.slot_prices_today,
            slot_prices_tomorrow=state.slot_prices_tomorrow,
            pv_hourly_kwh=_synthesize_pv_hourly(forecast_total),
            pv_forecast_remaining=forecast_remaining,
            pv_forecast_today=forecast_total,
            pv_forecast_tomorrow=state.pv_forecast_tomorrow,
            pv_actual_today_kwh=state.pv_actual_today_kwh,
            consumption_hourly_kwh=state.consumption_hourly_kwh,
            previous_pv_confidence=state.previous_pv_confidence,
            last_modbus_read_ts=state.last_modbus_read_ts,
            pv_fallback_today_kwh=state.pv_fallback_today_kwh,
            current_hour=state.current_hour,
            current_minute=state.current_minute,
        )
        if used_fallback:
            _LOGGER.warning(
                "PV forecast unavailable — using fallback (%.1f kWh from "
                "7-day actual avg, %.1f kWh remaining)",
                forecast_total, forecast_remaining,
            )
        else:
            _LOGGER.debug(
                "Synthesized hourly PV from daily forecast (%.1f kWh)",
                forecast_total,
            )

    battery_soc = state.battery_soc_pct
    current_kwh = (battery_soc / 100.0) * config.battery_capacity_kwh if battery_soc is not None else 0.0
    net_pv = calculate_net_pv_surplus(
        remaining, num_slots, config.consumption_est_kwh,
        state.pv_hourly_kwh, state.pv_forecast_remaining,
        state.pv_actual_today_kwh, state.pv_forecast_today,
        state.current_hour, state.current_minute,
    )
    energy_per_slot = config.safe_power_kw * slot_duration_hours

    if config.grid_mode == "from_grid":
        result = _schedule_from_grid(
            config, state, remaining, current_kwh, net_pv,
            energy_per_slot, num_slots, current_slot,
        )
    elif config.grid_mode == "to_grid":
        result = _schedule_to_grid(
            config, state, remaining, current_kwh, net_pv,
            energy_per_slot, num_slots, current_slot,
        )
    elif config.grid_mode == "both":
        result = _schedule_both(
            config, state, remaining, current_kwh, net_pv,
            energy_per_slot, num_slots, current_slot,
        )

    # Urgent recovery: when battery is below discharge_min, force immediate
    # charge slots starting from the current slot.  The cheapest-slot optimizer
    # may schedule charge hours from now, leaving the battery critically low
    # in the meantime.  This ensures the inverter starts charging NOW.
    min_kwh = (config.battery_discharge_min_pct / 100.0) * config.battery_capacity_kwh
    if (current_kwh < min_kwh
            and config.grid_mode in ("from_grid", "both")
            and result.scheduled_slots is not None):
        effective_per_slot = config.safe_power_kw * (minutes_per_slot / 60.0) * config.efficiency
        recovery_needed = min_kwh - current_kwh
        urgent_slots_needed = math.ceil(recovery_needed / effective_per_slot) if effective_per_slot > 0 else 0
        added = 0
        for i in range(current_slot, num_slots):
            if added >= urgent_slots_needed:
                break
            if result.scheduled_slots.get(i) != "charge":
                result.scheduled_slots[i] = "charge"
                added += 1
        if added:
            _LOGGER.info(
                "Urgent recovery: battery at %.1f kWh (min=%.1f), "
                "forced %d immediate charge slot(s)",
                current_kwh, min_kwh, added,
            )

    # Update status
    if not result.scheduled_slots:
        if result.status not in ("off", "no_price_data", "day_complete"):
            result.status = "no_action_needed"
    elif current_slot in result.scheduled_slots:
        result.status = "active"
    else:
        result.status = "waiting"

    # Compute authoritative SOC trajectory with the finalized schedule
    result.soc_trajectory = _compute_scheduled_soc_trajectory(
        prices, num_slots, minutes_per_slot,
        current_kwh, current_slot,
        result.scheduled_slots,
        config, state,
    )

    # Compute tomorrow's schedule and trajectory (if tomorrow prices exist)
    if state.slot_prices_tomorrow:
        tmr_slots, tmr_traj = _compute_tomorrow_schedule(
            config, state, result, result.soc_trajectory,
        )
        result.tomorrow_scheduled_slots = tmr_slots
        result.tomorrow_soc_trajectory = tmr_traj

    return result


def _schedule_from_grid(
    config: EMSConfig,
    state: EMSState,
    remaining: list[tuple[int, float]],
    current_kwh: float,
    net_pv: float,
    energy_per_slot: float,
    num_slots: int,
    current_slot: int,
) -> ScheduleResult:
    """Schedule from_grid mode: charge at cheapest prices."""
    result = ScheduleResult()
    effective_per_slot = energy_per_slot * config.efficiency

    reserve_kwh = calculate_self_consumption_reserve(
        config.consumption_est_kwh, state.pv_hourly_kwh)
    result.self_consumption_reserve = round(reserve_kwh, 2)

    min_kwh = (config.battery_discharge_min_pct / 100.0) * config.battery_capacity_kwh
    reserve_target = _compute_reserve_target(config, reserve_kwh)
    result.reserve_target_pct = round(
        (reserve_target / config.battery_capacity_kwh) * 100.0, 1,
    ) if config.battery_capacity_kwh > 0 else 0.0

    battery_shortfall = max(0.0, reserve_target - current_kwh)
    snapshot_deficit = max(0.0, battery_shortfall - net_pv)

    # Predictive: simulate SOC trajectory to catch future shortfalls
    minutes_per_slot = (24 * 60) / num_slots
    consumption_per_slot = config.consumption_est_kwh / num_slots
    pv_confidence = _calculate_pv_confidence(
        state.pv_hourly_kwh, state.pv_actual_today_kwh,
        state.current_hour, state.current_minute,
        previous_confidence=state.previous_pv_confidence,
    )
    _, min_projected, max_projected = _project_soc_trajectory(
        remaining, current_kwh, consumption_per_slot,
        state.pv_hourly_kwh, minutes_per_slot, pv_confidence,
        config.battery_capacity_kwh,
        consumption_hourly_kwh=state.consumption_hourly_kwh,
    )
    predictive_deficit = max(0.0, reserve_target - min_projected)

    # When solar will fill the battery to (near) capacity, grid charging
    # cannot add more useful energy — the battery will be full regardless.
    # Any evening drain below reserve_target is unavoidable and cannot be
    # prevented by charging earlier.
    max_battery_kwh = (config.battery_charge_max_pct / 100.0) * config.battery_capacity_kwh
    if max_projected >= max_battery_kwh * 0.95:
        predictive_deficit = 0.0

    base_deficit = max(snapshot_deficit, predictive_deficit)

    _LOGGER.debug(
        "from_grid deficit: snapshot=%.2f, predictive=%.2f "
        "(min_projected=%.1f, max_projected=%.1f, reserve_target=%.1f)",
        snapshot_deficit, predictive_deficit, min_projected, max_projected,
        reserve_target,
    )

    if config.yesterday_deficit_kwh > 0 and battery_shortfall > base_deficit:
        carryover = min(config.yesterday_deficit_kwh, battery_shortfall - base_deficit)
        energy_deficit = base_deficit + carryover
    else:
        energy_deficit = base_deficit

    selected, tomorrow_slots, tomorrow_charge_kwh = select_unified_charge_slots(
        remaining, energy_deficit, effective_per_slot,
        config.battery_capacity_kwh, config.battery_discharge_min_pct,
        config.consumption_est_kwh, config.efficiency, energy_per_slot,
        current_kwh=current_kwh, net_pv=net_pv,
        charge_max_pct=config.battery_charge_max_pct,
        slot_prices_tomorrow=state.slot_prices_tomorrow,
        pv_forecast_tomorrow=state.pv_forecast_tomorrow,
        pv_hourly_kwh=state.pv_hourly_kwh,
        current_hour=state.current_hour,
    )

    result.tomorrow_precharge = round(-tomorrow_charge_kwh, 2) if tomorrow_charge_kwh > 0 else 0.0
    result.tomorrow_planned_slots = len(tomorrow_slots)
    result.tomorrow_planned_kwh = tomorrow_charge_kwh

    # charge_to_full_on_negative_price: when any negative-price slot
    # exists in the remaining window, ensure ALL of them get scheduled by
    # adding them to the selection (deduplicated).  The user has opted to
    # charge at every p<0 slot for the revenue, accepting potential PV
    # curtailment.  SOC validation will keep these slots even if they'd
    # otherwise overflow (see keep_all_negative_charges below).
    if config.charge_to_full_on_negative_price:
        selected_indices = {idx for idx, _ in selected}
        for idx, price in remaining:
            if price is not None and price < 0 and idx not in selected_indices:
                selected.append((idx, price))
                selected_indices.add(idx)

    if not selected and not config.discharge_to_make_room_for_negative_price:
        result.status = "no_action_needed"
        return result

    # Per-slot SOC validation: ensure charge doesn't push SOC above capacity
    charge_set = {s[0] for s in selected}
    validated_charge, _ = _validate_schedule_soc(
        remaining, charge_set, set(),
        current_kwh, consumption_per_slot,
        state.pv_hourly_kwh, minutes_per_slot, pv_confidence,
        config.battery_capacity_kwh, min_kwh,
        energy_per_slot, config.efficiency,
        consumption_hourly_kwh=state.consumption_hourly_kwh,
        inverter_max_power_kw=config.inverter_max_power_kw,
        safe_power_kw=config.safe_power_kw,
        keep_all_negative_charges=config.charge_to_full_on_negative_price,
    )
    selected = [(idx, p) for idx, p in selected if idx in validated_charge]

    # discharge_to_make_room_for_negative_price: in from_grid mode we
    # normally don't discharge.  When this opt-in is enabled, schedule
    # pre-emptive discharges before negative-price + PV windows so PV
    # can fill the battery without forced grid export at penalty rates.
    discharge_for_headroom: set[int] = set()
    if config.discharge_to_make_room_for_negative_price:
        discharge_for_headroom = _select_discharges_for_pv_headroom(
            remaining, current_kwh, set(validated_charge),
            config, state, minutes_per_slot, pv_confidence, reserve_target,
        )

    if not selected and not discharge_for_headroom:
        result.status = "no_action_needed"
        return result

    result.scheduled_slots = {s[0]: "charge" for s in selected}
    for idx in discharge_for_headroom:
        result.scheduled_slots[idx] = "discharge"
    result.cheap_slots_remaining = len(selected)
    result.grid_energy_planned = round(len(selected) * effective_per_slot, 2)

    if selected:
        result.price_threshold = max(s[1] for s in selected)

    return result


def _schedule_to_grid(
    config: EMSConfig,
    state: EMSState,
    remaining: list[tuple[int, float]],
    current_kwh: float,
    net_pv: float,
    energy_per_slot: float,
    num_slots: int,
    current_slot: int,
) -> ScheduleResult:
    """Schedule to_grid mode: sell at best prices with predictive awareness."""
    result = ScheduleResult()

    # Reserve-aware: protect self-consumption reserve
    reserve_kwh = calculate_self_consumption_reserve(
        config.consumption_est_kwh, state.pv_hourly_kwh)
    result.self_consumption_reserve = round(reserve_kwh, 2)
    reserve_target = _compute_reserve_target(config, reserve_kwh)
    result.reserve_target_pct = round(
        (reserve_target / config.battery_capacity_kwh) * 100.0, 1,
    ) if config.battery_capacity_kwh > 0 else 0.0

    # Predictive: project peak SOC to determine total sellable energy
    pv_confidence = _calculate_pv_confidence(
        state.pv_hourly_kwh, state.pv_actual_today_kwh,
        state.current_hour, state.current_minute,
        previous_confidence=state.previous_pv_confidence,
    )
    minutes_per_slot = (24 * 60) / num_slots
    consumption_per_slot = config.consumption_est_kwh / num_slots
    _, _, max_projected = _project_soc_trajectory(
        remaining, current_kwh, consumption_per_slot,
        state.pv_hourly_kwh, minutes_per_slot, pv_confidence,
        config.battery_capacity_kwh,
        consumption_hourly_kwh=state.consumption_hourly_kwh,
    )
    # Safety margin (15%): accounts for consumption estimate errors,
    # PV forecast uncertainty, and the gap between peak SOC (midday)
    # and actual SOC when discharge slots fire (evening).
    sellable = max(0.0, max_projected - reserve_target) * config.efficiency * 0.85

    _LOGGER.debug(
        "to_grid predictive: max_projected=%.1f, reserve_target=%.1f, "
        "sellable=%.1f kWh (with 15%% safety margin)",
        max_projected, reserve_target, sellable,
    )

    if sellable <= 0:
        result.status = "no_action_needed"
        return result

    # Filter sell candidates: strictly positive prices.  When
    # block_export_on_negative_price is True, zero-price slots also pass
    # through but negatives are blocked.
    positive_slots = [(i, p) for i, p in remaining if p > 0]

    # Apply battery cycle wear cost: every sold kWh wears the battery.
    cycle_cost = config.battery_cycle_cost_eur_kwh
    if config.optimization_priority == "longevity":
        cycle_cost = max(cycle_cost, 0.05)
    round_trip_eff = config.efficiency * config.efficiency
    cycle_cost_per_sold_kwh = cycle_cost / round_trip_eff if round_trip_eff > 0 else 0.0
    if cycle_cost_per_sold_kwh > 0:
        positive_slots = [(i, p) for i, p in positive_slots if p >= cycle_cost_per_sold_kwh]

    slots_needed = math.ceil(sellable / energy_per_slot) if energy_per_slot > 0 else 0
    sorted_slots = sorted(positive_slots, key=lambda x: -x[1])
    selected = sorted_slots[:slots_needed]

    # Per-slot SOC validation: ensure discharge doesn't drop SOC below min
    discharge_set = {s[0] for s in selected}
    _, validated_discharge = _validate_schedule_soc(
        remaining, set(), discharge_set,
        current_kwh, consumption_per_slot,
        state.pv_hourly_kwh, minutes_per_slot, pv_confidence,
        config.battery_capacity_kwh, reserve_target,
        energy_per_slot, config.efficiency,
        consumption_hourly_kwh=state.consumption_hourly_kwh,
        inverter_max_power_kw=config.inverter_max_power_kw,
        safe_power_kw=config.safe_power_kw,
    )
    selected = [(idx, p) for idx, p in selected if idx in validated_discharge]

    if not selected:
        result.status = "no_action_needed"
        return result

    result.scheduled_slots = {s[0]: "discharge" for s in selected}
    result.cheap_slots_remaining = len(result.scheduled_slots)
    result.grid_energy_planned = round(min(sellable, len(selected) * energy_per_slot), 2)

    if selected:
        result.price_threshold = min(s[1] for s in selected)

    return result


def _schedule_both(
    config: EMSConfig,
    state: EMSState,
    remaining: list[tuple[int, float]],
    current_kwh: float,
    net_pv: float,
    energy_per_slot: float,
    num_slots: int,
    current_slot: int,
) -> ScheduleResult:
    """Schedule both mode: charge cheap + sell expensive."""
    result = ScheduleResult()
    effective_per_slot = energy_per_slot * config.efficiency
    round_trip_eff = config.efficiency * config.efficiency

    reserve_kwh = calculate_self_consumption_reserve(
        config.consumption_est_kwh, state.pv_hourly_kwh)
    result.self_consumption_reserve = round(reserve_kwh, 2)

    reserve_target = _compute_reserve_target(config, reserve_kwh)
    result.reserve_target_pct = round(
        (reserve_target / config.battery_capacity_kwh) * 100.0, 1,
    ) if config.battery_capacity_kwh > 0 else 0.0
    battery_shortfall = max(0.0, reserve_target - current_kwh)
    snapshot_deficit = max(0.0, battery_shortfall - net_pv)

    # Predictive: simulate SOC trajectory for both charge and sell decisions
    minutes_per_slot = (24 * 60) / num_slots
    consumption_per_slot = config.consumption_est_kwh / num_slots
    pv_confidence = _calculate_pv_confidence(
        state.pv_hourly_kwh, state.pv_actual_today_kwh,
        state.current_hour, state.current_minute,
        previous_confidence=state.previous_pv_confidence,
    )
    _, min_projected, max_projected = _project_soc_trajectory(
        remaining, current_kwh, consumption_per_slot,
        state.pv_hourly_kwh, minutes_per_slot, pv_confidence,
        config.battery_capacity_kwh,
        consumption_hourly_kwh=state.consumption_hourly_kwh,
    )
    predictive_deficit = max(0.0, reserve_target - min_projected)

    # When solar will fill the battery to (near) capacity, grid charging
    # cannot add more useful energy — the battery will be full regardless.
    max_battery_kwh = (config.battery_charge_max_pct / 100.0) * config.battery_capacity_kwh
    if max_projected >= max_battery_kwh * 0.95:
        predictive_deficit = 0.0

    base_deficit = max(snapshot_deficit, predictive_deficit)

    _LOGGER.debug(
        "both deficit: snapshot=%.2f, predictive=%.2f "
        "(min=%.1f, max=%.1f, reserve=%.1f)",
        snapshot_deficit, predictive_deficit,
        min_projected, max_projected, reserve_target,
    )

    if config.yesterday_deficit_kwh > 0 and battery_shortfall > base_deficit:
        carryover = min(config.yesterday_deficit_kwh, battery_shortfall - base_deficit)
        energy_deficit = base_deficit + carryover
    else:
        energy_deficit = base_deficit

    # Both mode: charge to full capacity when profitable buy-sell pairs exist.
    # The user opted into arbitrage by selecting 'both' — the algorithm should
    # maximise stored energy available for selling at peak prices, not just
    # target the conservative reserve.  Skip when solar already fills the
    # battery (no point grid-charging).
    arbitrage_active = False
    prices_remaining = [p for _, p in remaining if p is not None]
    if prices_remaining and max_projected < max_battery_kwh * 0.95:
        cheapest_buy = min(prices_remaining)
        most_expensive = max(prices_remaining)
        min_profitable_sell = cheapest_buy / round_trip_eff
        if most_expensive >= min_profitable_sell:
            arbitrage_active = True
            full_charge_kwh = max_battery_kwh - current_kwh
            energy_deficit = max(energy_deficit, max(0.0, full_charge_kwh - net_pv))
            _LOGGER.debug(
                "Both-mode arbitrage: buy=%.4f, sell=%.4f (min profitable=%.4f), "
                "charging to full (deficit=%.2f kWh)",
                cheapest_buy, most_expensive, min_profitable_sell, energy_deficit,
            )

    # Arbitrage price delta: optional additional constraint.  When set > 0,
    # requires the price spread to exceed this threshold before activating
    # full charging.  With the default of 0, the profitability check above
    # handles arbitrage automatically.
    if not arbitrage_active and config.arbitrage_price_delta > 0 and prices_remaining:
        price_spread = max(prices_remaining) - min(prices_remaining)
        if price_spread >= config.arbitrage_price_delta:
            arbitrage_active = True
            full_charge_kwh = max_battery_kwh - current_kwh
            energy_deficit = max(energy_deficit, max(0.0, full_charge_kwh - net_pv))
            _LOGGER.debug(
                "Arbitrage delta active: spread=%.4f >= delta=%.4f, "
                "charging to full (deficit=%.2f kWh)",
                price_spread, config.arbitrage_price_delta, energy_deficit,
            )

    charge_slots, tomorrow_slots, tomorrow_charge_kwh = select_unified_charge_slots(
        remaining, energy_deficit, effective_per_slot,
        config.battery_capacity_kwh, config.battery_discharge_min_pct,
        config.consumption_est_kwh, config.efficiency, energy_per_slot,
        current_kwh=current_kwh, net_pv=net_pv,
        charge_max_pct=config.battery_charge_max_pct,
        slot_prices_tomorrow=state.slot_prices_tomorrow,
        pv_forecast_tomorrow=state.pv_forecast_tomorrow,
        pv_hourly_kwh=state.pv_hourly_kwh,
        current_hour=state.current_hour,
    )

    result.tomorrow_precharge = round(-tomorrow_charge_kwh, 2) if tomorrow_charge_kwh > 0 else 0.0
    result.tomorrow_planned_slots = len(tomorrow_slots)
    result.tomorrow_planned_kwh = tomorrow_charge_kwh

    # Sellable energy: peak projected SOC above reserve.
    # After charge selection, include the planned charge energy so the sell
    # side knows the battery will have surplus to sell.  Without this, a low
    # PV-confidence day would show 0 sellable despite grid-charging the
    # battery well above reserve.  The downstream SOC validation prunes any
    # sell slots that would actually drain the battery below reserve.
    if arbitrage_active:
        sellable = max(0.0, max_battery_kwh - reserve_target) * config.efficiency * 0.85
    else:
        charge_energy_planned = len(charge_slots) * effective_per_slot
        peak_with_charge = min(max_battery_kwh, max_projected + charge_energy_planned)
        sellable = max(0.0, peak_with_charge - reserve_target) * config.efficiency * 0.85

    charge_slot_indices = {s[0] for s in charge_slots}

    # Sell side: filter to strictly positive prices.  When
    # block_export_on_negative_price is True (default), zero-price slots
    # also pass through but negatives are blocked — selling at p < 0 means
    # paying the grid to take energy.
    sell_price_floor = 0.0 if config.block_export_on_negative_price else float("-inf")
    non_negative_slots = [(i, p) for i, p in remaining if p > sell_price_floor]
    available_for_sell = [(i, p) for i, p in non_negative_slots
                         if p > 0 and i not in charge_slot_indices]

    # Effective cycle wear cost per kWh sold (longevity priority increases
    # this).  Roundtrip-divided because every sold kWh required ≈1/η stored.
    cycle_cost = config.battery_cycle_cost_eur_kwh
    if config.optimization_priority == "longevity":
        cycle_cost = max(cycle_cost, 0.05)  # enforce a longevity floor
    cycle_cost_per_sold_kwh = cycle_cost / round_trip_eff if round_trip_eff > 0 else 0.0

    min_sell_price = 0.0
    if charge_slots:
        max_buy_price = max(s[1] for s in charge_slots)
        # Sell must cover: round-trip losses on the buy AND cycle wear cost.
        min_sell_price = (max_buy_price / round_trip_eff) + cycle_cost_per_sold_kwh
        available_for_sell = [(i, p) for i, p in available_for_sell if p >= min_sell_price]
    elif cycle_cost_per_sold_kwh > 0:
        # No buy slots: still require sell price > cycle wear cost
        # (selling stored PV/yesterday's charge wears the battery too).
        available_for_sell = [(i, p) for i, p in available_for_sell if p >= cycle_cost_per_sold_kwh]

    sell_needed = math.ceil(sellable / energy_per_slot) if energy_per_slot > 0 else 0
    sorted_expensive = sorted(available_for_sell, key=lambda x: -x[1])
    sell_selected = sorted_expensive[:sell_needed]

    # charge_to_full_on_negative_price: ensure ALL negative-price slots
    # are charged (in addition to the cheapest selection above).  Sell
    # slots already exclude charge indices.
    if config.charge_to_full_on_negative_price:
        existing = {idx for idx, _ in charge_slots}
        for idx, price in remaining:
            if price is not None and price < 0 and idx not in existing:
                charge_slots.append((idx, price))
                existing.add(idx)

    # Per-slot SOC validation: ensure combined schedule respects battery bounds
    charge_set = {s[0] for s in charge_slots}
    discharge_set = {s[0] for s in sell_selected}
    validated_charge, validated_discharge = _validate_schedule_soc(
        remaining, charge_set, discharge_set,
        current_kwh, consumption_per_slot,
        state.pv_hourly_kwh, minutes_per_slot, pv_confidence,
        config.battery_capacity_kwh, reserve_target,
        energy_per_slot, config.efficiency,
        consumption_hourly_kwh=state.consumption_hourly_kwh,
        inverter_max_power_kw=config.inverter_max_power_kw,
        safe_power_kw=config.safe_power_kw,
        keep_all_negative_charges=config.charge_to_full_on_negative_price,
    )
    charge_slots = [(idx, p) for idx, p in charge_slots if idx in validated_charge]
    sell_selected = [(idx, p) for idx, p in sell_selected if idx in validated_discharge]

    # discharge_to_make_room_for_negative_price: add pre-emptive discharges
    # before negative-price PV windows.  Both mode may already have sell
    # slots — merge them; sell slots take precedence (higher revenue) when
    # the same slot index would appear twice.
    discharge_for_headroom: set[int] = set()
    if config.discharge_to_make_room_for_negative_price:
        existing_discharge = {idx for idx, _ in sell_selected}
        existing_charge = {idx for idx, _ in charge_slots}
        candidate = _select_discharges_for_pv_headroom(
            remaining, current_kwh,
            existing_charge,
            config, state, minutes_per_slot, pv_confidence, reserve_target,
        )
        discharge_for_headroom = candidate - existing_discharge - existing_charge

    result.scheduled_slots = {}
    for s in charge_slots:
        result.scheduled_slots[s[0]] = "charge"
    for s in sell_selected:
        result.scheduled_slots[s[0]] = "discharge"
    for idx in discharge_for_headroom:
        result.scheduled_slots[idx] = "discharge"

    result.cheap_slots_remaining = len(charge_slots)
    charge_energy = round(len(charge_slots) * effective_per_slot, 2) if charge_slots else 0
    sell_energy = round(min(sellable, len(sell_selected) * energy_per_slot), 2) if sellable > 0 else 0
    result.grid_energy_planned = round(charge_energy + sell_energy, 2)

    if charge_slots:
        result.price_threshold = max(s[1] for s in charge_slots)
    elif sell_selected:
        result.price_threshold = min(s[1] for s in sell_selected)

    return result


def calculate_available_info(
    config: EMSConfig,
    state: EMSState,
    price_threshold: float | None,
    grid_energy_planned: float = 0.0,
) -> AvailableInfo:
    """Calculate available slots and charge likelihood."""
    info = AvailableInfo()

    if not state.slot_prices_today or price_threshold is None:
        return info

    prices = state.slot_prices_today
    num_slots = len(prices)
    minutes_per_slot = (24 * 60) / num_slots
    current_slot = int((state.current_hour * 60 + state.current_minute) / minutes_per_slot)
    current_slot = min(current_slot, num_slots - 1)
    slot_duration_hours = minutes_per_slot / 60.0

    remaining = [(i, prices[i]) for i in range(current_slot, num_slots) if prices[i] is not None]
    energy_per_slot = config.safe_power_kw * slot_duration_hours * config.efficiency

    effective_mode = config.grid_mode if config.grid_mode not in ("off", "both") else "from_grid"

    if effective_mode == "from_grid":
        available = [s for s in remaining if s[1] <= price_threshold]
    else:
        available = [s for s in remaining if s[1] >= price_threshold]

    # Include tomorrow's slots in capacity calculation so likelihood
    # reflects the full two-day picture the scheduler actually uses.
    tomorrow_available = 0
    if state.slot_prices_tomorrow:
        for tp in state.slot_prices_tomorrow:
            if tp is None:
                continue
            if effective_mode == "from_grid" and tp <= price_threshold:
                tomorrow_available += 1
            elif effective_mode != "from_grid" and tp >= price_threshold:
                tomorrow_available += 1

    info.available_slots = len(available)
    total_with_tomorrow = len(available) + tomorrow_available
    info.available_energy_capacity = round(total_with_tomorrow * energy_per_slot, 2)

    if state.battery_soc_pct is None:
        info.charge_likelihood = "unknown"
        return info

    current_kwh = (state.battery_soc_pct / 100.0) * config.battery_capacity_kwh
    net_pv = calculate_net_pv_surplus(
        remaining, num_slots, config.consumption_est_kwh,
        state.pv_hourly_kwh, state.pv_forecast_remaining,
        state.pv_actual_today_kwh, state.pv_forecast_today,
        state.current_hour, state.current_minute,
    )

    if effective_mode == "from_grid":
        reserve_kwh = calculate_self_consumption_reserve(
            config.consumption_est_kwh, state.pv_hourly_kwh)
        reserve_target = _compute_reserve_target(config, reserve_kwh)
        shortfall = max(0.0, reserve_target - current_kwh)
        energy_deficit = max(0.0, shortfall - net_pv)

        if config.grid_mode == "off":
            if energy_deficit <= 0:
                info.charge_likelihood = "idle (no deficit)"
            elif info.available_energy_capacity >= energy_deficit:
                info.charge_likelihood = "idle (slots available)"
            else:
                info.charge_likelihood = "idle (insufficient slots)"
        elif energy_deficit <= 0:
            info.charge_likelihood = "on_track"
        else:
            planned = grid_energy_planned or 0.0
            capacity = max(planned, info.available_energy_capacity)
            if capacity >= energy_deficit * 1.2:
                info.charge_likelihood = "on_track"
            elif capacity >= energy_deficit:
                info.charge_likelihood = "tight"
            elif capacity >= energy_deficit * 0.5:
                info.charge_likelihood = "at_risk"
            else:
                info.charge_likelihood = "insufficient"
    else:
        min_kwh = (config.battery_discharge_min_pct / 100.0) * config.battery_capacity_kwh
        sellable = max(0.0, current_kwh - min_kwh)
        if config.grid_mode == "off":
            info.charge_likelihood = "idle (sell mode info)"
        elif sellable <= 0:
            info.charge_likelihood = "nothing_to_sell"
        elif total_with_tomorrow > 0:
            info.charge_likelihood = "selling"
        else:
            info.charge_likelihood = "no_profitable_slots"

    return info
