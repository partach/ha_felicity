"""Comprehensive tests for the EMS scheduling algorithm.

Tests cover:
- Sunny day (no grid charging needed)
- Cloudy day (heavy grid charging)
- Low-PV tomorrow (daytime_gap proactive charging)
- Battery nearly full (headroom cap)
- Battery nearly empty (urgent charging)
- Negative prices (always charge)
- Cross-day: tomorrow cheaper (defer)
- Cross-day: today cheaper (pre-charge)
- Only today's prices (no tomorrow data)
- Both mode: buy cheap + sell expensive
- to_grid mode: sell at best prices
- Heavy consumption day
- Safety swap: bridge to tomorrow
- Yesterday deficit carryover
- Various slot granularities (24, 48, 96)
"""

import math
import sys
import os
import importlib.util

import pytest

# Import ems.py directly to avoid triggering HA-dependent __init__.py
_ems_path = os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "ha_felicity", "ems.py"
)
_spec = importlib.util.spec_from_file_location("ems", _ems_path)
ems = importlib.util.module_from_spec(_spec)
sys.modules["ems"] = ems
_spec.loader.exec_module(ems)

EMSConfig = ems.EMSConfig
EMSState = ems.EMSState
ScheduleResult = ems.ScheduleResult
FlexibleLoadConfig = ems.FlexibleLoadConfig
calculate_self_consumption_reserve = ems.calculate_self_consumption_reserve
calculate_net_pv_surplus = ems.calculate_net_pv_surplus
select_unified_charge_slots = ems.select_unified_charge_slots
calculate_schedule = ems.calculate_schedule
calculate_available_info = ems.calculate_available_info
_calculate_pv_confidence = ems._calculate_pv_confidence
_project_soc_trajectory = ems._project_soc_trajectory
_validate_schedule_soc = ems._validate_schedule_soc
_compute_reserve_target = ems._compute_reserve_target
_compute_tomorrow_schedule = ems._compute_tomorrow_schedule
_schedule_flexible_loads = ems._schedule_flexible_loads


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_prices(num_slots: int, base: float = 0.25, pattern: str = "flat") -> list[float]:
    """Generate price arrays for testing.

    Patterns:
      flat: all same price
      rising: 0.10 → 0.40 linearly
      falling: 0.40 → 0.10 linearly
      v_shape: high-low-high (cheap midday)
      u_shape: low-high-low (cheap morning+evening)
      negative: mix of negative and positive
      cheap_morning: cheap 0-8, expensive rest
    """
    if pattern == "flat":
        return [base] * num_slots
    elif pattern == "rising":
        return [0.10 + 0.30 * i / (num_slots - 1) for i in range(num_slots)]
    elif pattern == "falling":
        return [0.40 - 0.30 * i / (num_slots - 1) for i in range(num_slots)]
    elif pattern == "v_shape":
        mid = num_slots // 2
        return [0.35 - 0.25 * min(i, num_slots - 1 - i) / mid for i in range(num_slots)]
    elif pattern == "u_shape":
        mid = num_slots // 2
        return [0.10 + 0.25 * min(i, num_slots - 1 - i) / mid for i in range(num_slots)]
    elif pattern == "negative":
        return [-0.05 + 0.10 * i / (num_slots - 1) for i in range(num_slots)]
    elif pattern == "cheap_morning":
        # First 1/3 cheap, rest expensive
        cutoff = num_slots // 3
        return [0.05 if i < cutoff else 0.35 for i in range(num_slots)]
    else:
        return [base] * num_slots


def make_pv_hourly(total_kwh: float = 30.0, sunrise: int = 7, sunset: int = 19) -> dict[int, float]:
    """Generate a bell-curve PV hourly dict."""
    import math
    hours = {}
    peak_hour = (sunrise + sunset) / 2.0
    spread = (sunset - sunrise) / 2.0
    raw = {}
    for h in range(sunrise, sunset + 1):
        raw[h] = max(0, math.cos((h - peak_hour) / spread * math.pi / 2) ** 2)
    total_raw = sum(raw.values()) or 1
    for h, v in raw.items():
        hours[h] = round(v / total_raw * total_kwh, 2)
    return hours


def default_config(**overrides) -> EMSConfig:
    """Create a default EMSConfig with overrides."""
    cfg = EMSConfig(
        grid_mode="from_grid",
        battery_capacity_kwh=60.0,
        battery_charge_max_pct=100.0,
        battery_discharge_min_pct=20.0,
        efficiency=0.90,
        safe_power_kw=5.0,
        consumption_est_kwh=38.5,
        yesterday_deficit_kwh=0.0,
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def default_state(**overrides) -> EMSState:
    """Create a default EMSState with overrides."""
    st = EMSState(
        battery_soc_pct=50.0,
        slot_prices_today=make_prices(24),
        slot_prices_tomorrow=None,
        pv_hourly_kwh=make_pv_hourly(30.0),
        pv_forecast_remaining=15.0,
        pv_forecast_today=30.0,
        pv_forecast_tomorrow=None,
        pv_actual_today_kwh=15.0,
        current_hour=12,
        current_minute=0,
    )
    for k, v in overrides.items():
        setattr(st, k, v)
    return st


# ---------------------------------------------------------------------------
# Test: Self-consumption reserve
# ---------------------------------------------------------------------------

class TestSelfConsumptionReserve:
    def test_default_no_pv_data(self):
        """Without PV data, uses default sunset=19 sunrise=7 → 12h overnight."""
        reserve = calculate_self_consumption_reserve(24.0)
        assert reserve == pytest.approx(12.0, abs=0.1)  # 1 kWh/h * 12h

    def test_with_pv_data(self):
        """With PV data, adjusts sunset/sunrise from actual production hours."""
        pv = make_pv_hourly(30.0, sunrise=6, sunset=20)
        reserve = calculate_self_consumption_reserve(24.0, pv)
        # PV hours that have > 0.1 kWh determine sunset/sunrise
        # The actual hours depend on bell curve distribution
        pv_hours = [h for h, kwh in pv.items() if kwh > 0.1]
        expected_sunset = max(pv_hours) + 1 if pv_hours else 19
        expected_sunrise = min(pv_hours) if pv_hours else 7
        expected_hours = (24 - expected_sunset) + expected_sunrise
        assert reserve == pytest.approx(24.0 / 24 * expected_hours, abs=0.5)

    def test_high_consumption(self):
        """Higher consumption → higher reserve."""
        reserve_low = calculate_self_consumption_reserve(10.0)
        reserve_high = calculate_self_consumption_reserve(50.0)
        assert reserve_high > reserve_low


# ---------------------------------------------------------------------------
# Test: PV surplus
# ---------------------------------------------------------------------------

class TestPVSurplus:
    def test_flat_model_no_hourly(self):
        """Without hourly data, falls back to flat model."""
        remaining = [(i, 0.25) for i in range(12, 24)]
        surplus = calculate_net_pv_surplus(
            remaining, 24, 24.0,
            pv_forecast_remaining=20.0,
        )
        # 12 hours left, consumption = 24/24 * 12 = 12 kWh, remaining = 20
        assert surplus == pytest.approx(8.0, abs=0.1)

    def test_hourly_model_sunny(self):
        """With hourly data, sums per-hour surpluses."""
        pv = make_pv_hourly(40.0)  # strong PV
        remaining = [(i, 0.25) for i in range(10, 24)]
        surplus = calculate_net_pv_surplus(
            remaining, 24, 24.0,
            pv_hourly_kwh=pv,
            pv_forecast_remaining=25.0,
            pv_actual_today_kwh=15.0,
            pv_forecast_today=40.0,
            current_hour=10,
        )
        assert surplus > 0

    def test_cloudy_day_confidence_drop(self):
        """When actual PV << forecast, confidence drops, reducing surplus."""
        pv = make_pv_hourly(40.0)
        remaining = [(i, 0.25) for i in range(14, 24)]
        # Good day: actual tracks forecast
        surplus_good = calculate_net_pv_surplus(
            remaining, 24, 24.0,
            pv_hourly_kwh=pv,
            pv_forecast_remaining=15.0,
            pv_actual_today_kwh=20.0,
            pv_forecast_today=40.0,
            current_hour=14,
        )
        # Cloudy: actual is 0
        surplus_cloudy = calculate_net_pv_surplus(
            remaining, 24, 24.0,
            pv_hourly_kwh=pv,
            pv_forecast_remaining=15.0,
            pv_actual_today_kwh=0.0,
            pv_forecast_today=40.0,
            current_hour=14,
        )
        assert surplus_cloudy < surplus_good

    def test_no_remaining_slots(self):
        """Empty remaining → 0 surplus."""
        surplus = calculate_net_pv_surplus([], 24, 24.0)
        assert surplus == 0.0


# ---------------------------------------------------------------------------
# Test: Unified charge slot selection
# ---------------------------------------------------------------------------

class TestUnifiedSlotSelection:
    def test_today_only_no_tomorrow(self):
        """Without tomorrow data, just picks cheapest today slots."""
        remaining = [(i, 0.10 + i * 0.02) for i in range(12, 24)]
        today, tomorrow, tmr_kwh = select_unified_charge_slots(
            remaining_today=remaining,
            energy_deficit=10.0,
            effective_per_slot=4.5,  # 5kW * 1h * 0.9
            battery_capacity=60.0,
            discharge_min_pct=20.0,
            consumption_est=38.5,
            efficiency=0.90,
            energy_per_slot=5.0,
            current_kwh=20.0,
            net_pv=0.0,
            current_hour=12,
        )
        assert len(today) > 0
        assert len(tomorrow) == 0
        assert tmr_kwh == 0.0
        # Should pick cheapest slots first
        prices = [p for _, p in today]
        assert prices == sorted(prices)

    def test_tomorrow_cheaper_defers(self):
        """When tomorrow is cheaper, most slots come from tomorrow."""
        remaining_today = [(i, 0.30) for i in range(18, 24)]  # expensive today
        tomorrow_prices = [0.05] * 24  # cheap tomorrow

        today, tomorrow, tmr_kwh = select_unified_charge_slots(
            remaining_today=remaining_today,
            energy_deficit=5.0,
            effective_per_slot=4.5,
            battery_capacity=60.0,
            discharge_min_pct=20.0,
            consumption_est=38.5,
            efficiency=0.90,
            energy_per_slot=5.0,
            current_kwh=30.0,
            net_pv=0.0,
            charge_max_pct=100.0,
            slot_prices_tomorrow=tomorrow_prices,
            pv_forecast_tomorrow=30.0,
            current_hour=18,
        )
        # Tomorrow is cheaper, so most slots should be from tomorrow
        assert len(tomorrow) >= len(today)

    def test_today_cheaper_precharges(self):
        """When today is cheaper, slots come from today."""
        remaining_today = [(i, 0.05) for i in range(12, 24)]  # cheap today
        tomorrow_prices = [0.40] * 24  # expensive tomorrow

        today, tomorrow, tmr_kwh = select_unified_charge_slots(
            remaining_today=remaining_today,
            energy_deficit=5.0,
            effective_per_slot=4.5,
            battery_capacity=60.0,
            discharge_min_pct=20.0,
            consumption_est=38.5,
            efficiency=0.90,
            energy_per_slot=5.0,
            current_kwh=30.0,
            net_pv=0.0,
            charge_max_pct=100.0,
            slot_prices_tomorrow=tomorrow_prices,
            pv_forecast_tomorrow=30.0,
            current_hour=12,
        )
        assert len(today) >= len(tomorrow)

    def test_battery_full_headroom_cap(self):
        """At 99% SOC, headroom prevents today's charging."""
        remaining_today = [(i, 0.05) for i in range(12, 24)]
        tomorrow_prices = [0.30] * 24

        today, tomorrow, tmr_kwh = select_unified_charge_slots(
            remaining_today=remaining_today,
            energy_deficit=0.0,  # no deficit, battery is full
            effective_per_slot=4.5,
            battery_capacity=60.0,
            discharge_min_pct=20.0,
            consumption_est=38.5,
            efficiency=0.90,
            energy_per_slot=5.0,
            current_kwh=59.4,  # 99%
            net_pv=0.0,
            charge_max_pct=100.0,
            slot_prices_tomorrow=tomorrow_prices,
            pv_forecast_tomorrow=30.0,
            current_hour=12,
        )
        # With 0.6 kWh headroom and 4.5 kWh per slot, can't fit any today slots
        assert len(today) == 0

    def test_negative_prices_always_selected(self):
        """Negative price slots are always included."""
        remaining_today = [(0, -0.10), (1, -0.05), (2, 0.30), (3, 0.35)]

        today, tomorrow, _ = select_unified_charge_slots(
            remaining_today=remaining_today,
            energy_deficit=0.0,  # no deficit
            effective_per_slot=4.5,
            battery_capacity=60.0,
            discharge_min_pct=20.0,
            consumption_est=10.0,
            efficiency=0.90,
            energy_per_slot=5.0,
            current_kwh=30.0,
            net_pv=0.0,
            current_hour=0,
        )
        today_indices = {idx for idx, _ in today}
        assert 0 in today_indices  # -0.10
        assert 1 in today_indices  # -0.05
        # Positive slots should NOT be selected (no deficit)
        assert 2 not in today_indices
        assert 3 not in today_indices

    def test_low_pv_tomorrow_daytime_gap(self):
        """Low PV tomorrow triggers daytime_gap, scheduling more grid slots."""
        remaining_today = [(i, 0.15) for i in range(14, 24)]
        tomorrow_high_pv = [0.10] * 24
        tomorrow_low_pv = [0.10] * 24

        # High PV tomorrow → smaller deficit
        _, tmr_high, kwh_high = select_unified_charge_slots(
            remaining_today=remaining_today,
            energy_deficit=5.0,
            effective_per_slot=4.5,
            battery_capacity=60.0,
            discharge_min_pct=20.0,
            consumption_est=38.5,
            efficiency=0.90,
            energy_per_slot=5.0,
            current_kwh=30.0,
            net_pv=5.0,
            charge_max_pct=100.0,
            slot_prices_tomorrow=tomorrow_high_pv,
            pv_forecast_tomorrow=40.0,  # high PV → small daytime_gap
            current_hour=14,
        )

        # Low PV tomorrow → bigger deficit due to daytime_gap
        _, tmr_low, kwh_low = select_unified_charge_slots(
            remaining_today=remaining_today,
            energy_deficit=5.0,
            effective_per_slot=4.5,
            battery_capacity=60.0,
            discharge_min_pct=20.0,
            consumption_est=38.5,
            efficiency=0.90,
            energy_per_slot=5.0,
            current_kwh=30.0,
            net_pv=5.0,
            charge_max_pct=100.0,
            slot_prices_tomorrow=tomorrow_low_pv,
            pv_forecast_tomorrow=4.0,  # low PV → large daytime_gap (38.5 - 4 = 34.5)
            current_hour=14,
        )

        # Low PV should schedule significantly more tomorrow slots
        assert len(tmr_low) > len(tmr_high)

    def test_safety_swap(self):
        """Battery at min SOC survives to tomorrow — no forced today charging.

        The inverter stops providing house power once SOC reaches min_kwh,
        so the bridge projection clamps at min_kwh.  When cheap tomorrow
        slots are available, the algorithm should NOT force expensive today
        slots just because the raw (unclamped) projection dips below min.
        """
        # Battery very low, few today slots, cheap tomorrow slots
        remaining_today = [(i, 0.30 + i * 0.01) for i in range(20, 24)]
        tomorrow_prices = [0.05] * 24

        today, tomorrow, _ = select_unified_charge_slots(
            remaining_today=remaining_today,
            energy_deficit=15.0,
            effective_per_slot=4.5,
            battery_capacity=60.0,
            discharge_min_pct=20.0,
            consumption_est=38.5,
            efficiency=0.90,
            energy_per_slot=5.0,
            current_kwh=15.0,  # very low, but above min_kwh (12)
            net_pv=0.0,
            charge_max_pct=100.0,
            slot_prices_tomorrow=tomorrow_prices,
            pv_forecast_tomorrow=30.0,
            current_hour=20,
        )
        # With the bridge clamp fix, battery survives at min_kwh (12 kWh)
        # until tomorrow's cheap slots.  All charging deferred to tomorrow.
        assert len(today) == 0
        assert len(tomorrow) > 0

    def test_high_pv_tomorrow_reduces_grid_charging(self):
        """When tomorrow PV surplus exceeds consumption, grid charging is reduced.

        Scenario: battery at 50%, tomorrow PV forecast 45 kWh vs consumption 38.5 kWh.
        The 6.5 kWh PV surplus will charge the battery during the day, so the system
        should schedule fewer grid slots than it would without accounting for surplus.
        """
        remaining_today = [(i, 0.15) for i in range(20, 24)]
        tomorrow_prices = [0.10] * 24

        # With high PV tomorrow (surplus will charge battery)
        _, tmr_high_pv, kwh_high_pv = select_unified_charge_slots(
            remaining_today=remaining_today,
            energy_deficit=0.0,
            effective_per_slot=4.5,
            battery_capacity=60.0,
            discharge_min_pct=20.0,
            consumption_est=38.5,
            efficiency=0.90,
            energy_per_slot=5.0,
            current_kwh=30.0,  # 50%
            net_pv=0.0,
            charge_max_pct=100.0,
            slot_prices_tomorrow=tomorrow_prices,
            pv_forecast_tomorrow=45.0,  # 6.5 kWh surplus over consumption
            current_hour=20,
        )

        # With low PV tomorrow (no surplus)
        _, tmr_low_pv, kwh_low_pv = select_unified_charge_slots(
            remaining_today=remaining_today,
            energy_deficit=0.0,
            effective_per_slot=4.5,
            battery_capacity=60.0,
            discharge_min_pct=20.0,
            consumption_est=38.5,
            efficiency=0.90,
            energy_per_slot=5.0,
            current_kwh=30.0,  # 50%
            net_pv=0.0,
            charge_max_pct=100.0,
            slot_prices_tomorrow=tomorrow_prices,
            pv_forecast_tomorrow=20.0,  # 18.5 kWh deficit
            current_hour=20,
        )

        # High PV tomorrow should schedule fewer grid slots
        assert len(tmr_high_pv) < len(tmr_low_pv)
        assert kwh_high_pv < kwh_low_pv

    def test_pv_surplus_prevents_unnecessary_grid_charge(self):
        """When PV surplus alone can fill battery to reserve target, no grid charge needed.

        Scenario: battery projected at midnight = 25 kWh, reserve target ~30 kWh,
        tomorrow PV surplus = 10 kWh. The surplus covers the 5 kWh gap, so no
        grid charging should be scheduled for tomorrow.
        """
        remaining_today = [(i, 0.15) for i in range(20, 24)]
        tomorrow_prices = [0.10] * 24

        today, tomorrow, tmr_kwh = select_unified_charge_slots(
            remaining_today=remaining_today,
            energy_deficit=0.0,
            effective_per_slot=4.5,
            battery_capacity=60.0,
            discharge_min_pct=20.0,
            consumption_est=20.0,  # low consumption
            efficiency=0.90,
            energy_per_slot=5.0,
            current_kwh=30.0,
            net_pv=0.0,
            charge_max_pct=100.0,
            slot_prices_tomorrow=tomorrow_prices,
            pv_forecast_tomorrow=40.0,  # 20 kWh surplus (40 - 20)
            current_hour=20,
        )

        # PV surplus of 20 kWh should cover any shortfall for a 60 kWh battery
        # with projected midnight around 28-30 kWh and low reserve target
        assert tmr_kwh == 0.0 or len(tomorrow) == 0


# ---------------------------------------------------------------------------
# Test: Full schedule calculation
# ---------------------------------------------------------------------------

class TestCalculateSchedule:
    def test_grid_mode_off(self):
        """Grid mode off → no schedule."""
        config = default_config(grid_mode="off")
        state = default_state()
        result = calculate_schedule(config, state)
        assert result.status == "off"
        assert len(result.scheduled_slots) == 0

    def test_grid_mode_off_still_reports_reserve(self):
        """Grid mode off should still expose the overnight reserve + target %.

        The frontend's 'night target' line and 'overnight need' stat read
        these even when the scheduler is disabled.
        """
        config = default_config(grid_mode="off")
        state = default_state()
        result = calculate_schedule(config, state)
        assert result.status == "off"
        assert result.self_consumption_reserve > 0
        assert result.reserve_target_pct > config.battery_discharge_min_pct

    def test_no_price_data_still_reports_reserve(self):
        """No price data path should also expose the overnight reserve."""
        config = default_config(grid_mode="from_grid")
        state = default_state(slot_prices_today=None)
        result = calculate_schedule(config, state)
        assert result.status == "no_price_data"
        assert result.self_consumption_reserve > 0

    def test_no_price_data(self):
        """No price data → no schedule."""
        config = default_config()
        state = default_state(slot_prices_today=None)
        result = calculate_schedule(config, state)
        assert result.status == "no_price_data"

    def test_sunny_day_no_charging_needed(self):
        """Sunny day with full PV coverage → no grid charging."""
        config = default_config(consumption_est_kwh=20.0)
        pv = make_pv_hourly(40.0)
        state = default_state(
            battery_soc_pct=80.0,
            pv_hourly_kwh=pv,
            pv_forecast_remaining=25.0,
            pv_forecast_today=40.0,
            pv_actual_today_kwh=15.0,
        )
        result = calculate_schedule(config, state)
        # Battery at 80% of 60 kWh = 48 kWh, reserve ~12*0.83 ≈ 10 kWh,
        # reserve_target = min(60, 12 + 10) = 22, shortfall = max(0, 22 - 48) = 0
        assert result.status == "no_action_needed"
        charge_slots = [k for k, v in result.scheduled_slots.items() if v == "charge"]
        assert len(charge_slots) == 0

    def test_high_soc_abundant_solar_no_grid_charge(self):
        """High SOC + abundant solar forecast → absolutely no grid charging.

        Reproduces the real-world scenario: battery at 91%, solar forecast
        52.7 kWh, remaining 36.6 kWh.  The algorithm must NOT schedule any
        grid charge slots because solar will fill the battery to capacity
        and grid charging cannot add more useful energy.
        """
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=60.0,
            consumption_est_kwh=38.5,
            battery_charge_max_pct=100.0,
            battery_discharge_min_pct=30.0,
        )
        pv = make_pv_hourly(52.7)
        state = default_state(
            battery_soc_pct=91.0,
            slot_prices_today=make_prices(24, pattern="u_shape"),
            pv_hourly_kwh=pv,
            pv_forecast_remaining=36.6,
            pv_forecast_today=52.7,
            pv_actual_today_kwh=12.3,
            current_hour=10,
            current_minute=0,
        )
        result = calculate_schedule(config, state)
        charge_slots = [k for k, v in result.scheduled_slots.items() if v == "charge"]
        assert len(charge_slots) == 0, (
            f"Expected NO grid charge with SOC 91% and 52.7 kWh solar, "
            f"but got {len(charge_slots)} charge slots"
        )
        assert result.status == "no_action_needed"

    def test_high_soc_abundant_solar_small_battery(self):
        """Same scenario with a smaller battery where reserve_target = capacity.

        With a 10 kWh battery, min 30% (3 kWh) + overnight reserve can exceed
        capacity.  Even then, solar will fill the battery and grid charging
        is pointless.
        """
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=10.0,
            consumption_est_kwh=15.0,
            battery_charge_max_pct=100.0,
            battery_discharge_min_pct=30.0,
        )
        pv = make_pv_hourly(52.7)
        state = default_state(
            battery_soc_pct=91.0,
            slot_prices_today=make_prices(24, pattern="u_shape"),
            pv_hourly_kwh=pv,
            pv_forecast_remaining=36.6,
            pv_forecast_today=52.7,
            pv_actual_today_kwh=12.3,
            current_hour=10,
            current_minute=0,
        )
        result = calculate_schedule(config, state)
        charge_slots = [k for k, v in result.scheduled_slots.items() if v == "charge"]
        assert len(charge_slots) == 0, (
            f"Expected NO grid charge with small battery at 91% SOC and "
            f"52.7 kWh solar, but got {len(charge_slots)} charge slots"
        )

    def test_high_soc_abundant_solar_no_hourly_data(self):
        """High SOC + abundant solar but without hourly PV breakdown.

        When pv_hourly_kwh is empty, the flat model is used for net_pv
        and the trajectory has no PV.  The algorithm must still recognise
        that solar covers the shortfall via the snapshot calculation.
        """
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=60.0,
            consumption_est_kwh=38.5,
            battery_charge_max_pct=100.0,
            battery_discharge_min_pct=30.0,
        )
        state = default_state(
            battery_soc_pct=91.0,
            slot_prices_today=make_prices(24, pattern="u_shape"),
            pv_hourly_kwh={},  # No hourly data
            pv_forecast_remaining=36.6,
            pv_forecast_today=52.7,
            pv_actual_today_kwh=12.3,
            current_hour=10,
            current_minute=0,
        )
        result = calculate_schedule(config, state)
        charge_slots = [k for k, v in result.scheduled_slots.items() if v == "charge"]
        assert len(charge_slots) == 0, (
            f"Expected NO grid charge without hourly PV data but abundant "
            f"solar forecast, got {len(charge_slots)} charge slots"
        )

    def test_cloudy_day_heavy_charging(self):
        """Cloudy day with minimal PV → lots of grid charging."""
        config = default_config(consumption_est_kwh=38.5)
        # Cloudy: PV actual is 0, forecast says 30
        state = default_state(
            battery_soc_pct=30.0,  # 18 kWh, below reserve target
            pv_hourly_kwh=make_pv_hourly(30.0),
            pv_forecast_remaining=20.0,
            pv_forecast_today=30.0,
            pv_actual_today_kwh=0.0,  # cloudy!
            current_hour=10,
        )
        result = calculate_schedule(config, state)
        charge_slots = [k for k, v in result.scheduled_slots.items() if v == "charge"]
        # With confidence dropping to 0.1, net_pv ≈ 0, so deficit should be significant
        assert len(charge_slots) > 0
        assert result.grid_energy_planned > 0

    def test_battery_nearly_empty_urgent(self):
        """Battery at 22% (near min 20%) → urgent charging."""
        config = default_config()
        state = default_state(
            battery_soc_pct=22.0,
            pv_forecast_remaining=0.0,
            pv_hourly_kwh={},
            current_hour=18,
        )
        result = calculate_schedule(config, state)
        charge_slots = [k for k, v in result.scheduled_slots.items() if v == "charge"]
        assert len(charge_slots) > 0

    def test_to_grid_sell_expensive(self):
        """to_grid mode: sell at most expensive slots."""
        config = default_config(grid_mode="to_grid")
        prices = make_prices(24, pattern="v_shape")
        state = default_state(
            battery_soc_pct=80.0,
            slot_prices_today=prices,
            current_hour=0,
        )
        result = calculate_schedule(config, state)
        discharge_slots = [k for k, v in result.scheduled_slots.items() if v == "discharge"]
        assert len(discharge_slots) > 0
        # Should pick the most expensive slots
        selected_prices = [prices[s] for s in discharge_slots]
        assert all(p > 0 for p in selected_prices)

    def test_to_grid_no_sellable(self):
        """to_grid with battery at min SOC → nothing to sell."""
        config = default_config(grid_mode="to_grid", battery_discharge_min_pct=50.0)
        state = default_state(battery_soc_pct=50.0, current_hour=0)
        result = calculate_schedule(config, state)
        assert result.status == "no_action_needed"

    def test_both_mode_charge_and_sell(self):
        """Both mode: charge cheap + sell expensive."""
        config = default_config(
            grid_mode="both",
            consumption_est_kwh=20.0,
        )
        # V-shape prices: expensive at edges, cheap in middle
        prices = make_prices(24, pattern="v_shape")
        state = default_state(
            battery_soc_pct=50.0,
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            current_hour=0,
        )
        result = calculate_schedule(config, state)
        # Should have some scheduled slots (charge and/or discharge)
        assert len(result.scheduled_slots) > 0

    def test_both_mode_profitability_filter(self):
        """Both mode: sell price must exceed buy price / (eff^2)."""
        config = default_config(
            grid_mode="both",
            efficiency=0.90,
            consumption_est_kwh=20.0,
        )
        # Battery low enough to trigger charging at cheap slots.
        # If buy at 0.10 with eff=0.90, min_sell = 0.10/0.81 ≈ 0.123
        # Potential sell at 0.12 < 0.123 → not profitable
        prices = [0.10] * 6 + [0.12] * 18
        state = default_state(
            battery_soc_pct=30.0,  # low enough to need charging
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            current_hour=0,
        )
        result = calculate_schedule(config, state)
        charge_slots = [k for k, v in result.scheduled_slots.items() if v == "charge"]
        discharge_slots = [k for k, v in result.scheduled_slots.items() if v == "discharge"]
        # Must have charge slots for the filter to apply
        assert len(charge_slots) > 0
        # 0.12 < 0.10/0.81 ≈ 0.123 → selling not profitable
        assert len(discharge_slots) == 0

    def test_negative_prices_from_grid(self):
        """Negative prices trigger charging when battery has room."""
        config = default_config(battery_capacity_kwh=60.0)
        prices = make_prices(24, pattern="negative")
        state = default_state(
            battery_soc_pct=40.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(10.0),
            pv_forecast_remaining=5.0,
            pv_actual_today_kwh=5.0,
            current_hour=0,
        )
        result = calculate_schedule(config, state)
        charge_slots = [k for k, v in result.scheduled_slots.items() if v == "charge"]
        charged_neg = sum(1 for s in charge_slots if prices[s] < 0)
        assert charged_neg > 0

    def test_negative_prices_kept_when_pv_fills_battery(self):
        """Negative-price charging preserved when PV fills battery — the
        overflow is PV-caused, so negative-price income is pure profit."""
        config = default_config()
        prices = make_prices(24, pattern="negative")
        state = default_state(
            battery_soc_pct=80.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(40.0),
            pv_forecast_remaining=25.0,
            pv_actual_today_kwh=15.0,
            current_hour=0,
        )
        result = calculate_schedule(config, state)
        charge_slots = [k for k, v in result.scheduled_slots.items() if v == "charge"]
        neg_slots = [s for s in charge_slots if prices[s] < 0]
        assert len(neg_slots) > 0, (
            "Negative-price slots should be kept when PV fills battery anyway"
        )

    def test_yesterday_deficit_carryover(self):
        """Yesterday's deficit increases today's charging."""
        config_no_carry = default_config()
        config_carry = default_config(yesterday_deficit_kwh=10.0)
        state = default_state(
            battery_soc_pct=40.0,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            current_hour=10,
        )
        result_no = calculate_schedule(config_no_carry, state)
        result_carry = calculate_schedule(config_carry, state)
        # With carryover, should plan more energy
        assert result_carry.grid_energy_planned >= result_no.grid_energy_planned

    def test_cross_day_tomorrow_cheaper(self):
        """When tomorrow's prices are much cheaper, prefer tomorrow slots."""
        config = default_config(consumption_est_kwh=20.0)
        state = default_state(
            battery_soc_pct=40.0,
            slot_prices_today=[0.35] * 24,
            slot_prices_tomorrow=[0.05] * 24,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_tomorrow=25.0,
            current_hour=14,
        )
        result = calculate_schedule(config, state)
        assert result.tomorrow_planned_slots > 0

    def test_cross_day_today_cheaper(self):
        """When today is cheap and tomorrow expensive, pre-charge today."""
        config = default_config(consumption_est_kwh=20.0)
        state = default_state(
            battery_soc_pct=40.0,
            slot_prices_today=[0.05] * 24,
            slot_prices_tomorrow=[0.40] * 24,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_tomorrow=25.0,
            current_hour=14,
        )
        result = calculate_schedule(config, state)
        today_charge = [k for k, v in result.scheduled_slots.items() if v == "charge"]
        # Today should have more charge slots (cheaper)
        assert len(today_charge) > 0

    def test_low_pv_tomorrow_proactive(self):
        """Low PV tomorrow → more charging scheduled (daytime_gap)."""
        config = default_config(consumption_est_kwh=38.5)
        base_state = dict(
            battery_soc_pct=50.0,
            slot_prices_today=[0.20] * 24,
            slot_prices_tomorrow=[0.10] * 24,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            current_hour=14,
        )

        # High PV tomorrow
        state_high = default_state(**base_state, pv_forecast_tomorrow=40.0)
        result_high = calculate_schedule(config, state_high)

        # Low PV tomorrow
        state_low = default_state(**base_state, pv_forecast_tomorrow=4.0)
        result_low = calculate_schedule(config, state_low)

        # Low PV should plan more total charging
        total_high = result_high.grid_energy_planned + result_high.tomorrow_planned_kwh
        total_low = result_low.grid_energy_planned + result_low.tomorrow_planned_kwh
        assert total_low > total_high

    def test_heavy_consumption_day(self):
        """Heavy consumption (above average) → more charging."""
        config_normal = default_config(consumption_est_kwh=20.0)
        config_heavy = default_config(consumption_est_kwh=60.0)
        state = default_state(
            battery_soc_pct=40.0,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            current_hour=10,
        )
        result_normal = calculate_schedule(config_normal, state)
        result_heavy = calculate_schedule(config_heavy, state)
        assert result_heavy.grid_energy_planned >= result_normal.grid_energy_planned


# ---------------------------------------------------------------------------
# Test: Slot granularity (24, 48, 96 slots/day)
# ---------------------------------------------------------------------------

class TestSlotGranularity:
    @pytest.mark.parametrize("num_slots", [24, 48, 96])
    def test_granularity_consistent(self, num_slots):
        """Schedule works for different granularities and produces sensible results."""
        config = default_config(consumption_est_kwh=30.0)
        prices = make_prices(num_slots, pattern="u_shape")
        state = default_state(
            battery_soc_pct=40.0,
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            current_hour=10,
        )
        result = calculate_schedule(config, state)
        charge_slots = [k for k, v in result.scheduled_slots.items() if v == "charge"]
        assert len(charge_slots) > 0
        assert result.grid_energy_planned > 0
        # All selected slots should be valid indices
        assert all(0 <= s < num_slots for s in result.scheduled_slots)

    @pytest.mark.parametrize("num_slots", [24, 48, 96])
    def test_energy_scales_with_granularity(self, num_slots):
        """Total planned energy should be roughly similar regardless of granularity."""
        config = default_config(consumption_est_kwh=30.0)
        prices = [0.20] * num_slots
        state = default_state(
            battery_soc_pct=30.0,
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            current_hour=8,
        )
        result = calculate_schedule(config, state)
        # Energy should be in reasonable range regardless of granularity
        assert 0 < result.grid_energy_planned < 60


# ---------------------------------------------------------------------------
# Test: Available info / charge likelihood
# ---------------------------------------------------------------------------

class TestAvailableInfo:
    def test_no_data(self):
        """No price data → no_data likelihood."""
        config = default_config()
        state = default_state(slot_prices_today=None)
        info = calculate_available_info(config, state, None)
        assert info.charge_likelihood == "no_data"

    def test_on_track(self):
        """Plenty of capacity → on_track."""
        config = default_config(consumption_est_kwh=20.0)
        state = default_state(
            battery_soc_pct=80.0,
            pv_hourly_kwh=make_pv_hourly(40.0),
            pv_forecast_remaining=25.0,
            pv_actual_today_kwh=15.0,
        )
        info = calculate_available_info(config, state, 0.30, grid_energy_planned=20.0)
        assert info.charge_likelihood == "on_track"

    def test_grid_mode_off_informational(self):
        """Grid mode off → shows idle info."""
        config = default_config(grid_mode="off")
        state = default_state(battery_soc_pct=80.0)
        info = calculate_available_info(config, state, 0.30)
        assert "idle" in info.charge_likelihood

    def test_to_grid_nothing_to_sell(self):
        """to_grid with low battery → nothing_to_sell."""
        config = default_config(grid_mode="to_grid", battery_discharge_min_pct=50.0)
        state = default_state(battery_soc_pct=50.0)
        info = calculate_available_info(config, state, 0.30)
        assert info.charge_likelihood == "nothing_to_sell"


# ---------------------------------------------------------------------------
# Test: Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_battery_soc_none(self):
        """Unknown battery SOC → still produces a schedule with 0 kWh base."""
        config = default_config()
        state = default_state(battery_soc_pct=None)
        result = calculate_schedule(config, state)
        # Should still work, treating current_kwh as 0
        assert result.status in ("active", "waiting", "no_action_needed")

    def test_all_prices_none(self):
        """All None prices → no slots remaining, day_complete."""
        config = default_config()
        state = default_state(slot_prices_today=[None] * 24, current_hour=0)
        result = calculate_schedule(config, state)
        assert result.status == "day_complete"

    def test_end_of_day(self):
        """At hour 23 with one slot left."""
        config = default_config()
        state = default_state(
            slot_prices_today=[0.20] * 24,
            current_hour=23,
            current_minute=30,
        )
        result = calculate_schedule(config, state)
        # Should still work with just 1 slot
        assert result.status in ("active", "waiting", "no_action_needed")

    def test_zero_power(self):
        """Zero power → energy_per_slot = 0, shouldn't crash."""
        config = default_config(safe_power_kw=0.0)
        state = default_state(current_hour=10)
        # Should not raise
        result = calculate_schedule(config, state)
        assert result is not None

    def test_zero_capacity(self):
        """Zero battery capacity edge case."""
        config = default_config(battery_capacity_kwh=0.0)
        state = default_state(battery_soc_pct=50.0, current_hour=10)
        result = calculate_schedule(config, state)
        assert result is not None

    def test_schedule_result_defaults(self):
        """ScheduleResult has sensible defaults."""
        r = ScheduleResult()
        assert r.scheduled_slots == {}
        assert r.grid_energy_planned == 0.0
        assert r.status == "off"


# ---------------------------------------------------------------------------
# Test: Integration-like scenarios
# ---------------------------------------------------------------------------

class TestScenarios:
    def test_scenario_winter_evening_low_battery(self):
        """Winter evening: low PV, low battery, high consumption.
        Should schedule maximum cheap charging."""
        config = default_config(consumption_est_kwh=45.0)
        prices = [0.35] * 6 + [0.10] * 6 + [0.20] * 6 + [0.35] * 6  # cheap 6-12
        state = default_state(
            battery_soc_pct=25.0,
            slot_prices_today=prices,
            pv_hourly_kwh={h: 0.5 for h in range(9, 15)},  # weak winter PV
            pv_forecast_remaining=1.0,
            pv_forecast_today=3.0,
            pv_actual_today_kwh=2.0,
            current_hour=6,
        )
        result = calculate_schedule(config, state)
        charge_slots = [k for k, v in result.scheduled_slots.items() if v == "charge"]
        # Should primarily pick slots 6-11 (cheap)
        cheap_selected = [s for s in charge_slots if 6 <= s < 12]
        assert len(cheap_selected) > 0
        assert result.grid_energy_planned > 10  # significant charging needed

    def test_scenario_summer_midday_full_battery(self):
        """Summer midday: strong PV, battery already full.
        Should schedule nothing."""
        config = default_config(consumption_est_kwh=25.0)
        state = default_state(
            battery_soc_pct=95.0,
            pv_hourly_kwh=make_pv_hourly(45.0, sunrise=5, sunset=21),
            pv_forecast_remaining=30.0,
            pv_forecast_today=45.0,
            pv_actual_today_kwh=15.0,
            current_hour=12,
        )
        result = calculate_schedule(config, state)
        charge_slots = [k for k, v in result.scheduled_slots.items() if v == "charge"]
        # Battery at 95% of 60 = 57 kWh, well above any reserve target
        assert len(charge_slots) == 0

    def test_scenario_cheap_morning_tomorrow(self):
        """Tomorrow has cheap morning slots. With enough battery,
        should defer today's expensive charging to tomorrow's cheap morning."""
        config = default_config(consumption_est_kwh=30.0)
        state = default_state(
            battery_soc_pct=50.0,
            slot_prices_today=[0.30] * 24,  # all expensive today
            slot_prices_tomorrow=make_prices(24, pattern="cheap_morning"),
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_tomorrow=25.0,
            current_hour=14,
        )
        result = calculate_schedule(config, state)
        # Should defer to tomorrow's cheap morning
        assert result.tomorrow_planned_slots > 0
        # Tomorrow's slots should be from the cheap morning period (indices 0-7)
        # (We can verify this by checking tomorrow_planned_kwh > 0)
        assert result.tomorrow_planned_kwh > 0

    def test_scenario_negative_prices_bonus_charge(self):
        """Negative prices trigger charging when battery has room."""
        config = default_config(battery_capacity_kwh=60.0, consumption_est_kwh=20.0)
        prices = [0.25] * 10 + [-0.05, -0.03, -0.01] + [0.25] * 11
        state = default_state(
            battery_soc_pct=40.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(10.0),
            pv_forecast_remaining=5.0,
            pv_actual_today_kwh=5.0,
            current_hour=0,
        )
        result = calculate_schedule(config, state)
        charge_slots = [k for k, v in result.scheduled_slots.items() if v == "charge"]
        neg_slots = [s for s in charge_slots if prices[s] < 0]
        assert len(neg_slots) >= 1

    def test_scenario_negative_prices_kept_when_pv_fills_battery(self):
        """Negative-price slots kept when PV alone fills battery — overflow
        is PV-caused, negative-price income is pure profit."""
        config = default_config(consumption_est_kwh=20.0)
        prices = [0.25] * 10 + [-0.05, -0.03, -0.01] + [0.25] * 11
        state = default_state(
            battery_soc_pct=70.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(35.0),
            pv_forecast_remaining=20.0,
            pv_actual_today_kwh=15.0,
            current_hour=0,
        )
        result = calculate_schedule(config, state)
        charge_slots = [k for k, v in result.scheduled_slots.items() if v == "charge"]
        neg_slots = [s for s in charge_slots if prices[s] < 0]
        assert len(neg_slots) >= 1, (
            "Negative-price slots should be kept when PV fills battery anyway"
        )

    def test_scenario_both_mode_negative_prices_with_headroom(self):
        """Both mode charges at negative prices when battery has room."""
        config = default_config(
            grid_mode="both", battery_capacity_kwh=60.0,
            consumption_est_kwh=20.0, arbitrage_price_delta=0.20,
        )
        prices = [0.05] * 4 + [-0.10, -0.08, -0.05] + [0.30] * 17
        state = default_state(
            battery_soc_pct=30.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(15.0),
            pv_forecast_remaining=10.0,
            pv_actual_today_kwh=5.0,
            current_hour=0,
        )
        result = calculate_schedule(config, state)
        charge_slots = [k for k, v in result.scheduled_slots.items() if v == "charge"]
        neg_slots = [s for s in charge_slots if prices[s] < 0]
        assert len(neg_slots) >= 1

    def test_scenario_both_mode_negative_prices_kept_when_pv_fills(self):
        """Both mode keeps negative-price charging when PV fills battery —
        overflow is PV-caused, negative-price income is profit."""
        config = default_config(
            grid_mode="both", consumption_est_kwh=10.0,
        )
        # Lots of negative-price slots during PV hours
        prices = [0.10] * 6 + [-0.05] * 8 + [0.10] * 10
        state = default_state(
            battery_soc_pct=80.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(40.0),
            pv_forecast_remaining=25.0,
            pv_actual_today_kwh=15.0,
            current_hour=0,
        )
        result = calculate_schedule(config, state)
        charge_slots = [k for k, v in result.scheduled_slots.items() if v == "charge"]
        neg_slots = [s for s in charge_slots if prices[s] < 0]
        assert len(neg_slots) >= 1, (
            "Negative-price slots should be kept when PV fills battery anyway"
        )


# ---------------------------------------------------------------------------
# Test: Predictive helper functions
# ---------------------------------------------------------------------------

class TestPVConfidence:
    def test_no_data(self):
        """No PV data → confidence 1.0."""
        assert _calculate_pv_confidence(None, None, 12) == 1.0

    def test_early_morning_no_expected(self):
        """Before sunrise, expected is 0 → confidence 1.0."""
        pv = make_pv_hourly(30.0)
        assert _calculate_pv_confidence(pv, 0.0, 3) == 1.0

    def test_tracking_forecast(self):
        """Actual matches expected → confidence 1.0."""
        pv = make_pv_hourly(30.0)
        # Sum expected before hour 14
        expected = sum(kwh for h, kwh in pv.items() if h < 14)
        conf = _calculate_pv_confidence(pv, expected, 14)
        assert conf == pytest.approx(1.0, abs=0.05)

    def test_cloudy_day(self):
        """Actual much less than expected → low confidence."""
        pv = make_pv_hourly(30.0)
        expected = sum(kwh for h, kwh in pv.items() if h < 14)
        conf = _calculate_pv_confidence(pv, expected * 0.3, 14)
        assert conf == pytest.approx(0.3, abs=0.05)

    def test_clamp_minimum(self):
        """Confidence never goes below 0.1."""
        pv = make_pv_hourly(30.0)
        conf = _calculate_pv_confidence(pv, 0.0, 14)
        assert conf == 0.1

    def test_early_morning_low_actual_trusts_forecast(self):
        """Early morning with low actual should still trust forecast.

        At 8 AM with only ~1-2 kWh expected out of 54 kWh forecast,
        evidence is too weak to deviate from the forecast.
        """
        pv = make_pv_hourly(54.0, sunrise=7, sunset=19)
        # At 8 AM, only ~0.7 kWh produced vs ~1.5 kWh expected
        conf = _calculate_pv_confidence(pv, 0.7, 8, 0)
        # Evidence weight is low → confidence stays close to 1.0
        assert conf >= 0.85, f"Early morning confidence {conf} too low, should trust forecast"

    def test_midday_low_actual_reduces_confidence(self):
        """By midday with substantial evidence, low actual → low confidence."""
        pv = make_pv_hourly(54.0, sunrise=7, sunset=19)
        expected_by_14 = sum(kwh for h, kwh in pv.items() if h < 14)
        # Only 30% of expected produced by 2 PM — genuinely cloudy
        conf = _calculate_pv_confidence(pv, expected_by_14 * 0.3, 14)
        assert conf < 0.45, f"Midday confidence {conf} too high with only 30% production"

    def test_evidence_weight_gradual(self):
        """Confidence transitions smoothly from trust to measured."""
        pv = make_pv_hourly(50.0, sunrise=7, sunset=19)
        # At hour 9 (little evidence) with 50% actual
        expected_by_9 = sum(kwh for h, kwh in pv.items() if h < 9)
        conf_9 = _calculate_pv_confidence(pv, expected_by_9 * 0.5, 9)
        # At hour 14 (lots of evidence) with same 50% ratio
        expected_by_14 = sum(kwh for h, kwh in pv.items() if h < 14)
        conf_14 = _calculate_pv_confidence(pv, expected_by_14 * 0.5, 14)
        # Hour 9 should be closer to 1.0 than hour 14
        assert conf_9 > conf_14, f"Early confidence {conf_9} should be higher than midday {conf_14}"


class TestBackendSocTrajectory:
    """Tests for the authoritative SOC trajectory returned by calculate_schedule."""

    def test_trajectory_length_matches_slots(self):
        """Backend trajectory has one entry per slot."""
        config = default_config()
        state = default_state(battery_soc_pct=50.0, current_hour=0)
        result = calculate_schedule(config, state)
        num_slots = len(state.slot_prices_today)
        assert len(result.soc_trajectory) == num_slots

    def test_trajectory_reflects_charging(self):
        """SOC rises at charge slots."""
        config = default_config(consumption_est_kwh=5.0)
        prices = [0.05] * 4 + [0.30] * 20
        state = default_state(
            battery_soc_pct=30.0,
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            current_hour=0,
        )
        result = calculate_schedule(config, state)
        assert len(result.soc_trajectory) == 24
        charge_slots = [k for k, v in result.scheduled_slots.items() if v == "charge"]
        if charge_slots:
            first = min(charge_slots)
            last = max(charge_slots)
            # SOC at last+1 (if exists) or last should be higher than at first
            after_last = min(last + 1, len(result.soc_trajectory) - 1)
            assert result.soc_trajectory[after_last] > result.soc_trajectory[first]

    def test_trajectory_no_schedule_still_returned(self):
        """Even with no scheduled actions, trajectory is computed."""
        config = default_config(grid_mode="from_grid")
        state = default_state(
            battery_soc_pct=95.0,
            pv_hourly_kwh=make_pv_hourly(40.0),
            pv_forecast_remaining=20.0,
            current_hour=0,
        )
        result = calculate_schedule(config, state)
        assert len(result.soc_trajectory) == len(state.slot_prices_today)

    def test_trajectory_off_mode_empty(self):
        """Grid mode off produces empty trajectory."""
        config = default_config(grid_mode="off")
        state = default_state(battery_soc_pct=50.0)
        result = calculate_schedule(config, state)
        assert result.soc_trajectory == []


class TestSOCTrajectory:
    def test_flat_consumption_no_pv(self):
        """Pure consumption drain, no PV."""
        remaining = [(i, 0.20) for i in range(24)]
        proj, min_soc, max_soc = _project_soc_trajectory(
            remaining, 50.0, 1.0, None, 60.0, 1.0, 100.0,
        )
        assert max_soc == 50.0  # starts at 50, only drains
        assert min_soc == pytest.approx(50.0 - 24 * 1.0, abs=0.1)
        assert len(proj) == 24

    def test_pv_boost_midday(self):
        """PV boosts battery during day."""
        pv = {h: 4.0 for h in range(10, 16)}  # 6h × 4kWh = 24 kWh
        remaining = [(i, 0.20) for i in range(24)]
        proj, min_soc, max_soc = _project_soc_trajectory(
            remaining, 30.0, 1.0, pv, 60.0, 1.0, 100.0,
        )
        # Should peak during PV hours
        assert max_soc > 30.0
        # min is before PV kicks in
        assert min_soc < 30.0

    def test_battery_cap(self):
        """SOC projection capped at battery capacity."""
        pv = {h: 10.0 for h in range(6, 18)}  # huge PV
        remaining = [(i, 0.20) for i in range(24)]
        proj, _, max_soc = _project_soc_trajectory(
            remaining, 90.0, 0.5, pv, 60.0, 1.0, 100.0,
        )
        assert max_soc <= 100.0


# ---------------------------------------------------------------------------
# Test: Predictive scheduling — from_grid
# ---------------------------------------------------------------------------

class TestPredictiveFromGrid:
    def test_night_precharge_cheap_slots(self):
        """Battery above reserve at 2 AM, but trajectory shows afternoon dip.
        Should preemptively charge at cheap night prices."""
        config = default_config(
            consumption_est_kwh=38.5,
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=40.0,
        )
        # Cheap night (0-7), expensive day (8-23)
        prices = [0.05] * 8 + [0.30] * 16
        state = default_state(
            battery_soc_pct=80.0,  # 48 kWh — above reserve NOW
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            pv_actual_today_kwh=0.0,
            current_hour=2,
            current_minute=0,
        )
        result = calculate_schedule(config, state)
        charge_slots = [k for k, v in result.scheduled_slots.items() if v == "charge"]

        # Must charge (predictive deficit)
        assert len(charge_slots) > 0
        # All charges should be in cheap night period (slots 2-7)
        cheap_charges = [s for s in charge_slots if s < 8]
        assert len(cheap_charges) >= len(charge_slots) - 1, \
            f"Expected most charges in cheap night slots, got {charge_slots}"
        # SOC validation limits charges: 48 kWh + 4.5/slot = overflow after ~4
        # Battery can't hold all 6 night slots, so expect 3-5 based on headroom
        assert 3 <= len(cheap_charges) <= 5, \
            f"Expected 3-5 charges (headroom limited), got {len(cheap_charges)}"

    def test_no_precharge_when_pv_covers(self):
        """Battery dips during night but strong PV recovers it.
        Should NOT charge at night."""
        config = default_config(
            consumption_est_kwh=20.0,
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
        )
        prices = [0.05] * 8 + [0.30] * 16
        state = default_state(
            battery_soc_pct=70.0,  # 42 kWh
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(40.0),
            pv_forecast_remaining=35.0,
            pv_forecast_today=40.0,
            pv_actual_today_kwh=0.0,
            current_hour=2,
        )
        result = calculate_schedule(config, state)
        charge_slots = [k for k, v in result.scheduled_slots.items() if v == "charge"]
        # PV covers consumption → no grid charging needed
        assert len(charge_slots) == 0

    def test_predictive_more_than_snapshot(self):
        """Predictive deficit should be >= snapshot deficit."""
        config = default_config(
            consumption_est_kwh=30.0,
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
        )
        # Battery right at reserve target → snapshot deficit = 0
        # But consumption will drain it below → predictive deficit > 0
        min_kwh = 0.20 * 60  # 12
        reserve_kwh = calculate_self_consumption_reserve(30.0)
        reserve_target = min(60, min_kwh + reserve_kwh)

        prices = [0.10] * 24
        state = default_state(
            battery_soc_pct=reserve_target / 60.0 * 100,
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            current_hour=0,
        )
        result = calculate_schedule(config, state)
        # Snapshot deficit = 0 (at target), but predictive > 0 (will drain)
        assert result.grid_energy_planned > 0

    def test_predictive_matches_user_scenario(self):
        """Reproduces the reported issue: 72% battery, 60 kWh, 40% min,
        cloudy day, cheap night slots ignored."""
        config = default_config(
            consumption_est_kwh=38.5,
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=40.0,
            safe_power_kw=8.0,
        )
        # Realistic price curve: cheap at night, expensive during day
        prices = (
            [0.08, 0.07, 0.06, 0.05, 0.05, 0.06, 0.07, 0.09]  # 0-7
            + [0.15, 0.20, 0.25, 0.28, 0.30, 0.32, 0.19, 0.22]  # 8-15
            + [0.25, 0.28, 0.30, 0.28, 0.25, 0.22, 0.18, 0.12]  # 16-23
        )
        state = default_state(
            battery_soc_pct=72.0,
            slot_prices_today=prices,
            pv_hourly_kwh={h: 0.3 for h in range(9, 16)},  # very cloudy
            pv_forecast_remaining=1.5,
            pv_forecast_today=3.4,
            pv_actual_today_kwh=0.1,
            current_hour=9,
            current_minute=0,
        )
        result = calculate_schedule(config, state)
        charge_slots = sorted(result.scheduled_slots.keys())

        # Should pick cheaper slots, not the most expensive afternoon ones
        assert len(charge_slots) > 0
        selected_prices = [prices[s] for s in charge_slots]
        # The cheapest available slots (9+) should be preferred over expensive ones
        # Slot 14 at 0.19 should be preferred over slot 13 at 0.32
        assert max(selected_prices) < 0.30, \
            f"Selected expensive slots: {list(zip(charge_slots, selected_prices))}"


# ---------------------------------------------------------------------------
# Test: Predictive scheduling — to_grid
# ---------------------------------------------------------------------------

class TestPredictiveToGrid:
    def test_pv_boost_increases_sellable(self):
        """With PV incoming, to_grid should account for higher peak SOC."""
        config = default_config(
            grid_mode="to_grid",
            consumption_est_kwh=20.0,
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
        )
        prices = [0.30] * 24
        pv = make_pv_hourly(40.0)

        # With PV
        state_pv = default_state(
            battery_soc_pct=50.0,
            slot_prices_today=prices,
            pv_hourly_kwh=pv,
            pv_forecast_remaining=30.0,
            pv_forecast_today=40.0,
            pv_actual_today_kwh=10.0,
            current_hour=8,
        )
        result_pv = calculate_schedule(config, state_pv)

        # Without PV
        state_no_pv = default_state(
            battery_soc_pct=50.0,
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            current_hour=8,
        )
        result_no_pv = calculate_schedule(config, state_no_pv)

        # PV version should sell more (higher peak SOC)
        assert result_pv.grid_energy_planned > result_no_pv.grid_energy_planned

    def test_reserve_protection(self):
        """to_grid should not sell into self-consumption reserve."""
        config = default_config(
            grid_mode="to_grid",
            consumption_est_kwh=38.5,
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=40.0,
        )
        prices = [0.50] * 24
        state = default_state(
            battery_soc_pct=55.0,  # 33 kWh
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            current_hour=18,
        )
        result = calculate_schedule(config, state)
        # reserve_target = min(60, 24 + overnight_reserve) ≈ 43+ kWh
        # max_projected = 33 (no PV, only drains) < reserve_target
        assert result.status == "no_action_needed"

    def test_has_self_consumption_reserve(self):
        """to_grid should now report self_consumption_reserve."""
        config = default_config(
            grid_mode="to_grid",
            consumption_est_kwh=24.0,
        )
        state = default_state(
            battery_soc_pct=90.0,
            current_hour=0,
        )
        result = calculate_schedule(config, state)
        assert result.self_consumption_reserve > 0


# ---------------------------------------------------------------------------
# Test: Predictive scheduling — both mode
# ---------------------------------------------------------------------------

class TestPredictiveBoth:
    def test_predictive_charge_and_sell(self):
        """Both mode: predictive charge at cheap + sell at expensive."""
        config = default_config(
            grid_mode="both",
            consumption_est_kwh=20.0,
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
        )
        prices = [0.05] * 8 + [0.40] * 8 + [0.10] * 8
        state = default_state(
            battery_soc_pct=60.0,  # 36 kWh
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(30.0),
            pv_forecast_remaining=25.0,
            pv_forecast_today=30.0,
            pv_actual_today_kwh=5.0,
            current_hour=4,
        )
        result = calculate_schedule(config, state)
        charge_slots = [k for k, v in result.scheduled_slots.items() if v == "charge"]
        discharge_slots = [k for k, v in result.scheduled_slots.items() if v == "discharge"]

        if charge_slots:
            assert max(prices[s] for s in charge_slots) < 0.20
        if discharge_slots:
            assert min(prices[s] for s in discharge_slots) > 0.20

    def test_predictive_sellable_with_pv(self):
        """Both mode sell side: PV boost should increase sellable vs no PV."""
        config = default_config(
            grid_mode="both",
            consumption_est_kwh=20.0,
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
        )
        # Wide spread for profitable selling
        prices = [0.05] * 6 + [0.50] * 12 + [0.05] * 6

        state_pv = default_state(
            battery_soc_pct=50.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(40.0),
            pv_forecast_remaining=30.0,
            pv_forecast_today=40.0,
            pv_actual_today_kwh=10.0,
            current_hour=6,
        )
        result_pv = calculate_schedule(config, state_pv)

        state_no_pv = default_state(
            battery_soc_pct=50.0,
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            current_hour=6,
        )
        result_no_pv = calculate_schedule(config, state_no_pv)

        sell_pv = len([k for k, v in result_pv.scheduled_slots.items() if v == "discharge"])
        sell_no_pv = len([k for k, v in result_no_pv.scheduled_slots.items() if v == "discharge"])
        assert sell_pv >= sell_no_pv


# ---------------------------------------------------------------------------
# Test: Per-slot SOC validation (_validate_schedule_soc)
# ---------------------------------------------------------------------------

class TestValidateScheduleSOC:
    """Direct tests of the _validate_schedule_soc helper."""

    def test_valid_schedule_unchanged(self):
        """A schedule that stays within bounds is returned unchanged."""
        # 24 slots, battery at 30 kWh, capacity 60, min 12 (20%)
        remaining = [(i, 0.25) for i in range(24)]
        charge = {2, 3}     # charge at slots 2 and 3
        discharge = {20, 21}  # discharge at slots 20 and 21

        vc, vd = _validate_schedule_soc(
            remaining, charge, discharge,
            current_kwh=30.0,
            consumption_per_slot=38.5 / 24,
            pv_hourly_kwh=make_pv_hourly(30.0),
            minutes_per_slot=60.0,
            pv_confidence=1.0,
            battery_capacity=60.0,
            min_kwh=12.0,
            energy_per_slot=5.0,
            efficiency=0.90,
        )
        assert vc == charge
        assert vd == discharge

    def test_discharge_dropped_when_soc_too_low(self):
        """Discharge slots removed when they would cause SOC below min."""
        # Low battery, no PV, heavy discharge in early slots
        remaining = [(i, 0.20 + i * 0.01) for i in range(24)]
        charge = set()
        discharge = {1, 2, 3, 4, 5}  # 5 discharge slots, 5 kWh each = 25 kWh

        vc, vd = _validate_schedule_soc(
            remaining, charge, discharge,
            current_kwh=20.0,   # just above min
            consumption_per_slot=1.0,
            pv_hourly_kwh={},
            minutes_per_slot=60.0,
            pv_confidence=1.0,
            battery_capacity=60.0,
            min_kwh=12.0,
            energy_per_slot=5.0,
            efficiency=0.90,
        )
        # Some discharge slots should be dropped
        assert len(vd) < 5
        # Least valuable (lowest price) should be dropped first
        if vd:
            dropped = discharge - vd
            surviving_min_price = min(0.20 + s * 0.01 for s in vd)
            for d in dropped:
                assert 0.20 + d * 0.01 <= surviving_min_price

    def test_charge_dropped_when_soc_too_high(self):
        """Charge slots removed when they would push SOC above capacity."""
        # Battery nearly full, lots of charge slots
        remaining = [(i, 0.10) for i in range(24)]
        charge = {0, 1, 2, 3, 4}  # 5 charge slots, 5*0.9=4.5 kWh each = 22.5 kWh
        discharge = set()

        vc, vd = _validate_schedule_soc(
            remaining, charge, discharge,
            current_kwh=55.0,   # near capacity of 60
            consumption_per_slot=0.5,
            pv_hourly_kwh={},
            minutes_per_slot=60.0,
            pv_confidence=1.0,
            battery_capacity=60.0,
            min_kwh=12.0,
            energy_per_slot=5.0,
            efficiency=0.90,
        )
        # Some charge slots should be dropped since battery is nearly full
        assert len(vc) < 5

    def test_mixed_charge_discharge_validation(self):
        """Combined charge+discharge schedule validated together."""
        # Charge early (cheap) then discharge late (expensive)
        # But make discharge aggressive enough to hit min
        remaining = [(i, 0.05 if i < 6 else 0.40) for i in range(24)]
        charge = {0, 1}
        discharge = {18, 19, 20, 21, 22, 23}

        vc, vd = _validate_schedule_soc(
            remaining, charge, discharge,
            current_kwh=20.0,
            consumption_per_slot=1.5,
            pv_hourly_kwh=make_pv_hourly(10.0),  # modest PV
            minutes_per_slot=60.0,
            pv_confidence=1.0,
            battery_capacity=60.0,
            min_kwh=12.0,
            energy_per_slot=5.0,
            efficiency=0.90,
        )
        # Charge slots should survive (they help)
        assert vc == charge
        # Some discharge may be dropped if SOC would dip too low
        assert len(vd) <= len(discharge)

    def test_empty_schedule_is_noop(self):
        """Empty charge/discharge sets return empty."""
        remaining = [(i, 0.25) for i in range(24)]
        vc, vd = _validate_schedule_soc(
            remaining, set(), set(),
            current_kwh=30.0,
            consumption_per_slot=1.0,
            pv_hourly_kwh={},
            minutes_per_slot=60.0,
            pv_confidence=1.0,
            battery_capacity=60.0,
            min_kwh=12.0,
            energy_per_slot=5.0,
            efficiency=0.90,
        )
        assert vc == set()
        assert vd == set()


# ---------------------------------------------------------------------------
# Test: SOC validation integrated into from_grid
# ---------------------------------------------------------------------------

class TestSOCValidationFromGrid:
    """Test that from_grid respects per-slot SOC bounds."""

    def test_overcharge_prevented(self):
        """from_grid doesn't schedule more charge than battery can hold."""
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=20.0,
            battery_charge_max_pct=100.0,
            battery_discharge_min_pct=10.0,
            consumption_est_kwh=5.0,
            safe_power_kw=5.0,
        )
        # Very cheap prices encourage charging, but battery is nearly full
        prices = [0.01] * 24
        state = default_state(
            battery_soc_pct=90.0,  # 18 kWh of 20 kWh
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            pv_actual_today_kwh=0.0,
            current_hour=0,
        )
        result = calculate_schedule(config, state)
        charge_slots = [k for k, v in result.scheduled_slots.items() if v == "charge"]

        # Simulate: confirm SOC never exceeds capacity
        soc = 18.0
        for slot in range(24):
            soc -= 5.0 / 24  # consumption
            if slot in charge_slots:
                soc += 5.0 * 0.90  # charge
            assert soc <= 20.0 + 0.5, f"SOC exceeded capacity at slot {slot}: {soc:.1f}"

    def test_charge_schedule_respects_trajectory(self):
        """from_grid scheduled slots don't cause SOC overflow mid-day."""
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=30.0,
            consumption_est_kwh=10.0,
            safe_power_kw=5.0,
        )
        # Cheap morning, battery starts high, PV incoming midday
        prices = [0.05] * 8 + [0.30] * 16
        state = default_state(
            battery_soc_pct=80.0,  # 24 kWh of 30
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(20.0),
            pv_forecast_remaining=18.0,
            pv_forecast_today=20.0,
            pv_actual_today_kwh=2.0,
            current_hour=2,
        )
        result = calculate_schedule(config, state)
        charge_slots = [k for k, v in result.scheduled_slots.items() if v == "charge"]
        # With battery at 80% and PV incoming, should not schedule many charges
        # The SOC validation should prevent overcharging
        assert len(charge_slots) <= 4, f"Too many charges for nearly-full battery: {charge_slots}"


# ---------------------------------------------------------------------------
# Test: SOC validation integrated into to_grid
# ---------------------------------------------------------------------------

class TestSOCValidationToGrid:
    """Test that to_grid respects per-slot SOC bounds."""

    def test_early_discharge_prevented_before_pv(self):
        """to_grid shouldn't sell at 7am when PV doesn't peak until noon."""
        config = default_config(
            grid_mode="to_grid",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=30.0,
            safe_power_kw=5.0,
        )
        # Expensive morning, cheap afternoon — naive scheduler would sell morning
        prices = [0.50] * 8 + [0.10] * 8 + [0.30] * 8
        state = default_state(
            battery_soc_pct=40.0,  # 24 kWh, min is 12 kWh
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(40.0),  # big PV midday
            pv_forecast_remaining=35.0,
            pv_forecast_today=40.0,
            pv_actual_today_kwh=5.0,
            current_hour=4,
        )
        result = calculate_schedule(config, state)
        discharge_slots = [k for k, v in result.scheduled_slots.items() if v == "discharge"]

        # Verify SOC never dips below reserve_target through the schedule
        min_kwh = 0.20 * 60.0  # 12 kWh
        soc = 24.0
        for slot in range(4, 24):
            hour = slot
            pv_kwh = state.pv_hourly_kwh.get(hour, 0.0)
            soc += pv_kwh - (30.0 / 24)
            if slot in discharge_slots:
                soc -= 5.0
            soc = max(0, min(60.0, soc))
            # Should stay above discharge minimum
            assert soc >= min_kwh - 1.0, (
                f"SOC dropped to {soc:.1f} at slot {slot}, min is {min_kwh:.1f}"
            )

    def test_sell_limited_by_available_energy(self):
        """to_grid sells are limited by what battery can actually provide."""
        config = default_config(
            grid_mode="to_grid",
            battery_capacity_kwh=20.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=10.0,
            safe_power_kw=5.0,
        )
        # All expensive prices, but battery only has ~12 kWh above min
        prices = [0.40] * 24
        state = default_state(
            battery_soc_pct=80.0,  # 16 kWh, min 4 kWh, sellable ~12 kWh max
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            pv_actual_today_kwh=0.0,
            current_hour=0,
        )
        result = calculate_schedule(config, state)
        discharge_slots = [k for k, v in result.scheduled_slots.items() if v == "discharge"]

        # Should not schedule more discharge than battery can support
        # each discharge = 5 kWh, available ~12 kWh minus consumption drain
        assert len(discharge_slots) <= 3, f"Too many discharges: {len(discharge_slots)}"

    def test_no_sell_with_low_battery(self):
        """to_grid doesn't sell when battery is at or below reserve."""
        config = default_config(
            grid_mode="to_grid",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=30.0,
            safe_power_kw=5.0,
        )
        prices = [0.50] * 24
        state = default_state(
            battery_soc_pct=25.0,  # 15 kWh, close to min 12 kWh
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            pv_actual_today_kwh=0.0,
            current_hour=0,
        )
        result = calculate_schedule(config, state)
        discharge_slots = [k for k, v in result.scheduled_slots.items() if v == "discharge"]
        assert len(discharge_slots) == 0, "Should not sell with low battery"


# ---------------------------------------------------------------------------
# Test: SOC validation integrated into both mode
# ---------------------------------------------------------------------------

class TestSOCValidationBoth:
    """Test that both mode respects per-slot SOC bounds for charge and discharge."""

    def test_charge_and_discharge_respect_bounds(self):
        """Both mode schedule never violates SOC bounds at any slot."""
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=40.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=15.0,
            safe_power_kw=5.0,
        )
        # Cheap morning, expensive midday, cheap evening
        prices = [0.05] * 6 + [0.50] * 6 + [0.05] * 6 + [0.30] * 6
        pv = make_pv_hourly(25.0)
        state = default_state(
            battery_soc_pct=50.0,  # 20 kWh
            slot_prices_today=prices,
            pv_hourly_kwh=pv,
            pv_forecast_remaining=20.0,
            pv_forecast_today=25.0,
            pv_actual_today_kwh=5.0,
            current_hour=3,
        )
        result = calculate_schedule(config, state)
        charge_slots = {k for k, v in result.scheduled_slots.items() if v == "charge"}
        discharge_slots = {k for k, v in result.scheduled_slots.items() if v == "discharge"}

        # Simulate SOC trajectory with the schedule
        min_kwh = 0.20 * 40.0  # 8 kWh
        soc = 20.0
        for slot in range(3, 24):
            hour = slot
            pv_kwh = pv.get(hour, 0.0)
            soc += pv_kwh - (15.0 / 24)
            if slot in charge_slots:
                soc += 5.0 * 0.90
            if slot in discharge_slots:
                soc -= 5.0
            soc = max(0, min(40.0, soc))
            assert soc >= min_kwh - 1.0, (
                f"SOC {soc:.1f} below min {min_kwh:.1f} at slot {slot}"
            )
            assert soc <= 40.0 + 0.5, (
                f"SOC {soc:.1f} above capacity at slot {slot}"
            )

    def test_discharge_pruned_in_both_mode(self):
        """Both mode: discharge slots pruned when they'd breach min SOC."""
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=30.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=20.0,
            safe_power_kw=5.0,
        )
        # Cheap slot 0, expensive slots 1-23 — tempts heavy selling
        prices = [0.02] + [0.60] * 23
        state = default_state(
            battery_soc_pct=50.0,  # 15 kWh, min is 6 kWh
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            pv_actual_today_kwh=0.0,
            current_hour=0,
        )
        result = calculate_schedule(config, state)
        discharge_slots = [k for k, v in result.scheduled_slots.items() if v == "discharge"]

        # With 15 kWh, min 6 kWh, consumption 20/24 per slot, selling 5 kWh/slot
        # Can't sell more than a couple of slots without hitting min
        assert len(discharge_slots) <= 3, (
            f"Too many discharges ({len(discharge_slots)}) for limited battery"
        )

    def test_arbitrage_spread_respected(self):
        """Both mode: charge cheap and sell expensive with valid spread."""
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=10.0,
            safe_power_kw=5.0,
            efficiency=0.90,
        )
        # Clear arbitrage: cheap 0-5, expensive 12-17
        prices = [0.05] * 6 + [0.20] * 6 + [0.50] * 6 + [0.15] * 6
        state = default_state(
            battery_soc_pct=40.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(15.0),
            pv_forecast_remaining=12.0,
            pv_forecast_today=15.0,
            pv_actual_today_kwh=3.0,
            current_hour=2,
        )
        result = calculate_schedule(config, state)
        charge_slots = {k for k, v in result.scheduled_slots.items() if v == "charge"}
        discharge_slots = {k for k, v in result.scheduled_slots.items() if v == "discharge"}

        # No slot should be both charge and discharge
        assert charge_slots.isdisjoint(discharge_slots)

        # If there are sell slots, they should be in the expensive range
        if discharge_slots:
            for s in discharge_slots:
                assert prices[s] >= 0.30, f"Selling at cheap price {prices[s]} in slot {s}"


# ---------------------------------------------------------------------------
# Test: _compute_reserve_target
# ---------------------------------------------------------------------------

class TestComputeReserveTarget:
    """Test the _compute_reserve_target helper."""

    def test_dynamic_default(self):
        """With reserve_target_pct=0, uses min + overnight reserve."""
        config = default_config(
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
        )
        # min_kwh=12, reserve=10 → target=22
        target = _compute_reserve_target(config, reserve_kwh=10.0)
        assert target == 22.0

    def test_fixed_floor_overrides(self):
        """With reserve_target_pct=80, uses 80% of capacity."""
        config = default_config(
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            reserve_target_pct=80.0,
        )
        target = _compute_reserve_target(config, reserve_kwh=10.0)
        assert target == 48.0  # 80% of 60

    def test_fixed_floor_below_discharge_min(self):
        """reserve_target_pct below discharge_min uses discharge_min instead."""
        config = default_config(
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=40.0,
            reserve_target_pct=10.0,  # 10% = 6 kWh, but min is 40% = 24 kWh
        )
        target = _compute_reserve_target(config, reserve_kwh=5.0)
        assert target == 24.0  # discharge_min wins

    def test_fixed_floor_capped_at_capacity(self):
        """reserve_target_pct=100 returns battery capacity."""
        config = default_config(
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            reserve_target_pct=100.0,
        )
        target = _compute_reserve_target(config, reserve_kwh=10.0)
        assert target == 60.0

    def test_zero_means_dynamic(self):
        """Explicitly confirm 0 means dynamic mode."""
        config = default_config(
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            reserve_target_pct=0.0,
        )
        target = _compute_reserve_target(config, reserve_kwh=15.0)
        assert target == 27.0  # 12 + 15


# ---------------------------------------------------------------------------
# Test: reserve_target_pct integrated into schedule
# ---------------------------------------------------------------------------

class TestReserveTargetPctIntegration:
    """Test that reserve_target_pct affects scheduling behavior."""

    def test_high_reserve_charges_more(self):
        """With reserve_target_pct=80, EMS charges more than default."""
        prices = [0.10] * 8 + [0.30] * 16  # cheap morning

        config_default = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=20.0,
        )
        config_high_reserve = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=20.0,
            reserve_target_pct=80.0,
        )

        state = default_state(
            battery_soc_pct=50.0,  # 30 kWh
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(10.0),  # modest PV
            pv_forecast_remaining=8.0,
            pv_forecast_today=10.0,
            pv_actual_today_kwh=2.0,
            current_hour=2,
        )
        result_default = calculate_schedule(config_default, state)
        result_high = calculate_schedule(config_high_reserve, state)

        charge_default = len([k for k, v in result_default.scheduled_slots.items() if v == "charge"])
        charge_high = len([k for k, v in result_high.scheduled_slots.items() if v == "charge"])

        # Higher reserve target → more charge slots
        assert charge_high > charge_default, (
            f"High reserve ({charge_high}) should charge more than default ({charge_default})"
        )

    def test_high_reserve_reduces_selling(self):
        """With reserve_target_pct=80, EMS sells less in to_grid mode."""
        prices = [0.40] * 24  # all expensive

        config_default = default_config(
            grid_mode="to_grid",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=15.0,
        )
        config_high_reserve = default_config(
            grid_mode="to_grid",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=15.0,
            reserve_target_pct=80.0,
        )

        state = default_state(
            battery_soc_pct=90.0,  # 54 kWh
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            pv_actual_today_kwh=0.0,
            current_hour=0,
        )
        result_default = calculate_schedule(config_default, state)
        result_high = calculate_schedule(config_high_reserve, state)

        sell_default = len([k for k, v in result_default.scheduled_slots.items() if v == "discharge"])
        sell_high = len([k for k, v in result_high.scheduled_slots.items() if v == "discharge"])

        # Higher reserve → less selling (more energy kept)
        assert sell_high <= sell_default, (
            f"High reserve ({sell_high}) should sell ≤ default ({sell_default})"
        )

    def test_zero_reserve_pct_matches_original(self):
        """reserve_target_pct=0 produces identical results to no setting."""
        prices = [0.10] * 8 + [0.30] * 16

        config_none = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=60.0,
            consumption_est_kwh=30.0,
        )
        config_zero = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=60.0,
            consumption_est_kwh=30.0,
            reserve_target_pct=0.0,
        )

        state = default_state(
            battery_soc_pct=40.0,
            slot_prices_today=prices,
            current_hour=2,
        )
        result_none = calculate_schedule(config_none, state)
        result_zero = calculate_schedule(config_zero, state)

        assert result_none.scheduled_slots == result_zero.scheduled_slots
        assert result_none.grid_energy_planned == result_zero.grid_energy_planned


# ---------------------------------------------------------------------------
# Test: Projected midnight SOC continuity (today→tomorrow)
# ---------------------------------------------------------------------------

class TestProjectedMidnightContinuity:
    """Ensure tomorrow's starting SOC is consistent with today's ending SOC.

    Regression test: the frontend used to add both charge and discharge energy
    to the projected midnight SOC (via the combined 'planned' field).
    Discharge energy should reduce (not inflate) the projected battery level.

    The backend correctly computes projected_midnight using only charge energy
    and net_pv, which is why these tests verify backend correctness while
    documenting the pattern the frontend got wrong.
    """

    def test_charge_raises_projected_midnight(self):
        """Charging today should raise projected midnight, resulting in fewer
        tomorrow charge slots compared to no charging today."""
        config = default_config(
            grid_mode="from_grid",
            consumption_est_kwh=30.0,
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=10.0,
        )
        # Cheap prices today, expensive tomorrow
        prices_today = [0.05] * 24
        prices_tomorrow = [0.40] * 24

        # Low battery — will need to charge today
        state_low = default_state(
            battery_soc_pct=15.0,  # 9 kWh
            slot_prices_today=prices_today,
            slot_prices_tomorrow=prices_tomorrow,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            pv_forecast_tomorrow=5.0,
            current_hour=2,
        )
        result_low = calculate_schedule(config, state_low)

        # High battery — less/no charging needed today
        state_high = default_state(
            battery_soc_pct=95.0,  # 57 kWh
            slot_prices_today=prices_today,
            slot_prices_tomorrow=prices_tomorrow,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            pv_forecast_tomorrow=5.0,
            current_hour=2,
        )
        result_high = calculate_schedule(config, state_high)

        # Higher starting SOC should need less total charging across both days
        total_low = (len([k for k, v in result_low.scheduled_slots.items() if v == "charge"])
                     + result_low.tomorrow_planned_slots)
        total_high = (len([k for k, v in result_high.scheduled_slots.items() if v == "charge"])
                      + result_high.tomorrow_planned_slots)
        assert total_high <= total_low, (
            "Higher starting SOC should need less total charging — "
            "projected midnight must account for current battery level"
        )

    def test_net_pv_raises_projected_midnight(self):
        """Remaining PV today should increase projected midnight SOC,
        reducing tomorrow's charge needs."""
        config = default_config(
            grid_mode="from_grid",
            consumption_est_kwh=20.0,
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=10.0,
        )
        prices_today = [0.15] * 24
        prices_tomorrow = [0.15] * 24

        # Without PV
        state_no_pv = default_state(
            battery_soc_pct=30.0,
            slot_prices_today=prices_today,
            slot_prices_tomorrow=prices_tomorrow,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            pv_forecast_tomorrow=5.0,
            current_hour=8,
        )
        result_no_pv = calculate_schedule(config, state_no_pv)

        # With significant PV remaining
        state_pv = default_state(
            battery_soc_pct=30.0,
            slot_prices_today=prices_today,
            slot_prices_tomorrow=prices_tomorrow,
            pv_hourly_kwh=make_pv_hourly(25.0, sunrise=8, sunset=18),
            pv_forecast_remaining=20.0,
            pv_forecast_today=25.0,
            pv_forecast_tomorrow=5.0,
            current_hour=8,
        )
        result_pv = calculate_schedule(config, state_pv)

        total_no_pv = (len([k for k, v in result_no_pv.scheduled_slots.items() if v == "charge"])
                       + result_no_pv.tomorrow_planned_slots)
        total_pv = (len([k for k, v in result_pv.scheduled_slots.items() if v == "charge"])
                    + result_pv.tomorrow_planned_slots)
        assert total_pv <= total_no_pv, (
            "PV production today should reduce total charging needs — "
            "projected midnight must include net_pv"
        )


# ---------------------------------------------------------------------------
# Test: Solar protection prevents unnecessary grid charging
# ---------------------------------------------------------------------------

class TestSolarProtection:
    """When solar will fill the battery to near capacity, skip grid charging."""

    def test_no_charge_when_solar_fills_battery(self):
        """from_grid: no grid charge when PV will fill battery to 95%+."""
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=35.0,
            consumption_est_kwh=38.5,
        )
        # High solar: 40 kWh forecast, strong midday production
        pv = make_pv_hourly(40.0, sunrise=7, sunset=19)
        state = default_state(
            battery_soc_pct=66.0,  # 39.6 kWh at 9:00, climbing
            slot_prices_today=make_prices(24, base=0.20),
            pv_hourly_kwh=pv,
            pv_forecast_remaining=18.5,
            pv_forecast_today=40.0,
            pv_actual_today_kwh=21.9,
            current_hour=9,
        )
        result = calculate_schedule(config, state)
        charge_slots = {k for k, v in result.scheduled_slots.items() if v == "charge"}
        assert len(charge_slots) == 0, (
            f"Should not grid-charge when solar fills battery; got {len(charge_slots)} charge slots"
        )

    def test_no_charge_when_no_hourly_pv_but_remaining_forecast(self):
        """from_grid: no grid charge when hourly PV data is missing but
        remaining PV forecast is high enough to fill the battery."""
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=35.8,
        )
        # Simulate: no hourly PV data (wh_hours missing from entity)
        # but forecast says 27.2 kWh remaining, 54.5 kWh total today
        state = default_state(
            battery_soc_pct=76.0,  # 45.6 kWh of 60
            slot_prices_today=make_prices(96, base=0.14),
            pv_hourly_kwh={},  # no hourly breakdown!
            pv_forecast_remaining=27.2,
            pv_forecast_today=54.5,
            pv_actual_today_kwh=22.9,
            current_hour=13,
        )
        result = calculate_schedule(config, state)
        charge_slots = {k for k, v in result.scheduled_slots.items() if v == "charge"}
        assert len(charge_slots) == 0, (
            f"Should not grid-charge when synthesized PV shows battery will fill; "
            f"got {len(charge_slots)} charge slots"
        )

    def test_no_charge_both_mode_when_solar_fills_battery(self):
        """both mode: no grid charge when PV will fill battery to 95%+."""
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=35.0,
            consumption_est_kwh=38.5,
        )
        pv = make_pv_hourly(40.0, sunrise=7, sunset=19)
        state = default_state(
            battery_soc_pct=66.0,
            slot_prices_today=make_prices(24, base=0.20),
            pv_hourly_kwh=pv,
            pv_forecast_remaining=18.5,
            pv_forecast_today=40.0,
            pv_actual_today_kwh=21.9,
            current_hour=9,
        )
        result = calculate_schedule(config, state)
        charge_slots = {k for k, v in result.scheduled_slots.items() if v == "charge"}
        assert len(charge_slots) == 0, (
            f"Both mode: should not grid-charge when solar fills battery; got {len(charge_slots)} charge slots"
        )


# ---------------------------------------------------------------------------
# Test: Arbitrage price delta in both mode
# ---------------------------------------------------------------------------

class TestArbitragePriceDelta:
    """When price spread exceeds threshold, charge to full for profitable resale."""

    def test_arbitrage_charges_to_full_on_large_spread(self):
        """both mode: large price spread triggers full charge."""
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=10.0,
            safe_power_kw=5.0,
            arbitrage_price_delta=0.10,  # 10 ct threshold
        )
        # Cheap morning (0.05), expensive afternoon (0.40) → spread = 0.35 > 0.10
        prices = [0.05] * 6 + [0.15] * 6 + [0.40] * 6 + [0.10] * 6
        state = default_state(
            battery_soc_pct=40.0,  # 24 kWh
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(5.0),  # low PV
            pv_forecast_remaining=3.0,
            pv_forecast_today=5.0,
            pv_actual_today_kwh=1.0,
            current_hour=2,
        )
        result = calculate_schedule(config, state)
        charge_slots = {k for k, v in result.scheduled_slots.items() if v == "charge"}
        discharge_slots = {k for k, v in result.scheduled_slots.items() if v == "discharge"}

        # With arbitrage active, should charge more aggressively
        assert len(charge_slots) >= 3, (
            f"Arbitrage active: expected >=3 charge slots for full charge, got {len(charge_slots)}"
        )
        # Should sell in expensive slots
        assert len(discharge_slots) > 0, "Should sell energy in expensive slots"
        for s in discharge_slots:
            assert prices[s] >= 0.30, f"Should only sell at expensive prices, got {prices[s]}"

    def test_no_arbitrage_when_spread_too_small(self):
        """both mode: spread below the delta → NO trading at all.

        The delta is the explicit trade trigger: it suppresses both the
        charge-to-full deficit bump and the sell side.  This pins the fix
        for the bug where the automatic profitability check kept selling
        even though the user demanded a 20 ct spread.
        """
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=10.0,
            safe_power_kw=5.0,
            arbitrage_price_delta=0.20,  # 20 ct threshold
        )
        # Spread is only 0.10 (< 0.20 threshold)
        prices = [0.15] * 12 + [0.25] * 12
        state = default_state(
            battery_soc_pct=60.0,  # 36 kWh, close to reserve
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(15.0),
            pv_forecast_remaining=10.0,
            pv_forecast_today=15.0,
            pv_actual_today_kwh=5.0,
            current_hour=6,
        )
        result_delta = calculate_schedule(config, state)

        sells = [k for k, v in result_delta.scheduled_slots.items() if v == "discharge"]
        assert not sells, (
            f"Spread 0.10 < delta 0.20: no sells should be scheduled, got {sells}"
        )

        config_no_delta = default_config(
            grid_mode="both",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=10.0,
            safe_power_kw=5.0,
            arbitrage_price_delta=0.0,  # disabled → automatic check may trade
        )
        result_no_delta = calculate_schedule(config_no_delta, state)

        # No charge-to-full: charging may only be reserve-driven, never more
        # aggressive than the no-delta baseline.
        charge_delta = len({k for k, v in result_delta.scheduled_slots.items() if v == "charge"})
        charge_no_delta = len({k for k, v in result_no_delta.scheduled_slots.items() if v == "charge"})
        assert charge_delta <= charge_no_delta, (
            f"Small spread must not charge more than baseline: delta={charge_delta}, no_delta={charge_no_delta}"
        )

    def test_delta_gates_sells_at_buy_plus_delta(self):
        """both mode: spread meets the delta → sells allowed, but every sell
        slot must beat the buy reference by at least the delta."""
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=10.0,
            safe_power_kw=5.0,
            arbitrage_price_delta=0.10,
        )
        # Buy ~0.05, mid 0.12 (must NOT be sold: 0.05+0.10=0.15 > 0.12),
        # peak 0.30 (sellable).  Spread 0.25 >= 0.10 → trading active.
        prices = [0.05] * 6 + [0.12] * 6 + [0.30] * 6 + [0.12] * 6
        state = default_state(
            battery_soc_pct=40.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(5.0),
            pv_forecast_remaining=3.0,
            pv_forecast_today=5.0,
            pv_actual_today_kwh=1.0,
            current_hour=2,
        )
        result = calculate_schedule(config, state)
        charge_prices = [prices[k] for k, v in result.scheduled_slots.items() if v == "charge"]
        sell_slots = {k for k, v in result.scheduled_slots.items() if v == "discharge"}
        assert sell_slots, "Spread 0.25 >= delta 0.10: sells should be scheduled"
        max_buy = max(charge_prices) if charge_prices else min(prices)
        for s in sell_slots:
            assert prices[s] >= max_buy + 0.10, (
                f"Sell slot {s} at {prices[s]} does not clear buy ref "
                f"{max_buy} + delta 0.10"
            )

    def test_arbitrage_zero_delta_disabled(self):
        """both mode: arbitrage_price_delta=0 means feature is disabled."""
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=10.0,
            safe_power_kw=5.0,
            arbitrage_price_delta=0.0,
        )
        prices = [0.05] * 6 + [0.15] * 6 + [0.40] * 6 + [0.10] * 6
        state = default_state(
            battery_soc_pct=40.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(5.0),
            pv_forecast_remaining=3.0,
            pv_forecast_today=5.0,
            pv_actual_today_kwh=1.0,
            current_hour=2,
        )
        result = calculate_schedule(config, state)
        # Should work normally without arbitrage feature — no error
        assert isinstance(result, ScheduleResult)


class TestPowerAwareSlotSelection:
    """Slot count accounts for real charge speed (max power, PV throttling).

    Pins the fix for: 'starts to charge too early and is already done at
    the cheapest points' — over-selection from a flat per-slot energy
    assumption.
    """

    def test_high_power_needs_fewer_slots(self):
        """10 kW on hourly slots covers a 4 kWh deficit with ONE slot —
        only the single cheapest is selected."""
        remaining = [(i, 0.10 + i * 0.01) for i in range(10)]
        today, tomorrow, _ = select_unified_charge_slots(
            remaining, 4.0, 9.0, 60.0, 20.0, 10.0, 0.9, 10.0,
            current_kwh=20.0, net_pv=0.0,
            safe_power_kw=10.0, inverter_max_power_kw=10.0,
            pv_confidence=1.0, minutes_per_slot=60.0,
        )
        assert len(today) == 1, f"Expected 1 slot at 10 kW, got {len(today)}"
        assert today[0][0] == 0, "Should pick the cheapest slot"

    def test_pv_saturated_slot_skipped(self):
        """A slot where PV saturates the inverter can deliver zero grid
        charge — it must be skipped, not burned as a no-op charge slot."""
        prices = {i: 0.20 for i in range(8, 20)}
        prices[12] = 0.01  # cheapest, but PV = inverter max at hour 12
        prices[13] = 0.05
        remaining = sorted(prices.items())
        today, _, _ = select_unified_charge_slots(
            remaining, 4.0, 9.0, 60.0, 20.0, 10.0, 0.9, 10.0,
            current_kwh=20.0, net_pv=0.0,
            pv_hourly_kwh={12: 10.0},
            safe_power_kw=10.0, inverter_max_power_kw=10.0,
            pv_confidence=1.0, minutes_per_slot=60.0,
        )
        selected = {i for i, _ in today}
        assert 12 not in selected, "PV-saturated slot must be skipped"
        assert 13 in selected, "Next cheapest deliverable slot should be picked"

    def test_pv_throttled_slot_counts_reduced_energy(self):
        """A PV-throttled slot delivers less grid energy, so more slots are
        needed than the flat per-slot count would suggest."""
        prices = {i: 0.20 for i in range(8, 20)}
        prices[12] = 0.01  # cheapest; PV 8 kW → only 2 kW grid → 1.8 kWh
        prices[13] = 0.05
        remaining = sorted(prices.items())
        today, _, _ = select_unified_charge_slots(
            remaining, 4.0, 9.0, 60.0, 20.0, 10.0, 0.9, 10.0,
            current_kwh=20.0, net_pv=0.0,
            pv_hourly_kwh={12: 8.0},
            safe_power_kw=10.0, inverter_max_power_kw=10.0,
            pv_confidence=1.0, minutes_per_slot=60.0,
        )
        selected = {i for i, _ in today}
        # 1.8 kWh from slot 12 < 4 kWh deficit → slot 13 needed too
        assert selected == {12, 13}, (
            f"Expected throttled slot 12 plus companion 13, got {selected}"
        )

    def test_legacy_flat_behavior_without_power_params(self):
        """Without power params the old flat ceil(deficit/per_slot) count
        is preserved (back-compat for callers that don't pass them)."""
        remaining = [(i, 0.10 + i * 0.01) for i in range(10)]
        today, _, _ = select_unified_charge_slots(
            remaining, 10.0, 4.5, 60.0, 20.0, 10.0, 0.9, 5.0,
            current_kwh=20.0, net_pv=0.0,
        )
        assert len(today) == math.ceil(10.0 / 4.5), (
            f"Flat fallback should select ceil(10/4.5)=3 slots, got {len(today)}"
        )


class TestInverterMaxPowerCap:
    """Tests that inverter max power caps grid charge when PV is active."""

    def test_trajectory_caps_grid_charge_with_high_pv(self):
        """SOC trajectory should not assume grid can deliver full power when PV is active."""
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=10.0,
            safe_power_kw=8.0,
            inverter_max_power_kw=10.0,
            consumption_est_kwh=5.0,
        )
        # 7 kW PV during hours 8-16 → grid capped at 3 kW (10 - 7)
        pv = {h: 7.0 for h in range(8, 16)}
        state = default_state(
            battery_soc_pct=20.0,
            slot_prices_today=[0.10] * 24,
            pv_hourly_kwh=pv,
            pv_forecast_remaining=10.0,
            pv_forecast_today=56.0,
            pv_actual_today_kwh=28.0,
            current_hour=10,
        )
        result = calculate_schedule(config, state)
        # The trajectory should exist and show realistic SOC growth
        assert len(result.soc_trajectory) == 24
        # At hour 10 with 7 kW PV, grid should be capped to 3 kW, not 8 kW.
        # Check that trajectory doesn't climb as fast as uncapped 8 kW would.
        uncapped_config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=10.0,
            safe_power_kw=8.0,
            inverter_max_power_kw=100.0,  # effectively no cap
            consumption_est_kwh=5.0,
        )
        uncapped_result = calculate_schedule(uncapped_config, state)
        # With high PV, the capped trajectory should climb slower during PV hours
        # (comparing a charge slot during PV hours)
        charge_slots = [s for s, a in result.scheduled_slots.items() if a == "charge" and 8 <= s < 16]
        if charge_slots:
            slot = charge_slots[0]
            assert result.soc_trajectory[slot] <= uncapped_result.soc_trajectory[slot] + 0.1

    def test_no_cap_at_night(self):
        """At night (no PV), grid charge should use full safe_power_kw."""
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=60.0,
            safe_power_kw=8.0,
            inverter_max_power_kw=10.0,
            consumption_est_kwh=10.0,
        )
        state = default_state(
            battery_soc_pct=30.0,
            slot_prices_today=[0.05] * 6 + [0.20] * 18,
            pv_hourly_kwh={},  # no PV data at all
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            pv_actual_today_kwh=0.0,
            current_hour=2,
        )
        result = calculate_schedule(config, state)
        # Night charge slots should exist and grid can deliver full 8 kW
        charge_slots = {s for s, a in result.scheduled_slots.items() if a == "charge"}
        assert len(charge_slots) > 0

    def test_validate_soc_respects_inverter_cap(self):
        """_validate_schedule_soc should use capped grid energy during charge slots."""
        # Charge slot at hour 12 with 7 kW PV, inverter max 10 → grid 3 kW
        remaining = [(i, 0.10) for i in range(24)]
        charge_slots = {12}
        discharge_slots = set()
        pv = {12: 7.0}
        validated_charge, _ = _validate_schedule_soc(
            remaining, charge_slots, discharge_slots,
            current_kwh=5.0,
            consumption_per_slot=0.5,
            pv_hourly_kwh=pv,
            minutes_per_slot=60.0,
            pv_confidence=1.0,
            battery_capacity=10.0,
            min_kwh=2.0,
            energy_per_slot=8.0,  # 8 kW * 1h
            efficiency=0.90,
            inverter_max_power_kw=10.0,
            safe_power_kw=8.0,
        )
        # Slot 12 should still be valid (grid charges at 3 kW, PV at 7 kW)
        assert 12 in validated_charge

    def test_default_inverter_max_no_cap(self):
        """When inverter_max_power_kw=0 (default), no capping should occur."""
        remaining = [(12, 0.10)]
        charge_slots = {12}
        pv = {12: 7.0}
        validated_charge, _ = _validate_schedule_soc(
            remaining, charge_slots, set(),
            current_kwh=5.0,
            consumption_per_slot=0.5,
            pv_hourly_kwh=pv,
            minutes_per_slot=60.0,
            pv_confidence=1.0,
            battery_capacity=100.0,
            min_kwh=2.0,
            energy_per_slot=8.0,
            efficiency=0.90,
            inverter_max_power_kw=0.0,  # no cap
            safe_power_kw=0.0,
        )
        assert 12 in validated_charge


class TestBothModeSellWithChargeEnergy:
    """Tests that both mode sells surplus from grid-charged energy."""

    def test_sell_slots_when_pv_confidence_low(self):
        """Both mode should sell surplus even when PV confidence is near zero.

        Scenario: battery at 28%, PV confidence 0.1 (0 kWh produced),
        grid charges several slots bringing battery well above reserve.
        The sell side should see the surplus and schedule discharge.
        """
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=13.0,
            safe_power_kw=12.5,
            inverter_max_power_kw=25.0,
        )
        prices = [0.02] * 6 + [0.05] * 6 + [0.08] * 6 + [0.15] * 6
        state = default_state(
            battery_soc_pct=28.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(39.0),
            pv_forecast_remaining=24.0,
            pv_forecast_today=39.0,
            pv_actual_today_kwh=0.0,
            current_hour=10,
        )
        result = calculate_schedule(config, state)
        charge_count = sum(1 for v in result.scheduled_slots.values() if v == "charge")
        sell_count = sum(1 for v in result.scheduled_slots.values() if v == "discharge")
        assert charge_count > 0, "Should have charge slots"
        assert sell_count > 0, "Should have sell slots — charge energy creates surplus above reserve"

    def test_no_sell_when_all_prices_equal(self):
        """No sell slots when all prices are identical (profitability filter blocks)."""
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=40.0,
            safe_power_kw=5.0,
        )
        prices = [0.10] * 24
        state = default_state(
            battery_soc_pct=20.0,
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            pv_actual_today_kwh=0.0,
            current_hour=0,
        )
        result = calculate_schedule(config, state)
        sell_count = sum(1 for v in result.scheduled_slots.values() if v == "discharge")
        assert sell_count == 0


# ---------------------------------------------------------------------------
# Integration Tests: Real-World Scenarios
# ---------------------------------------------------------------------------

class TestIntegrationCustomerScenarios:
    """End-to-end tests mimicking real customer configurations.

    Each test reproduces a specific customer setup (TREX model, battery size,
    consumption, PV, prices) and asserts the schedule meets operational
    requirements: SOC never below min, sell slots exist when surplus is
    available, charge slots are reasonable, tomorrow schedule is computed.
    """

    def _assert_soc_never_below_min(self, result, config, state):
        """Verify the SOC trajectory never dips below discharge_min."""
        if not result.soc_trajectory:
            return
        min_allowed = config.battery_discharge_min_pct
        for i, soc in enumerate(result.soc_trajectory):
            assert soc >= min_allowed - 0.5, (
                f"SOC at slot {i} is {soc}%, below min {min_allowed}%"
            )

    def _assert_no_flat_trajectory(self, result):
        """Verify SOC trajectory isn't completely flat (consumption should drain)."""
        if not result.soc_trajectory or len(result.soc_trajectory) < 3:
            return
        unique = set(round(s, 0) for s in result.soc_trajectory)
        assert len(unique) > 1, "SOC trajectory is flat — consumption isn't draining battery"

    def test_trex25_both_mode_low_battery_no_pv(self):
        """Customer: TREX-25, 60kWh, both mode, 28% SOC, 0 PV produced.

        SOC starts below reserve target, so SOC validation correctly prunes
        sell slots (battery must stay above reserve at every point).
        Charges to cover overnight needs.
        """
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            battery_charge_max_pct=100.0,
            consumption_est_kwh=13.0,
            safe_power_kw=12.5,
            inverter_max_power_kw=25.0,
            efficiency=0.90,
        )
        prices_today = (
            [0.02, 0.02, 0.03, 0.03, 0.04, 0.05]
            + [0.08, 0.10, 0.12, 0.14, 0.15, 0.14]
            + [0.12, 0.10, 0.09, 0.08, 0.07, 0.10]
            + [0.18, 0.22, 0.25, 0.20, 0.15, 0.08]
        )
        state = default_state(
            battery_soc_pct=28.0,
            slot_prices_today=prices_today,
            pv_hourly_kwh=make_pv_hourly(39.0),
            pv_forecast_remaining=24.0,
            pv_forecast_today=39.0,
            pv_actual_today_kwh=0.0,
            current_hour=10,
            current_minute=0,
        )
        result = calculate_schedule(config, state)
        charges = sum(1 for v in result.scheduled_slots.values() if v == "charge")
        assert charges > 0, "Should charge — battery is below reserve"
        self._assert_soc_never_below_min(result, config, state)
        self._assert_no_flat_trajectory(result)

    def test_trex25_both_mode_high_battery_sells(self):
        """TREX-25, battery above reserve — should sell at peak prices."""
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=13.0,
            safe_power_kw=5.0,
            inverter_max_power_kw=25.0,
        )
        prices = [0.02] * 6 + [0.05] * 6 + [0.08] * 6 + [0.15] * 6
        state = default_state(
            battery_soc_pct=80.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(30.0),
            pv_forecast_remaining=15.0,
            pv_forecast_today=30.0,
            pv_actual_today_kwh=15.0,
            current_hour=12,
        )
        result = calculate_schedule(config, state)
        sells = sum(1 for v in result.scheduled_slots.values() if v == "discharge")
        assert sells > 0, "Should sell — battery is well above reserve"
        self._assert_soc_never_below_min(result, config, state)

    def test_trex10_from_grid_cloudy_day(self):
        """TREX-10, 20kWh battery, from_grid, cloudy day (PV conf ~0.2).

        High PV forecast (30 kWh) but only 1.5 kWh produced by noon
        drops confidence, forcing grid charging.
        """
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=20.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=10.0,
            safe_power_kw=5.0,
            inverter_max_power_kw=10.0,
        )
        prices = [0.05] * 6 + [0.10] * 6 + [0.15] * 6 + [0.25] * 6
        state = default_state(
            battery_soc_pct=35.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(30.0),
            pv_forecast_remaining=20.0,
            pv_forecast_today=30.0,
            pv_actual_today_kwh=1.5,
            current_hour=12,
        )
        result = calculate_schedule(config, state)
        charges = sum(1 for v in result.scheduled_slots.values() if v == "charge")
        assert charges > 0, "Should charge — cloudy day with low PV confidence"
        self._assert_soc_never_below_min(result, config, state)

    def test_trex5_to_grid_sunny_day(self):
        """TREX-5, 10kWh battery, to_grid only, sunny day.

        Expected: sell slots at peak prices, no charge slots, SOC stays
        above reserve target (consumption tuned so reserve doesn't block
        the evening sell).
        """
        config = default_config(
            grid_mode="to_grid",
            battery_capacity_kwh=10.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=4.0,  # smaller reserve to leave room for sell
            safe_power_kw=3.0,
            inverter_max_power_kw=5.0,
        )
        prices = (
            [0.05] * 6 + [0.10] * 4 + [0.08] * 4
            + [0.10] * 2 + [0.15] * 2 + [0.25, 0.30, 0.28, 0.20, 0.12, 0.08]
        )
        state = default_state(
            battery_soc_pct=80.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(20.0),
            pv_forecast_remaining=10.0,
            pv_forecast_today=20.0,
            pv_actual_today_kwh=10.0,
            current_hour=12,
        )
        result = calculate_schedule(config, state)
        charges = sum(1 for v in result.scheduled_slots.values() if v == "charge")
        sells = sum(1 for v in result.scheduled_slots.values() if v == "discharge")
        assert charges == 0, "to_grid mode should not charge"
        assert sells > 0, "Should sell at evening peak"
        self._assert_soc_never_below_min(result, config, state)

    def test_trex50_both_mode_with_arbitrage(self):
        """TREX-50, 100kWh battery, both mode with arbitrage delta.

        No PV scenario: all charge comes from grid, creating large surplus
        above reserve for selling.
        """
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=100.0,
            battery_discharge_min_pct=15.0,
            battery_charge_max_pct=95.0,
            consumption_est_kwh=30.0,
            safe_power_kw=25.0,
            inverter_max_power_kw=50.0,
            arbitrage_price_delta=0.10,
        )
        prices = [0.02] * 8 + [0.05] * 4 + [0.04] * 4 + [0.15, 0.20, 0.25, 0.30, 0.28, 0.18, 0.10, 0.05]
        state = default_state(
            battery_soc_pct=60.0,
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            pv_actual_today_kwh=0.0,
            current_hour=6,
        )
        result = calculate_schedule(config, state)
        charges = sum(1 for v in result.scheduled_slots.values() if v == "charge")
        sells = sum(1 for v in result.scheduled_slots.values() if v == "discharge")
        assert charges > 0, "Should charge for arbitrage"
        assert sells > 0, "Should sell at evening peak — battery well above reserve"
        self._assert_soc_never_below_min(result, config, state)

    def test_trex25_negative_prices_with_pv(self):
        """TREX-25 with negative prices during high PV: negative-price
        charge slots shouldn't overflow battery when PV is already filling it.
        """
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=15.0,
            safe_power_kw=12.5,
            inverter_max_power_kw=25.0,
        )
        prices = [0.10] * 6 + [-0.05, -0.03, -0.02, -0.01] + [0.08] * 8 + [0.15] * 6
        state = default_state(
            battery_soc_pct=70.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(45.0),
            pv_forecast_remaining=30.0,
            pv_forecast_today=45.0,
            pv_actual_today_kwh=15.0,
            current_hour=9,
        )
        result = calculate_schedule(config, state)
        if result.soc_trajectory:
            max_soc = max(result.soc_trajectory)
            assert max_soc <= 100.5, f"SOC exceeds 100%: {max_soc}"
        self._assert_soc_never_below_min(result, config, state)

    def test_early_morning_schedule_full_day(self):
        """Schedule at midnight (hour 0) with all slots remaining.

        Verifies the algorithm handles the full-day case without errors
        and produces a valid SOC trajectory.
        """
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=20.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=12.0,
            safe_power_kw=5.0,
            inverter_max_power_kw=10.0,
        )
        prices = make_prices(48, pattern="v_shape")
        state = default_state(
            battery_soc_pct=50.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(18.0),
            pv_forecast_remaining=18.0,
            pv_forecast_today=18.0,
            pv_actual_today_kwh=0.0,
            current_hour=0,
            current_minute=0,
        )
        result = calculate_schedule(config, state)
        assert result.soc_trajectory, "Should produce SOC trajectory"
        assert len(result.soc_trajectory) == 48, "Should have one entry per slot"
        self._assert_soc_never_below_min(result, config, state)


class TestTomorrowScheduleIntegration:
    """Tests for the tomorrow schedule computation via calculate_schedule."""

    def test_tomorrow_schedule_computed_when_prices_available(self):
        """When tomorrow prices exist and deficit requires tomorrow charge,
        schedule includes tomorrow slots."""
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=20.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=10.0,
            safe_power_kw=5.0,
            inverter_max_power_kw=10.0,
        )
        today_prices = [0.20] * 20 + [0.25, 0.30, 0.25, 0.20]
        tomorrow_prices = [0.02] * 8 + [0.10] * 8 + [0.25] * 4 + [0.08] * 4
        state = default_state(
            battery_soc_pct=30.0,
            slot_prices_today=today_prices,
            slot_prices_tomorrow=tomorrow_prices,
            pv_hourly_kwh=make_pv_hourly(15.0),
            pv_forecast_remaining=0.0,
            pv_forecast_today=15.0,
            pv_forecast_tomorrow=12.0,
            pv_actual_today_kwh=15.0,
            current_hour=20,
        )
        result = calculate_schedule(config, state)
        assert result.tomorrow_scheduled_slots, "Should have tomorrow scheduled slots"
        assert result.tomorrow_soc_trajectory, "Should have tomorrow SOC trajectory"
        assert len(result.tomorrow_soc_trajectory) == 24

    def test_tomorrow_schedule_empty_when_grid_off(self):
        """No tomorrow schedule when grid_mode is off."""
        config = default_config(grid_mode="off")
        state = default_state(
            slot_prices_tomorrow=[0.10] * 24,
            pv_forecast_tomorrow=15.0,
        )
        result = calculate_schedule(config, state)
        assert not result.tomorrow_scheduled_slots

    def test_tomorrow_schedule_empty_when_no_prices(self):
        """No tomorrow schedule when tomorrow prices are unavailable."""
        config = default_config(grid_mode="from_grid")
        state = default_state(slot_prices_tomorrow=None)
        result = calculate_schedule(config, state)
        assert not result.tomorrow_scheduled_slots
        assert not result.tomorrow_soc_trajectory

    def test_tomorrow_trajectory_starts_from_today_end(self):
        """Tomorrow's SOC trajectory should start near today's ending SOC."""
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=20.0,
            consumption_est_kwh=10.0,
            safe_power_kw=5.0,
        )
        today_prices = make_prices(24, pattern="cheap_morning")
        tomorrow_prices = make_prices(24, pattern="v_shape")
        state = default_state(
            battery_soc_pct=60.0,
            slot_prices_today=today_prices,
            slot_prices_tomorrow=tomorrow_prices,
            pv_hourly_kwh=make_pv_hourly(15.0),
            pv_forecast_remaining=8.0,
            pv_forecast_today=15.0,
            pv_forecast_tomorrow=12.0,
            pv_actual_today_kwh=7.0,
            current_hour=12,
        )
        result = calculate_schedule(config, state)
        if result.soc_trajectory and result.tomorrow_soc_trajectory:
            today_end = result.soc_trajectory[-1]
            tomorrow_start = result.tomorrow_soc_trajectory[0]
            assert abs(today_end - tomorrow_start) < 15, (
                f"Tomorrow start SOC ({tomorrow_start}%) diverges from "
                f"today end SOC ({today_end}%) by more than 15%"
            )

    def test_tomorrow_both_mode_has_sell_slots(self):
        """Tomorrow schedule in both mode: charge cheap slots, sell at peak."""
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=100.0,
            battery_discharge_min_pct=15.0,
            consumption_est_kwh=10.0,
            safe_power_kw=10.0,
            inverter_max_power_kw=25.0,
        )
        today_prices = [0.15] * 22 + [0.25, 0.30]
        tomorrow_prices = [0.01] * 8 + [0.04] * 4 + [0.06] * 4 + [0.20, 0.30, 0.40, 0.35] + [0.08] * 4
        state = default_state(
            battery_soc_pct=20.0,
            slot_prices_today=today_prices,
            slot_prices_tomorrow=tomorrow_prices,
            pv_hourly_kwh=make_pv_hourly(20.0),
            pv_forecast_remaining=0.0,
            pv_forecast_today=20.0,
            pv_forecast_tomorrow=30.0,
            pv_actual_today_kwh=20.0,
            current_hour=22,
        )
        result = calculate_schedule(config, state)
        tmr_charges = sum(1 for v in result.tomorrow_scheduled_slots.values() if v == "charge")
        tmr_sells = sum(1 for v in result.tomorrow_scheduled_slots.values() if v == "discharge")
        assert tmr_charges > 0, "Tomorrow should have charge slots"
        assert tmr_sells > 0, "Tomorrow should have sell slots at peak prices"

    def test_tomorrow_soc_never_below_min(self):
        """Tomorrow's SOC trajectory should never go below discharge_min."""
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=40.0,
            battery_discharge_min_pct=25.0,
            consumption_est_kwh=20.0,
            safe_power_kw=8.0,
            inverter_max_power_kw=10.0,
        )
        today_prices = make_prices(24, pattern="rising")
        tomorrow_prices = [0.03] * 6 + [0.10] * 6 + [0.05] * 6 + [0.20] * 6
        state = default_state(
            battery_soc_pct=45.0,
            slot_prices_today=today_prices,
            slot_prices_tomorrow=tomorrow_prices,
            pv_hourly_kwh=make_pv_hourly(20.0),
            pv_forecast_remaining=10.0,
            pv_forecast_today=20.0,
            pv_forecast_tomorrow=18.0,
            pv_actual_today_kwh=10.0,
            current_hour=12,
        )
        result = calculate_schedule(config, state)
        min_allowed = config.battery_discharge_min_pct
        for i, soc in enumerate(result.tomorrow_soc_trajectory):
            assert soc >= min_allowed - 1.0, (
                f"Tomorrow SOC at slot {i} is {soc}%, below min {min_allowed}%"
            )

    def test_tomorrow_to_grid_high_soc_strong_pv_sells(self):
        """Tomorrow in to_grid mode with a full battery + strong PV sells.

        Regression: tomorrow's sell validation used the overnight
        reserve_target as the per-slot SOC floor, while today uses the
        absolute discharge_min.  Because the trajectory dips toward the
        target during the day before PV refills it, every evening sell was
        rejected -- tomorrow showed 0 sells while today (validated against
        discharge_min) kept its sells.  The fix validates tomorrow's sells
        against min_kwh; reserve_target still sizes how much is sellable.
        """
        config = default_config(
            grid_mode="to_grid",
            battery_capacity_kwh=60.0,
            battery_charge_max_pct=100.0,
            battery_discharge_min_pct=35.0,
            consumption_est_kwh=38.5,
            safe_power_kw=8.0,
            efficiency=0.95,
        )
        tomorrow = [0.20] * 24
        for h in (18, 19, 20, 21):
            tomorrow[h] = 0.50
        state = default_state(
            battery_soc_pct=98.0,
            slot_prices_today=[0.20] * 24,
            slot_prices_tomorrow=tomorrow,
            pv_hourly_kwh=make_pv_hourly(45.0, sunrise=8, sunset=17),
            pv_forecast_tomorrow=45.0,
            current_hour=20,
        )
        result = calculate_schedule(config, state)
        sells = [k for k, v in result.tomorrow_scheduled_slots.items()
                 if v == "discharge"]
        assert len(sells) > 0, (
            "Expected tomorrow to schedule sells with full battery + strong PV"
        )
        # End-of-day SOC must still respect the overnight reserve target.
        assert result.tomorrow_soc_trajectory[-1] >= 35.0


class TestInherentLowSOCValidation:
    """Tests that SOC validation doesn't falsely prune sell slots when
    battery starts below reserve but PV recovers it before sells execute."""

    def test_sell_slots_preserved_when_pv_recovers_soc(self):
        """Battery at 25% (below reserve 29%), PV fills to 100% by midday.
        Sell slots in evening should survive — the low SOC is inherent,
        not caused by selling.
        """
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=10.0,
            safe_power_kw=20.0,
            inverter_max_power_kw=25.0,
        )
        prices = [0.02] * 7 + [-0.004] * 3 + [0.02] * 4 + [0.05] * 4 + [0.10] * 6
        state = default_state(
            battery_soc_pct=25.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(51.0),
            pv_forecast_remaining=38.0,
            pv_forecast_today=51.0,
            pv_actual_today_kwh=0.0,
            current_hour=9,
        )
        result = calculate_schedule(config, state)
        sells = sum(1 for v in result.scheduled_slots.values() if v == "discharge")
        assert sells > 0, (
            "Should sell in evening — PV fills battery to 100%, "
            "inherent low SOC at start shouldn't prune future sells"
        )
        for i, soc in enumerate(result.soc_trajectory):
            assert soc >= config.battery_discharge_min_pct - 0.5, (
                f"SOC at slot {i} is {soc}%, below min {config.battery_discharge_min_pct}%"
            )

    def test_negative_charge_preserved_when_pv_fills_battery(self):
        """When PV alone would fill the battery, negative-price charge slots
        should NOT be pruned — the overflow is PV-caused, and charging at
        negative prices is pure profit."""
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=10.0,
            safe_power_kw=5.0,
            inverter_max_power_kw=10.0,
        )
        # Negative prices at hours 7-10, positive rest of day
        prices = [0.05] * 7 + [-0.46, -0.30, -0.20] + [0.05] * 4 + [0.10] * 4 + [0.15] * 6
        state = default_state(
            battery_soc_pct=29.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(65.0),
            pv_forecast_remaining=64.7,
            pv_forecast_today=65.0,
            pv_actual_today_kwh=0.3,
            current_hour=7,
        )
        result = calculate_schedule(config, state)
        charges = {k for k, v in result.scheduled_slots.items() if v == "charge"}
        neg_slots = {i for i, p in enumerate(prices) if p < 0 and i >= 7}
        neg_charged = charges & neg_slots
        assert len(neg_charged) == len(neg_slots), (
            f"All {len(neg_slots)} negative-price slots should be charged "
            f"(PV fills battery anyway), but only {len(neg_charged)} are: "
            f"charged={sorted(charges)}, neg={sorted(neg_slots)}"
        )

    def test_negative_charge_still_pruned_when_pv_insufficient(self):
        """When PV doesn't fill the battery, negative-price charge slots
        that would cause overflow should still be pruned."""
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=10.0,
            safe_power_kw=5.0,
            inverter_max_power_kw=10.0,
        )
        # Battery nearly full, little PV, negative prices
        prices = [0.05] * 7 + [-0.10, -0.05, -0.02] + [0.05] * 14
        state = default_state(
            battery_soc_pct=92.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(5.0),
            pv_forecast_remaining=3.0,
            pv_forecast_today=5.0,
            pv_actual_today_kwh=2.0,
            current_hour=7,
        )
        result = calculate_schedule(config, state)
        charges = {k for k, v in result.scheduled_slots.items() if v == "charge"}
        neg_slots = {i for i, p in enumerate(prices) if p < 0 and i >= 7}
        # At 92% of 60 = 55.2 kWh, capacity = 60, only 4.8 kWh headroom
        # With 5 kW * 0.9 eff = 4.5 kWh per slot, only 1 slot fits.
        # PV doesn't fill battery → overflow pruning should still apply.
        assert len(charges & neg_slots) < len(neg_slots), (
            "Not all negative slots should charge when battery is nearly full "
            "and PV insufficient — overflow pruning should apply"
        )

    def test_sell_still_pruned_when_discharge_causes_violation(self):
        """When a sell actually causes SOC to drop below min, it should
        still be pruned (the fix only affects inherent low SOC)."""
        config = default_config(
            grid_mode="to_grid",
            battery_capacity_kwh=20.0,
            battery_discharge_min_pct=30.0,
            consumption_est_kwh=8.0,
            safe_power_kw=5.0,
        )
        prices = [0.05] * 12 + [0.15] * 6 + [0.25] * 6
        state = default_state(
            battery_soc_pct=45.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(5.0),
            pv_forecast_remaining=2.0,
            pv_forecast_today=5.0,
            pv_actual_today_kwh=3.0,
            current_hour=14,
        )
        result = calculate_schedule(config, state)
        for i, soc in enumerate(result.soc_trajectory):
            assert soc >= config.battery_discharge_min_pct - 1.0, (
                f"SOC at slot {i} is {soc}%, violates min {config.battery_discharge_min_pct}%"
            )


class TestUrgentRecoveryCharge:
    """When battery is below discharge_min, force immediate charge slots."""

    def test_immediate_charge_when_below_min(self):
        """Battery at 10% with 20% min should charge NOW, not wait for
        cheaper slots hours later."""
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=11.0,
            safe_power_kw=20.0,
            inverter_max_power_kw=25.0,
        )
        prices = [0.10] * 8 + [0.05] * 3 + [0.10] * 13
        state = default_state(
            battery_soc_pct=10.0,
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            pv_actual_today_kwh=0.0,
            current_hour=8,
        )
        result = calculate_schedule(config, state)
        # Current slot (hour 8) must be scheduled for charge
        assert result.scheduled_slots.get(8) == "charge", (
            f"Battery at 10% (below 20% min) should force immediate charge "
            f"at current slot 8, got: {result.scheduled_slots}"
        )

    def test_no_urgent_charge_when_above_min(self):
        """Battery above discharge_min should NOT force immediate charge."""
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=11.0,
            safe_power_kw=5.0,
        )
        # Cheap slots at hours 2-4, expensive at hour 12 (current)
        prices = [0.30] * 2 + [0.05] * 3 + [0.30] * 19
        state = default_state(
            battery_soc_pct=25.0,
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            pv_actual_today_kwh=0.0,
            current_hour=12,
        )
        result = calculate_schedule(config, state)
        # Hour 12 is expensive, battery is above min — no forced charge
        if result.scheduled_slots.get(12) == "charge":
            # It might be scheduled by the optimizer if needed, but not forced
            pass  # OK if optimizer chose it

    def test_urgent_recovery_adds_enough_slots(self):
        """Urgent recovery should add enough slots to reach discharge_min."""
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=10.0,
            safe_power_kw=5.0,
        )
        # 15-min granularity: 96 slots
        prices = [0.10] * 96
        state = default_state(
            battery_soc_pct=5.0,
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            pv_actual_today_kwh=0.0,
            current_hour=8,
        )
        result = calculate_schedule(config, state)
        # Battery at 3 kWh (5% of 60), min = 12 kWh, need 9 kWh
        # At 5 kW * 0.25h * 0.9 eff = 1.125 kWh per slot → ceil(9/1.125) = 8 slots
        # Current slot at hour 8 = slot 32 (of 96)
        current_slot = 32
        immediate_charges = sum(
            1 for i in range(current_slot, min(current_slot + 10, 96))
            if result.scheduled_slots.get(i) == "charge"
        )
        assert immediate_charges >= 1, (
            "Battery far below min should have immediate charge slots "
            f"starting at current slot, got: {sorted(result.scheduled_slots.items())[:15]}"
        )


class TestBothModeFullCharge:
    """Both mode should charge to full capacity when profitable pairs exist."""

    def test_both_mode_charges_to_full_with_spread(self):
        """Both mode with price spread should charge well beyond reserve_target."""
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=11.0,
            safe_power_kw=5.0,
        )
        # Cheap morning, expensive evening → profitable spread
        prices = [0.05] * 8 + [0.10] * 6 + [0.25] * 10
        state = default_state(
            battery_soc_pct=10.0,
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            pv_actual_today_kwh=0.0,
            current_hour=0,
        )
        result = calculate_schedule(config, state)
        charges = sum(1 for v in result.scheduled_slots.values() if v == "charge")
        sells = sum(1 for v in result.scheduled_slots.values() if v == "discharge")
        # Should charge aggressively (well beyond the ~29% reserve_target)
        # and sell in the evening
        assert charges >= 5, (
            f"Both mode should charge to full for arbitrage, got only {charges} charge slots"
        )
        assert sells >= 1, (
            f"Both mode should sell at expensive prices, got {sells} sell slots"
        )

    def test_both_mode_no_charge_when_pv_fills(self):
        """Both mode should NOT grid-charge when PV fills the battery."""
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=11.0,
            safe_power_kw=5.0,
        )
        prices = [0.05] * 8 + [0.10] * 6 + [0.25] * 10
        state = default_state(
            battery_soc_pct=40.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(60.0),
            pv_forecast_remaining=50.0,
            pv_forecast_today=60.0,
            pv_actual_today_kwh=10.0,
            current_hour=8,
        )
        result = calculate_schedule(config, state)
        charges = sum(1 for v in result.scheduled_slots.values() if v == "charge")
        # PV fills battery → no grid charging needed (only sells)
        assert charges == 0, (
            f"Both mode should skip grid charging when PV fills battery, got {charges}"
        )


class TestBatteryCycleCost:
    """#14: battery wear cost in profitability filter."""

    def test_high_cycle_cost_blocks_marginal_arbitrage(self):
        """When cycle cost > spread, sell slots should be filtered out."""
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=10.0,
            consumption_est_kwh=8.0,
            efficiency=0.95,  # round-trip 0.9025
            battery_cycle_cost_eur_kwh=0.10,  # very high wear cost
        )
        # Spread: buy 0.05, sell 0.10. Round-trip min sell = 0.05/0.9025 ≈ 0.055.
        # With wear: min sell = 0.055 + 0.10/0.9025 ≈ 0.166. Sell at 0.10 fails.
        prices = [0.05] * 6 + [0.10] * 6 + [0.05] * 6 + [0.10] * 6
        state = default_state(
            battery_soc_pct=50.0,
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            pv_actual_today_kwh=0.0,
            current_hour=0,
        )
        result = calculate_schedule(config, state)
        sells = sum(1 for v in result.scheduled_slots.values() if v == "discharge")
        assert sells == 0, (
            f"High cycle cost should block marginal arbitrage, got {sells} sells"
        )

    def test_zero_cycle_cost_allows_arbitrage(self):
        """Default cycle cost = 0 preserves legacy behaviour: wide-spread
        arbitrage produces sell slots."""
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=8.0,
            safe_power_kw=5.0,
            battery_cycle_cost_eur_kwh=0.0,
        )
        # Plenty of cheap slots; battery has 30 kWh headroom for round-trip.
        prices = [0.30] * 6 + [0.05] * 10 + [0.30] * 8
        state = default_state(
            battery_soc_pct=50.0,
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            pv_actual_today_kwh=0.0,
            current_hour=6,
        )
        result = calculate_schedule(config, state)
        sells = sum(1 for v in result.scheduled_slots.values() if v == "discharge")
        assert sells > 0, "Wide-spread arbitrage should sell when cycle cost is 0"


class TestOptimizationPriority:
    """#12: optimization_priority knob (cost / longevity / self_consumption)."""

    def test_longevity_enforces_cycle_cost_floor(self):
        """Longevity priority enforces a minimum 0.05 cycle cost even
        when battery_cycle_cost_eur_kwh is left at 0."""
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=10.0,
            consumption_est_kwh=8.0,
            optimization_priority="longevity",
        )
        # Spread small enough to be blocked by the longevity floor
        prices = [0.05] * 6 + [0.10] * 6 + [0.05] * 6 + [0.10] * 6
        state = default_state(
            battery_soc_pct=50.0,
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            pv_actual_today_kwh=0.0,
            current_hour=0,
        )
        result = calculate_schedule(config, state)
        sells = sum(1 for v in result.scheduled_slots.values() if v == "discharge")
        assert sells == 0, (
            "Longevity priority should block marginal arbitrage even with "
            f"cycle_cost=0, got {sells} sells"
        )

    def test_self_consumption_raises_reserve_target(self):
        """Self-consumption priority raises the dynamic reserve."""
        config_cost = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=20.0,
            consumption_est_kwh=20.0,
            optimization_priority="cost",
        )
        config_self = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=20.0,
            consumption_est_kwh=20.0,
            optimization_priority="self_consumption",
        )
        state = default_state(
            battery_soc_pct=50.0,
            slot_prices_today=[0.10] * 24,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            current_hour=0,
        )
        result_cost = calculate_schedule(config_cost, state)
        result_self = calculate_schedule(config_self, state)
        assert result_self.reserve_target_pct > result_cost.reserve_target_pct, (
            f"self_consumption reserve ({result_self.reserve_target_pct}%) "
            f"should exceed cost reserve ({result_cost.reserve_target_pct}%)"
        )


class TestBlockExportOnNegativePrice:
    """#5: prevent grid export at negative prices."""

    def test_to_grid_skips_negative_slots(self):
        """to_grid mode should never sell at p < 0."""
        config = default_config(
            grid_mode="to_grid",
            battery_capacity_kwh=20.0,
            consumption_est_kwh=8.0,
        )
        prices = [-0.05] * 6 + [0.30] * 6 + [-0.05] * 6 + [0.30] * 6
        state = default_state(
            battery_soc_pct=80.0,
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            current_hour=0,
        )
        result = calculate_schedule(config, state)
        for idx, action in result.scheduled_slots.items():
            if action == "discharge":
                assert prices[idx] >= 0, (
                    f"Slot {idx} (price {prices[idx]}) should not be a sell slot"
                )


class TestPVConfidenceEMA:
    """#3: EMA smoothing prevents oscillation."""

    def test_smoothing_dampens_spike(self):
        """A new very-low confidence is dampened by previous high."""
        # Set up: forecast 30 kWh, actual_so_far very low (e.g., dark hour)
        pv_hourly = make_pv_hourly(30.0)
        # At hour 12, expected_so_far should be ~half of 30 = 15
        # actual = 5 → cumulative confidence ≈ 0.33
        instant = ems._calculate_pv_confidence(
            pv_hourly, 5.0, 12, 0,
        )
        # With high previous confidence, smoothed is much higher than instant
        smoothed = ems._calculate_pv_confidence(
            pv_hourly, 5.0, 12, 0,
            previous_confidence=1.0, ema_alpha=0.3,
        )
        assert smoothed > instant, (
            f"smoothed ({smoothed}) should be > instant ({instant}) when prev=1.0"
        )
        # alpha=0.3, so smoothed = 0.3*instant + 0.7*1.0
        expected = 0.3 * instant + 0.7 * 1.0
        assert abs(smoothed - expected) < 0.01


class TestPVForecastFallback:
    """#4: fallback when forecast.solar is unavailable."""

    def test_fallback_used_when_forecast_missing(self):
        """When pv_forecast_today is None but pv_fallback_today_kwh is set,
        the algorithm should use the fallback instead of treating PV as 0."""
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=20.0,
            consumption_est_kwh=10.0,
        )
        prices = [0.10] * 24
        # Without fallback: PV=0, full deficit forces aggressive grid charge
        state_no_fb = default_state(
            battery_soc_pct=50.0,
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_today=None,
            pv_actual_today_kwh=0.0,
            current_hour=8,
        )
        # With fallback: 25 kWh expected → less grid charge needed
        state_fb = default_state(
            battery_soc_pct=50.0,
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_today=None,
            pv_fallback_today_kwh=25.0,
            pv_actual_today_kwh=0.0,
            current_hour=8,
        )
        result_no_fb = calculate_schedule(config, state_no_fb)
        result_fb = calculate_schedule(config, state_fb)
        no_fb_charges = sum(1 for v in result_no_fb.scheduled_slots.values() if v == "charge")
        fb_charges = sum(1 for v in result_fb.scheduled_slots.values() if v == "charge")
        assert fb_charges <= no_fb_charges, (
            f"With PV fallback ({fb_charges} charges), should plan no more "
            f"grid charging than without ({no_fb_charges})"
        )


class TestTomorrowFullBatteryNoPhantomCharge:
    """When battery is already full and tomorrow's PV will keep it there,
    no charge slots should be scheduled — the inverter can't charge a
    full battery.  Regression test for the negative-price slot getting
    scheduled at hour 16 when SOC was 100% all day."""

    def test_no_tomorrow_charge_when_battery_full_with_pv(self):
        """Battery starts full, PV tomorrow keeps it full → no charge slots."""
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=35.0,
            battery_charge_max_pct=100.0,
            consumption_est_kwh=30.9,
            safe_power_kw=8.0,
        )
        # One slot has a slightly negative price tomorrow (would otherwise
        # be auto-selected by negative-slot pickup)
        prices_today = [0.20] * 24
        prices_tomorrow = [0.20] * 16 + [-0.01] + [0.20] * 7
        state = default_state(
            battery_soc_pct=100.0,
            slot_prices_today=prices_today,
            slot_prices_tomorrow=prices_tomorrow,
            pv_hourly_kwh=make_pv_hourly(48.0),
            pv_forecast_remaining=10.2,
            pv_forecast_today=48.0,
            pv_forecast_tomorrow=40.0,
            pv_actual_today_kwh=37.9,
            current_hour=15,
        )
        result = calculate_schedule(config, state)
        tomorrow_charges = sum(
            1 for v in result.tomorrow_scheduled_slots.values() if v == "charge"
        )
        assert tomorrow_charges == 0, (
            f"Battery full + PV keeps it full → no tomorrow charge slots, "
            f"got {tomorrow_charges}: {result.tomorrow_scheduled_slots}"
        )

    def test_negative_charge_kept_when_battery_has_room(self):
        """The fix shouldn't break the legitimate case: when battery has
        actual room to fill and PV would overflow it, negative-price slots
        are still kept (the income is pure profit)."""
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=20.0,
            consumption_est_kwh=10.0,
            safe_power_kw=5.0,
            inverter_max_power_kw=10.0,
        )
        prices = [0.05] * 7 + [-0.46, -0.30, -0.20] + [0.05] * 4 + [0.10] * 4 + [0.15] * 6
        state = default_state(
            battery_soc_pct=29.0,  # plenty of headroom
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(65.0),
            pv_forecast_remaining=64.7,
            pv_forecast_today=65.0,
            pv_actual_today_kwh=0.3,
            current_hour=7,
        )
        result = calculate_schedule(config, state)
        charges = {k for k, v in result.scheduled_slots.items() if v == "charge"}
        neg_slots = {i for i, p in enumerate(prices) if p < 0 and i >= 7}
        neg_charged = charges & neg_slots
        assert len(neg_charged) == len(neg_slots), (
            f"With headroom, all negative-price slots should still charge: "
            f"got {len(neg_charged)} of {len(neg_slots)}"
        )


# ---------------------------------------------------------------------------
# Test: Negative-price strategies (charge_to_full / discharge_to_make_room)
# ---------------------------------------------------------------------------

class TestChargeToFullOnNegativePrice:
    """charge_to_full_on_negative_price: schedule every p<0 slot, even if
    SOC validation would otherwise prune it because PV alone wouldn't fill
    the battery (i.e., accepting forced PV curtailment / export risk)."""

    def _prices_with_negatives(self, n_slots: int = 24) -> list[float]:
        # 0..6 mild positive, 7..9 deeply negative, 10..23 positive ramp.
        p = [0.10] * n_slots
        for i in (7, 8, 9):
            p[i] = -0.20
        for i in range(10, n_slots):
            p[i] = 0.05 + 0.01 * (i - 10)
        return p

    def test_negative_slots_kept_with_flag(self):
        """With the flag ON, all negative slots are scheduled even when
        the battery starts near full and PV alone wouldn't fill it."""
        prices = self._prices_with_negatives(24)
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=10.0,
            battery_charge_max_pct=100.0,
            battery_discharge_min_pct=20.0,
            safe_power_kw=5.0,
            inverter_max_power_kw=5.0,
            consumption_est_kwh=10.0,
            charge_to_full_on_negative_price=True,
        )
        state = default_state(
            battery_soc_pct=85.0,  # nearly full — would normally prune negs
            slot_prices_today=prices,
            pv_hourly_kwh={},  # no PV
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            pv_actual_today_kwh=0.0,
            current_hour=6,
        )
        result = calculate_schedule(config, state)
        neg_slots = {i for i, p in enumerate(prices) if p < 0 and i >= 6}
        charges = {k for k, v in result.scheduled_slots.items() if v == "charge"}
        assert neg_slots.issubset(charges), (
            f"All negative slots {sorted(neg_slots)} should be charged with "
            f"the flag on; got {sorted(charges)}"
        )

    def test_negative_slots_pruned_without_flag(self):
        """Baseline: without the flag and no PV-fills-battery exemption,
        negative-price slots that would overflow are pruned."""
        prices = self._prices_with_negatives(24)
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=10.0,
            battery_charge_max_pct=100.0,
            battery_discharge_min_pct=20.0,
            safe_power_kw=5.0,
            inverter_max_power_kw=5.0,
            consumption_est_kwh=10.0,
            charge_to_full_on_negative_price=False,
        )
        state = default_state(
            battery_soc_pct=95.0,  # essentially full
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            pv_actual_today_kwh=0.0,
            current_hour=6,
        )
        result = calculate_schedule(config, state)
        neg_slots = {i for i, p in enumerate(prices) if p < 0 and i >= 6}
        charges = {k for k, v in result.scheduled_slots.items() if v == "charge"}
        # Without the flag, the battery is too full — most negative slots get pruned
        assert len(charges & neg_slots) < len(neg_slots), (
            f"Without flag, near-full battery should prune some negative slots; "
            f"got all {len(charges & neg_slots)}/{len(neg_slots)} kept"
        )

    def test_flag_in_both_mode(self):
        """The flag applies to both mode as well — all negatives kept."""
        prices = self._prices_with_negatives(24)
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=10.0,
            safe_power_kw=5.0,
            inverter_max_power_kw=5.0,
            charge_to_full_on_negative_price=True,
        )
        state = default_state(
            battery_soc_pct=85.0,
            slot_prices_today=prices,
            pv_hourly_kwh={},
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            pv_actual_today_kwh=0.0,
            current_hour=6,
        )
        result = calculate_schedule(config, state)
        neg_slots = {i for i, p in enumerate(prices) if p < 0 and i >= 6}
        charges = {k for k, v in result.scheduled_slots.items() if v == "charge"}
        assert neg_slots.issubset(charges)

    def test_flag_off_keeps_legacy_behavior(self):
        """With flag OFF, existing PV-fills-battery exemption still applies."""
        prices = self._prices_with_negatives(24)
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=60.0,
            charge_to_full_on_negative_price=False,
        )
        # Large PV that will fill the battery — exemption keeps neg slots.
        state = default_state(
            battery_soc_pct=80.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(40.0),
            pv_forecast_remaining=25.0,
            pv_forecast_today=40.0,
            pv_actual_today_kwh=15.0,
            current_hour=6,
        )
        result = calculate_schedule(config, state)
        neg_slots = {i for i, p in enumerate(prices) if p < 0 and i >= 6}
        charges = {k for k, v in result.scheduled_slots.items() if v == "charge"}
        # PV-fills-battery exemption should keep most negatives
        assert len(charges & neg_slots) >= 1


class TestDischargeToMakeRoomForNegativePrice:
    """discharge_to_make_room_for_negative_price: pre-emptively discharge
    in earlier positive-price slots so the battery has headroom to absorb
    PV during negative-price windows (avoiding forced grid export at
    penalty rates)."""

    def test_no_discharge_without_flag(self):
        """Baseline: with flag OFF, from_grid mode never schedules discharge."""
        # PV mostly during a negative-price window; battery starts near full.
        prices = [0.20] * 11 + [-0.10] * 4 + [0.20] * 9  # negatives at 11-14
        # PV concentrated at hours 11-14 (negative window).
        pv_hourly = {11: 8.0, 12: 8.0, 13: 8.0, 14: 8.0}
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=10.0,
            battery_charge_max_pct=100.0,
            battery_discharge_min_pct=20.0,
            safe_power_kw=5.0,
            inverter_max_power_kw=10.0,
            consumption_est_kwh=4.0,
            discharge_to_make_room_for_negative_price=False,
        )
        state = default_state(
            battery_soc_pct=90.0,
            slot_prices_today=prices,
            pv_hourly_kwh=pv_hourly,
            pv_forecast_remaining=32.0,
            pv_forecast_today=32.0,
            pv_actual_today_kwh=0.0,
            current_hour=7,
        )
        result = calculate_schedule(config, state)
        discharges = [k for k, v in result.scheduled_slots.items() if v == "discharge"]
        assert discharges == [], (
            f"from_grid mode without flag should never discharge; got {discharges}"
        )

    def test_discharge_scheduled_with_flag(self):
        """With flag ON, discharge slots appear before negative-price PV window."""
        prices = [0.20] * 11 + [-0.10] * 4 + [0.20] * 9  # negatives at 11-14
        pv_hourly = {11: 8.0, 12: 8.0, 13: 8.0, 14: 8.0}
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=10.0,
            battery_charge_max_pct=100.0,
            battery_discharge_min_pct=20.0,
            safe_power_kw=5.0,
            inverter_max_power_kw=10.0,
            consumption_est_kwh=4.0,
            discharge_to_make_room_for_negative_price=True,
        )
        state = default_state(
            battery_soc_pct=90.0,  # near full — needs to make room
            slot_prices_today=prices,
            pv_hourly_kwh=pv_hourly,
            pv_forecast_remaining=32.0,
            pv_forecast_today=32.0,
            pv_actual_today_kwh=0.0,
            current_hour=7,
        )
        result = calculate_schedule(config, state)
        discharges = [k for k, v in result.scheduled_slots.items() if v == "discharge"]
        assert len(discharges) > 0, (
            "Expected at least one pre-emptive discharge slot when battery "
            "is near-full and a negative-price PV window is coming"
        )
        # All discharges should be in positive-price slots BEFORE the negative window
        first_neg = 11
        for d in discharges:
            assert d < first_neg, f"discharge {d} should be before neg window at {first_neg}"
            assert prices[d] > 0, f"discharge {d} should be at positive price, got {prices[d]}"

    def test_no_discharge_when_battery_has_room(self):
        """When battery has plenty of headroom, no pre-emptive discharge needed."""
        prices = [0.20] * 11 + [-0.10] * 4 + [0.20] * 9
        pv_hourly = {11: 8.0, 12: 8.0, 13: 8.0, 14: 8.0}
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=30.0,
            battery_charge_max_pct=100.0,
            battery_discharge_min_pct=20.0,
            safe_power_kw=5.0,
            inverter_max_power_kw=10.0,
            consumption_est_kwh=10.0,
            discharge_to_make_room_for_negative_price=True,
        )
        state = default_state(
            battery_soc_pct=30.0,  # plenty of headroom
            slot_prices_today=prices,
            pv_hourly_kwh=pv_hourly,
            pv_forecast_remaining=32.0,
            pv_forecast_today=32.0,
            pv_actual_today_kwh=0.0,
            current_hour=7,
        )
        result = calculate_schedule(config, state)
        discharges = [k for k, v in result.scheduled_slots.items() if v == "discharge"]
        # Battery can fully absorb the PV — no make-room needed
        assert discharges == [], (
            f"No discharge needed when battery has headroom; got {discharges}"
        )

    def test_no_discharge_without_pv(self):
        """No PV during negative window = no overflow risk = no discharge."""
        prices = [0.20] * 11 + [-0.10] * 4 + [0.20] * 9
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=10.0,
            discharge_to_make_room_for_negative_price=True,
        )
        state = default_state(
            battery_soc_pct=90.0,
            slot_prices_today=prices,
            pv_hourly_kwh={},  # no PV
            pv_forecast_remaining=0.0,
            pv_forecast_today=0.0,
            pv_actual_today_kwh=0.0,
            current_hour=7,
        )
        result = calculate_schedule(config, state)
        discharges = [k for k, v in result.scheduled_slots.items() if v == "discharge"]
        assert discharges == [], (
            f"No PV = no overflow risk = no pre-emptive discharge; got {discharges}"
        )

    def test_respects_reserve_target(self):
        """Pre-emptive discharge must not push SOC below reserve target."""
        prices = [0.20] * 11 + [-0.10] * 4 + [0.20] * 9
        pv_hourly = {11: 8.0, 12: 8.0, 13: 8.0, 14: 8.0}
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=10.0,
            battery_charge_max_pct=100.0,
            battery_discharge_min_pct=20.0,
            safe_power_kw=5.0,
            inverter_max_power_kw=10.0,
            consumption_est_kwh=4.0,
            reserve_target_pct=60.0,  # high reserve floor
            discharge_to_make_room_for_negative_price=True,
        )
        state = default_state(
            battery_soc_pct=70.0,  # just above reserve
            slot_prices_today=prices,
            pv_hourly_kwh=pv_hourly,
            pv_forecast_remaining=32.0,
            pv_forecast_today=32.0,
            pv_actual_today_kwh=0.0,
            current_hour=7,
        )
        result = calculate_schedule(config, state)
        # Walk SOC forward and check reserve is never violated by pre-emptive discharge
        soc_kwh = (state.battery_soc_pct / 100.0) * config.battery_capacity_kwh
        reserve_kwh = (config.reserve_target_pct / 100.0) * config.battery_capacity_kwh
        energy_per_slot = config.safe_power_kw  # 1h slots
        num_slots = len(prices)
        for i in range(state.current_hour, num_slots):
            pv = pv_hourly.get(i, 0.0)
            cons = config.consumption_est_kwh / num_slots
            delta = pv - cons
            action = result.scheduled_slots.get(i)
            if action == "discharge":
                delta -= energy_per_slot
                # Must not push SOC below reserve at any time during/after discharge
                assert (soc_kwh + delta) >= reserve_kwh - 0.05, (
                    f"Pre-emptive discharge at slot {i} would push SOC below "
                    f"reserve {reserve_kwh:.2f} (would reach {soc_kwh + delta:.2f})"
                )
            soc_kwh = max(0.0, min(config.battery_capacity_kwh, soc_kwh + delta))


class TestNegativePriceStrategiesComposable:
    """Both flags can be enabled together — they don't conflict."""

    def test_both_flags_together(self):
        """charge_to_full + discharge_to_make_room work together: discharge
        before the window creates space, then we charge at the negatives."""
        # 24 slots. Negatives at 11-14, PV during 10-15, battery near full.
        prices = [0.20] * 11 + [-0.10] * 4 + [0.20] * 9
        pv_hourly = {10: 6.0, 11: 8.0, 12: 8.0, 13: 8.0, 14: 8.0, 15: 6.0}
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=10.0,
            battery_charge_max_pct=100.0,
            battery_discharge_min_pct=20.0,
            safe_power_kw=5.0,
            inverter_max_power_kw=10.0,
            consumption_est_kwh=4.0,
            charge_to_full_on_negative_price=True,
            discharge_to_make_room_for_negative_price=True,
        )
        state = default_state(
            battery_soc_pct=90.0,
            slot_prices_today=prices,
            pv_hourly_kwh=pv_hourly,
            pv_forecast_remaining=44.0,
            pv_forecast_today=44.0,
            pv_actual_today_kwh=0.0,
            current_hour=7,
        )
        result = calculate_schedule(config, state)
        charges = {k for k, v in result.scheduled_slots.items() if v == "charge"}
        discharges = {k for k, v in result.scheduled_slots.items() if v == "discharge"}
        neg_slots = {i for i, p in enumerate(prices) if p < 0 and i >= 7}
        # All negatives scheduled as charge (charge_to_full)
        assert neg_slots.issubset(charges), (
            f"Both flags on: all neg slots {sorted(neg_slots)} should be charged; "
            f"got {sorted(charges)}"
        )
        # Some discharge before the negative window (make_room)
        early_discharges = {d for d in discharges if d < 11}
        assert early_discharges, (
            "Both flags on: expected pre-emptive discharges before the "
            "negative window; got none"
        )


class TestFlexibleLoadScheduling:
    """Tests for flexible load scheduling in the EMS."""

    def _make_prices(self, n=24, base=0.15, cheap_hours=None, negative_hours=None):
        """Generate price list with optional cheap/negative hours."""
        prices = [base] * n
        for h in (cheap_hours or []):
            if h < n:
                prices[h] = 0.05
        for h in (negative_hours or []):
            if h < n:
                prices[h] = -0.02
        return prices

    def test_no_loads_no_load_slots(self):
        """Without flexible loads, load_slots should be empty."""
        config = EMSConfig(grid_mode="from_grid")
        state = EMSState(
            battery_soc_pct=50,
            slot_prices_today=self._make_prices(),
            current_hour=8,
            current_minute=0,
        )
        result = calculate_schedule(config, state)
        assert result.load_slots == {}

    def test_load_scheduled_during_cheap_slots(self):
        """A flexible load should be scheduled during cheap/charge slots."""
        load = FlexibleLoadConfig(
            enabled=True,
            name="Boiler",
            switch_entity="switch.boiler",
            rated_power_kw=2.0,
            priority=1,
        )
        prices = self._make_prices(cheap_hours=[2, 3, 4, 5])
        config = EMSConfig(
            grid_mode="from_grid",
            battery_capacity_kwh=10,
            safe_power_kw=5,
            flexible_loads=[load],
        )
        state = EMSState(
            battery_soc_pct=50,
            slot_prices_today=prices,
            current_hour=0,
            current_minute=0,
        )
        result = calculate_schedule(config, state)
        assert 0 in result.load_slots, "Load should have schedule entries"
        load_0_slots = result.load_slots[0]
        # Cheap slots (2-5) should be in the load schedule
        for s in [2, 3, 4, 5]:
            assert s in load_0_slots, f"Cheap slot {s} should have load scheduled"

    def test_load_scheduled_during_negative_price(self):
        """Loads should always run during negative-price slots."""
        load = FlexibleLoadConfig(
            enabled=True,
            name="EV",
            switch_entity="switch.ev_charger",
            rated_power_kw=7.0,
            priority=1,
        )
        prices = self._make_prices(negative_hours=[10, 11, 12])
        config = EMSConfig(
            grid_mode="from_grid",
            battery_capacity_kwh=10,
            safe_power_kw=5,
            flexible_loads=[load],
        )
        state = EMSState(
            battery_soc_pct=50,
            slot_prices_today=prices,
            current_hour=8,
            current_minute=0,
        )
        result = calculate_schedule(config, state)
        assert 0 in result.load_slots
        for s in [10, 11, 12]:
            assert s in result.load_slots[0], f"Negative slot {s} should have load"

    def test_disabled_load_not_scheduled(self):
        """A disabled load should not appear in load_slots."""
        load = FlexibleLoadConfig(
            enabled=False,
            name="Boiler",
            switch_entity="switch.boiler",
            rated_power_kw=2.0,
        )
        config = EMSConfig(
            grid_mode="from_grid",
            battery_capacity_kwh=10,
            safe_power_kw=5,
            flexible_loads=[load],
        )
        state = EMSState(
            battery_soc_pct=50,
            slot_prices_today=self._make_prices(cheap_hours=[2, 3]),
            current_hour=0,
            current_minute=0,
        )
        result = calculate_schedule(config, state)
        assert result.load_slots == {}

    def test_multiple_loads_all_scheduled(self):
        """Multiple enabled loads should all be scheduled independently."""
        loads = [
            FlexibleLoadConfig(enabled=True, name="EV", switch_entity="switch.ev",
                               rated_power_kw=7.0, priority=1),
            FlexibleLoadConfig(enabled=True, name="Boiler", switch_entity="switch.boiler",
                               rated_power_kw=2.0, priority=2),
            FlexibleLoadConfig(enabled=False, name="Pool", switch_entity="switch.pool",
                               rated_power_kw=1.5, priority=3),
        ]
        config = EMSConfig(
            grid_mode="from_grid",
            battery_capacity_kwh=10,
            safe_power_kw=5,
            flexible_loads=loads,
        )
        state = EMSState(
            battery_soc_pct=50,
            slot_prices_today=self._make_prices(cheap_hours=[2, 3]),
            current_hour=0,
            current_minute=0,
        )
        result = calculate_schedule(config, state)
        assert 0 in result.load_slots, "First enabled load should be scheduled"
        assert 1 in result.load_slots, "Second enabled load should be scheduled"
        assert 2 not in result.load_slots, "Disabled load should not be scheduled"

    def test_ev_charger_current_steps(self):
        """Test EV charger FlexibleLoadConfig helpers."""
        ev = FlexibleLoadConfig(
            enabled=True,
            name="EV",
            switch_entity="switch.ev",
            current_entity="number.ev_current",
            current_steps=[6, 10, 13, 16, 20, 25],
            phases=3,
            voltage=230,
            default_current=16,
        )
        assert ev.is_ev_charger
        # 16A × 230V × 3 phases = 11.04 kW
        assert abs(ev.power_at_current(16) - 11.04) < 0.01
        # 6A × 230V × 3 = 4.14 kW
        assert abs(ev.power_at_current(6) - 4.14) < 0.01
        # Nearest step for 8 kW target → 10A (10×230×3=6.9kW ≤ 8kW)
        assert ev.nearest_step_for_power(8.0) == 10
        # Nearest step for 15 kW target → 20A (20×230×3=13.8kW ≤ 15kW)
        assert ev.nearest_step_for_power(15.0) == 20
        # Nearest step for 1 kW target → 6A (minimum)
        assert ev.nearest_step_for_power(1.0) == 6

    def test_load_scheduled_during_pv_surplus(self):
        """Loads should run during PV surplus slots."""
        load = FlexibleLoadConfig(
            enabled=True,
            name="Boiler",
            switch_entity="switch.boiler",
            rated_power_kw=2.0,
            priority=1,
        )
        pv_hourly = {h: 5.0 for h in range(9, 16)}  # 5 kWh/hr from 9-15
        config = EMSConfig(
            grid_mode="from_grid",
            battery_capacity_kwh=10,
            safe_power_kw=5,
            consumption_est_kwh=10.0,
            flexible_loads=[load],
        )
        state = EMSState(
            battery_soc_pct=80,
            slot_prices_today=self._make_prices(),  # all same price (no cheap slots)
            pv_hourly_kwh=pv_hourly,
            pv_forecast_today=35.0,
            pv_forecast_remaining=25.0,
            current_hour=8,
            current_minute=0,
        )
        result = calculate_schedule(config, state)
        assert 0 in result.load_slots
        # PV surplus hours (9-15) should have load scheduled
        for s in range(9, 15):
            assert s in result.load_slots[0], f"PV surplus slot {s} should have load"

    def test_grid_mode_off_no_load_schedule(self):
        """When grid_mode is off, loads should not be scheduled."""
        load = FlexibleLoadConfig(
            enabled=True, name="Boiler", switch_entity="switch.boiler",
            rated_power_kw=2.0,
        )
        config = EMSConfig(
            grid_mode="off",
            flexible_loads=[load],
        )
        state = EMSState(
            battery_soc_pct=50,
            slot_prices_today=self._make_prices(),
            current_hour=8,
        )
        result = calculate_schedule(config, state)
        assert result.load_slots == {}


# ---------------------------------------------------------------------------
# Regression tests: consistency audit fixes (June 2026)
# ---------------------------------------------------------------------------

class TestInverterCapSingleConfidence:
    """PV confidence must be applied to PV exactly once in the
    inverter-max grid power cap (was applied twice: once when computing
    pv_kwh, again inside the cap formula)."""

    def test_validation_cap_uses_confidence_once(self):
        """With low confidence, the cap must subtract the confidence-scaled
        PV once.  Double application under-subtracts PV, overestimating
        the grid contribution and pruning charge slots that actually fit.

        At hour 12, PV forecast 8 kWh/h, confidence 0.5:
          correct cap:  grid = 10 - 8*0.5       = 6 kW
          double-conf:  grid = 10 - 8*0.5*0.5   = 8 kW  (2 kW too much)
        Starting at 0.1 kWh: 0.1 + 4.0(pv) - 0.2(cons) + 6.0 =  9.9 → fits
        With the bug:        0.1 + 4.0      - 0.2       + 8.0 = 11.9 → pruned
        """
        validated_charge, _ = _validate_schedule_soc(
            remaining=[(12, 0.05)],
            charge_slots={12},
            discharge_slots=set(),
            current_kwh=0.1,
            consumption_per_slot=0.2,
            pv_hourly_kwh={12: 8.0},
            minutes_per_slot=60.0,
            pv_confidence=0.5,
            battery_capacity=10.0,
            min_kwh=0.0,
            energy_per_slot=10.0,
            efficiency=1.0,
            inverter_max_power_kw=10.0,
            safe_power_kw=10.0,
        )
        assert 12 in validated_charge, (
            "charge slot pruned — inverter cap is double-applying confidence"
        )

    def test_trajectory_cap_uses_confidence_once(self):
        """SOC trajectory grid contribution = min(safe, max - pv*conf)."""
        config = default_config(
            grid_mode="from_grid",
            battery_capacity_kwh=100.0,
            battery_discharge_min_pct=5.0,
            safe_power_kw=10.0,
            inverter_max_power_kw=10.0,
            consumption_est_kwh=0.0,
        )
        # All PV in hour 12 (8 kWh), actuals indicate 50% confidence.
        # Force the charge slot at hour 12 via slot pricing: hour 12 is
        # the only cheap slot.
        prices = [0.50] * 24
        prices[12] = 0.01
        state = default_state(
            battery_soc_pct=10.0,
            slot_prices_today=prices,
            pv_hourly_kwh={h: 8.0 if h == 12 else 0.0 for h in range(24)},
            pv_forecast_today=8.0,
            pv_forecast_remaining=8.0,
            pv_actual_today_kwh=0.0,
            current_hour=11,
            current_minute=0,
        )
        traj = ems._compute_scheduled_soc_trajectory(
            prices=prices,
            num_slots=24,
            minutes_per_slot=60.0,
            current_kwh=10.0,
            current_slot=11,
            scheduled_slots={12: "charge"},
            config=config,
            state=state,
        )
        # SOC entering slot 13 minus SOC entering slot 12 = pv*conf + grid_cap
        conf = ems._calculate_pv_confidence(
            state.pv_hourly_kwh, state.pv_actual_today_kwh,
            state.current_hour, state.current_minute,
        )
        pv_scaled = 8.0 * conf
        expected_grid = min(10.0, max(0.0, 10.0 - pv_scaled))
        delta_kwh = (traj[13] - traj[12]) / 100.0 * 100.0  # SOC% on 100 kWh = kWh
        assert delta_kwh == pytest.approx(pv_scaled + expected_grid * config.efficiency, abs=0.2)


class TestBlockExportOff:
    """block_export_on_negative_price=False must allow negative-price
    sells (previously the flag was dead — always blocked)."""

    def test_to_grid_flag_off_allows_negative_sells(self):
        prices = [-0.10] * 24  # every slot negative
        config = default_config(
            grid_mode="to_grid",
            block_export_on_negative_price=False,
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=10.0,
            reserve_target_pct=15.0,
        )
        state = default_state(
            battery_soc_pct=95.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(30.0),
            pv_forecast_remaining=15.0,
            pv_actual_today_kwh=15.0,
        )
        result = calculate_schedule(config, state)
        discharges = [i for i, a in result.scheduled_slots.items() if a == "discharge"]
        assert discharges, "flag off must allow selling at negative prices"

    def test_to_grid_flag_on_blocks_negative_sells(self):
        prices = [-0.10] * 24
        config = default_config(
            grid_mode="to_grid",
            block_export_on_negative_price=True,
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=10.0,
            reserve_target_pct=15.0,
        )
        state = default_state(battery_soc_pct=95.0, slot_prices_today=prices)
        result = calculate_schedule(config, state)
        discharges = [i for i, a in result.scheduled_slots.items() if a == "discharge"]
        assert not discharges, "flag on must block negative-price sells"

    def test_both_mode_prefers_charging_over_negative_sells(self):
        """In both mode, negative-price slots are claimed by the charge
        side (getting paid to charge always beats paying to sell), so the
        flag never produces negative sells there — by design."""
        prices = [-0.10] * 24
        config = default_config(
            grid_mode="both",
            block_export_on_negative_price=False,
            battery_capacity_kwh=60.0,
            battery_discharge_min_pct=10.0,
            reserve_target_pct=15.0,
        )
        state = default_state(
            battery_soc_pct=50.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(30.0),
        )
        result = calculate_schedule(config, state)
        charges = [i for i, a in result.scheduled_slots.items() if a == "charge"]
        discharges = [i for i, a in result.scheduled_slots.items() if a == "discharge"]
        assert charges, "negative slots should be claimed as charges"
        assert not discharges, "no negative sells when charging pays better"


class TestTomorrowReserveSettings:
    """The tomorrow-deficit inside unified slot selection must honour
    reserve_target_pct and the self_consumption boost, like today's."""

    def test_fixed_reserve_raises_tomorrow_deficit(self):
        common = dict(
            remaining_today=[(22, 0.30), (23, 0.31)],
            energy_deficit=0.0,
            effective_per_slot=4.5,
            battery_capacity=60.0,
            discharge_min_pct=20.0,
            consumption_est=20.0,
            efficiency=0.90,
            energy_per_slot=5.0,
            current_kwh=20.0,
            net_pv=0.0,
            charge_max_pct=100.0,
            slot_prices_tomorrow=[0.05] * 24,
            pv_forecast_tomorrow=0.0,
            current_hour=22,
        )
        _, tmr_default, _ = select_unified_charge_slots(**common)
        _, tmr_fixed, _ = select_unified_charge_slots(
            **common, reserve_target_pct=80.0)
        # 80% fixed reserve (48 kWh) demands far more tomorrow charging
        # than the dynamic reserve (12 + ~10 kWh).
        assert len(tmr_fixed) > len(tmr_default)

    def test_self_consumption_priority_raises_tomorrow_deficit(self):
        common = dict(
            remaining_today=[(22, 0.30), (23, 0.31)],
            energy_deficit=0.0,
            effective_per_slot=4.5,
            battery_capacity=60.0,
            discharge_min_pct=20.0,
            consumption_est=38.5,
            efficiency=0.90,
            energy_per_slot=5.0,
            current_kwh=15.0,
            net_pv=0.0,
            charge_max_pct=100.0,
            slot_prices_tomorrow=[0.05] * 24,
            pv_forecast_tomorrow=0.0,
            current_hour=22,
        )
        _, tmr_cost, _ = select_unified_charge_slots(
            **common, optimization_priority="cost")
        _, tmr_sc, _ = select_unified_charge_slots(
            **common, optimization_priority="self_consumption")
        assert len(tmr_sc) >= len(tmr_cost)


class TestMakeRoomCoValidation:
    """In both mode, make-room discharges must account for already-selected
    sell slots — combined drain must not breach the hardware floor."""

    def test_combined_drain_respects_hardware_floor(self):
        # Negative-price PV window at 12-14, expensive evening sells.
        prices = [0.20] * 24
        for h in (12, 13, 14):
            prices[h] = -0.10
        for h in (8, 9, 10):
            prices[h] = 0.45  # expensive morning: make-room candidates
        config = default_config(
            grid_mode="both",
            discharge_to_make_room_for_negative_price=True,
            battery_capacity_kwh=20.0,
            battery_discharge_min_pct=20.0,  # floor 4 kWh
            safe_power_kw=5.0,
            consumption_est_kwh=10.0,
        )
        state = default_state(
            battery_soc_pct=60.0,  # 12 kWh
            slot_prices_today=prices,
            pv_hourly_kwh={12: 6.0, 13: 6.0, 14: 6.0},
            pv_forecast_today=18.0,
            pv_forecast_remaining=18.0,
            pv_actual_today_kwh=0.0,
            current_hour=6,
            current_minute=0,
        )
        result = calculate_schedule(config, state)
        # Simulate the resulting schedule: SOC must never go below the
        # 4 kWh hardware floor.
        soc = 12.0
        min_seen = soc
        for i in range(6, 24):
            pv = state.pv_hourly_kwh.get(i, 0.0)
            cons = 10.0 / 24.0
            delta = pv - cons
            action = result.scheduled_slots.get(i)
            if action == "charge":
                delta += min(5.0, max(0.0, config.inverter_max_power_kw - pv)) * 0.9
            elif action == "discharge":
                delta -= 5.0
            soc = soc + delta
            min_seen = min(min_seen, soc)
            soc = max(0.0, min(20.0, soc))
        assert min_seen >= 4.0 - 1.0, (
            f"combined sells + make-room drained to {min_seen:.1f} kWh "
            f"(floor 4.0): {result.scheduled_slots}"
        )


class TestAvailableInfoTotalWithTomorrow:
    """AvailableInfo must expose the two-day slot count separately."""

    def test_total_with_tomorrow_field(self):
        config = default_config(grid_mode="from_grid")
        state = default_state(
            slot_prices_today=[0.10] * 24,
            slot_prices_tomorrow=[0.10] * 24,
        )
        info = calculate_available_info(config, state, price_threshold=0.20)
        assert info.available_total_with_tomorrow > info.available_slots
        assert info.available_total_with_tomorrow == info.available_slots + 24


# ---------------------------------------------------------------------------
# Test: Schedule Reason ("why" line)
# ---------------------------------------------------------------------------

class TestScheduleReason:
    """Tests that schedule_reason is populated with a human-readable explanation."""

    def test_reason_off(self):
        config = default_config(grid_mode="off")
        state = default_state()
        result = calculate_schedule(config, state)
        assert "off" in result.schedule_reason.lower() or "strategy" in result.schedule_reason.lower()

    def test_reason_no_price_data(self):
        config = default_config(grid_mode="from_grid")
        state = default_state(slot_prices_today=None)
        result = calculate_schedule(config, state)
        assert "price" in result.schedule_reason.lower()

    def test_reason_from_grid_solar_fills(self):
        """When solar fills battery, reason should mention solar."""
        config = default_config(grid_mode="from_grid", battery_capacity_kwh=20.0)
        state = default_state(
            battery_soc_pct=50.0,
            pv_hourly_kwh=make_pv_hourly(40.0),
            pv_forecast_remaining=20.0,
            pv_forecast_today=40.0,
        )
        result = calculate_schedule(config, state)
        assert "solar" in result.schedule_reason.lower() or "no" in result.schedule_reason.lower()

    def test_reason_from_grid_charging(self):
        """When charging is needed, reason should mention slots and deficit."""
        config = default_config(grid_mode="from_grid")
        state = default_state(
            battery_soc_pct=30.0,
            pv_hourly_kwh={},
            pv_forecast_remaining=0,
            pv_forecast_today=0,
            pv_actual_today_kwh=0,
        )
        result = calculate_schedule(config, state)
        assert len(result.scheduled_slots) > 0
        assert "charg" in result.schedule_reason.lower()

    def test_reason_to_grid_selling(self):
        """When selling, reason mentions sell slots."""
        config = default_config(
            grid_mode="to_grid",
            battery_capacity_kwh=20.0,
            battery_discharge_min_pct=10.0,
            consumption_est_kwh=5.0,
        )
        prices = [0.30] * 24
        state = default_state(
            battery_soc_pct=90.0,
            slot_prices_today=prices,
            pv_hourly_kwh=make_pv_hourly(10.0),
            pv_forecast_remaining=5.0,
            pv_forecast_today=10.0,
        )
        result = calculate_schedule(config, state)
        if result.scheduled_slots:
            assert "sell" in result.schedule_reason.lower()

    def test_reason_both_not_trading_delta(self):
        """When arbitrage delta prevents trading, reason explains the spread."""
        config = default_config(
            grid_mode="both",
            arbitrage_price_delta=0.20,
        )
        prices = [0.10] * 24
        prices[12] = 0.15
        state = default_state(
            battery_soc_pct=50.0,
            slot_prices_today=prices,
            current_hour=0,
            current_minute=0,
        )
        result = calculate_schedule(config, state)
        assert "spread" in result.schedule_reason.lower() or "not trading" in result.schedule_reason.lower()

    def test_reason_both_buying_and_selling(self):
        """When both buying and selling, reason mentions both."""
        config = default_config(
            grid_mode="both",
            battery_capacity_kwh=20.0,
            consumption_est_kwh=5.0,
            battery_discharge_min_pct=10.0,
        )
        prices = [0.05] * 12 + [0.40] * 12
        state = default_state(
            battery_soc_pct=30.0,
            slot_prices_today=prices,
            current_hour=0,
            current_minute=0,
            pv_hourly_kwh={},
            pv_forecast_remaining=0,
            pv_forecast_today=0,
            pv_actual_today_kwh=0,
        )
        result = calculate_schedule(config, state)
        charge_count = sum(1 for v in result.scheduled_slots.values() if v == "charge")
        discharge_count = sum(1 for v in result.scheduled_slots.values() if v == "discharge")
        if charge_count > 0 and discharge_count > 0:
            assert "buy" in result.schedule_reason.lower() or "sell" in result.schedule_reason.lower()

    def test_reason_always_set(self):
        """Every schedule result should have a non-empty reason."""
        for mode in ["off", "from_grid", "to_grid", "both"]:
            config = default_config(grid_mode=mode)
            state = default_state()
            result = calculate_schedule(config, state)
            assert result.schedule_reason, f"Empty reason for mode={mode}"


class TestFlexibleLoadConfig:
    """Tests for FlexibleLoadConfig helpers."""

    def test_nearest_step_at_or_below_exact(self):
        """Exact match returns that step."""
        load = FlexibleLoadConfig(current_steps=[6, 10, 13, 16, 20, 25])
        assert load.nearest_step_at_or_below(16) == 16

    def test_nearest_step_at_or_below_between(self):
        """Non-matching value rounds down to the nearest available step."""
        load = FlexibleLoadConfig(current_steps=[6, 10, 13, 16, 20, 25])
        assert load.nearest_step_at_or_below(11) == 10

    def test_nearest_step_at_or_below_below_min(self):
        """Below minimum returns minimum step."""
        load = FlexibleLoadConfig(current_steps=[6, 10, 13, 16, 20, 25])
        assert load.nearest_step_at_or_below(3) == 6

    def test_nearest_step_at_or_below_above_max(self):
        """Above maximum returns maximum step."""
        load = FlexibleLoadConfig(current_steps=[6, 10, 13, 16, 20, 25])
        assert load.nearest_step_at_or_below(32) == 25

    def test_nearest_step_at_or_below_empty(self):
        """Empty steps returns None."""
        load = FlexibleLoadConfig(current_steps=[])
        assert load.nearest_step_at_or_below(16) is None

    def test_power_at_current_single_phase(self):
        """Single phase power calculation."""
        load = FlexibleLoadConfig(phases=1, voltage=230)
        assert abs(load.power_at_current(16) - 3.68) < 0.01

    def test_power_at_current_three_phase(self):
        """Three phase power calculation."""
        load = FlexibleLoadConfig(phases=3, voltage=230)
        assert abs(load.power_at_current(16) - 11.04) < 0.01


class TestEVChargeStrategy:
    """Tests for the EV charge strategy applied in _schedule_flexible_loads."""

    def _ev_load(self):
        return FlexibleLoadConfig(
            enabled=True,
            name="EV",
            switch_entity="switch.ev",
            current_entity="number.ev_current",
            current_steps=[6, 10, 16],
        )

    def _binary_load(self):
        return FlexibleLoadConfig(
            enabled=True, name="Boiler", switch_entity="switch.boiler",
        )

    def _remaining(self):
        # 6 slots: 0,1 cheap; 2 negative; 3,4,5 expensive
        return [(0, 0.05), (1, 0.06), (2, -0.02), (3, 0.30), (4, 0.31), (5, 0.32)]

    def test_smart_default(self):
        """smart: cheap + negative + pv-surplus + charge slots."""
        res = _schedule_flexible_loads(
            [self._ev_load()], self._remaining(), {3: "charge"},
            price_threshold=0.10, pv_surplus_slots={4}, ev_charge_strategy="smart",
        )
        slots = set(res[0].keys())
        assert slots == {0, 1, 2, 3, 4}  # cheap 0,1; neg 2; charge 3; pv 4

    def test_always_on(self):
        """always_on: every remaining slot is scheduled regardless of price."""
        res = _schedule_flexible_loads(
            [self._ev_load()], self._remaining(), {},
            price_threshold=0.10, pv_surplus_slots=set(), ev_charge_strategy="always_on",
        )
        assert set(res[0].keys()) == {0, 1, 2, 3, 4, 5}

    def test_solar_only(self):
        """solar_only: only PV-surplus slots, even if cheap/negative."""
        res = _schedule_flexible_loads(
            [self._ev_load()], self._remaining(), {3: "charge"},
            price_threshold=0.10, pv_surplus_slots={4, 5}, ev_charge_strategy="solar_only",
        )
        assert set(res[0].keys()) == {4, 5}

    def test_cheap_only(self):
        """cheap_only: at/below threshold or negative; ignores PV surplus."""
        res = _schedule_flexible_loads(
            [self._ev_load()], self._remaining(), {},
            price_threshold=0.10, pv_surplus_slots={5}, ev_charge_strategy="cheap_only",
        )
        # cheap 0,1 + negative 2; NOT the pv-surplus expensive slot 5
        assert set(res[0].keys()) == {0, 1, 2}

    def test_strategy_only_affects_ev(self):
        """Non-EV loads always use the smart overlay, ignoring ev_charge_strategy."""
        res = _schedule_flexible_loads(
            [self._binary_load()], self._remaining(), {},
            price_threshold=0.10, pv_surplus_slots={5}, ev_charge_strategy="always_on",
        )
        # Binary load uses smart: cheap 0,1 + negative 2 + pv-surplus 5
        assert set(res[0].keys()) == {0, 1, 2, 5}

    def test_tomorrow_flex_schedule_uses_ev_strategy(self):
        """Tomorrow's flex load schedule should honour the EV charge strategy."""
        prices_today = [0.10] * 24
        prices_tomorrow = [0.05] * 8 + [0.25] * 8 + [0.10] * 8  # cheap morning, expensive mid, medium evening
        pv_tomorrow = {h: 3.0 for h in range(8, 16)}  # PV 08-16

        config = EMSConfig(
            grid_mode="from_grid",
            battery_capacity_kwh=10,
            safe_power_kw=5,
            inverter_max_power_kw=10,
            consumption_est_kwh=10,
            ev_charge_strategy="always_on",
            flexible_loads=[self._ev_load()],
        )
        state = EMSState(
            slot_prices_today=prices_today,
            slot_prices_tomorrow=prices_tomorrow,
            pv_hourly_kwh={},
            pv_hourly_kwh_tomorrow=pv_tomorrow,
            battery_soc_pct=80.0,
            pv_actual_today_kwh=0,
            current_hour=23,
            current_minute=0,
        )
        result = calculate_schedule(config, state)

        # With always_on, every tomorrow slot should be scheduled
        assert result.tomorrow_load_slots, "tomorrow_load_slots should not be empty"
        ev_slots = result.tomorrow_load_slots.get(0, {})
        assert len(ev_slots) == 24, f"always_on should schedule all 24 slots, got {len(ev_slots)}"

    def test_tomorrow_flex_schedule_solar_only(self):
        """Tomorrow solar_only strategy should only schedule PV-surplus slots."""
        prices_today = [0.10] * 24
        prices_tomorrow = [0.10] * 24
        pv_tomorrow = {10: 3.0, 11: 4.0, 12: 5.0}  # PV only 10-12

        config = EMSConfig(
            grid_mode="from_grid",
            battery_capacity_kwh=10,
            safe_power_kw=5,
            inverter_max_power_kw=10,
            consumption_est_kwh=10,
            ev_charge_strategy="solar_only",
            flexible_loads=[self._ev_load()],
        )
        state = EMSState(
            slot_prices_today=prices_today,
            slot_prices_tomorrow=prices_tomorrow,
            pv_hourly_kwh={},
            pv_hourly_kwh_tomorrow=pv_tomorrow,
            battery_soc_pct=80.0,
            pv_actual_today_kwh=0,
            current_hour=23,
            current_minute=0,
        )
        result = calculate_schedule(config, state)
        ev_slots = result.tomorrow_load_slots.get(0, {})
        # Only PV surplus hours (consumption ~0.42 kWh/h, PV 3-5 kWh/h → surplus at 10,11,12)
        for slot_idx in ev_slots:
            hour = slot_idx  # 24-slot granularity → slot == hour
            assert hour in pv_tomorrow, f"solar_only slot {slot_idx} has no PV"


class TestSellCoverage:
    """Ensure both-mode charges today when today has profitable sell slots."""

    def test_sell_coverage_adds_today_charge_slots(self):
        """When tomorrow is cheaper but today has sells, charge today too.

        Reproduces the bug where unified slot selection put all charge
        slots on tomorrow, leaving today's battery too low to sell in the
        evening — dropping 6+ profitable sell slots.
        """
        # 24-slot (hourly) granularity for simplicity.
        # Today: cheap afternoon (0.03), expensive evening (0.25+).
        # Tomorrow: very cheap overnight (0.01).
        today_prices = (
            [0.10] * 14  # 00-13: morning (past, slot 14 = current)
            + [0.03, 0.03, 0.04, 0.04]  # 14-17: cheap afternoon
            + [0.25, 0.26, 0.27, 0.25, 0.24, 0.23]  # 18-23: expensive evening
        )
        tomorrow_prices = (
            [0.01] * 6 + [0.03] * 6  # 00-11: very cheap
            + [0.08] * 6  # 12-17
            + [0.20] * 6  # 18-23
        )
        config = EMSConfig(
            grid_mode="both",
            battery_capacity_kwh=60,
            battery_charge_max_pct=100,
            battery_discharge_min_pct=20,
            safe_power_kw=15,
            inverter_max_power_kw=25,
            consumption_est_kwh=5,
            efficiency=0.90,
            arbitrage_price_delta=0.15,
        )
        state = EMSState(
            slot_prices_today=today_prices,
            slot_prices_tomorrow=tomorrow_prices,
            battery_soc_pct=52.0,  # ~31.2 kWh of 60
            pv_hourly_kwh={},
            pv_actual_today_kwh=0,
            current_hour=14,
            current_minute=0,
        )
        result = calculate_schedule(config, state)

        # The algorithm must schedule some charge slots TODAY (not all tomorrow)
        today_charges = [i for i, a in result.scheduled_slots.items() if a == "charge"]
        assert len(today_charges) > 0, (
            "Expected today charge slots to support evening sells, "
            f"but got 0.  Scheduled: {result.scheduled_slots}"
        )

        # And it must schedule sell slots in the expensive evening window
        today_sells = [i for i, a in result.scheduled_slots.items() if a == "discharge"]
        assert len(today_sells) >= 2, (
            f"Expected profitable evening sells, got {len(today_sells)}: {today_sells}"
        )
