#!/usr/bin/env python3
"""Agentic-mode runner — BizCoder CLI adapter.

Agentic tasks are executed by a coding-agent CLI in a sandboxed workspace
rather than through the chat API. This adapter drives the BizCoder CLI; to
use a different agent CLI, copy this file and swap the subprocess invocation
and the session-cost lookup (steps 2–3 below).

For each (agentic task, arm):
  1. copy the task fixture into a fresh sandbox workspace
  2. `bizcoder run -m bizrouter/<model> --auto "<prompt>"` in that workspace
  3. find the CLI session id (local sqlite, keyed by workspace directory)
  4. run the task's objective check against the workspace
  5. append a response-shaped record to results/<run-id>/responses.jsonl
     (session_id = CLI session id, so gateway-side cost collection can join)

Run from repo root:
    python3 adapters/agentic_bizcoder.py --run-id r1 [--only id,id] [--arms configs/arms.json]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from smartroute_bench.checks import run_agentic_check  # noqa: E402
from smartroute_bench.runner import (  # noqa: E402
    append_attempt_event, load_arms, load_tasks, new_attempt_id)

BIZCODER_DB = os.path.expanduser("~/.local/share/bizcoder/opencode.db")
SESSION_TIMEOUT = 1500  # seconds per agentic session


def find_session(directory: str, started_after_ms: int) -> dict | None:
    con = sqlite3.connect(f"file:{BIZCODER_DB}?mode=ro", uri=True)
    try:
        row = con.execute(
            """SELECT id, title, cost, tokens_input, tokens_output FROM session
               WHERE directory = ? AND time_created >= ?
               ORDER BY time_created DESC LIMIT 1""",
            (directory, started_after_ms),
        ).fetchone()
    finally:
        con.close()
    if not row:
        return None
    return {"id": row[0], "title": row[1], "local_cost": row[2],
            "in": row[3], "out": row[4]}


def run_one(task: dict, arm: dict, run_id: str, out_path: str, tasks_dir: str) -> dict:
    fixture = task["check"]["fixture"]
    ws = os.path.join(ROOT, "sandbox", run_id, f"{task['id']}--{arm['id']}")
    if os.path.exists(ws):
        shutil.rmtree(ws)
    src = os.path.join(tasks_dir, "fixtures", fixture)
    shutil.copytree(src, ws)
    # keep the agent from wandering into the parent git repo
    subprocess.run(["git", "init", "-q"], cwd=ws, check=False)

    prompt = task["turns"][0]
    t0_ms = int(time.time() * 1000) - 2000
    t0 = time.time()
    attempt_id = new_attempt_id(run_id, task["id"], arm["id"])
    # write-ahead: if this process dies while the CLI is spending money, the
    # start event (with the workspace dir) is what lets the paid session be
    # recovered from the CLI's local sqlite and reconciled against the gateway
    append_attempt_event(os.path.dirname(out_path), {
        "event": "start", "attempt_id": attempt_id, "task_id": task["id"],
        "arm_id": arm["id"], "mode": "agentic",
        "requested_model": f"bizrouter/{arm['model']}", "workspace": ws,
        "started_after_ms": t0_ms})
    try:
        p = subprocess.run(
            ["bizcoder", "run", "-m", f"bizrouter/{arm['model']}", "--auto", prompt],
            cwd=ws, capture_output=True, text=True, timeout=SESSION_TIMEOUT,
            # stdin must be closed explicitly: inherited from a detached shell it
            # is an open pipe with no writer, and the CLI blocks on it forever —
            # the session never starts, so it burns the full SESSION_TIMEOUT and
            # records a failure that looks like a gateway hang
            stdin=subprocess.DEVNULL,
        )
        tail = (p.stdout or "")[-3000:]
        rc = p.returncode
    except subprocess.TimeoutExpired as e:
        tail = ((e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes)
                else (e.stdout or ""))[-3000:]
        rc = -1
    wall_ms = int((time.time() - t0) * 1000)

    sess = find_session(ws, t0_ms)
    check = run_agentic_check(task, ws)

    rec = {
        "task_id": task["id"], "arm_id": arm["id"],
        "attempt_id": attempt_id,
        "requested_model": f"bizrouter/{arm['model']}",
        "resolved_model": None,  # gateway meters agentic route calls without
                                 # routed-model metadata; joins by session_id
        "session_id": sess["id"] if sess else None,
        "category": task["category"], "difficulty": task["difficulty"],
        "language": task.get("language"), "mode": "agentic",
        "ok": rc == 0 and sess is not None,
        "error": None if rc == 0 else f"exit={rc}",
        "turns": [{"turn": 0, "ok": rc == 0, "status": None, "error": None,
                   "text": tail, "model_reported": None, "routed_model": None,
                   "routed_provider": None, "in_tokens": sess["in"] if sess else 0,
                   "out_tokens": sess["out"] if sess else 0,
                   "cost_reported": None, "latency_ms": wall_ms, "usage": None}],
        "routed_models": [],
        "cost_reported_sum": 0.0,
        "latency_ms_sum": wall_ms,
        "in_tokens_sum": sess["in"] if sess else 0,
        "out_tokens_sum": sess["out"] if sess else 0,
        "final_text": tail,
        "agentic_check": check,
        "workspace": ws,
        "ts": time.time(),
    }
    with open(out_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    append_attempt_event(os.path.dirname(out_path), {
        "event": "end", "attempt_id": attempt_id, "task_id": task["id"],
        "arm_id": arm["id"], "ok": rec["ok"], "session_id": rec["session_id"]})
    flag = "?" if not check else ("PASS" if check["passed"] else "FAIL")
    print(f"  [{arm['id']:>8}] {task['id']:<22} exit={rc} check={flag} "
          f"session={rec['session_id']} wall={wall_ms//1000}s", flush=True)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--arms", default=os.path.join(ROOT, "configs", "arms.json"))
    ap.add_argument("--tasks", default=os.path.join(ROOT, "tasks", "base"))
    ap.add_argument("--only", default=None)
    ap.add_argument("--arm", default=None, help="run a single arm id")
    ap.add_argument("--skip-done", action="store_true")
    args = ap.parse_args()

    cfg = load_arms(args.arms)
    tasks = load_tasks(args.tasks, modes=("agentic",))
    if args.only:
        only = set(args.only.split(","))
        tasks = [t for t in tasks if t["id"] in only]
    arms = [a for a in cfg["arms"] if not args.arm or a["id"] == args.arm]

    out_dir = os.path.join(ROOT, "results", args.run_id)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "responses.jsonl")

    done = set()
    if args.skip_done and os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if r.get("mode") == "agentic" and r.get("session_id"):
                        done.add((r["task_id"], r["arm_id"]))
                except Exception:  # noqa: BLE001
                    pass

    jobs = [(t, a) for t in tasks for a in arms if (t["id"], a["id"]) not in done]
    print(f"{len(jobs)} agentic sessions to run ({len(done)} done)")
    for t, a in jobs:
        run_one(t, a, args.run_id, out_path, args.tasks)


if __name__ == "__main__":
    main()
