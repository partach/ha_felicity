#!/usr/bin/env python3
"""Diagnose why the EMS card shows "Greedy (fallback)" instead of MILP.

Run this INSIDE the Home Assistant environment (the same Python that runs HA),
so it probes the interpreter the integration actually uses:

    # HA OS / Supervised — from the Terminal & SSH add-on:
    docker exec homeassistant python3 /config/custom_components/ha_felicity/../../tools/check_milp.py
    # HA Container:
    docker exec -it homeassistant python3 /config/check_milp.py
    # HA Core (venv):
    source /srv/homeassistant/bin/activate && python3 tools/check_milp.py

It answers, in order: is pulp installed, which solver (if any) is runnable, and
does a real LP actually solve.  Prints a fix for whatever it finds.

Nothing here is specific to your data — it only probes the solver.
"""
from __future__ import annotations

import platform
import sys

FIX_CBC = (
    "    pip install 'pulp[cbc]'        # CBC via the cbcbox package\n"
    "  or\n"
    "    pip install 'pulp[highs]'      # HiGHS — pure pip wheel, no system libs\n"
    "  then RESTART Home Assistant (the solver is re-checked only at startup)."
)


def main() -> int:
    print("=" * 68)
    print("ha_felicity — MILP solver diagnosis")
    print("=" * 68)
    print(f"Python      : {sys.version.split()[0]} ({sys.executable})")
    print(f"Platform    : {platform.system()} {platform.machine()}")

    # --- 1. is pulp importable? ---------------------------------------------
    try:
        import pulp
    except Exception as err:
        print(f"\npulp        : NOT INSTALLED ({type(err).__name__}: {err})")
        print("\nVERDICT: MILP cannot run — pulp is missing from THIS interpreter.")
        print("  If HA installed it elsewhere you are probing the wrong Python.")
        print("  Otherwise install it:\n" + FIX_CBC)
        return 1

    version = getattr(pulp, "__version__", "unknown")
    print(f"pulp        : {version}")
    if str(version).split(".")[0] not in ("", "unknown") and str(version)[0] >= "4":
        print("  ! PuLP 4.x REMOVED the bundled PULP_CBC_CMD solver.")
        print("    The manifest pins <4.0 for this reason; a manual upgrade breaks MILP.")

    # --- 2. which solvers are actually runnable? -----------------------------
    try:
        available = list(pulp.listSolvers(onlyAvailable=True) or [])
    except Exception as err:
        available = []
        print(f"  (listSolvers failed: {type(err).__name__}: {err})")
    print(f"Solvers     : {', '.join(available) if available else 'NONE AVAILABLE'}")

    # Probe exactly the order milp._pick_solver uses.
    picked = None
    for name in ("PULP_CBC_CMD", "COIN_CMD", "HiGHS", "HiGHS_CMD"):
        cls = getattr(pulp, name, None)
        if cls is None:
            print(f"  - {name:<14} absent from this pulp build")
            continue
        try:
            solver = cls(msg=0)
            ok = solver.available()
            print(f"  - {name:<14} {'RUNNABLE' if ok else 'present but not runnable'}")
            if ok and picked is None:
                picked = (name, solver)
        except Exception as err:
            print(f"  - {name:<14} probe raised {type(err).__name__}: {err}")

    if picked is None:
        print("\nVERDICT: no usable LP solver → the EMS correctly falls back to greedy.")
        print("  This is the common cause. Fix:\n" + FIX_CBC)
        return 1

    # --- 3. does a real solve actually work? ---------------------------------
    name, solver = picked
    try:
        prob = pulp.LpProblem("probe", pulp.LpMinimize)
        x = pulp.LpVariable("x", 0, 10)
        prob += x
        prob += x >= 3
        prob.solve(solver)
        status = pulp.LpStatus[prob.status]
    except FileNotFoundError as err:
        print(f"\nVERDICT: {name} reports available but its BINARY is missing:")
        print(f"  {err}")
        print("  (classic on uncommon CPU arches / very new Python.) Fix:\n" + FIX_CBC)
        return 1
    except Exception as err:
        print(f"\nVERDICT: {name} failed to solve: {type(err).__name__}: {err}")
        print("  Fix:\n" + FIX_CBC)
        return 1

    if status != "Optimal" or abs(pulp.value(x) - 3) > 1e-6:
        print(f"\nVERDICT: {name} solved but gave a wrong/odd answer "
              f"(status={status}, x={pulp.value(x)}).")
        print("  Try a different solver:\n" + FIX_CBC)
        return 1

    print(f"\nVERDICT: {name} works — MILP CAN run here.")
    print("  If the card still shows 'Greedy (fallback)':")
    print("   * restart HA (the disable flag only clears at startup), then")
    print("   * check the schedule_status attribute `milp_status` in")
    print("     Developer Tools → States for the exact reason, and")
    print("   * grep the log for 'MILP' — a repeated 'non-optimal' means the")
    print("     solver runs but the model was infeasible for those inputs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
