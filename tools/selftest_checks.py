#!/usr/bin/env python3
"""Prove every deterministic check works in both directions.

A check that always passes makes its task worthless — it stops separating a
capable model from a cheap one, and nothing in a run's output says so. A check
that can never pass is worse: it looks like every model failed.

So each deterministic task carries a golden pair in
`tasks/<suite>/_golden/*.json`:

    {"code-py-easy-01":            "<a correct answer>",
     "__wrong__code-py-easy-01":   "<a plausible but wrong answer>"}

This tool runs the task's real check against both and demands PASS on the
correct one and FAIL on the wrong one. A task with no golden is reported as
unverified rather than assumed fine.

    python3 tools/selftest_checks.py                    # both suites
    python3 tools/selftest_checks.py --suite tasks/k
    python3 tools/selftest_checks.py --only tool-use    # one category

Chat-mode checks only. Agentic checks need a workspace an agent produced, so
they are verified by the agentic runs themselves.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from smartroute_bench.checks import run_chat_check  # noqa: E402
from smartroute_bench.runner import load_tasks      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITES = ["tasks/base", "tasks/k"]
WRONG = "__wrong__"


def load_goldens(suite_dir: str) -> dict[str, str]:
    """Goldens live with the public suite. The held-out split lives under
    internal/tasks/<suite>, so look there too — otherwise the tasks nobody may
    publish are also the tasks nobody verifies."""
    dirs = [suite_dir]
    tail = os.path.basename(suite_dir.rstrip(os.sep))
    if os.sep + "internal" + os.sep in suite_dir + os.sep or \
            suite_dir.startswith("internal"):
        dirs.append(os.path.join(ROOT, "tasks", tail))
    out: dict[str, str] = {}
    for d in dirs:
        for path in sorted(glob.glob(os.path.join(d, "_golden", "*.json"))):
            with open(path, encoding="utf-8") as f:
                out.update(json.load(f))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", action="append", default=None)
    ap.add_argument("--only", help="comma-separated categories")
    ap.add_argument("--list-missing", action="store_true",
                    help="print the task ids that still have no golden")
    args = ap.parse_args()

    only = {x.strip() for x in args.only.split(",")} if args.only else None
    failures, verified, missing = [], 0, []

    for suite in (args.suite or SUITES):
        path = os.path.join(ROOT, suite)
        if not os.path.isdir(path):
            continue
        goldens = load_goldens(path)
        tasks = [t for t in load_tasks(path, modes=("chat",))
                 if t.get("check") and (not only or t["category"] in only)]
        print(f"\n── {suite}: {len(tasks)} deterministic chat tasks, "
              f"{len(goldens) // 2} golden pairs")

        for t in sorted(tasks, key=lambda x: x["id"]):
            tid = t["id"]
            good, bad = goldens.get(tid), goldens.get(WRONG + tid)
            if good is None and bad is None:
                missing.append(f"{suite}:{tid}")
                continue
            if good is not None:
                r = run_chat_check(t, good)
                if not r or not r.get("passed"):
                    failures.append(
                        f"{suite}:{tid} — the CORRECT answer does not pass: "
                        f"{(r or {}).get('detail', 'check did not run')[:160]}")
                else:
                    verified += 1
            if bad is not None:
                r = run_chat_check(t, bad)
                if r and r.get("passed"):
                    failures.append(
                        f"{suite}:{tid} — the WRONG answer also passes; this "
                        f"check cannot separate anything")
                else:
                    verified += 1
            else:
                missing.append(f"{suite}:{tid} (no {WRONG} control)")

    print("\n" + "─" * 72)
    print(f"verified {verified} direction(s)")
    if missing:
        print(f"{len(missing)} task(s) unverified (no golden / no wrong control)")
        if args.list_missing:
            for m in missing:
                print("  ·", m)
    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for f in failures:
            print("  ✗", f)
        return 1
    print("every golden-covered check passes the correct answer and rejects the wrong one")
    return 0


if __name__ == "__main__":
    sys.exit(main())
