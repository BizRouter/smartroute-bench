#!/usr/bin/env python3
"""Evolve cheap-sufficient tasks into harder, still-verifiable descendants.

Why. The score matrix says most of the work track is `cheap-sufficient`: every
pool model passes, so the task carries no information about routing. The B2
gate wants >=40% `discriminative`; v12 measured 13.8%. Writing harder tasks by
hand is slow and the author's difficulty guess is usually wrong (that is why
bands are measured, not declared).

What. This is a small implementation of *environment evolution* (Fan et al.,
arXiv 2609.04128, Tencent Hunyuan, Sep 2026) for chat-mode benchmark tasks.
A task is treated as a sequence of (scenario, skill) steps. Each generation
edits that sequence along ONE of three directions the paper derives from the
multi-turn learning objective:

    length    insert extra scenario-skill steps (more dependencies to satisfy)
    scenario  replace one scenario, keep its skill (novel setting, same skill)
    skill     replace one skill, keep the scenario (rarer technique required)

and the result must survive the same gates the paper uses, mapped onto our
check/golden conventions:

    oracle       the new golden answer PASSES the new check (both languages)
    invalid      a plausible wrong answer FAILS it, and so does an empty reply
    harder       the SEED's correct answer FAILS the new check — the edit
                 actually raised the bar instead of rewording the prompt
    rubric       an LLM reviewer (different vendor from the synthesiser) checks
                 the plan before, and the task after, against a fixed rubric
    crossover    the two cheapest pool models attempt it once each; if both
                 still pass in both languages the child is not discriminative
                 yet and (budget permitting) evolves one more generation

Off-policy in the paper's sense: nothing here reads the router. Difficulty is a
property of the task, calibrated afterwards by the full pool matrix as usual
(tools/build_score_matrix.py), so the oracle metrics keep one definition.

Outputs
    tasks/_src/evolved.json                     bilingual source records
    tasks/{k,base}/_golden/evolved.json         golden + __wrong__ controls
    internal/evolution/<seed>.jsonl             every call, verdict, cost
    internal/evolution/summary.json             accept/reject table + spend

Then, as for any authored task:
    python3 tools/author_tasks.py && python3 tools/selftest_checks.py
    python3 tools/validate_suite.py --warn-only

Usage
    python3 tools/evolve_tasks.py --seeds cw-py-roman-01,dbg-py-page-01
    python3 tools/evolve_tasks.py --category code-write --max-seeds 6
    python3 tools/evolve_tasks.py --category code-write --budget-krw 15000

Spend is capped by --budget-krw (client-reported cost; the gateway's own
metering is the source of truth and is reconciled in the report).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import glob
import json
import os
import random
import re
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smartroute_bench.checks import run_chat_check  # noqa: E402
from smartroute_bench.client import call_llm  # noqa: E402

SRC_DIR = os.path.join(ROOT, "tasks", "_src")
OUT_SRC = os.path.join(SRC_DIR, "evolved.json")
OUT_GOLD = {"ko": os.path.join(ROOT, "tasks", "k", "_golden", "evolved.json"),
            "en": os.path.join(ROOT, "tasks", "base", "_golden", "evolved.json")}
LOG_DIR = os.path.join(ROOT, "internal", "evolution")

def _base_url() -> str:
    # same endpoint the runner uses (configs/arms.json); SRB_BASE_URL overrides
    env = os.environ.get("SRB_BASE_URL")
    if env:
        return env
    with open(os.path.join(ROOT, "configs", "arms.json"), encoding="utf-8") as f:
        return json.load(f)["endpoint"]["base_url"]


BASE_URL = _base_url()
# Synthesiser and reviewer are different vendors on purpose: a model reviewing
# its own plan shares its blind spots. Probes are the two cheapest pool models
# (by measured cost in results/_matrix/poolk.json), which is what
# `cheap-sufficient` is defined against.
SYNTH = {"model": "anthropic/claude-opus-5", "wire": "messages"}
REVIEW = {"model": "openai/gpt-5.6-sol", "wire": "chat"}
PROBES = [{"model": "google/gemini-3.5-flash-lite", "wire": "chat"},
          {"model": "openai/gpt-5.6-luna", "wire": "chat"}]

DIRECTIONS = ("length", "scenario", "skill")
SUPPORTED_CHECKS = {"python_tests", "node_tests", "go_tests", "sql_result",
                    "rust_tests", "json_match", "constraints", "contains_all",
                    "answer_match"}

_lock = threading.Lock()
_spend = {"krw": 0.0, "calls": 0}
_tokens = {"in": 0, "out": 0}


# ── I/O ─────────────────────────────────────────────────────────────────────

def load(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_src() -> dict[str, dict]:
    recs = {}
    for path in sorted(glob.glob(os.path.join(SRC_DIR, "*.json"))):
        with open(path, encoding="utf-8") as f:
            for r in json.load(f):
                r["_src"] = os.path.relpath(path, ROOT)
                recs[r["id"]] = r
    return recs


def load_goldens() -> dict[str, dict[str, str]]:
    out = {"ko": {}, "en": {}}
    for side, suite in (("ko", "tasks/k"), ("en", "tasks/base")):
        for path in sorted(glob.glob(os.path.join(ROOT, suite, "_golden", "*.json"))):
            with open(path, encoding="utf-8") as f:
                out[side].update(json.load(f))
    return out


def atomic_write_json(path: str, data) -> None:
    # read-then-truncate loses the file if anything throws in between; write a
    # sibling and rename over it.
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def append_outputs(rec: dict, goldens: dict[str, dict[str, str]],
                   replace_id: str | None = None) -> None:
    """Add a descendant; optionally retire the cheap ancestor it supersedes
    (record and both golden controls) so the suite does not keep a task the
    pool already showed to be cheap-sufficient."""
    with _lock:
        cur = []
        if os.path.exists(OUT_SRC):
            with open(OUT_SRC, encoding="utf-8") as f:
                cur = json.load(f)
        cur = [r for r in cur if r["id"] not in (rec["id"], replace_id)] + [rec]
        atomic_write_json(OUT_SRC, cur)
        for side, path in OUT_GOLD.items():
            g = {}
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    g = json.load(f)
            if replace_id:
                g.pop(replace_id, None)
                g.pop(f"__wrong__{replace_id}", None)
            g.update(goldens[side])
            atomic_write_json(path, g)


def log_event(seed: str, ev: dict) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    ev = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), **ev}
    with _lock:
        with open(os.path.join(LOG_DIR, f"{seed}.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


# ── LLM plumbing ───────────────────────────────────────────────────────────

def stream_messages(*, api_key: str, model: str, prompt: str, system: str | None,
                    max_tokens: int, session_id: str, timeout: int = 1500) -> dict:
    """Streaming call on the Anthropic messages wire.

    The gateway aborts a non-streaming request whose first byte takes longer
    than 300 s, and a 30k-token generation from Opus 5 does. Streaming gets the
    first byte in seconds and keeps the connection alive for the rest."""
    import urllib.request
    url = BASE_URL.rstrip("/") + "/messages"
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
               "max_tokens": max_tokens, "stream": True}
    if system:
        payload["system"] = system
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                 "Accept": "text/event-stream", "anthropic-version": "2023-06-01",
                 "X-Session-Id": session_id}, method="POST")
    t0 = time.time()
    text, in_tok, out_tok, stop, err = [], 0, 0, None, None
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = resp.status
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                ev = json.loads(data)
            except json.JSONDecodeError:
                continue
            t = ev.get("type")
            if t == "message_start":
                in_tok = int(((ev.get("message") or {}).get("usage") or {}).get("input_tokens") or 0)
            elif t == "content_block_delta":
                d = ev.get("delta") or {}
                if d.get("type") == "text_delta":
                    text.append(d.get("text", ""))
            elif t == "message_delta":
                stop = (ev.get("delta") or {}).get("stop_reason") or stop
                out_tok = int((ev.get("usage") or {}).get("output_tokens") or out_tok)
            elif t == "error":
                err = json.dumps(ev.get("error"))[:300]
    if err:
        return {"ok": False, "status": status, "error": err, "text": "", "in_tokens": in_tok,
                "out_tokens": out_tok, "cost_reported": None, "finish": None}
    return {"ok": True, "status": status, "error": None, "text": "".join(text),
            "in_tokens": in_tok, "out_tokens": out_tok, "cost_reported": None,
            "finish": stop, "latency_ms": int((time.time() - t0) * 1000)}


def llm(cfg: dict, prompt: str, *, system: str | None = None,
        max_tokens: int = 8192, seed: str = "", tag: str = "") -> tuple[str, float]:
    api_key = os.environ.get("SRB_API_KEY")
    if not api_key:
        raise SystemExit("SRB_API_KEY not set (set -a; . internal/env.sh; set +a)")
    sid = f"srb-evo-{seed}-{tag}-{int(time.time())}"
    r = None
    for attempt in range(4):
        try:
            if cfg["wire"] == "messages":
                r = stream_messages(api_key=api_key, model=cfg["model"], prompt=prompt,
                                    system=system, max_tokens=max_tokens, session_id=sid)
            else:
                r = call_llm(base_url=BASE_URL, api_key=api_key, wire=cfg["wire"],
                             model=cfg["model"], messages=[{"role": "user", "content": prompt}],
                             system=system, max_tokens=max_tokens, session_id=sid)
        except Exception as e:  # noqa: BLE001 — DNS blips, resets, read timeouts
            r = {"ok": False, "status": 0, "error": f"{type(e).__name__}: {e}"[:300], "text": ""}
        # transport-level failures (status 0) and gateway 5xx/timeouts are worth a
        # long back-off; model refusals and 4xx are not
        transient = (not r.get("ok")) and (r.get("status", 0) in (0, 408, 429, 500, 502, 503, 504, 524)
                                           or "timeout" in str(r.get("error", "")).lower())
        if r.get("ok") or not transient or attempt == 3:
            break
        log_event(seed, {"retry": tag, "attempt": attempt + 1, "error": r.get("error")})
        time.sleep(30 * (attempt + 1))
    cost = float(r.get("cost_reported") or 0.0)
    with _lock:
        _spend["krw"] += cost
        _spend["calls"] += 1
        _tokens["in"] += int(r.get("in_tokens") or 0)
        _tokens["out"] += int(r.get("out_tokens") or 0)
    log_event(seed, {"call": tag, "model": cfg["model"], "ok": r.get("ok"),
                     "finish": r.get("finish"), "in": r.get("in_tokens"),
                     "out": r.get("out_tokens"), "cost_krw": cost,
                     "error": r.get("error")})
    if not r.get("ok"):
        raise RuntimeError(f"{tag}: {r.get('error')}")
    if r.get("finish") in ("length", "max_tokens"):
        raise RuntimeError(f"{tag}: output truncated at max_tokens={max_tokens}")
    return r["text"], cost


def parse_json(text: str):
    """The model is told to answer with one JSON object. Accept a fenced block
    or a bare object; refuse anything else loudly (a half-parsed plan would be
    worse than a failed one)."""
    m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    cand = m.group(1) if m else text
    start, end = cand.find("{"), cand.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in reply")
    return json.loads(cand[start:end + 1])


# ── prompts ────────────────────────────────────────────────────────────────

SYSTEM_SYNTH = """You design benchmark tasks that measure whether an LLM router \
should send a request to a cheap model or an expensive one. You will evolve an \
existing task into a strictly harder descendant. You always answer with exactly \
one JSON object and nothing else."""

DIRECTION_TEXT = {
    "length": ("LENGTH — insert one or more additional scenario-skill steps that "
               "the solver must satisfy IN ADDITION to everything the seed already "
               "requires. The steps must be dependent (later steps rely on earlier "
               "ones), not a list of unrelated extras."),
    "scenario": ("SCENARIO — replace exactly one scenario (the setting, data shape, "
                 "domain, or input regime) with a less common one, while keeping "
                 "the paired skill the same. The skill is unchanged; the situation "
                 "in which it must be applied is novel."),
    "skill": ("SKILL — keep the scenario but replace exactly one required skill "
              "with a rarer, more demanding one (an edge-case-heavy variant, a "
              "stricter invariant, a technique that weak models usually botch)."),
}

PROPOSE_TMPL = """SEED TASK (bilingual benchmark item; both languages are the same task):
{seed_json}

SEED CHECK (deterministic verifier the seed uses):
{check_json}

EVOLUTION DIRECTION: {direction_text}
EVOLUTION EFFORT: high — edit one contiguous span of the sequence, not the whole task.

Step 1. Extract the seed's execution sequence as a list of steps, each
        {{"scenario": "...", "skill": "..."}} (3-6 steps).
Step 2. Apply the direction to produce an edited sequence.
Step 3. Write an evolution plan: what changes in the prompt, what the new
        verifier must additionally test, and WHY the seed's correct answer will
        FAIL the new verifier.

Hard constraints on the plan:
- The descendant must stay checkable by the same verifier type ({check_type}).
- It must remain a single-turn chat task with an unambiguous, deterministic
  correct answer. No hidden information, no trick wording, no trivia.
- A strong frontier model must be able to solve it; a small cheap model should
  plausibly fail it. Raise difficulty through reasoning/edge cases/invariants,
  not through prompt length or obscurity for its own sake.
- Do not change the programming language or the answer format.
{feedback}
Answer with one JSON object:
{{"sequence": [...], "edited_sequence": [...], "plan": "...",
  "why_seed_answer_fails": "...", "new_test_ideas": ["...", "..."]}}"""

REVIEW_PLAN_TMPL = """You are the plan reviewer in a task-evolution pipeline. Reject weak plans.

SEED TASK:
{seed_json}

PROPOSED EVOLUTION PLAN (direction={direction}):
{plan_json}

Rubric — every item must hold to accept:
R1 The change is a genuine capability step-up (edge cases, invariants, combined
   requirements), not longer text, obscurity, or a trick.
R2 The descendant stays deterministic and machine-checkable with the same
   verifier type ({check_type}); the plan names concrete new test cases.
R3 The seed's correct answer would fail the new tests (so difficulty really rose).
R4 A frontier model can still solve it in one reply within {max_tokens} tokens.
R5 Nothing depends on the model's language (Korean and English twins can be
   written that are the same task).
R6 It stays in category "{category}" and does not turn into a different kind of task.

Answer with one JSON object: {{"accept": true|false, "feedback": "specific, actionable"}}"""

MODIFY_TMPL = """Build the descendant task from this approved plan.

SEED TASK:
{seed_json}

SEED CHECK:
{check_json}

APPROVED PLAN (direction={direction}):
{plan_json}
{feedback}
Produce ALL of the following, as one JSON object:

{{
 "subcategory": "short-kebab-case",
 "ko_turns": ["<the full Korean prompt — same task as en_turns>"],
 "en_turns": ["<the full English prompt — same task as ko_turns>"],
 "check_ko": {check_schema},
 "check_en": <same shape; identical to check_ko unless the expected values are
              locale-specific (e.g. Korean vs English required strings)>,
 "golden_ko": "<a complete CORRECT reply to ko_turns exactly as a model would write it, including the code block / JSON block the verifier extracts>",
 "golden_en": "<a complete CORRECT reply to en_turns>",
 "wrong_ko": "<a plausible but INCORRECT reply to ko_turns — ideally the kind of answer a weaker model gives: it handles the seed's requirements but misses the new ones>",
 "wrong_en": "<the same for en_turns>",
 "seed_solution": "<a complete CORRECT reply to the SEED task (must pass the SEED check). Reuse the seed's known answer if one is shown above.>",
 "notes": "one paragraph: what the new tests cover that the seed's did not"
}}

Rules:
- The prompt must state every requirement the tests check; no test may depend on
  something the prompt does not say. Keep the seed's output-format instruction
  (e.g. 'reply in a single ```python block').
- Tests must be self-contained and runnable exactly like the seed's (same
  imports/harness style). For python_tests: `from solution import ...` with
  unittest. For node_tests: check.js requiring ./solution.js, throw on failure.
  For go_tests: package {package}, _test.go using testing. For sql_result:
  keep the same `setup` unless the plan changes the schema, and `expected`
  as a list of rows (lists) in the exact order the query must return.
  For json_match: `expected` is the exact JSON value the reply must contain.
  For constraints: only these rule kinds — max_words, min_words, max_chars,
  line_count, max_lines, forbid, require, regex, forbid_regex, no_markdown,
  json_only, starts_with, ends_with, must_ask.
- ko_turns and en_turns must be faithful twins: same numbers, names, limits,
  required behaviours, and format instruction.
- Escape newlines inside JSON strings properly."""

REVIEW_TASK_TMPL = """You are the quality reviewer for a benchmark task that just passed its
mechanical checks. Judge the TASK ITSELF, not the answers.

TASK (Korean and English twins):
{task_json}

VERIFIER:
{check_json}

Rubric — every item must hold to accept:
Q1 The prompt states every requirement the verifier tests; nothing is hidden.
Q2 The correct answer is unique up to formatting; no reasonable alternative
   reading of the prompt produces a different verifier outcome.
Q3 Korean and English prompts are the same task (same constants, same rules,
   same output format).
Q4 It is a realistic request someone would actually make, in category "{category}".
Q5 No trick, trivia, or dependence on obscure external facts.
Q6 The difficulty comes from reasoning, edge cases, or combined constraints.

Answer with one JSON object: {{"accept": true|false, "feedback": "specific, actionable"}}"""


# ── verification ───────────────────────────────────────────────────────────

def run_check(check: dict, text: str) -> dict:
    # A malformed check (e.g. a constraints rule missing its key) raises inside
    # the checker. That is the modifier's bug to fix, so surface it as a
    # verification problem instead of aborting the whole direction.
    try:
        res = run_chat_check({"check": check}, text or "")
    except Exception as e:  # noqa: BLE001
        return {"ran": False, "passed": None, "detail": f"check raised {e!r} — the check itself is malformed"}
    return res or {"ran": False, "passed": None, "detail": "no check"}


def verify_candidate(seed: dict, seed_checks: dict, cand: dict,
                     seed_gold: dict[str, str | None]) -> tuple[bool, list[str]]:
    """The paper's three verifiers, plus our 'strictly harder' proof."""
    problems: list[str] = []
    for side in ("ko", "en"):
        chk = cand[f"check_{side}"]
        if chk.get("type") != seed_checks[side].get("type"):
            problems.append(f"{side}: check type changed to {chk.get('type')}")
            continue
        g = run_check(chk, cand[f"golden_{side}"])
        if g.get("passed") is not True:
            problems.append(f"{side}: GOLDEN does not pass — {str(g.get('detail'))[-600:]}")
        w = run_check(chk, cand[f"wrong_{side}"])
        if w.get("passed") is not False:
            problems.append(f"{side}: WRONG control does not fail — {str(w.get('detail'))[-300:]}")
        e = run_check(chk, "")
        if e.get("passed") is not False:
            problems.append(f"{side}: an EMPTY reply is not rejected — {str(e.get('detail'))[-200:]}")
        # strictly harder: the seed's correct answer must fail the new check
        base = seed_gold.get(side) or cand.get("seed_solution")
        if base:
            sb = run_check(seed_checks[side], base)
            if sb.get("passed") is not True:
                problems.append(f"{side}: seed_solution does not even pass the SEED check — "
                                f"{str(sb.get('detail'))[-300:]}")
            else:
                hb = run_check(chk, base)
                if hb.get("passed") is not False:
                    problems.append(f"{side}: the SEED's correct answer still passes the new "
                                    f"check — the task did not get harder")
        else:
            problems.append(f"{side}: no seed solution available to prove the task got harder")
    # twins must have the same structure
    if len(cand["ko_turns"]) != len(cand["en_turns"]):
        problems.append("twin turn counts differ")
    return (not problems), problems


# ── one lineage ────────────────────────────────────────────────────────────

def side_check(rec: dict, side: str) -> dict:
    v = rec.get(side) or {}
    return v.get("check") if v.get("check") is not None else rec.get("check")


def compact_seed(rec: dict) -> dict:
    return {"id": rec["id"], "category": rec["category"],
            "subcategory": rec.get("subcategory"), "language": rec.get("language"),
            "max_tokens": rec.get("max_tokens"),
            "ko_turns": rec["ko"]["turns"], "en_turns": rec["en"]["turns"]}


def evolve_one(seed_rec: dict, goldens: dict, args, rng: random.Random) -> dict:
    """Evolve one lineage: seed -> g1 -> g2 ... until the cheap probes fail
    (candidate for the discriminative band) or --max-generations is reached."""
    seed = seed_rec["id"]
    result = {"seed": seed, "category": seed_rec["category"], "status": "rejected",
              "generation": 0, "attempts": [], "cost_krw": 0.0, "tokens": {"in": 0, "out": 0}}
    spent0 = _spend["krw"]
    tok0 = dict(_tokens)
    cur_rec = seed_rec
    cur_gold = {s: goldens[s].get(seed) for s in ("ko", "en")}
    chain: list[dict] = []
    last_direction = None
    start_gen = 1
    replace_id = None
    if seed_rec.get("source") == "evolved":
        # re-evolving a descendant that the pool measured as still cheap: keep
        # the original seed as the lineage root, continue the generation count,
        # and on success replace the cheap child in the suite.
        lin = seed_rec["lineage"]
        seed = lin["seed"]
        result["seed"] = seed
        chain = list(lin.get("chain") or [{"id": seed_rec["id"], "direction": lin["direction"],
                                           "probe_hint": lin.get("probe_hint")}])
        start_gen = lin["generation"] + 1
        last_direction = lin["direction"]
        replace_id = seed_rec["id"]
        cur_gold = {s: goldens[s].get(seed_rec["id"]) for s in ("ko", "en")}
    for gen in range(start_gen, start_gen + args.max_generations):
        child, golds, att_list, direction = evolve_step(
            seed, cur_rec, cur_gold, gen, rng, args, avoid=last_direction)
        result["attempts"].extend(att_list)
        if child is None:
            result["status"] = f"rejected at g{gen}"
            break
        hint = child["lineage"]["probe_hint"]
        chain.append({"id": child["id"], "direction": direction, "probe_hint": hint})
        result.update({"generation": gen, "direction": direction,
                       "probe": child["lineage"]["probe"], "hint": hint})
        if hint == "candidate-discriminative":
            child["lineage"]["chain"] = chain
            append_outputs(child, golds, replace_id=replace_id)
            result.update({"status": "accepted", "id": child["id"], "replaced": replace_id})
            break
        # still cheap: keep evolving from the child (the paper's lineage)
        last_direction = direction
        cur_rec = child
        cur_gold = {s: golds[s][child["id"]] for s in ("ko", "en")}
        if gen == start_gen + args.max_generations - 1:
            if args.keep_still_cheap:
                child["lineage"]["chain"] = chain
                append_outputs(child, golds)
                result.update({"status": "still-cheap (written)", "id": child["id"]})
            else:
                result["status"] = f"still-cheap after g{gen} (not written)"
    result["cost_krw"] = round(_spend["krw"] - spent0, 2)
    result["tokens"] = {"in": _tokens["in"] - tok0["in"], "out": _tokens["out"] - tok0["out"]}
    return result


def evolve_step(seed: str, cur_rec: dict, cur_gold: dict, gen: int,
                rng: random.Random, args, avoid: str | None):
    """One generation: plan -> review -> modify -> verify -> review -> probe.
    Returns (child_record, goldens, attempts, direction) or (None, None, attempts, None)."""
    attempts: list[dict] = []
    seed_checks = {s: side_check(cur_rec, s) for s in ("ko", "en")}
    ctype = seed_checks["ko"]["type"]
    if ctype not in SUPPORTED_CHECKS or seed_checks["en"]["type"] != ctype:
        attempts.append({"outcome": f"unsupported check type {ctype}"})
        return None, None, attempts, None
    seed_rec = cur_rec
    seed_gold = cur_gold
    seed_json = json.dumps(compact_seed(seed_rec), ensure_ascii=False, indent=1)
    check_json = json.dumps(seed_checks["ko"], ensure_ascii=False, indent=1)
    result = {"attempts": attempts}

    directions = list(DIRECTIONS)
    rng.shuffle(directions)
    if avoid in directions:
        directions.remove(avoid)
        directions.append(avoid)
    for direction in directions:
        att = {"generation": gen, "direction": direction, "steps": []}
        attempts.append(att)
        try:
            # Loop 1 — plan refinement (propose → review, one revision)
            feedback = ""
            plan = None
            for _round in range(2):
                txt, _ = llm(SYNTH, PROPOSE_TMPL.format(
                    seed_json=seed_json, check_json=check_json,
                    direction_text=DIRECTION_TEXT[direction], check_type=ctype,
                    feedback=(f"\nREVIEWER FEEDBACK ON YOUR PREVIOUS PLAN:\n{feedback}\n"
                              if feedback else "")),
                    system=SYSTEM_SYNTH, max_tokens=10000, seed=seed, tag=f"propose-{direction}")
                plan = parse_json(txt)
                rtxt, _ = llm(REVIEW, REVIEW_PLAN_TMPL.format(
                    seed_json=seed_json, direction=direction,
                    plan_json=json.dumps(plan, ensure_ascii=False, indent=1),
                    check_type=ctype, max_tokens=seed_rec.get("max_tokens", 4096),
                    category=seed_rec["category"]),
                    max_tokens=4096, seed=seed, tag=f"review-plan-{direction}")
                verdict = parse_json(rtxt)
                att["steps"].append({"plan_review": verdict})
                if verdict.get("accept"):
                    break
                feedback = verdict.get("feedback", "")
                plan = None
            if plan is None:
                att["outcome"] = "plan rejected twice"
                continue

            # Loop 2 — environment refinement (modify → verify, two repairs)
            feedback = ""
            cand = None
            for _round in range(3):
                schema_hint = json.dumps(seed_checks["ko"], ensure_ascii=False)
                txt, _ = llm(SYNTH, MODIFY_TMPL.format(
                    seed_json=seed_json, check_json=check_json, direction=direction,
                    plan_json=json.dumps(plan, ensure_ascii=False, indent=1),
                    check_schema=f"<same shape as the seed check: {schema_hint[:400]}…>",
                    package=seed_checks["ko"].get("package", "main"),
                    feedback=(f"\nYOUR PREVIOUS ATTEMPT FAILED VERIFICATION:\n{feedback}\n"
                              "Fix every listed problem. Keep the same plan.\n"
                              if feedback else "")),
                    system=SYSTEM_SYNTH, max_tokens=32000, seed=seed, tag=f"modify-{direction}")
                cand = parse_json(txt)
                for k in ("ko_turns", "en_turns", "check_ko", "check_en",
                          "golden_ko", "golden_en", "wrong_ko", "wrong_en"):
                    if k not in cand:
                        raise ValueError(f"modifier output missing {k}")
                if isinstance(cand["ko_turns"], str):
                    cand["ko_turns"] = [cand["ko_turns"]]
                if isinstance(cand["en_turns"], str):
                    cand["en_turns"] = [cand["en_turns"]]
                ok, problems = verify_candidate(seed_rec, seed_checks, cand, seed_gold)
                att["steps"].append({"verify": {"ok": ok, "problems": problems}})
                log_event(seed, {"verify": ok, "direction": direction, "problems": problems})
                if ok:
                    break
                feedback = "\n".join(f"- {p}" for p in problems)
                cand = None
            if cand is None:
                att["outcome"] = "failed mechanical verification 3x"
                continue

            # Rubric review of the finished task (one repair)
            task_view = {"ko_turns": cand["ko_turns"], "en_turns": cand["en_turns"],
                         "subcategory": cand.get("subcategory")}
            rtxt, _ = llm(REVIEW, REVIEW_TASK_TMPL.format(
                task_json=json.dumps(task_view, ensure_ascii=False, indent=1),
                check_json=json.dumps({"ko": cand["check_ko"], "en": cand["check_en"]},
                                      ensure_ascii=False, indent=1)[:6000],
                category=seed_rec["category"]),
                max_tokens=4096, seed=seed, tag=f"review-task-{direction}")
            q = parse_json(rtxt)
            att["steps"].append({"task_review": q})
            if not q.get("accept"):
                # one repair pass with the reviewer's feedback, then re-verify
                txt, _ = llm(SYNTH, MODIFY_TMPL.format(
                    seed_json=seed_json, check_json=check_json, direction=direction,
                    plan_json=json.dumps(plan, ensure_ascii=False, indent=1),
                    check_schema="<same shape as the seed check>",
                    package=seed_checks["ko"].get("package", "main"),
                    feedback=(f"\nQUALITY REVIEWER REJECTED YOUR TASK:\n{q.get('feedback')}\n"
                              "Revise the prompts/tests accordingly and regenerate everything.\n")),
                    system=SYSTEM_SYNTH, max_tokens=32000, seed=seed, tag=f"modify-q-{direction}")
                cand2 = parse_json(txt)
                for k in ("ko_turns", "en_turns", "check_ko", "check_en",
                          "golden_ko", "golden_en", "wrong_ko", "wrong_en"):
                    if k not in cand2:
                        raise ValueError(f"repair output missing {k}")
                ok, problems = verify_candidate(seed_rec, seed_checks, cand2, seed_gold)
                att["steps"].append({"verify_after_review": {"ok": ok, "problems": problems}})
                if not ok:
                    att["outcome"] = "quality repair broke verification"
                    continue
                cand = cand2

            # Crossover probe — the two cheapest pool models, once each, both languages
            probe = {}
            cheap_pass_both = True
            for p in PROBES:
                for side in ("ko", "en"):
                    prompt = "\n\n".join(cand[f"{side}_turns"])
                    try:
                        ptxt, _ = llm(p, prompt, max_tokens=seed_rec.get("max_tokens", 4096),
                                      seed=seed, tag=f"probe-{p['model'].split('/')[-1]}-{side}")
                        pr = run_check(cand[f"check_{side}"], ptxt)
                        passed = pr.get("passed")
                    except Exception as e:  # noqa: BLE001
                        passed, pr = None, {"detail": str(e)}
                    probe[f"{p['model']}|{side}"] = passed
                    if passed is not True:
                        cheap_pass_both = False
            att["probe"] = probe
            # The band rule calls a task cheap-sufficient when the CHEAPEST scored
            # model passes, and on this pool the cheapest is usually gpt-5.6-luna,
            # which is also strong. So a child only counts as a candidate when, in
            # at least one language, BOTH cheap probes fail — one cheap model
            # failing while the other passes is still cheap-sufficient.
            both_fail_langs = [side for side in ("ko", "en")
                               if all(probe.get(f"{p['model']}|{side}") is not True for p in PROBES)]
            band_hint = "candidate-discriminative" if both_fail_langs else "still-cheap"
            att["both_fail_langs"] = both_fail_langs
            att["outcome"] = band_hint

            new_id = f"{seed}-g{gen}"
            rec = {
                "id": new_id, "pair_id": new_id,
                "track": seed_rec["track"], "block": seed_rec["block"],
                "category": seed_rec["category"],
                "subcategory": cand.get("subcategory") or seed_rec.get("subcategory"),
                "difficulty_prior": "hard", "mode": "chat",
                "language": seed_rec.get("language"),
                "max_tokens": seed_rec.get("max_tokens", 4096),
                "scoring": "deterministic", "split": "dev",
                "source": "evolved",
                "lineage": {"seed": seed, "generation": gen, "direction": direction,
                            "effort": "high", "paper": "arXiv:2609.04128",
                            "synth": SYNTH["model"], "reviewer": REVIEW["model"],
                            "probe": probe, "probe_hint": band_hint,
                            "notes": cand.get("notes")},
                "ko": {"turns": cand["ko_turns"]},
                "en": {"turns": cand["en_turns"]},
            }
            if cand["check_ko"] == cand["check_en"]:
                rec["check"] = cand["check_ko"]
            else:
                rec["ko"]["check"] = cand["check_ko"]
                rec["en"]["check"] = cand["check_en"]
            golds = {"ko": {new_id: cand["golden_ko"], f"__wrong__{new_id}": cand["wrong_ko"]},
                     "en": {new_id: cand["golden_en"], f"__wrong__{new_id}": cand["wrong_en"]}}
            return rec, golds, attempts, direction
        except Exception as e:  # noqa: BLE001
            att["outcome"] = f"error: {e}"
            log_event(seed, {"error": str(e), "direction": direction, "generation": gen})
            continue
    return None, None, attempts, None


    result["cost_krw"] = round(_spend["krw"] - spent0, 2)
    return result


# ── main ───────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seeds", default=None, help="comma-separated seed task ids")
    ap.add_argument("--category", default=None, help="evolve every seed in this category")
    ap.add_argument("--bands", default="/tmp/srb_bands_k.json",
                    help="per-task band rows (from the matrix) to pick cheap-sufficient seeds")
    ap.add_argument("--max-seeds", type=int, default=None)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--budget-krw", type=float, default=20000.0)
    ap.add_argument("--max-generations", type=int, default=3,
                    help="keep evolving a lineage while both cheap probes still pass")
    ap.add_argument("--keep-still-cheap", action="store_true",
                    help="write descendants even when both cheap probes still pass")
    ap.add_argument("--rng-seed", type=int, default=20260907)
    ap.add_argument("--force", action="store_true", help="re-evolve seeds that already have a descendant")
    ap.add_argument("--reevolve-cheap", action="store_true",
                    help="continue lineages whose descendant the pool measured as cheap-sufficient")
    ap.add_argument("--categories", default=None,
                    help="comma-separated categories (alternative to --category)")
    args = ap.parse_args()

    src = load_src()
    goldens = load_goldens()
    if args.seeds:
        ids = [s.strip() for s in args.seeds.split(",") if s.strip()]
    elif args.category or args.categories:
        cats = set((args.categories or args.category).split(","))
        cheap = set()
        if os.path.exists(args.bands):
            for tid, band, *_rest in json.load(open(args.bands)):
                if band == "cheap-sufficient":
                    cheap.add(tid)
        ids = [i for i, r in src.items()
               if r["category"] in cats and (not cheap or i in cheap)
               and not (r.get("ko") or {}).get("generate")]
    elif args.reevolve_cheap:
        # descendants the pool measured as cheap-sufficient (K matrix), or not yet
        # measured but whose stored probe fails the strict rule
        matrix = load(os.path.join(ROOT, "results", "_matrix", "poolk.json")) or {"tasks": {}}
        from smartroute_bench import oracle  # noqa: E402
        ids = []
        for i, r in src.items():
            if r.get("source") != "evolved":
                continue
            cell = matrix["tasks"].get(i)
            if cell:
                view = oracle.task_view(cell)
                costs = {n: v["cost"] for n, v in view["scored"].items()}
                cheapest = min(costs, key=lambda n: (costs[n], n)) if costs else None
                if oracle.difficulty_band(view, cheapest) == "cheap-sufficient":
                    ids.append(i)
            else:
                probe = r["lineage"].get("probe") or {}
                strict = any(all(probe.get(f"{p['model']}|{side}") is not True for p in PROBES)
                             for side in ("ko", "en"))
                if not strict:
                    ids.append(i)
        print(f"re-evolving {len(ids)} cheap descendant(s)")
    else:
        raise SystemExit("give --seeds, --category, --categories or --reevolve-cheap")
    missing = [i for i in ids if i not in src]
    if missing:
        raise SystemExit(f"unknown seed ids: {missing}")
    if not args.reevolve_cheap:
        ids = [i for i in ids if src[i].get("source") != "evolved"]
    done_seeds = {r.get("lineage", {}).get("seed") for r in src.values()
                  if r.get("source") == "evolved"}
    if not args.force and not args.reevolve_cheap:
        skipped = [i for i in ids if i in done_seeds]
        ids = [i for i in ids if i not in done_seeds]
        if skipped:
            print(f"skipping {len(skipped)} seed(s) that already have a descendant "
                  f"(--force to redo): {', '.join(skipped)}")
    if args.max_seeds:
        ids = ids[:args.max_seeds]
    print(f"evolving {len(ids)} seed(s): {', '.join(ids)}")
    print(f"synth={SYNTH['model']} reviewer={REVIEW['model']} "
          f"probes={[p['model'] for p in PROBES]} budget=₩{args.budget_krw:,.0f}")

    results = []
    rng = random.Random(args.rng_seed)
    order = {i: random.Random(f"{args.rng_seed}-{i}") for i in ids}

    def run(i):
        if _spend["krw"] >= args.budget_krw:
            return {"seed": i, "status": "skipped (budget)", "cost_krw": 0.0}
        return evolve_one(src[i], goldens, args, order[i])

    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for res in ex.map(run, ids):
            results.append(res)
            print(f"  {res['seed']:<26} {res['status']:<38} g{res.get('generation',0)} "
                  f"dir={res.get('direction') or '-':<9} tok={res.get('tokens',{}).get('out',0):>6,}  "
                  f"probe={res.get('probe','')}")

    os.makedirs(LOG_DIR, exist_ok=True)
    summ_path = os.path.join(LOG_DIR, "summary.json")
    prev = []
    if os.path.exists(summ_path):
        prev = json.load(open(summ_path))
    prev = [r for r in prev if r["seed"] not in {x["seed"] for x in results}] + results
    atomic_write_json(summ_path, prev)
    acc = sum(1 for r in results if r["status"] == "accepted")
    print(f"\naccepted {acc}/{len(results)} · spend ₩{_spend['krw']:,.0f} client-reported over "
          f"{_spend['calls']} calls · tokens in={_tokens['in']:,} out={_tokens['out']:,} "
          f"(Anthropic messages-wire calls report ₩0 client-side; gateway metering is the truth) · summary -> "
          f"{os.path.relpath(summ_path, ROOT)}")
    print("next: python3 tools/author_tasks.py && python3 tools/selftest_checks.py "
          "--only <category>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
