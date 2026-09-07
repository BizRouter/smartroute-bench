"""Oracle-referenced routing metrics — the two-sided half of the benchmark.

Every metric in v1 compares the routing arm against *pinned* arms, so it can
only ever say "routing was cheaper" or "routing was worse". It cannot say the
one thing a router most needs to hear: **a cheaper model would have done this
job**. In run v10 that blind spot cost real money — a routing change upgraded
181 global queries, spent 5.1x more, scored 2.16pp worse, and our own suite
recorded it as the best result ever.

The fix is a score matrix: every task run against every model in the router's
candidate pool. From that, per task, we know the cheapest model that succeeds —
the oracle choice — and can measure error in both directions:

    over-route   the router succeeded, but something cheaper also would have
    under-route  the router failed where some pool model succeeded

An always-cheapest router loses on under-route; an always-frontier router loses
on over-route. Neither degenerate strategy can win, which is the whole point.

Nothing here calls the network. `tools/build_score_matrix.py` produces the
matrix from a pool run's analysis.json; this module is the arithmetic, kept
separate so it can be tested without spending anything.
"""
from __future__ import annotations

# A judged task has no pass/fail, so success is defined relative to the best
# score any pool model achieved on that task. An absolute bar would be
# meaningless on this suite: judge means sit in 8.8-9.4, so a bar of 8.0 marks
# everything a success and reports 100% over-routing, while a bar of 9.4 marks
# almost nothing and reports 100% under-routing. "As good as the best available,
# within tolerance" is the question a router actually faces.
JUDGE_TOLERANCE = 0.5

# Cost noise: two calls to the same model differ run to run. A model only
# counts as "cheaper" if it is cheaper by more than this fraction, so ordinary
# jitter is not reported as over-routing.
COST_MARGIN = 0.10


def succeeded(entry: dict, best_judge: float | None) -> bool | None:
    """One success rule, applied identically to the router and to pool models.

    Returns None when the task cannot be scored for this model (no data, or a
    check that could not run) — unknown is not failure, and folding it into
    failure would bias the oracle toward expensive models.
    """
    if entry is None:
        return None
    if entry.get("delivery") not in (None, "ok", "delivered"):
        return False          # a refusal or truncation is what the caller got
    passed = entry.get("check_passed")
    if passed is not None:
        return bool(passed)
    jm = entry.get("judge_mean")
    if jm is None or best_judge is None:
        return None
    return jm >= best_judge - JUDGE_TOLERANCE


def _cost(entry: dict) -> float | None:
    c = (entry or {}).get("cost_krw")
    return None if c is None else float(c)


def task_view(models: dict[str, dict]) -> dict:
    """Collapse one task's model column into the facts the metrics need."""
    judges = [m.get("judge_mean") for m in models.values()
              if m and m.get("judge_mean") is not None]
    best_judge = max(judges) if judges else None

    scored = {}
    for name, entry in models.items():
        ok = succeeded(entry, best_judge)
        if ok is None:
            continue
        cost = _cost(entry)
        if cost is None:
            continue
        scored[name] = {"ok": ok, "cost": cost,
                        "judge_mean": entry.get("judge_mean"),
                        "latency_ms": entry.get("latency_ms")}

    winners = {n: v for n, v in scored.items() if v["ok"]}
    # Deterministic tie-break: cheapest, then better judged, then name — so the
    # same matrix always names the same oracle.
    oracle = min(winners, key=lambda n: (winners[n]["cost"],
                                         -(winners[n]["judge_mean"] or 0), n)) \
        if winners else None
    return {
        "scored": scored,
        "best_judge": best_judge,
        "oracle_model": oracle,
        "oracle_cost": winners[oracle]["cost"] if oracle else None,
        "oracle_judge": winners[oracle]["judge_mean"] if oracle else None,
        "n_pass": len(winners),
        "n_scored": len(scored),
    }


def difficulty_band(view: dict, cheapest_model: str | None) -> str:
    """Empirical difficulty — what the pool actually did, not an author's guess.

    `discriminative` is the band that earns a routing decision: some models can
    do the task and some cannot. A suite made mostly of `cheap-sufficient`
    tasks cannot rank routers at all, which is exactly v1's problem.
    """
    if view["n_scored"] == 0:
        return "unknown"
    if view["n_pass"] == 0:
        return "unsolved"
    if cheapest_model and view["scored"].get(cheapest_model, {}).get("ok"):
        return "cheap-sufficient"
    if view["n_pass"] == view["n_scored"]:
        return "cheap-sufficient"
    # only the pricier end of the pool passes
    passing_costs = [v["cost"] for v in view["scored"].values() if v["ok"]]
    all_costs = [v["cost"] for v in view["scored"].values()]
    if min(passing_costs) > sorted(all_costs)[len(all_costs) // 2]:
        return "frontier-only"
    return "discriminative"


def evaluate(route_rows: dict[str, dict], matrix: dict,
             cost_margin: float = COST_MARGIN) -> dict:
    """Compare a routing arm against the oracle, task by task.

    `route_rows` maps task_id -> the routing arm's entry for that task (the same
    shape the matrix uses). `matrix` is {"models": [...], "tasks": {tid: {model: entry}}}.
    """
    pool = matrix.get("models") or []
    per_task = []
    off_pool: dict[str, int] = {}
    counted = 0
    over = under = optimal = 0
    cost_router = cost_oracle = 0.0
    judge_router: list[float] = []
    judge_oracle: list[float] = []
    bands: dict[str, int] = {}
    no_matrix = 0

    for tid, r_entry in sorted(route_rows.items()):
        models = (matrix.get("tasks") or {}).get(tid)
        if not models:
            no_matrix += 1
            continue
        view = task_view(models)
        scored_costs = {n: v["cost"] for n, v in view["scored"].items()}
        cheapest = min(scored_costs, key=lambda n: (scored_costs[n], n)) \
            if scored_costs else None
        band = difficulty_band(view, cheapest)
        bands[band] = bands.get(band, 0) + 1

        r_ok = succeeded(r_entry, view["best_judge"])
        r_cost = _cost(r_entry)
        chosen = (r_entry or {}).get("resolved_model")
        if chosen and pool and chosen not in pool:
            # The router picked something the matrix never measured, so no
            # oracle claim about this task is defensible. Counted, not hidden:
            # a large number here means the matrix is stale, and every metric
            # below is quietly narrower than it looks.
            off_pool[chosen] = off_pool.get(chosen, 0) + 1
            continue
        if r_ok is None or r_cost is None or view["oracle_model"] is None:
            continue

        counted += 1
        cost_router += r_cost
        cost_oracle += view["oracle_cost"]
        if r_entry.get("judge_mean") is not None and view["oracle_judge"] is not None:
            judge_router.append(r_entry["judge_mean"])
            judge_oracle.append(view["oracle_judge"])

        is_optimal = chosen == view["oracle_model"]
        optimal += 1 if is_optimal else 0
        is_over = bool(r_ok and view["oracle_cost"] <= r_cost * (1 - cost_margin))
        is_under = bool(not r_ok and view["n_pass"] > 0)
        over += 1 if is_over else 0
        under += 1 if is_under else 0

        per_task.append({
            "task_id": tid, "band": band,
            "router_model": chosen, "router_ok": r_ok,
            "router_cost_krw": round(r_cost, 4),
            "oracle_model": view["oracle_model"],
            "oracle_cost_krw": round(view["oracle_cost"], 4),
            "optimal": is_optimal, "over_route": is_over, "under_route": is_under,
            "pool_pass_n": view["n_pass"], "pool_scored_n": view["n_scored"],
        })

    def rate(n):
        return round(100.0 * n / counted, 2) if counted else None

    return {
        "n_tasks_compared": counted,
        "n_tasks_without_matrix": no_matrix,
        "off_pool_routes": off_pool,
        "optimal_selection_pct": rate(optimal),
        "over_route_pct": rate(over),
        "under_route_pct": rate(under),
        "cost_regret_x": (round(cost_router / cost_oracle, 3)
                          if cost_oracle else None),
        "router_cost_krw": round(cost_router, 2),
        "oracle_cost_krw": round(cost_oracle, 2),
        "quality_regret_points": (
            round(sum(judge_oracle) / len(judge_oracle)
                  - sum(judge_router) / len(judge_router), 3)
            if judge_router else None),
        "difficulty_bands": bands,
        "discriminative_share": (
            round(bands.get("discriminative", 0) / sum(bands.values()), 3)
            if bands else None),
        "settings": {"judge_tolerance": JUDGE_TOLERANCE,
                     "cost_margin": cost_margin},
        "tasks": per_task,
    }
