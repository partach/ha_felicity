#!/usr/bin/env python3
"""
EMS day-simulator / scenario harness  (Windows-runnable, no Home Assistant needed)
==================================================================================

Purpose
-------
Run the PURE scheduling algorithm (``ems.calculate_schedule``) on a library of
named, realistic scenarios — for BOTH engines (greedy and MILP) — and:

  * print a readable console report (which slots charge/sell, planned kWh,
    reserve target, projected SOC low, the human "reason"),
  * check per-scenario EXPECTATIONS (corner-case assertions) and exit non-zero
    if any fail (so it can gate a release),
  * render a PNG per scenario (price bars coloured by action + SOC trajectory +
    reserve + threshold), greedy vs MILP side by side, if ``matplotlib`` is
    installed.

This lets you SEE and CONFIRM what the algorithm does instead of taking anyone's
word for it.  Every config knob is exposed in the scenario definitions.

Run (Windows / Mac / Linux)
---------------------------
    cd ha_felicity
    pip install pulp matplotlib          # pulp = MILP engine, matplotlib = charts
    python tools/ems_simulator.py        # run all scenarios, write charts to tools/sim_output/
    python tools/ems_simulator.py --name self_suff_daytime_ev   # one scenario
    python tools/ems_simulator.py --no-plot                     # text only
    python tools/ems_simulator.py --engine greedy               # one engine

Exit code is 0 when all expectations pass, 1 otherwise.

Scenarios live in ``tools/scenarios.py`` so you can add your own without
touching this runner.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_PKG = os.path.join(_REPO, "custom_components", "ha_felicity")


def _load(modname: str, filename: str):
    spec = importlib.util.spec_from_file_location(modname, os.path.join(_PKG, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod          # so ems.py's lazy `import milp` resolves
    spec.loader.exec_module(mod)
    return mod


ems = _load("ems", "ems.py")
try:
    _load("milp", "milp.py")
    _HAS_MILP = True
except Exception as err:  # noqa: BLE001
    print(f"[warn] MILP engine unavailable ({err}); only greedy will run.")
    _HAS_MILP = False

# Scenario library lives next to this runner.
sys.path.insert(0, _HERE)
from scenarios import SCENARIOS  # noqa: E402


def _result_dict(engine, prices, sched, traj, cur_slot, *,
                 planned_kwh=0.0, reserve_pct=0.0, overnight_need=0.0,
                 status=None, reason=None, threshold=None):
    charge = sorted(i for i, a in (sched or {}).items() if a == "charge")
    sell = sorted(i for i, a in (sched or {}).items() if a == "discharge")
    future = [traj[i] for i in range(min(cur_slot, len(traj)), len(traj))] if traj else []
    return {
        "engine": engine,
        "charge_slots": charge,
        "sell_slots": sell,
        "charge_prices": [round(prices[i], 3) for i in charge if i < len(prices) and prices[i] is not None],
        "sell_prices": [round(prices[i], 3) for i in sell if i < len(prices) and prices[i] is not None],
        "planned_kwh": round(planned_kwh or 0.0, 2),
        "reserve_pct": round(reserve_pct or 0.0, 1),
        "overnight_need_kwh": round(overnight_need or 0.0, 2),
        "projected_low_pct": round(min(future), 1) if future else None,
        "status": status,
        "reason": reason,
        "threshold": threshold,
        "trajectory": traj,
    }


def run_one(scenario: dict, engine: str) -> dict:
    """Run one scenario through one optimizer engine; return a flat result dict."""
    cfg_kwargs = dict(scenario["config"])
    cfg_kwargs["scheduler_engine"] = engine
    config = ems.EMSConfig(**cfg_kwargs)
    state = ems.EMSState(**scenario["state"])
    result = ems.calculate_schedule(config, state)

    prices = state.slot_prices_today or []
    num = len(prices)
    cur_slot = int((state.current_hour * 60 + state.current_minute) / ((24 * 60) / num)) if num else 0
    return _result_dict(
        result.scheduler_active or engine, prices, result.scheduled_slots,
        result.soc_trajectory or [], cur_slot,
        planned_kwh=result.grid_energy_planned, reserve_pct=result.reserve_target_pct,
        overnight_need=result.self_consumption_reserve, status=result.status,
        reason=result.schedule_reason, threshold=result.price_threshold,
    )


def _manual_threshold(prices, level):
    """Mirror of coordinator's manual threshold formula (price_threshold_level 1-10)."""
    vals = [p for p in prices if p is not None]
    if not vals:
        return None
    pmin, pmax = min(vals), max(vals)
    pavg = sum(vals) / len(vals)
    if level <= 5:
        return pmin + (pavg - pmin) * ((level - 1) / 4.0)
    return pavg + (pmax - pavg) * ((level - 5) / 5.0)


def run_manual(scenario: dict) -> dict:
    """Manual price mode — a THRESHOLD rule, not the optimizer.

    Mirrors coordinator._build_manual_schedule: from_grid/both charge every
    remaining slot BELOW the threshold, to_grid/both sell every remaining slot
    ABOVE it.  Reuses ems._compute_scheduled_soc_trajectory for the SOC line so
    the trajectory math never drifts from the real code.
    """
    cfg = scenario["config"]
    # price_mode / price_threshold_level are coordinator options, not EMSConfig fields.
    _coord_only = {"price_mode", "price_threshold_level"}
    config = ems.EMSConfig(**{k: v for k, v in cfg.items() if k not in _coord_only})
    state = ems.EMSState(**scenario["state"])
    prices = state.slot_prices_today or []
    n = len(prices)
    grid_mode = cfg.get("grid_mode", "off")
    level = cfg.get("price_threshold_level", 5)
    threshold = _manual_threshold(prices, level)
    cur_slot = int((state.current_hour * 60 + state.current_minute) / ((24 * 60) / n)) if n else 0
    allow_charge = grid_mode in ("from_grid", "both")
    allow_sell = grid_mode in ("to_grid", "both")

    cap = config.battery_capacity_kwh
    cur_kwh = (state.battery_soc_pct / 100.0) * cap if state.battery_soc_pct is not None else 0.0
    mps = (24 * 60) / n if n else 60.0

    # SOC-aware (mirrors coordinator._build_manual_schedule): only mark a slot
    # 'charge' while the battery isn't full and 'sell' while it isn't empty, so
    # the chart never shows charging-while-full or selling-while-empty.
    sched = {}
    if threshold is not None and n:
        slot_h = mps / 60.0
        eff = config.efficiency
        max_kwh = (config.battery_charge_max_pct / 100.0) * cap
        min_kwh = (config.battery_discharge_min_pct / 100.0) * cap
        safe_kw = config.safe_power_kw
        inv_max = config.inverter_max_power_kw
        cons_profile = state.consumption_hourly_kwh or {}
        cons_flat = config.consumption_est_kwh / 24.0
        pv_hourly = state.pv_hourly_kwh or {}
        soc = cur_kwh
        for i in range(cur_slot, n):
            hour = int((i * mps) / 60) % 24
            pv_kw = pv_hourly.get(hour, pv_hourly.get(str(hour), 0.0))
            pv_e = pv_kw * slot_h
            cons = (cons_profile.get(hour, cons_flat) if cons_profile else cons_flat) * slot_h
            p = prices[i]
            charge_ok = p is not None and allow_charge and p < threshold
            sell_ok = p is not None and allow_sell and p > threshold
            near_full = soc >= max_kwh - 0.1
            near_empty = soc <= min_kwh + 0.1
            if charge_ok and not near_full:
                sched[i] = "charge"
                soc = soc + min(safe_kw, max(0.0, inv_max - pv_kw)) * slot_h * eff + pv_e - cons
            elif sell_ok and not near_empty:
                sched[i] = "discharge"
                soc = soc - safe_kw * slot_h + pv_e - cons
            elif charge_ok:
                soc = soc + pv_e   # full + low price → held full, no charge mark
            else:
                soc = soc + pv_e - cons
            soc = max(min_kwh, min(max_kwh, soc))

    traj = ems._compute_scheduled_soc_trajectory(
        prices, n, mps, cur_kwh, cur_slot, sched, config, state) if n else []
    n_c = sum(1 for v in sched.values() if v == "charge")
    n_s = sum(1 for v in sched.values() if v == "discharge")
    return _result_dict(
        "manual", prices, sched, traj, cur_slot,
        reason=f"Manual: {n_c} charge / {n_s} sell vs threshold "
               f"{threshold:.3f}" if threshold is not None else "Manual: no threshold",
        threshold=threshold,
    )


def _fmt_slots(slots, prices):
    if not slots:
        return "none"
    return ", ".join(
        f"{s}@{round(prices[s], 3) if s < len(prices) and prices[s] is not None else '?'}"
        for s in slots
    )


def report_one(scenario: dict, results: dict) -> bool:
    """Print a scenario block; run its expectation; return pass/fail."""
    print("=" * 78)
    print(f"SCENARIO: {scenario['name']}")
    print(f"  {scenario.get('desc', '')}")
    cfg = scenario["config"]
    st = scenario["state"]
    prices0 = st.get("slot_prices_today") or []
    n0 = len(prices0)
    pv_disp = effective_pv_per_slot(st, n0)
    pv_total = round(sum(pv_disp), 1)
    pv_src = "hourly" if st.get("pv_hourly_kwh") else ("synthesized" if (st.get("pv_forecast_today") or 0) > 0 else "none")
    cons_disp = effective_consumption_per_slot(cfg, st, n0)
    cons_src = "hourly" if st.get("consumption_hourly_kwh") else "flat"
    print(f"  grid_mode={cfg.get('grid_mode')} priority={cfg.get('optimization_priority','cost')} "
          f"cap={cfg.get('battery_capacity_kwh')}kWh SOC={st.get('battery_soc_pct')}% "
          f"time={st.get('current_hour',0):02d}:{st.get('current_minute',0):02d} "
          f"consumption={cfg.get('consumption_est_kwh')}kWh/d({cons_src}) "
          f"PV_today={pv_total}kWh({pv_src})")
    prices = st.get("slot_prices_today") or []
    for engine, r in results.items():
        print(f"  [{engine:>6}] charge={_fmt_slots(r['charge_slots'], prices)}")
        print(f"           sell={_fmt_slots(r['sell_slots'], prices)}")
        print(f"           planned={r['planned_kwh']}kWh reserve={r['reserve_pct']}% "
              f"overnight_need={r['overnight_need_kwh']}kWh projected_low={r['projected_low_pct']}% "
              f"engine_used={r['engine']}")
        print(f"           reason: {r['reason']}")

    ok = True
    expect = scenario.get("expect")
    if expect:
        for engine, r in results.items():
            try:
                passed, msg = expect(r, scenario)
            except Exception as e:  # noqa: BLE001
                passed, msg = False, f"expectation raised: {e}"
            tag = "PASS" if passed else "FAIL"
            print(f"  >>> [{engine:>6}] {tag}: {msg}")
            ok = ok and passed
    return ok


def effective_pv_per_slot(state_kwargs: dict, n: int, tomorrow: bool = False):
    """The per-slot PV (kWh) the algorithm effectively sees, for plotting.

    Uses the supplied hourly PV when present, otherwise SYNTHESIZES it from the
    daily forecast total with the SAME helper the algorithm uses
    (ems._synthesize_pv_hourly) — so 'daily-only forecast' scenarios still show
    a realistic solar hump instead of a flat zero.
    """
    if n == 0:
        return [0.0] * 0
    key = "pv_hourly_kwh_tomorrow" if tomorrow else "pv_hourly_kwh"
    hourly = state_kwargs.get(key)
    if not hourly:
        total_key = "pv_forecast_tomorrow" if tomorrow else "pv_forecast_today"
        total = state_kwargs.get(total_key) or 0.0
        hourly = ems._synthesize_pv_hourly(total) if total > 0 else {}
    mps = (24 * 60) / n
    out = []
    for i in range(n):
        hour = int((i * mps) / 60) % 24
        val = hourly.get(hour, hourly.get(str(hour), 0.0)) if hourly else 0.0
        out.append(val * (mps / 60.0))
    return out


def effective_consumption_per_slot(config_kwargs: dict, state_kwargs: dict, n: int):
    """Per-slot consumption (kWh) — hourly profile when present, else flat."""
    if n == 0:
        return []
    profile = state_kwargs.get("consumption_hourly_kwh") or {}
    flat = (config_kwargs.get("consumption_est_kwh") or 0.0) / 24.0
    mps = (24 * 60) / n
    out = []
    for i in range(n):
        hour = int((i * mps) / 60) % 24
        per_hour = profile.get(hour, profile.get(str(hour), flat)) if profile else flat
        out.append(per_hour * (mps / 60.0))
    return out


def plot_scenario(scenario: dict, results: dict, outdir: str):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    st = scenario["state"]
    prices = st.get("slot_prices_today") or []
    n = len(prices)
    if n == 0:
        return None
    engines = list(results.keys())
    fig, axes = plt.subplots(len(engines), 1, figsize=(12, 3.2 * len(engines)), squeeze=False)
    for row, engine in enumerate(engines):
        r = results[engine]
        ax = axes[row][0]
        xs = list(range(n))
        colors = []
        cset = set(r["charge_slots"])
        sset = set(r["sell_slots"])
        for i in xs:
            colors.append("#4CAF50" if i in cset else "#FF9800" if i in sset else "#cfcfcf")
        pr = [p if p is not None else 0 for p in prices]
        ax.bar(xs, pr, color=colors, width=0.9, align="edge")
        ax.set_ylabel("price")
        ax.set_xlim(0, n)
        if r.get("threshold") is not None:
            ax.axhline(r["threshold"], color="#d4b106", ls="--", lw=1,
                       label=f"threshold {r['threshold']:.3f}")
            ax.legend(loc="upper left", fontsize=7)
        ax.set_title(f"{scenario['name']} — {engine} (charge=green sell=orange)  reason: {r['reason']}", fontsize=8)

        # PV production (yellow hump) AND consumption (red line) on a shared
        # kWh/h axis — so you can see where PV exceeds consumption (surplus
        # charges the battery) vs where consumption exceeds PV (battery drains).
        pv = effective_pv_per_slot(scenario["state"], n)
        cons = effective_consumption_per_slot(scenario["config"], scenario["state"], n)
        kwh_max = max(max(pv, default=0), max(cons, default=0))
        if kwh_max > 0.001:
            axpv = ax.twinx()
            axpv.spines["right"].set_position(("outward", 38))
            axpv.fill_between([i + 0.5 for i in range(n)], pv, color="#ffd400",
                              alpha=0.30, step="mid", label="PV kWh/h")
            axpv.plot([i + 0.5 for i in range(n)], cons, color="#e53935", lw=1.3,
                      ls="-", label="consumption kWh/h")
            axpv.set_ylim(0, kwh_max * 1.4)
            axpv.set_ylabel("PV / load kWh/h", color="#b59500")
            axpv.tick_params(axis="y", labelcolor="#b59500")
            axpv.legend(loc="upper center", fontsize=7)

        # "now" marker — everything left of it is PAST (the SOC there is a flat
        # placeholder, not a real history, since the simulator has no recorder).
        cur_slot0 = int((scenario["state"].get("current_hour", 0) * 60
                         + scenario["state"].get("current_minute", 0)) / ((24 * 60) / n))
        if 0 < cur_slot0 < n:
            ax.axvline(cur_slot0, color="#e57373", lw=1.2, ls=":", alpha=0.8)

        # SOC trajectory on a twin axis
        traj = r["trajectory"]
        if traj:
            ax2 = ax.twinx()
            ax2.plot([i + 0.5 for i in range(len(traj))], traj, color="#08b2c9", lw=2, label="SOC%")
            ax2.set_ylim(0, 100)
            ax2.set_ylabel("SOC %")
            if r["reserve_pct"]:
                ax2.axhline(r["reserve_pct"], color="#ba91ff", ls="--", lw=1, label=f"reserve {r['reserve_pct']}%")
            ax2.legend(loc="upper right", fontsize=7)
    fig.tight_layout()
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"{scenario['name']}.png")
    fig.savefig(path, dpi=90)
    plt.close(fig)
    return path


def main():
    ap = argparse.ArgumentParser(description="EMS scenario simulator")
    ap.add_argument("--name", help="run only the scenario with this name")
    ap.add_argument("--engine", choices=["greedy", "milp", "both"], default="both")
    ap.add_argument("--no-plot", action="store_true")
    ap.add_argument("--outdir", default=os.path.join(_HERE, "sim_output"))
    args = ap.parse_args()

    engines = ["greedy", "milp"] if args.engine == "both" else [args.engine]
    if "milp" in engines and not _HAS_MILP:
        engines = [e for e in engines if e != "milp"] or ["greedy"]

    scenarios = [s for s in SCENARIOS if (not args.name or s["name"] == args.name)]
    if not scenarios:
        print(f"No scenario named {args.name!r}. Available:")
        for s in SCENARIOS:
            print(f"  {s['name']}")
        return 2

    all_ok = True
    plotted = []
    for sc in scenarios:
        if sc["config"].get("price_mode") == "manual":
            # Manual mode is engine-agnostic (a threshold rule, not the optimizer).
            results = {"manual": run_manual(sc)}
        else:
            results = {e: run_one(sc, e) for e in engines}
        ok = report_one(sc, results)
        all_ok = all_ok and ok
        if not args.no_plot:
            p = plot_scenario(sc, results, args.outdir)
            if p:
                plotted.append(p)
    print("=" * 78)
    if plotted:
        print(f"Charts written to: {args.outdir}  ({len(plotted)} file(s))")
    elif not args.no_plot:
        print("(matplotlib not installed — no charts.  `pip install matplotlib` for visuals.)")
    print("RESULT:", "ALL EXPECTATIONS PASSED" if all_ok else "SOME EXPECTATIONS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
