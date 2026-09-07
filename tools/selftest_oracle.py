#!/usr/bin/env python3
"""Prove the oracle metrics are two-sided, on a synthetic matrix.

The claim the whole v2 design rests on is that an always-cheapest router and an
always-frontier router must *both* score badly. That claim is arithmetic, so it
can be checked without spending a won — and it should be, because the real
matrix costs money to produce and a sign error in here would quietly bless
whichever behaviour it favours.

    python3 tools/selftest_oracle.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from smartroute_bench.oracle import evaluate, task_view, difficulty_band  # noqa: E402

CHEAP, MID, TOP = "g/flash-lite", "g/pro", "o/frontier"
POOL = [CHEAP, MID, TOP]


def entry(model, ok, cost, judge=None):
    return {"resolved_model": model, "check_passed": ok, "cost_krw": cost,
            "judge_mean": judge, "delivery": "ok"}


def matrix():
    """12 tasks: 6 easy (cheap model suffices), 4 discriminative, 2 frontier-only."""
    tasks = {}
    for i in range(6):
        tasks[f"easy-{i}"] = {
            CHEAP: entry(CHEAP, True, 1.0), MID: entry(MID, True, 8.0),
            TOP: entry(TOP, True, 40.0)}
    for i in range(4):
        tasks[f"disc-{i}"] = {
            CHEAP: entry(CHEAP, False, 1.0), MID: entry(MID, True, 8.0),
            TOP: entry(TOP, True, 40.0)}
    for i in range(2):
        tasks[f"hard-{i}"] = {
            CHEAP: entry(CHEAP, False, 1.0), MID: entry(MID, False, 8.0),
            TOP: entry(TOP, True, 40.0)}
    return {"models": POOL, "tasks": tasks}


def strategy(m, pick):
    """Build the routing arm's rows by always choosing `pick(tid)`."""
    rows = {}
    for tid, models in m["tasks"].items():
        chosen = pick(tid)
        e = dict(models[chosen])
        e["resolved_model"] = chosen
        rows[tid] = e
    return rows


FAILURES: list[str] = []


def expect(name, got, want, tol=0.005):
    ok = (want is None and got is None) or (
        got is not None and abs(got - want) <= tol)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {got} (want {want})")
    if not ok:
        FAILURES.append(name)


def main() -> int:
    m = matrix()

    print("\nbands (empirical difficulty from the pool, not the author)")
    bands = {}
    for tid, models in m["tasks"].items():
        v = task_view(models)
        b = difficulty_band(v, CHEAP)
        bands[b] = bands.get(b, 0) + 1
    print("  ", bands)
    if bands.get("cheap-sufficient") != 6 or bands.get("discriminative") != 4 \
            or bands.get("frontier-only") != 2:
        FAILURES.append("band classification")
        print("   FAIL band classification")

    print("\nalways-cheapest router (must lose on under-route)")
    r = evaluate(strategy(m, lambda _t: CHEAP), m)
    expect("under_route_pct", r["under_route_pct"], 50.0)   # 6 of 12 fail
    expect("over_route_pct", r["over_route_pct"], 0.0)
    expect("cost_regret_x", r["cost_regret_x"], 12 / 118)   # 12 vs 6*1+4*8+2*40
    print(f"       optimal_selection_pct={r['optimal_selection_pct']}")

    print("\nalways-frontier router (must lose on over-route)")
    r = evaluate(strategy(m, lambda _t: TOP), m)
    expect("under_route_pct", r["under_route_pct"], 0.0)
    expect("over_route_pct", r["over_route_pct"], 83.33)     # 10 of 12
    expect("cost_regret_x", r["cost_regret_x"], 480 / 118)
    print(f"       optimal_selection_pct={r['optimal_selection_pct']}")

    print("\noracle router (must be perfect)")
    pick = lambda t: CHEAP if t.startswith("easy") else (
        MID if t.startswith("disc") else TOP)
    r = evaluate(strategy(m, pick), m)
    expect("under_route_pct", r["under_route_pct"], 0.0)
    expect("over_route_pct", r["over_route_pct"], 0.0)
    expect("optimal_selection_pct", r["optimal_selection_pct"], 100.0)
    expect("cost_regret_x", r["cost_regret_x"], 1.0)

    print("\nthe v10 failure mode: upgrading easy tasks that were already right")
    pick = lambda t: TOP if t.startswith("easy") else (
        MID if t.startswith("disc") else TOP)
    r = evaluate(strategy(m, pick), m)
    expect("under_route_pct", r["under_route_pct"], 0.0)
    expect("over_route_pct", r["over_route_pct"], 50.0)      # the 6 easy ones
    if (r["cost_regret_x"] or 0) <= 1.5:
        FAILURES.append("v10 pattern must show cost regret")
    print(f"       cost_regret_x={r['cost_regret_x']} "
          f"(quality unchanged, cost up — the exact case v1 scored as a win)")

    print("\njudged tasks: success is relative to the best pool score")
    jm = {"models": POOL, "tasks": {"j-1": {
        CHEAP: {"resolved_model": CHEAP, "cost_krw": 1.0, "judge_mean": 9.1,
                "delivery": "ok"},
        MID: {"resolved_model": MID, "cost_krw": 8.0, "judge_mean": 9.3,
              "delivery": "ok"},
        TOP: {"resolved_model": TOP, "cost_krw": 40.0, "judge_mean": 9.4,
              "delivery": "ok"}}}}
    r = evaluate(strategy(jm, lambda _t: TOP), jm)
    expect("over_route_pct (9.1 is within tolerance of 9.4)",
           r["over_route_pct"], 100.0)

    print("\noff-pool routing is reported, not silently averaged away")
    rows = strategy(m, lambda _t: CHEAP)
    rows["easy-0"]["resolved_model"] = "someone/unmeasured"
    r = evaluate(rows, m)
    expect("n_tasks_compared", r["n_tasks_compared"], 11)
    if r["off_pool_routes"] != {"someone/unmeasured": 1}:
        FAILURES.append("off_pool_routes")
        print("   FAIL off_pool_routes:", r["off_pool_routes"])
    else:
        print("  ok   off_pool_routes:", r["off_pool_routes"])

    print("\n" + "─" * 72)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("two-sided: neither always-cheapest nor always-frontier scores well")
    return 0


if __name__ == "__main__":
    sys.exit(main())
