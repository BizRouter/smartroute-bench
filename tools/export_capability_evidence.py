#!/usr/bin/env python3
"""Derive router capability-proposal evidence from a run's analysis (schema v2).

Turns measured benchmark scores into the capability-evidence JSON a smart
routing gateway can ingest as *pending* capability proposals (BizRouter:
`manage.py import_benchmark_capability_proposals`) — measured data flows into
routing profiles through the gateway's normal review/approval pipeline, never
directly. Only models actually measured in the run are exported.

Schema v2 contract (the importer rejects anything weaker):
  - attempt accounting is deduplication-free: every attempt — retries and
    failures included — is counted, costed, and is a score input
    (score_input_count == attempt_count; a failed attempt scores 0), and
  - every attempt behind an exported task type is attributed to the concrete
    model (fixed arms pin the model in the request = "fixed_model"; the
    smart-routing arm needs per-call x-bizrouter-routed-model metadata =
    "routed_model_metadata"). Task types with any unattributable attempt are
    dropped from the export rather than claimed — agentic sessions currently
    expose no routed-model metadata, so the route arm never exports tool_use.

Usage:
  python3 tools/export_capability_evidence.py --run-id v4 --measured-at 2026-07-22 \
      > results/v4/capability_evidence.json
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ROUTE_VIRTUAL_MODEL = "bizrouter/route"
CONFIDENCE = 0.8  # full task set x 3-judge blind average
ROUTE_CONCENTRATION_MIN = 0.95
TOOL_USE_AFFINITY_CAP = 0.95

ATTEMPT_ACCOUNTING = {
    "deduplication": "none",
    "failed_attempts_included": True,
    "failed_attempt_costs_included": True,
}

# benchmark category(-ies) -> Smart Routing task type. Categories absent from
# a run simply don't produce that affinity (partial evidence is fine — the
# importer carries unmeasured task types over from the current profile).
AFFINITY_SOURCES = {
    "chat": ["daily", "character"],
    "coding": ["coding"],
    "analysis": ["business", "research"],
    "extraction": ["extraction"],
    "json_generation": ["json-gen"],
    "translation": ["translation"],
    "summarization": ["summarization"],
    "reasoning": ["reasoning"],
}


def chat_cells(analysis: dict, arm: str, cats: list[str]) -> list[dict]:
    return [
        row["arms"][arm]
        for row in analysis["tasks"]
        if row["mode"] == "chat" and row["category"] in cats
        and arm in row["arms"]
    ]


def agentic_cells(analysis: dict, arm: str) -> list[dict]:
    return [
        row["arms"][arm]
        for row in analysis["tasks"]
        if row["mode"] == "agentic" and arm in row["arms"]
    ]


def hard_chat_cells(analysis: dict, arm: str) -> list[dict]:
    return [
        row["arms"][arm]
        for row in analysis["tasks"]
        if row["mode"] == "chat" and row["difficulty"] == "hard"
        and arm in row["arms"]
    ]


def attempt_inclusive_judge(cells: list[dict]) -> tuple[float, int] | None:
    """(mean score over every attempt, input count) — a retried or failed
    attempt is a 0-score input, and so is an undelivered final answer
    (refusal/empty) the judges had nothing to score. None only when a
    *delivered* answer went unjudged (the accounting contract can't be met,
    so no claim is made)."""
    inputs: list[float] = []
    for c in cells:
        if not c["attempt_n"]:
            continue
        if c["ok"] and c["judge_mean"] is None and c["delivery"] == "ok":
            return None
        final = (c["judge_mean"] or 0.0) if c["ok"] else 0.0
        inputs += [0.0] * (c["attempt_n"] - 1) + [final]
    if not inputs:
        return None
    return sum(inputs) / len(inputs), len(inputs)


def task_evidence_counts(cells: list[dict], model_code: str) -> dict | None:
    """Schema-v2 per-task-type accounting; None when any attempt cannot be
    attributed to model_code (that task type must not be exported)."""
    attempt_n = sum(c["attempt_n"] for c in cells)
    failed_n = sum(c["failed_attempt_n"] for c in cells)
    attributed_n = sum(
        1 for c in cells for m in c["attempt_models"] if m == model_code)
    if not attempt_n or attributed_n != attempt_n:
        return None
    return {
        "attempt_count": attempt_n,
        "failed_attempt_count": failed_n,
        "score_input_count": attempt_n,
        "concrete_model_attributed_count": attempt_n,
    }


def route_concentrated_model(analysis: dict) -> str | None:
    """The route arm measures a concrete model only when routing is (near-)
    fully concentrated on it — otherwise scores blend models and are not
    attributable evidence. Concentration is judged over attributed chat
    attempts (routed-model response metadata)."""
    dist = Counter(analysis.get("route_distribution", {}).get("overall", {}))
    if not dist:
        return None
    model, hits = dist.most_common(1)[0]
    total = sum(dist.values())
    return model if total and hits / total >= ROUTE_CONCENTRATION_MIN else None


def build_entry(analysis: dict, arm: str, model_code: str,
                attribution_method: str, note: str,
                rationale_common: str) -> dict | None:
    affinities: dict[str, float] = {}
    evidence: dict[str, dict] = {}
    dropped: list[str] = []
    for task_type, cats in AFFINITY_SOURCES.items():
        cells = chat_cells(analysis, arm, cats)
        counts = task_evidence_counts(cells, model_code)
        scored = attempt_inclusive_judge(cells)
        if counts is None or scored is None:
            if cells:
                dropped.append(task_type)
            continue
        score, n_inputs = scored
        if n_inputs != counts["attempt_count"]:
            dropped.append(task_type)
            continue
        affinities[task_type] = round(score / 10.0, 2)
        evidence[task_type] = {**counts, "attribution_method": attribution_method}

    # tool_use: agentic completion rate over every attempt
    ag = agentic_cells(analysis, arm)
    ag_counts = task_evidence_counts(ag, model_code)
    tool_note = None
    if ag and ag_counts:
        passes = sum(1 for c in ag if c.get("check_passed"))
        rate = passes / ag_counts["attempt_count"]
        # small-sample binary metric: a perfect pass rate must not claim a
        # perfect affinity, so the exported value is capped
        affinities["tool_use"] = min(round(rate, 2), TOOL_USE_AFFINITY_CAP)
        evidence["tool_use"] = {**ag_counts, "attribution_method": attribution_method}
        tool_note = (
            f"tool_use 는 min(에이전틱 전 시도 완주·체크 통과율, {TOOL_USE_AFFINITY_CAP})"
            f" — 시도 {ag_counts['attempt_count']}회"
            f"(실패 {ag_counts['failed_attempt_count']}회 포함) 중 {passes}회 통과"
            f"({rate:.2f})이며, 소표본 이진 지표 보수 상한이 적용됩니다."
        )
    elif ag:
        dropped.append("tool_use")

    if not affinities:
        return None

    rationale = [rationale_common, note]
    entry: dict = {
        "model_code": model_code,
        "task_affinities": affinities,
        "task_evidence": evidence,
        "confidence": CONFIDENCE,
    }
    hard = attempt_inclusive_judge(hard_chat_cells(analysis, arm))
    if hard is not None:
        entry["complexity_capacity"] = round(hard[0] / 10.0, 2)
        rationale.append(
            f"complexity_capacity 는 hard 난이도 전체 시도 포함 judge 평균"
            f"({hard[0]:.2f}/10)입니다."
        )
    if tool_note:
        rationale.append(tool_note)
    elif dropped:
        rationale.append(
            "구체 모델 귀속 메타데이터가 없는 작업 유형("
            + ", ".join(sorted(dropped))
            + ")은 근거에서 제외했습니다."
        )
    entry["rationale"] = rationale
    return entry


# ── mirror of the importer's schema-v2 validation (fail here, not there) ────

def validate_payload(payload: dict) -> None:
    assert payload["schema_version"] == 2
    assert payload["attempt_accounting"] == ATTEMPT_ACCOUNTING
    for key in ("benchmark", "run_id", "measured_at"):
        assert isinstance(payload[key], str) and payload[key].strip(), key
    assert payload["models"], "no exportable models"
    seen = set()
    for entry in payload["models"]:
        code = entry["model_code"]
        assert code and code not in seen, f"duplicate model_code: {code}"
        seen.add(code)
        aff, ev = entry["task_affinities"], entry["task_evidence"]
        assert aff and set(aff) == set(ev), f"{code}: affinities/evidence key mismatch"
        for task_type, e in ev.items():
            assert e["attempt_count"] >= 1, (code, task_type)
            assert 0 <= e["failed_attempt_count"] <= e["attempt_count"]
            assert e["score_input_count"] == e["attempt_count"]
            assert e["concrete_model_attributed_count"] == e["attempt_count"]
            assert e["attribution_method"] in ("fixed_model", "routed_model_metadata")
            assert 0.0 <= aff[task_type] <= 1.0
        assert 0.0 <= entry["confidence"] <= 1.0
        if entry.get("complexity_capacity") is not None:
            assert 0.0 <= entry["complexity_capacity"] <= 1.0
        rationale = entry["rationale"]
        assert 1 <= len(rationale) <= 4, f"{code}: leave room for the importer's carried-over note"
        for item in rationale:
            assert item.strip() and len(item) <= 300
            assert any("가" <= ch <= "힣" for ch in item), f"rationale must be Korean: {item}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--measured-at", required=True, help="YYYY-MM-DD of the run")
    ap.add_argument("--arms", default=os.path.join(ROOT, "configs", "arms.json"))
    ap.add_argument(
        "--export-arms", default="gpt56sol",
        help="comma-separated fixed-arm ids to export as direct measurements "
             "(model codes resolved from the arms config)")
    args = ap.parse_args()

    with open(args.arms, encoding="utf-8") as f:
        arm_models = {a["id"]: a["model"] for a in json.load(f)["arms"]}
    fixed_arm_models = {}
    for arm_id in filter(None, args.export_arms.split(",")):
        if arm_id not in arm_models:
            raise SystemExit(f"unknown arm id: {arm_id}")
        if arm_models[arm_id] == ROUTE_VIRTUAL_MODEL:
            raise SystemExit(f"{arm_id} is the routed arm, not a fixed arm")
        fixed_arm_models[arm_id] = arm_models[arm_id]

    with open(os.path.join(ROOT, "results", args.run_id, "analysis.json")) as f:
        analysis = json.load(f)
    acct = analysis.get("attempt_accounting") or {}
    if acct.get("deduplication") != "none":
        raise SystemExit(
            "analysis.json lacks deduplication-free attempt accounting — "
            "re-run smartroute_bench.analyze first")

    n_tasks = max(a.get("n") or 0 for a in analysis["overall"].values())
    rationale_common = (
        f"smartroute-bench {args.run_id}({n_tasks}과제·3사 블라인드 심사 평균·{args.measured_at})"
        " 실측 점수를 실패·재시도 시도까지 포함해 10점 만점 기준 0~1로 정규화했습니다."
    )

    models = []
    route_model = route_concentrated_model(analysis)
    if route_model:
        entry = build_entry(
            analysis, "route", route_model, "routed_model_metadata",
            f"스마트 라우팅 arm 의 챗 시도 전건이 응답 메타데이터"
            f"(x-bizrouter-routed-model) 기준 {route_model} 로 귀속되어"
            " 해당 모델의 실측으로 기록했습니다.",
            rationale_common,
        )
        if entry:
            models.append(entry)
    for arm, model_code in fixed_arm_models.items():
        entry = build_entry(
            analysis, arm, model_code, "fixed_model",
            "고정 arm 직접 실측입니다(요청에 모델을 고정해 전 시도 귀속).",
            rationale_common,
        )
        if entry:
            models.append(entry)

    payload = {
        "schema_version": 2,
        "benchmark": "smartroute-bench",
        "run_id": args.run_id,
        "measured_at": args.measured_at,
        "attempt_accounting": ATTEMPT_ACCOUNTING,
        "models": models,
    }
    validate_payload(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
