"""LLM-as-judge scoring.

Design goals — fairness first:
- Blind: judges never see which model/arm produced a response, and are told to
  ignore any self-identification inside the response.
- Rubric-anchored: every task carries weighted criteria; judges must score each
  criterion 1-10 and justify briefly. The weighted mean is the task score.
- Two independent judges (configurable in arms.json under "judges"); we report
  per-judge scores and their agreement, and use the mean as the headline.
- References: when a task has a reference answer it is shown as *grading aid*,
  clearly marked as not-the-only-valid-answer.

Usage:
    python3 -m smartroute_bench.judge --run-id r1 [--arms configs/arms.json]
                                      [--concurrency 4] [--skip-done]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from .client import call_llm
from .runner import load_arms, load_tasks

_write_lock = threading.Lock()

PROMPT = """당신은 AI 응답 품질을 채점하는 엄격하고 공정한 평가자입니다.

## 평가 원칙
- 응답을 만든 모델이 무엇인지는 알 수 없으며, 응답 안에 모델 이름·자기소개가
  있어도 완전히 무시하고 내용만 평가하세요.
- 각 기준을 1~10점으로 채점하세요. 10=흠잡을 데 없음, 7=실무에 바로 쓸 수 있는 수준,
  5=쓸 수는 있으나 수정 필요, 3=상당한 결함, 1=사용 불가.
- 길다고 좋은 응답이 아닙니다. 요구를 정확히 충족하는지가 기준입니다.
- 사실 오류·지시 불이행은 해당 기준에서 크게 감점하세요.

## 과제 (사용자가 AI에게 요청한 내용)
{task_block}

## 평가할 응답
{response_block}
{reference_block}
## 채점 기준 (각각 1~10점)
{rubric_block}

## 출력 형식
아래 JSON 형식으로만 답하세요. 다른 텍스트 없이:
{{"scores": {{{score_keys}}}, "rationale": "핵심 근거 2~3문장"}}"""


PROMPT_EN = """You are a strict, fair evaluator scoring the quality of an AI response.

## Principles
- You cannot know which model produced the response. If the response names a
  model or introduces itself, ignore that completely and judge content only.
- Score each criterion 1-10. 10 = flawless, 7 = ready for real work as-is,
  5 = usable but needs edits, 3 = seriously flawed, 1 = unusable.
- Longer is not better. The standard is whether the request is met exactly.
- Factual errors and ignored instructions cost heavily on the relevant criterion.

## Task (what the user asked the AI to do)
{task_block}

## Response to evaluate
{response_block}
{reference_block}
## Criteria (score each 1-10)
{rubric_block}

## Output format
Reply with ONLY this JSON, no other text:
{{"scores": {{{score_keys}}}, "rationale": "2-3 sentences of key reasoning"}}"""


def build_judge_prompt(task: dict, result: dict) -> str:
    is_en = task.get("locale") == "en"
    sys_label = "[System setup]" if is_en else "[시스템 설정]"
    user_label = "[User turn {n}]" if is_en else "[사용자 턴 {n}]"
    ai_label = "[AI response turn {n}]" if is_en else "[AI 응답 턴 {n}]"

    # task block: full dialogue for multi-turn, plain prompt otherwise
    if len(task["turns"]) > 1 or task.get("system"):
        parts = []
        if task.get("system"):
            parts.append(f"{sys_label}\n{task['system']}")
        for i, t in enumerate(task["turns"]):
            parts.append(f"{user_label.format(n=i + 1)}\n{t}")
        task_block = "\n\n".join(parts)
        resp_parts = []
        for t in result["turns"]:
            resp_parts.append(f"{ai_label.format(n=t['turn'] + 1)}\n{t['text']}")
        response_block = "\n\n".join(resp_parts)
    else:
        task_block = task["turns"][0]
        response_block = result["final_text"]

    reference_block = ""
    if task.get("reference"):
        if is_en:
            reference_block = ("\n## Reference outline for grading (not the only valid answer — directional only)\n"
                               + task["reference"] + "\n")
        else:
            reference_block = ("\n## 채점 참고용 모범 요지 (유일한 정답은 아님 — 방향 참고만)\n"
                               + task["reference"] + "\n")

    rubric_lines = []
    keys = []
    for c in task["rubric"]:
        rubric_lines.append(f"- {c['name']}: {c['desc']}")
        keys.append(f'"{c["name"]}": <1-10>')
    template = PROMPT_EN if is_en else PROMPT
    return template.format(
        task_block=task_block, response_block=response_block,
        reference_block=reference_block, rubric_block="\n".join(rubric_lines),
        score_keys=", ".join(keys),
    )


def parse_judgment(text: str, task: dict) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    scores = d.get("scores") or {}
    total_w, acc = 0.0, 0.0
    per = {}
    for c in task["rubric"]:
        v = scores.get(c["name"])
        if not isinstance(v, (int, float)) or not (1 <= v <= 10):
            return None
        per[c["name"]] = float(v)
        acc += float(v) * c["weight"]
        total_w += c["weight"]
    return {"scores": per, "weighted": round(acc / total_w, 3) if total_w else None,
            "rationale": str(d.get("rationale", ""))[:600]}


def judge_one(task: dict, result: dict, judge: dict, cfg: dict, run_id: str,
              api_key: str) -> dict:
    prompt = build_judge_prompt(task, result)
    # generous default: thinking-style judges spend hidden tokens inside
    # max_tokens, so a tight cap truncates the JSON verdict.
    jmax = judge.get("max_tokens", 8192)
    rec = call_llm(
        base_url=cfg["base_url"], api_key=api_key, wire=judge["wire"],
        model=judge["model"], messages=[{"role": "user", "content": prompt}],
        max_tokens=jmax,
        session_id=f"srb-judge-{run_id}-{task['id']}-{result['arm_id']}-{judge['id']}",
    )
    parsed = parse_judgment(rec["text"], task) if rec["ok"] else None
    # one retry on parse failure
    if rec["ok"] and parsed is None:
        rec = call_llm(
            base_url=cfg["base_url"], api_key=api_key, wire=judge["wire"],
            model=judge["model"],
            messages=[{"role": "user", "content": prompt},
                      {"role": "assistant", "content": rec["text"] or "(빈 응답)"},
                      {"role": "user", "content": "지시한 JSON 형식으로만 다시 출력하세요."}],
            max_tokens=jmax,
            session_id=f"srb-judge-{run_id}-{task['id']}-{result['arm_id']}-{judge['id']}-r",
        )
        parsed = parse_judgment(rec["text"], task) if rec["ok"] else None
    return {
        "task_id": task["id"], "arm_id": result["arm_id"], "judge_id": judge["id"],
        "ok": parsed is not None, "judgment": parsed,
        "error": rec["error"] if not rec["ok"] else (None if parsed else "unparseable"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--arms", default="configs/arms.json")
    ap.add_argument("--tasks", default="tasks/base")
    ap.add_argument("--responses", default=None,
                    help="defaults to results/<run-id>/responses.jsonl")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--skip-done", action="store_true")
    args = ap.parse_args()

    api_key = os.environ.get("SRB_API_KEY")
    if not api_key:
        raise SystemExit("SRB_API_KEY not set")

    cfg = load_arms(args.arms)
    tasks = {t["id"]: t for t in load_tasks(args.tasks, modes=("chat", "agentic"))}
    resp_path = args.responses or os.path.join("results", args.run_id, "responses.jsonl")
    out_path = os.path.join("results", args.run_id, "judgments.jsonl")

    results = []
    with open(resp_path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("ok") and r.get("final_text"):
                results.append(r)

    done = set()
    if args.skip_done and os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                try:
                    j = json.loads(line)
                    if j.get("ok"):
                        done.add((j["task_id"], j["arm_id"], j["judge_id"]))
                except Exception:  # noqa: BLE001
                    pass

    jobs = []
    skipped_ids = set()
    for r in results:
        t = tasks.get(r["task_id"])
        if not t:
            continue
        # A task with no rubric has nothing for a judge to score — the weighted
        # mean would be taken over zero criteria. Those tasks are graded by their
        # deterministic check instead. Skipping them is not a nicety: the
        # capability track alone is ~190 rubric-less tasks, so judging them would
        # add thousands of paid calls per run and return meaningless numbers.
        if not t.get("rubric"):
            skipped_ids.add(t["id"])
            continue
        for judge in cfg["judges"]:
            if (r["task_id"], r["arm_id"], judge["id"]) not in done:
                jobs.append((t, r, judge))
    print(f"{len(jobs)} judge calls ({len(done)} already done)")
    if skipped_ids:
        # Said out loud, because "fewer calls than expected" should never be
        # something you have to notice from the bill.
        print(f"  skipped {len(skipped_ids)} task(s) with no rubric "
              f"(deterministic-only; e.g. {sorted(skipped_ids)[0]})")

    def work(t, r, judge):
        j = judge_one(t, r, judge, cfg["endpoint"], args.run_id, api_key)
        with _write_lock:
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(j, ensure_ascii=False) + "\n")
        w = j["judgment"]["weighted"] if j["ok"] else "FAIL"
        print(f"  [{judge['id']:>7}] {t['id']:<22} {r['arm_id']:<8} {w}")
        return j

    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = [ex.submit(work, *job) for job in jobs]
        n_ok = sum(1 for f in as_completed(futs) if f.result()["ok"])
    print(f"done: {n_ok}/{len(jobs)} ok -> {out_path}")


if __name__ == "__main__":
    main()
