#!/usr/bin/env python3
"""Build the score matrix from one or more pool runs.

A "pool run" is an ordinary run whose arms are the router's *candidate models*,
one arm per concrete model, instead of the usual four comparison arms. Its
analysis.json therefore already holds every number the oracle metrics need —
pass/fail, judge mean, cost, latency, per task per model. This tool reshapes it
into task -> model -> entry and records what it was built from.

    python3 tools/build_score_matrix.py --from-run pool-v1 [--from-run pool-v1b]
    python3 tools/build_score_matrix.py --from-run pool-v1 --out results/_matrix/pool-v1.json

Merging several runs is supported because a full pool sweep is usually done in
pieces (rate limits, a model added later). Later runs win on conflict, and every
source is listed in the output so a mixed-vintage matrix is visible rather than
implied.

The matrix is a claim about a moment: prices change, models get swapped behind a
name, and a routing policy shipped yesterday invalidates nothing here but does
change what the router will be compared against. So `built_from` carries the run
ids and the pool, and src/smartroute_bench/oracle.py counts any routed model the
matrix never measured instead of quietly narrowing its own scope.
"""
from __future__ import annotations

import argparse
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# what the oracle needs from each cell; anything else is noise in a file that
# gets read by eye during a regression hunt
CELL_KEYS = ("check_passed", "judge_mean", "cost_krw", "latency_ms", "delivery",
             "ok")


def load_analysis(run_id: str) -> dict:
    path = os.path.join(ROOT, "results", run_id, "analysis.json")
    if not os.path.exists(path):
        raise SystemExit(f"no analysis for run {run_id!r} at {path} — run "
                         f"`analyze --run-id {run_id}` first")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-run", action="append", required=True, dest="runs")
    ap.add_argument("--out", default=None)
    ap.add_argument("--tasks-label", default=None,
                    help="note which suite this was measured on (e.g. tasks/k)")
    args = ap.parse_args()

    tasks: dict[str, dict] = {}
    models: list[str] = []
    built_from = []

    for run_id in args.runs:
        a = load_analysis(run_id)
        arm_model = {arm["id"]: arm["model"] for arm in a["arms"]}
        virtual = [m for m in arm_model.values() if "/" not in m]
        if virtual:
            # A routing pseudo-model resolves per call, so its column is not a
            # model's score — it would make the oracle compare the router with
            # itself.
            raise SystemExit(
                f"run {run_id} contains non-concrete arm model(s) {virtual}; a "
                f"pool run must pin one real model per arm")
        for m in arm_model.values():
            if m not in models:
                models.append(m)
        n_cells = 0
        for row in a["tasks"]:
            cell = tasks.setdefault(row["task_id"], {})
            for aid, entry in (row.get("arms") or {}).items():
                model = arm_model.get(aid)
                if model is None:
                    continue
                keep = {k: entry.get(k) for k in CELL_KEYS if k in entry}
                keep["resolved_model"] = model
                cell[model] = keep
                n_cells += 1
        built_from.append({"run_id": run_id, "arms": sorted(arm_model.values()),
                           "n_tasks": len(a["tasks"]), "n_cells": n_cells})
        print(f"  {run_id}: {len(a['tasks'])} tasks x "
              f"{len(arm_model)} models = {n_cells} cells")

    out = args.out or os.path.join(ROOT, "results", "_matrix",
                                   f"{'+'.join(args.runs)}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    matrix = {"models": models, "built_from": built_from,
              "tasks_label": args.tasks_label, "tasks": tasks}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(matrix, f, ensure_ascii=False, indent=1)

    # Coverage is the number to look at before trusting anything downstream: a
    # task measured on two of five models still produces an "oracle", just a
    # much weaker one.
    full = sum(1 for c in tasks.values() if len(c) == len(models))
    print(f"-> {os.path.relpath(out, ROOT)}")
    print(f"   {len(tasks)} tasks x {len(models)} models · "
          f"{full} task(s) measured on the full pool "
          f"({100.0 * full / len(tasks):.0f}%)" if tasks else "   empty")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
