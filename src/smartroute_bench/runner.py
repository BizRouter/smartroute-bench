"""Chat-mode benchmark runner.

Runs every chat-mode task against every arm, turn by turn (multi-turn tasks
feed the arm's own previous answers back as context). Writes one JSON line per
(task, arm) to results/<run_id>/responses.jsonl.

Usage:
    python3 -m smartroute_bench.runner --run-id r1 [--tasks tasks] [--arms configs/arms.json]
                                       [--only task_id,task_id] [--concurrency 4]

Environment: SRB_API_KEY must hold the gateway API key.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from .client import call_llm

_write_lock = threading.Lock()

ROUTE_VIRTUAL_MODELS = {"route"}  # gateway virtual models that resolve per call


def new_attempt_id(run_id: str, task_id: str, arm_id: str) -> str:
    """Stable per-attempt join key, carried into raw records, the attempt
    ledger and the X-BizRouter-Usage-Subject header of every gateway call."""
    return f"srb-{run_id}-{task_id}-{arm_id}-{uuid.uuid4().hex[:8]}"


def append_attempt_event(out_dir: str, event: dict) -> None:
    """Write-ahead attempt ledger: a `start` event is written before any paid
    call is made, an `end` event after the record lands in responses.jsonl.
    A start with no matching end marks an orphaned attempt whose gateway cost
    must still be accounted for (runner killed mid-flight)."""
    event = {"ts": time.time(), **event}
    with _write_lock:
        with open(os.path.join(out_dir, "attempts.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_tasks(tasks_dir: str, modes=("chat",)) -> list[dict]:
    """Load a suite. `tasks_dir` may be a comma-separated list, which is how the
    held-out split is pulled in for an internal run:

        --tasks tasks/k                          # public dev split only
        --tasks tasks/k,internal/tasks/k         # dev + held-out test

    Sub-directories are searched too, so generated slices (capability/) land
    without a config change. Duplicate ids across dirs are an error rather than
    a silent last-one-wins."""
    tasks: list[dict] = []
    seen: dict[str, str] = {}
    for d in [x.strip() for x in tasks_dir.split(",") if x.strip()]:
        for path in sorted(glob.glob(os.path.join(d, "**", "*.jsonl"),
                                     recursive=True)):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    t = json.loads(line)
                    if t["id"] in seen:
                        raise SystemExit(
                            f"duplicate task id {t['id']} in {path} "
                            f"(already loaded from {seen[t['id']]})")
                    seen[t["id"]] = path
                    if t.get("mode", "chat") in modes:
                        tasks.append(t)
    return tasks


def load_arms(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_one(task: dict, arm: dict, cfg: dict, run_id: str, api_key: str,
            attempt_id: str | None = None) -> dict:
    session_id = f"srb-{run_id}-{task['id']}-{arm['id']}"
    requested_model = arm["model"]
    messages: list[dict] = []
    turns_out = []
    total_cost_reported = 0.0
    routed_models = []
    ok = True
    error = None

    for i, user_turn in enumerate(task["turns"]):
        messages.append({"role": "user", "content": user_turn})
        rec = call_llm(
            base_url=cfg["base_url"], api_key=api_key, wire=arm["wire"],
            model=requested_model, messages=messages, system=task.get("system"),
            max_tokens=task.get("max_tokens", 4096), session_id=session_id,
            attempt_id=attempt_id,
        )
        # resolved = what actually served the call: the gateway's routed-model
        # header for virtual models, the pinned model itself otherwise
        resolved = rec["routed_model"] if requested_model in ROUTE_VIRTUAL_MODELS \
            else (rec["routed_model"] or requested_model)
        turns_out.append({
            "turn": i, "ok": rec["ok"], "status": rec["status"], "error": rec["error"],
            "text": rec["text"], "model_reported": rec["model_reported"],
            "requested_model": requested_model, "resolved_model": resolved,
            "routed_model": rec["routed_model"], "routed_provider": rec["routed_provider"],
            "gateway_meta": rec.get("gateway_meta"),
            "in_tokens": rec["in_tokens"], "out_tokens": rec["out_tokens"],
            "cost_reported": rec["cost_reported"], "latency_ms": rec["latency_ms"],
            "usage": rec["usage"], "finish": rec.get("finish"),
        })
        if not rec["ok"]:
            ok, error = False, rec["error"]
            break
        if rec["cost_reported"] is not None:
            total_cost_reported += rec["cost_reported"]
        if rec["routed_model"]:
            routed_models.append(rec["routed_model"])
        messages.append({"role": "assistant", "content": rec["text"]})

    return {
        "task_id": task["id"], "arm_id": arm["id"], "session_id": session_id,
        "attempt_id": attempt_id, "requested_model": requested_model,
        "category": task["category"], "difficulty": task["difficulty"],
        "language": task.get("language"), "mode": task.get("mode", "chat"),
        "ok": ok, "error": error, "turns": turns_out,
        "routed_models": routed_models,
        "cost_reported_sum": round(total_cost_reported, 6),
        "latency_ms_sum": sum(t["latency_ms"] for t in turns_out),
        "in_tokens_sum": sum(t["in_tokens"] for t in turns_out),
        "out_tokens_sum": sum(t["out_tokens"] for t in turns_out),
        "final_text": turns_out[-1]["text"] if turns_out else "",
        "refusal": any(t.get("finish") == "refusal" for t in turns_out),
        "ts": time.time(),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--tasks", default="tasks/base")
    ap.add_argument("--arms", default="configs/arms.json")
    ap.add_argument("--only", default=None, help="comma-separated task ids")
    ap.add_argument("--skip-done", action="store_true",
                    help="skip (task, arm) pairs already in responses.jsonl")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    api_key = os.environ.get("SRB_API_KEY")
    if not api_key:
        raise SystemExit("SRB_API_KEY not set")

    cfg = load_arms(args.arms)
    tasks = load_tasks(args.tasks)
    if args.only:
        only = set(args.only.split(","))
        tasks = [t for t in tasks if t["id"] in only]

    out_dir = os.path.join("results", args.run_id)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "responses.jsonl")

    done = set()
    if args.skip_done and os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if r.get("ok"):
                        done.add((r["task_id"], r["arm_id"]))
                except Exception:  # noqa: BLE001
                    pass

    jobs = [(t, a) for t in tasks for a in cfg["arms"]
            if (t["id"], a["id"]) not in done]
    print(f"{len(tasks)} tasks x {len(cfg['arms'])} arms -> {len(jobs)} runs "
          f"({len(done)} already done)")

    def work(t, a):
        attempt_id = new_attempt_id(args.run_id, t["id"], a["id"])
        append_attempt_event(out_dir, {
            "event": "start", "attempt_id": attempt_id, "task_id": t["id"],
            "arm_id": a["id"], "mode": "chat", "requested_model": a["model"]})
        r = run_one(t, a, cfg["endpoint"], args.run_id, api_key, attempt_id=attempt_id)
        with _write_lock:
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        append_attempt_event(out_dir, {
            "event": "end", "attempt_id": attempt_id, "task_id": t["id"],
            "arm_id": a["id"], "ok": r["ok"], "session_id": r["session_id"]})
        flag = "ok" if r["ok"] else f"FAIL({r['error']})"
        if r.get("refusal"):
            flag += " REFUSAL"
        print(f"  [{r['arm_id']:>8}] {r['task_id']:<22} {flag} "
              f"cost={r['cost_reported_sum']} routed={r['routed_models'] or '-'}")
        return r

    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = [ex.submit(work, t, a) for t, a in jobs]
        n_ok = sum(1 for f in as_completed(futs) if f.result()["ok"])
    print(f"done: {n_ok}/{len(jobs)} ok -> {out_path}")


if __name__ == "__main__":
    main()
