"""Run objective checks over chat-mode responses.

    python3 -m smartroute_bench.run_checks --run-id r1

Writes results/<run-id>/checks.jsonl with one line per (task, arm) that has a
check defined. Agentic checks are run by the agentic runner at session end and
merged separately.
"""
from __future__ import annotations

import argparse
import json
import os

from .checks import run_chat_check
from .runner import load_tasks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--tasks", default="tasks/base")
    args = ap.parse_args()

    tasks = {t["id"]: t for t in load_tasks(args.tasks, modes=("chat",))}
    resp_path = os.path.join("results", args.run_id, "responses.jsonl")
    out_path = os.path.join("results", args.run_id, "checks.jsonl")

    rows = []
    with open(resp_path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            t = tasks.get(r["task_id"])
            if not t or not t.get("check"):
                continue
            if not r.get("ok"):
                res = {"ran": False, "passed": None, "detail": "response failed"}
            else:
                res = run_chat_check(t, r["final_text"])
            rows.append({"task_id": r["task_id"], "arm_id": r["arm_id"], **(res or {})})
            flag = {True: "PASS", False: "FAIL", None: "SKIP"}[res.get("passed") if res else None]
            print(f"  {r['task_id']:<22} {r['arm_id']:<8} {flag}")

    with open(out_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    n_pass = sum(1 for r in rows if r.get("passed"))
    print(f"done: {n_pass}/{len(rows)} pass -> {out_path}")


if __name__ == "__main__":
    main()
