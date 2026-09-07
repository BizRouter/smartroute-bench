"""Aggregate benchmark outputs into a single analysis.json.

Inputs (results/<run-id>/):
  responses.jsonl   — runner + agentic runner output
  checks.jsonl      — objective check results (chat mode)
  judgments.jsonl   — LLM judge scores
  server_costs.json — authoritative per-session costs (optional; falls back to
                      client-observed usage.cost when absent)

    python3 -m smartroute_bench.analyze --run-id r1
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
from collections import defaultdict

from . import oracle as oracle_mod
from .runner import load_arms, load_tasks

ROUTE_VIRTUAL_MODEL = "bizrouter/route"  # smart-routing alias, not a concrete model


def _load_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(st.mean(xs), 3) if xs else None


def _delivery(r):
    """Delivery status of a chat response: did a complete answer reach the
    caller? "refusal" needs the per-turn `finish` (stop_reason) field; records
    captured before that field existed fall back to text-shape heuristics
    (empty body, unclosed code fence = generation cut mid-stream)."""
    if r.get("mode", "chat") != "chat" or not r.get("ok"):
        return None
    if any(t.get("finish") == "refusal" for t in r.get("turns") or []):
        return "refusal"
    text = (r.get("final_text") or "").strip()
    if not text:
        return "empty"
    if text.count("```") % 2 == 1:
        return "truncated"
    return "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--tasks", default="tasks/base")
    ap.add_argument("--matrix", default=None,
                    help="score matrix from tools/build_score_matrix.py; adds "
                         "oracle-referenced metrics (over/under-routing)")
    ap.add_argument("--route-arm", default="route",
                    help="which arm to score against the oracle")
    ap.add_argument("--arms", default="configs/arms.json")
    args = ap.parse_args()

    d = os.path.join("results", args.run_id)
    responses = _load_jsonl(os.path.join(d, "responses.jsonl"))
    checks = _load_jsonl(os.path.join(d, "checks.jsonl"))
    judgments = _load_jsonl(os.path.join(d, "judgments.jsonl"))
    server_costs = {}
    sc_path = os.path.join(d, "server_costs.json")
    if os.path.exists(sc_path):
        server_costs = json.load(open(sc_path, encoding="utf-8"))

    cfg = load_arms(args.arms)
    arm_ids = [a["id"] for a in cfg["arms"]]
    arm_models = {a["id"]: a["model"] for a in cfg["arms"]}
    tasks = {t["id"]: t for t in load_tasks(args.tasks, modes=("chat", "agentic"))}

    # attempt ledger: every runner line is one attempt — failures and retries
    # included. Nothing is dropped; the "final" record below only picks which
    # answer text/judgment represents the cell, never which costs count.
    attempts = defaultdict(list)
    for r in responses:
        attempts[(r["task_id"], r["arm_id"])].append(r)

    # final record per (task, arm): the latest ok attempt, else the latest
    resp = {}
    for r in responses:
        key = (r["task_id"], r["arm_id"])
        if key not in resp or (r.get("ok") and not resp[key].get("ok")) \
           or (r.get("ok") == resp[key].get("ok") and r.get("ts", 0) >= resp[key].get("ts", 0)):
            resp[key] = r

    def attributed_model_of(r, aid):
        """Concrete model this attempt is attributable to, or None.

        Fixed arms pin the model in the request itself. The route arm is
        attributable only when every observed turn carries the same
        x-bizrouter-routed-model header; agentic route sessions expose no
        per-call routed-model metadata, so they are never attributable."""
        model = arm_models.get(aid)
        if model and model != ROUTE_VIRTUAL_MODEL:
            return model
        if r.get("mode", "chat") != "chat":
            return None
        turns = r.get("turns") or []
        routed = {t.get("routed_model") for t in turns}
        if turns and None not in routed and len(routed) == 1:
            return routed.pop()
        return None

    check_map = {(c["task_id"], c["arm_id"]): c for c in checks}
    for r in resp.values():  # agentic checks ride on the response record
        if r.get("agentic_check"):
            check_map[(r["task_id"], r["arm_id"])] = {
                "task_id": r["task_id"], "arm_id": r["arm_id"], **r["agentic_check"]}

    judge_map = defaultdict(dict)  # (task, arm) -> judge_id -> weighted
    for j in judgments:
        if j.get("ok"):
            judge_map[(j["task_id"], j["arm_id"])][j["judge_id"]] = j["judgment"]["weighted"]

    def cost_of(r):
        sid = r.get("session_id")
        if sid and sid in server_costs:
            return server_costs[sid]["cost_krw"], "server"
        if r.get("cost_reported_sum"):
            return r["cost_reported_sum"], "client"
        return None, None

    def cost_of_attempts(atts):
        """Total spend across every attempt of a cell — retries and failures
        included. Server-metered sessions are counted once per session (chat
        retries share a deterministic session id, so the session total already
        covers the failed calls); attempts the server never saw fall back to
        client-observed cost."""
        total, seen_sids, bases = 0.0, set(), set()
        for a in atts:
            sid = a.get("session_id")
            if sid and sid in server_costs:
                if sid not in seen_sids:
                    seen_sids.add(sid)
                    total += server_costs[sid]["cost_krw"]
                    bases.add("server")
            elif a.get("cost_reported_sum"):
                total += a["cost_reported_sum"]
                bases.add("client")
        if not bases:
            return None, None
        return total, ("server" if bases == {"server"} else "+".join(sorted(bases)))

    def cache_read_of(r):
        sid = r.get("session_id")
        if sid and sid in server_costs:
            calls = server_costs[sid]["calls"]
            return (sum(c.get("cache_read") or 0 for c in calls),
                    sum((c.get("in") or 0) for c in calls), len(calls))
        return (0, r.get("in_tokens_sum", 0), len(r.get("turns", [])))

    # ── per-task rows ──────────────────────────────────────────────────────
    # Only tasks the run actually measured: the task set can grow after a run,
    # and phantom all-empty rows would misstate the run's size everywhere.
    measured = {r["task_id"] for r in responses}
    rows = []
    for tid, t in sorted(tasks.items()):
        if tid not in measured:
            continue
        row = {"task_id": tid, "category": t["category"],
               "difficulty": t["difficulty"], "language": t.get("language"),
               "mode": t.get("mode", "chat"), "arms": {}}
        for aid in arm_ids:
            r = resp.get((tid, aid))
            if not r:
                continue
            atts = attempts.get((tid, aid), [])
            cost, basis = cost_of_attempts(atts)
            cost_final, _ = cost_of(r)
            ch = check_map.get((tid, aid))
            judges = judge_map.get((tid, aid), {})
            cr, ci, ncalls = cache_read_of(r)
            row["arms"][aid] = {
                "ok": r.get("ok", False), "error": r.get("error"),
                "delivery": _delivery(r),
                "cost_krw": round(cost, 4) if cost is not None else None,
                "cost_basis": basis,
                "cost_krw_final_attempt": (round(cost_final, 4)
                                           if cost_final is not None else None),
                "attempt_n": len(atts),
                "failed_attempt_n": sum(1 for a in atts if not a.get("ok")),
                "attempt_models": [attributed_model_of(a, aid) for a in atts],
                # The single concrete model this task is attributable to, or
                # None when the turns disagreed. The oracle metrics need one
                # choice per task and must not invent one: a multi-turn session
                # that used two models has no single "chosen model".
                "resolved_model": attributed_model_of(r, aid),
                "judge": {k: v for k, v in judges.items()},
                "judge_mean": _mean(list(judges.values())),
                "check_passed": ch.get("passed") if ch else None,
                "check_detail": (ch.get("detail") or "")[:300] if ch else None,
                "routed_models": r.get("routed_models") or [],
                "server_models": ([c["model"] for c in server_costs.get(r.get("session_id"), {}).get("calls", [])]
                                  if r.get("session_id") in server_costs else []),
                "latency_ms": r.get("latency_ms_sum"),
                "in_tokens": r.get("in_tokens_sum"), "out_tokens": r.get("out_tokens_sum"),
                "cache_read": cr, "n_calls": ncalls,
            }
        rows.append(row)

    # ── aggregates ─────────────────────────────────────────────────────────
    def agg(filter_fn):
        out = {}
        for aid in arm_ids:
            cells = [(row, row["arms"][aid]) for row in rows
                     if aid in row["arms"] and filter_fn(row)]
            costs = [c["cost_krw"] for _, c in cells if c["cost_krw"] is not None]
            # judge scores only count for chat-mode rows: agentic sessions expose
            # only a terminal-log tail to the judge (not the artifact), and failed
            # sessions are skipped — mixing those in would bias against whichever
            # arms completed. Agentic quality = deterministic checks.
            judges = [c["judge_mean"] for row, c in cells
                      if c["judge_mean"] is not None and row["mode"] == "chat"]
            # same aggregation minus delivery failures (refusal / empty /
            # truncated): "what the model can do" vs the headline "what the
            # caller actually received". Both are reported.
            judges_delivered = [c["judge_mean"] for row, c in cells
                                if c["judge_mean"] is not None and row["mode"] == "chat"
                                and c.get("delivery") == "ok"]
            ch = [c["check_passed"] for _, c in cells if c["check_passed"] is not None]
            lat = [c["latency_ms"] for _, c in cells if c["latency_ms"]]
            jb = {}
            for row, c in cells:
                if row["mode"] == "chat":
                    for jid, v in (c.get("judge") or {}).items():
                        jb.setdefault(jid, []).append(v)
            # attempt-inclusive score inputs (chat): every attempt is a score
            # input — an attempt that produced no judged answer scores 0, and
            # so does an undelivered final answer (refusal/empty) that left the
            # judges nothing to score. Only a *delivered* answer that was never
            # judged is a real accounting gap (judge_unscored_cells).
            score_inputs, unscored = [], 0
            for row, c in cells:
                if row["mode"] != "chat" or not c["attempt_n"]:
                    continue
                if c["ok"] and c["judge_mean"] is None and c["delivery"] == "ok":
                    unscored += 1
                    continue
                final_score = (c["judge_mean"] or 0.0) if c["ok"] else 0.0
                score_inputs += [0.0] * (c["attempt_n"] - 1) + [final_score]
            attribution = defaultdict(int)
            for _, c in cells:
                for m in c["attempt_models"]:
                    attribution[m or "_unattributed"] += 1
            out[aid] = {
                "attempt_n": sum(c["attempt_n"] for _, c in cells),
                "failed_attempt_n": sum(c["failed_attempt_n"] for _, c in cells),
                "judge_mean_attempt_incl": _mean(score_inputs),
                "judge_score_input_n": len(score_inputs),
                "judge_unscored_cells": unscored,
                "attribution": dict(attribution),
                "judge_by": {jid: _mean(vs) for jid, vs in jb.items()},
                "n": len(cells),
                "cost_total": round(sum(costs), 2) if costs else 0,
                "cost_total_final_attempt": round(
                    sum(c["cost_krw_final_attempt"] for _, c in cells
                        if c["cost_krw_final_attempt"] is not None), 2),
                "cost_mean": _mean(costs),
                "judge_mean": _mean(judges),
                "judge_mean_delivered": _mean(judges_delivered),
                "delivery_fail_n": sum(1 for row, c in cells
                                       if c.get("delivery") not in (None, "ok")),
                "check_pass": (round(sum(1 for x in ch if x) / len(ch), 3) if ch else None),
                "check_n": len(ch),
                "latency_mean_ms": int(st.mean(lat)) if lat else None,
                "fail_n": sum(1 for _, c in cells if not c["ok"]),
            }
        return out

    overall = agg(lambda r: True)
    by_category = {cat: agg(lambda r, c=cat: r["category"] == c)
                   for cat in sorted({r["category"] for r in rows})}
    by_difficulty = {dd: agg(lambda r, x=dd: r["difficulty"] == x)
                     for dd in ("easy", "medium", "hard")}
    by_mode = {m: agg(lambda r, x=m: r["mode"] == x) for m in ("chat", "agentic")}

    # ── routing distribution (route arm) ───────────────────────────────────
    route_dist = defaultdict(lambda: defaultdict(int))
    for row in rows:
        c = row["arms"].get("route")
        if not c:
            continue
        models = c["routed_models"] or (
            [m for m in c["server_models"] if m != "bizrouter/route"])
        for m in models:
            route_dist["overall"][m] += 1
            route_dist[f"cat:{row['category']}"][m] += 1
            route_dist[f"diff:{row['difficulty']}"][m] += 1

    # ── judge agreement (pairwise across all judges) ──────────────────────
    judge_ids = sorted({j for js in judge_map.values() for j in js})
    agreement = None
    if len(judge_ids) >= 2:
        pairwise = []
        for i in range(len(judge_ids)):
            for k in range(i + 1, len(judge_ids)):
                a, b = [], []
                for js in judge_map.values():
                    if judge_ids[i] in js and judge_ids[k] in js:
                        a.append(js[judge_ids[i]])
                        b.append(js[judge_ids[k]])
                if len(a) > 2:
                    try:
                        corr = round(st.correlation(a, b), 3)
                    except st.StatisticsError:
                        corr = None
                    pairwise.append({
                        "judges": [judge_ids[i], judge_ids[k]], "n": len(a),
                        "mean_abs_diff": _mean([abs(x - y) for x, y in zip(a, b)]),
                        "pearson": corr})
        if pairwise:
            agreement = {
                "n": max(p["n"] for p in pairwise),
                "mean_abs_diff": _mean([p["mean_abs_diff"] for p in pairwise]),
                "pearson": _mean([p["pearson"] for p in pairwise if p["pearson"] is not None]),
                "pairwise": pairwise,
            }
    # per-judge overall mean (chat rows), for the judge-bias table
    per_judge = {}
    for jid in judge_ids:
        vals = [js[jid] for (tid, aid), js in judge_map.items() if jid in js]
        per_judge[jid] = _mean(vals)

    # ── flags: potential mis-routes / notable cases ───────────────────────
    flags = []
    for row in rows:
        rc = row["arms"].get("route")
        if not rc:
            continue
        fixed = [row["arms"][a] for a in arm_ids if not a.startswith("route") and a in row["arms"]]
        if not fixed:
            continue
        routed = rc["routed_models"] or [m for m in rc["server_models"]
                                         if m != "bizrouter/route"]
        fixed_j = [f["judge_mean"] for f in fixed if f["judge_mean"] is not None]
        if rc.get("check_passed") is False and any(f.get("check_passed") for f in fixed):
            flags.append({"task_id": row["task_id"], "kind": "check_fail_vs_fixed",
                          "routed": routed})
        elif rc.get("judge_mean") is not None and fixed_j \
                and rc["judge_mean"] < min(fixed_j) - 1.5:
            flags.append({"task_id": row["task_id"], "kind": "quality_gap",
                          "route_judge": rc["judge_mean"], "fixed_min": min(fixed_j),
                          "routed": routed})

    delivery_failures = [
        {"task_id": row["task_id"], "arm_id": aid, "kind": c["delivery"]}
        for row in rows for aid, c in row["arms"].items()
        if c.get("delivery") not in (None, "ok")]

    # ── raw attempt ledger — one entry per runner line, nothing deduped ────
    # Client cost is per attempt; server-metered cost is per session (chat
    # retries share the session, so reconcile against server_costs.json).
    attempt_ledger = []
    for (tid, aid), atts in sorted(attempts.items()):
        for i, a in enumerate(atts):
            sid = a.get("session_id")
            attempt_ledger.append({
                "task_id": tid, "arm_id": aid, "attempt": i,
                "attempt_id": a.get("attempt_id"),
                "final": a is resp.get((tid, aid)),
                "ok": a.get("ok", False), "error": a.get("error"),
                "mode": a.get("mode", "chat"), "session_id": sid,
                "requested_model": a.get("requested_model") or arm_models.get(aid),
                "resolved_model": a.get("resolved_model")
                    if "resolved_model" in a else attributed_model_of(a, aid),
                "server_metered": bool(sid and sid in server_costs),
                "client_cost_krw": a.get("cost_reported_sum") or 0.0,
                "attributed_model": attributed_model_of(a, aid),
                "ts": a.get("ts"),
            })

    # orphaned attempts: a write-ahead `start` with no `end` and no response
    # record — the runner died while the CLI/gateway was already spending.
    # These carry real paid cost that no response record accounts for.
    attempt_events = _load_jsonl(os.path.join(d, "attempts.jsonl"))
    ended = {e.get("attempt_id") for e in attempt_events if e.get("event") == "end"}
    recorded = {a.get("attempt_id") for a in responses if a.get("attempt_id")}
    recovered_sid = {e.get("attempt_id"): e.get("session_id")
                     for e in attempt_events if e.get("event") == "recovered"}
    orphans = []
    for e in attempt_events:
        if e.get("event") != "start" or e.get("attempt_id") in ended \
           or e.get("attempt_id") in recorded:
            continue
        sid = recovered_sid.get(e.get("attempt_id"))
        orphans.append({
            "task_id": e["task_id"], "arm_id": e["arm_id"],
            "attempt_id": e.get("attempt_id"), "mode": e.get("mode"),
            "requested_model": e.get("requested_model"),
            "workspace": e.get("workspace"), "ts": e.get("ts"),
            "session_id": sid,
            "server_cost_krw": server_costs[sid]["cost_krw"]
                if sid and sid in server_costs else None,
        })

    attempt_accounting = {
        "deduplication": "none",
        "failed_attempts_included": True,
        "failed_attempt_costs_included": True,
        "orphaned_attempts": orphans,
        "orphaned_cost_krw_total": round(sum(
            o["server_cost_krw"] or 0 for o in orphans), 4),
        "by_arm": {aid: {
            "attempt_n": overall[aid]["attempt_n"],
            "failed_attempt_n": overall[aid]["failed_attempt_n"],
            "cost_total": overall[aid]["cost_total"],
            "cost_total_final_attempt": overall[aid]["cost_total_final_attempt"],
        } for aid in arm_ids},
    }

    # ── oracle-referenced metrics (optional) ───────────────────────────────
    # Without a matrix these stay absent rather than defaulting to something
    # that reads like a measurement.
    oracle_section = None
    if args.matrix:
        with open(args.matrix, encoding="utf-8") as f:
            matrix = json.load(f)
        route_rows = {row["task_id"]: row["arms"][args.route_arm]
                      for row in rows if args.route_arm in row["arms"]}
        if not route_rows:
            raise SystemExit(f"--matrix given but arm {args.route_arm!r} is not "
                             f"in this run; pass --route-arm")
        oracle_section = oracle_mod.evaluate(route_rows, matrix)
        oracle_section["matrix"] = {
            "path": os.path.basename(args.matrix),
            "models": matrix.get("models"),
            "built_from": matrix.get("built_from"),
            "tasks_label": matrix.get("tasks_label"),
        }

    analysis = {
        "run_id": args.run_id,
        "arms": cfg["arms"],
        "attempt_accounting": attempt_accounting,
        "attempt_ledger": attempt_ledger,
        "overall": overall,
        "delivery_failures": delivery_failures,
        "by_category": by_category,
        "by_difficulty": by_difficulty,
        "by_mode": by_mode,
        "route_distribution": {k: dict(v) for k, v in route_dist.items()},
        "judge_agreement": agreement,
        "per_judge_mean": per_judge,
        "flags": flags,
        "oracle": oracle_section,
        "tasks": rows,
    }
    out_path = os.path.join(d, "analysis.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=1)
    print(f"-> {out_path}")
    if oracle_section:
        o = oracle_section
        print(f"  oracle: compared {o['n_tasks_compared']} task(s) · "
              f"optimal {o['optimal_selection_pct']}% · "
              f"over-route {o['over_route_pct']}% · "
              f"under-route {o['under_route_pct']}% · "
              f"cost regret {o['cost_regret_x']}x · "
              f"discriminative {o['discriminative_share']}")
        if o["off_pool_routes"]:
            print(f"          off-pool routes (matrix never measured these): "
                  f"{o['off_pool_routes']}")
        if o["n_tasks_without_matrix"]:
            print(f"          {o['n_tasks_without_matrix']} task(s) absent from "
                  f"the matrix")
    for aid, o in overall.items():
        print(f"  {aid:>8}: n={o['n']} cost=₩{o['cost_total']:,} "
              f"(final-only=₩{o['cost_total_final_attempt']:,}) "
              f"judge={o['judge_mean']} (attempt-incl={o['judge_mean_attempt_incl']}, "
              f"delivered={o['judge_mean_delivered']}) check={o['check_pass']} "
              f"attempts={o['attempt_n']} failed={o['failed_attempt_n']} "
              f"undelivered={o['delivery_fail_n']}")


if __name__ == "__main__":
    main()
