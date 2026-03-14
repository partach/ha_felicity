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
calculate_self_consumption_reserve = ems.calculate_self_consumption_reserve
calculate_net_pv_surplus = ems.calculate_net_pv_surplus
select_unified_charge_slots = ems.select_unified_charge_slots
calculate_schedule = ems.calculate_schedule
calculate_available_info = ems.calculate_available_info
_calculate_pv_confidence = ems._calculate_pv_confidence
_project_soc_trajectory = ems._project_soc_trajectory
_validate_schedule_soc = ems._validate_schedule_soc


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
        """When battery can't bridge to tomorrow, swaps tomorrow→today slots."""
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
            current_kwh=15.0,  # very low
            net_pv=0.0,
            charge_max_pct=100.0,
            slot_prices_tomorrow=tomorrow_prices,
            pv_forecast_tomorrow=30.0,
            current_hour=20,
        )
        # Should have some today slots despite tomorrow being cheaper,
        # because battery needs to survive the bridge
        assert len(today) > 0


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
        """Negative prices always trigger charging in from_grid mode."""
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
        # Should charge during negative price slots even with full-ish battery
        charged_neg = sum(1 for s in charge_slots if prices[s] < 0)
        assert charged_neg > 0

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
        """When negative prices appear, always charge even if battery is fine."""
        config = default_config(consumption_est_kwh=20.0)
        # Some negative slots mixed in
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
        # Negative price slots should be included
        neg_slots = [s for s in charge_slots if prices[s] < 0]
        assert len(neg_slots) >= 1  # at least some negative slots selected


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
