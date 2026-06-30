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


def run_one(scenario: dict, engine: str) -> dict:
    """Run one scenario through one engine; return a flat result dict."""
    cfg_kwargs = dict(scenario["config"])
    cfg_kwargs["scheduler_engine"] = engine
    config = ems.EMSConfig(**cfg_kwargs)
    state = ems.EMSState(**scenario["state"])
    result = ems.calculate_schedule(config, state)

    prices = state.slot_prices_today or []
    cap = config.battery_capacity_kwh
    charge = sorted(i for i, a in (result.scheduled_slots or {}).items() if a == "charge")
    sell = sorted(i for i, a in (result.scheduled_slots or {}).items() if a == "discharge")
    traj = result.soc_trajectory or []
    # Projected low over the FUTURE part of the trajectory.
    num = len(prices)
    cur_slot = int((state.current_hour * 60 + state.current_minute) / ((24 * 60) / num)) if num else 0
    future = [traj[i] for i in range(min(cur_slot, len(traj)), len(traj))] if traj else []
    return {
        "engine": result.scheduler_active or engine,
        "charge_slots": charge,
        "sell_slots": sell,
        "charge_prices": [round(prices[i], 3) for i in charge if i < len(prices) and prices[i] is not None],
        "sell_prices": [round(prices[i], 3) for i in sell if i < len(prices) and prices[i] is not None],
        "planned_kwh": round(result.grid_energy_planned or 0.0, 2),
        "reserve_pct": round(result.reserve_target_pct or 0.0, 1),
        "overnight_need_kwh": round(result.self_consumption_reserve or 0.0, 2),
        "projected_low_pct": round(min(future), 1) if future else None,
        "status": result.status,
        "reason": result.schedule_reason,
        "trajectory": traj,
        "_result": result,
    }


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
    print(f"  grid_mode={cfg.get('grid_mode')} priority={cfg.get('optimization_priority','cost')} "
          f"cap={cfg.get('battery_capacity_kwh')}kWh SOC={st.get('battery_soc_pct')}% "
          f"time={st.get('current_hour',0):02d}:{st.get('current_minute',0):02d} "
          f"consumption={cfg.get('consumption_est_kwh')}kWh/d")
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
        cset = set(r["charge_slots"]); sset = set(r["sell_slots"])
        for i in xs:
            colors.append("#4CAF50" if i in cset else "#FF9800" if i in sset else "#cfcfcf")
        pr = [p if p is not None else 0 for p in prices]
        ax.bar(xs, pr, color=colors, width=0.9, align="edge")
        ax.set_ylabel("price")
        ax.set_xlim(0, n)
        ax.set_title(f"{scenario['name']} — {engine} (charge=green sell=orange)  reason: {r['reason']}", fontsize=8)
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
