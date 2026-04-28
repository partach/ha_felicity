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
    """
    min_kwh = (config.battery_discharge_min_pct / 100.0) * config.battery_capacity_kwh

    if config.reserve_target_pct > 0:
        fixed_floor = (config.reserve_target_pct / 100.0) * config.battery_capacity_kwh
        # Use the higher of fixed floor and discharge minimum
        return min(config.battery_capacity_kwh, max(fixed_floor, min_kwh))

    return min(config.battery_capacity_kwh, min_kwh + reserve_kwh)


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

    # PV confidence factor (uses sliding window for recovery on variable days)
    pv_confidence = _calculate_pv_confidence(
        pv_hourly_kwh, pv_actual_today_kwh, current_hour, current_minute,
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

    _LOGGER.debug(
        "PV confidence: cumulative=%.2f, window(%dh)=%.2f, raw=%.2f, "
        "evidence_weight=%.2f → final=%.2f "
        "(actual=%.1f, expected_total=%.1f, total_forecast=%.1f)",
        cumulative_confidence, window_hours, window_confidence, raw_confidence,
        evidence_weight, confidence,
        pv_actual_today_kwh, expected_so_far, total_forecast,
    )

    return max(0.1, min(1.0, confidence))


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
) -> tuple[set[int], set[int]]:
    """Validate schedule by simulating SOC at every slot, pruning violations.

    Like the VB Sell macro, this checks that SOC stays within [min_kwh,
    battery_capacity] at every time slot.  If a discharge would cause SOC to
    dip below min, it is removed (least valuable first).  If a charge would
    push SOC above capacity, it is removed (most expensive first).

    Returns pruned (charge_slots, discharge_slots).
    """
    charge_slots = set(charge_slots)
    discharge_slots = set(discharge_slots)

    # Build price lookup from remaining
    price_of: dict[int, float] = {idx: price for idx, price in remaining}

    max_iterations = len(charge_slots) + len(discharge_slots) + 1

    for _ in range(max_iterations):
        violation_slot: int | None = None
        violation_type: str | None = None  # "low" or "high"

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
            if slot_idx in charge_slots:
                if inverter_max_power_kw > 0:
                    grid_kw = min(safe_power_kw or energy_per_slot / (minutes_per_slot / 60.0),
                                  max(0.0, inverter_max_power_kw - pv_kwh * pv_confidence))
                    delta += grid_kw * (minutes_per_slot / 60.0) * efficiency
                else:
                    delta += energy_per_slot * efficiency
            if slot_idx in discharge_slots:
                delta -= energy_per_slot

            soc = soc + delta

            # Check bounds BEFORE clamping — charging a full battery wastes
            # energy, discharging an empty one is impossible.
            if soc < min_kwh - 0.01:
                violation_slot = slot_idx
                violation_type = "low"
                break
            if soc > battery_capacity + 0.01:
                violation_slot = slot_idx
                violation_type = "high"
                break

            # Clamp to physical limits for subsequent slot calculations
            soc = max(0.0, min(battery_capacity, soc))

        if violation_slot is None:
            break  # Schedule is valid

        if violation_type == "low":
            # Remove the least valuable discharge at or before violation
            candidates = [
                s for s in discharge_slots
                if s <= violation_slot
            ]
            if not candidates:
                # Remove any discharge slot (least valuable)
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
            # negative-price slots if those are the only ones left —
            # a negative-price slot that overflows the battery still
            # causes forced PV export at penalty rates.
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
    if (not state.pv_hourly_kwh
            and state.pv_forecast_today and state.pv_forecast_today > 0
            and state.pv_forecast_remaining and state.pv_forecast_remaining > 0):
        state = EMSState(
            battery_soc_pct=state.battery_soc_pct,
            slot_prices_today=state.slot_prices_today,
            slot_prices_tomorrow=state.slot_prices_tomorrow,
            pv_hourly_kwh=_synthesize_pv_hourly(state.pv_forecast_today),
            pv_forecast_remaining=state.pv_forecast_remaining,
            pv_forecast_today=state.pv_forecast_today,
            pv_forecast_tomorrow=state.pv_forecast_tomorrow,
            pv_actual_today_kwh=state.pv_actual_today_kwh,
            consumption_hourly_kwh=state.consumption_hourly_kwh,
            current_hour=state.current_hour,
            current_minute=state.current_minute,
        )
        _LOGGER.debug(
            "Synthesized hourly PV from daily forecast (%.1f kWh)",
            state.pv_forecast_today,
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

    if not selected:
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
    )
    selected = [(idx, p) for idx, p in selected if idx in validated_charge]

    if not selected:
        result.status = "no_action_needed"
        return result

    result.scheduled_slots = {s[0]: "charge" for s in selected}
    result.cheap_slots_remaining = len(result.scheduled_slots)
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

    positive_slots = [(i, p) for i, p in remaining if p > 0]
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

    # Arbitrage price delta: when the price spread is large enough, charge
    # to full capacity instead of just the reserve — the extra energy can be
    # sold at a profit that outweighs battery wear.
    arbitrage_active = False
    if config.arbitrage_price_delta > 0 and remaining:
        prices_remaining = [p for _, p in remaining if p is not None]
        if prices_remaining:
            price_spread = max(prices_remaining) - min(prices_remaining)
            if price_spread >= config.arbitrage_price_delta:
                arbitrage_active = True
                full_charge_kwh = max_battery_kwh - current_kwh
                energy_deficit = max(energy_deficit, max(0.0, full_charge_kwh - net_pv))
                _LOGGER.debug(
                    "Arbitrage active: spread=%.4f >= delta=%.4f, "
                    "charging to full (deficit=%.2f kWh)",
                    price_spread, config.arbitrage_price_delta, energy_deficit,
                )

    # Predictive sellable: use peak projected SOC above reserve.
    # When arbitrage is active, sellable is based on full capacity since
    # we're charging to max.
    # Safety margin (15%): accounts for consumption estimate errors,
    # PV forecast uncertainty, and the gap between peak SOC (midday)
    # and actual SOC when discharge slots fire (evening).
    if arbitrage_active:
        sellable = max(0.0, max_battery_kwh - reserve_target) * config.efficiency * 0.85
    else:
        sellable = max(0.0, max_projected - reserve_target) * config.efficiency * 0.85

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

    charge_slot_indices = {s[0] for s in charge_slots}

    # Sell side
    non_negative_slots = [(i, p) for i, p in remaining if p >= 0]
    available_for_sell = [(i, p) for i, p in non_negative_slots
                         if p > 0 and i not in charge_slot_indices]

    min_sell_price = 0.0
    if charge_slots:
        max_buy_price = max(s[1] for s in charge_slots)
        min_sell_price = max_buy_price / round_trip_eff
        available_for_sell = [(i, p) for i, p in available_for_sell if p >= min_sell_price]

    sell_needed = math.ceil(sellable / energy_per_slot) if energy_per_slot > 0 else 0
    sorted_expensive = sorted(available_for_sell, key=lambda x: -x[1])
    sell_selected = sorted_expensive[:sell_needed]

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
    )
    charge_slots = [(idx, p) for idx, p in charge_slots if idx in validated_charge]
    sell_selected = [(idx, p) for idx, p in sell_selected if idx in validated_discharge]

    result.scheduled_slots = {}
    for s in charge_slots:
        result.scheduled_slots[s[0]] = "charge"
    for s in sell_selected:
        result.scheduled_slots[s[0]] = "discharge"

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
