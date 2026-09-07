"""Render analysis.json into a self-contained HTML report (Korean or English).

    python3 -m smartroute_bench.report --run-id r1 [--insights results/r1/insights.html] [--lang ko|en]

Optional insights file: an HTML fragment (findings authored by the analyst)
injected into the "인사이트" section. It is embedded verbatim — supply a
language-appropriate file when rendering with --lang en.
"""
from __future__ import annotations

import argparse
import html
import json
import os
from datetime import date

# categorical palette — validated with the dataviz six-checks validator
# (light/dark pairs; below-3:1 slots carry direct labels everywhere).
ARM_COLORS = {  # arm_id -> (light, dark) — display-order palette validated
    "gpt56sol": ("#2a78d6", "#3987e5"),
    "fable5": ("#008300", "#008300"),
    "opus5": ("#008300", "#008300"),
    "opus48": ("#eda100", "#c98500"),
    "route": ("#e87ba4", "#d55181"),
    "route_ng": ("#eb6834", "#d95926"),
}
MODEL_COLORS = {  # routed-model -> (light, dark)  order: blue,aqua,yellow,violet
    "openai/gpt-5.6-sol": ("#2a78d6", "#3987e5"),
    "anthropic/claude-sonnet-5": ("#1baf7a", "#199e70"),
    "google/gemini-3.5-flash": ("#eda100", "#c98500"),
    "z-ai/glm-5.2": ("#4a3aa7", "#9085e9"),
}
FALLBACK_COLOR = ("#52514e", "#c3c2b7")

KRW_PER_USD = 1504.0

CAT_LABEL = {
    "coding": "코딩 (chat)", "business": "업무 문서", "research": "리서치·분석",
    "daily": "일상 대화", "character": "캐릭터 챗",
    "extraction": "정보 추출", "json-gen": "JSON 생성", "translation": "번역",
    "summarization": "요약", "reasoning": "추론",
    "agentic-coding": "에이전트 코딩", "agentic-artifact": "에이전트 문서 제작",
}
CAT_LABEL_EN = {
    "coding": "Coding (chat)", "business": "Business docs", "research": "Research & analysis",
    "daily": "Daily chat", "character": "Character chat",
    "extraction": "Extraction", "json-gen": "JSON generation", "translation": "Translation",
    "summarization": "Summarization", "reasoning": "Reasoning",
    "agentic-coding": "Agentic coding", "agentic-artifact": "Agentic artifacts",
}
DIFF_LABEL = {"easy": "쉬움", "medium": "보통", "hard": "어려움"}
DIFF_LABEL_EN = {"easy": "Easy", "medium": "Medium", "hard": "Hard"}
JUDGE_LABEL = {"j-fable": "Claude Fable 5", "j-opus5": "Claude Opus 5",
               "j-sol": "GPT-5.6 Sol",
               "j-gemini": "Gemini 3.1 Pro", "j-terra": "GPT-5.6 Terra",
               "j-sonnet": "Claude Sonnet 5", "j-flash": "Gemini 3.5 Flash"}
DELIVERY_LABEL = {"refusal": "거부(stop_reason=refusal)", "empty": "빈 응답",
                  "truncated": "생성 중 절단"}
DELIVERY_LABEL_EN = {"refusal": "Refusal (stop_reason=refusal)", "empty": "Empty response",
                     "truncated": "Cut off mid-generation"}

# ── user-facing chrome strings, per language ──────────────────────────────
# "ko" values must reproduce the historical output byte-for-byte.
STRINGS = {
    "ko": {
        "title": "SmartRoute-Bench 결과 리포트",
        "run_sub": ("run <code>{run}</code> · {date} ·\n"
                    "과제 {n}개 ({c}개 카테고리) × {m}개 arm · 블라인드 {j}심사 + 객관 체크 ·\n"
                    "비용 = 게이트웨이 서버측 계량(판매가 KRW 기준)"),
        "no_data": "데이터 없음",
        "cases": "{t}건",
        "tile_cost_save": "챗 워크로드 비용 절감<br>(vs {label})",
        "completed_cmp": "(완주 비교)",
        "tile_quality": "품질 유지율<br>(챗 judge, 고정 모델 최고 대비)",
        "out_of_10": "(10점 만점)",
        "tile_agentic": "에이전틱 세션 성공률<br>(스마트 라우팅, 현행 풀)",
        "ag_all_pass": "{n}건 전부 완주·체크 통과",
        "ag_fail": "{n}건 중 {fail}건 세션 실패",
        "partial_quote": "「{x}」",
        "partial_join": "·",
        "partial_note": "{labels}는 원인 격리용 부분 재실행으로, 종합 합계 비교에는 포함하지 않습니다.",
        "h_overall": "종합 — 비용과 품질",
        "overall_sub": "전 과제 합산 비용(왼쪽)과 챗 과제 judge 평균 점수(오른쪽, 10점 만점·블라인드 {j}심사 평균)",
        "h_delivery": "응답 미배달 — 거부·생성 중단",
        "delivery_prose": ("모델이 답변을 완주하지 못해 온전한 응답이 도착하지 않은 경우입니다\n"
                           "(제공사 안전 분류기의 거부 스톱, 빈 응답, 생성 중 절단). 본 벤치마크는 실서비스 배달\n"
                           "품질을 재기 위해 재시도 없는 1회 호출 정책을 쓰므로, 미배달도 실측 그대로 종합 점수에\n"
                           "반영합니다. 다만 이는 모델의 능력 차이가 아니라 배달 실패이므로, 능력 비교용으로\n"
                           "미배달 과제를 제외한 평균을 병기합니다."),
        "dh_judge_all": "챗 judge 평균<br>(전체·헤드라인)",
        "dh_judge_excl": "미배달 제외 평균<br>(능력 비교용)",
        "dh_undelivered": "미배달",
        "dh_tasks": "해당 과제",
        "none": "없음",
        "h_cat_cost": "카테고리별 비용",
        "h_cat_quality": "카테고리별 품질 (judge 평균)",
        "cat_quality_sub": "챗 과제만 집계 — 에이전틱 과제의 품질은 결정적 체크(테스트·산출물 검증)로 평가합니다.",
        "h_routing": "스마트 라우팅의 모델 선택 분포",
        "routing_sub": "route arm 이 카테고리별로 실제 선택한 모델 (응답 헤더 x-bizrouter-routed-model 기준)",
        "h_difficulty": "난이도별",
        "h_oracle": "라우팅 선택의 정확도 (양방향)",
        "oracle_sub": ("고정 모델과의 비교가 아니라, <b>그 일을 해냈을 가장 싼 모델</b>(oracle)과의 "
                       "비교예요. 무조건 싼 것만 고르는 라우터는 과소하향에서, 무조건 비싼 것만 "
                       "고르는 라우터는 과잉상향에서 점수를 잃어요 — 한쪽만 잘해서는 좋은 점수가 "
                       "안 나와요."),
        "o_optimal": "최적 선택률",
        "o_over": "과잉상향률",
        "o_under": "과소하향률",
        "o_costreg": "비용 후회",
        "o_qualreg": "품질 후회",
        "o_disc": "변별 구간 비중",
        "o_compared": "비교한 과제",
        "o_over_note": "성공했지만 더 싼 모델로도 됐을 건",
        "o_under_note": "라우터는 실패했는데 통과한 모델이 풀에 있었던 건",
        "o_offpool": "행렬이 재보지 않은 모델로 간 건",
        "o_nomatrix": "행렬에 없는 과제",
        "o_bands": "실측 난이도 구간",
        "h_mode": "모드별 (챗 vs 에이전틱)",
        "mode_chat": "챗 (단발·멀티턴)",
        "mode_agentic": "에이전틱 (CLI 세션)",
        "h_flags": "주의가 필요한 과제 (미스라우팅 후보)",
        "flags_sub": "스마트 라우팅 결과가 고정 모델 대비 유의미하게 나빴던 과제 — 라우터 개선의 직접 재료",
        "flag_check_fail": "체크 실패 (고정 모델은 통과)",
        "flag_quality_gap": "품질 격차 (judge -1.5 이상)",
        "flag_detail": "route {r} vs 고정 최저 {m}",
        "fh": "<th>과제</th><th>유형</th><th>라우팅된 모델</th><th>비고</th>",
        "no_flags": "플래그된 과제 없음",
        "h_insights": "발견 사항과 개선 제안",
        "h_judges": "심사 신뢰도",
        "judge_prose": ("서로 다른 3사 심사자가 독립 채점하고 평균을 최종 점수로 사용합니다. "
                        "심사는 블라인드(모델 정체 미공개·자기식별 무시 지시)로 진행되며, 아래 표로 "
                        "자사 모델 편향 여부를 직접 확인할 수 있습니다."),
        "h_judge_means": "arm별 · 심사자별 평균 (챗 과제)",
        "h_judge_agree": "심사자 간 합의도",
        "th_mean_final": "평균(최종)",
        "pwh": "<th>심사자 쌍</th><th>평균 절대 편차</th><th>피어슨</th><th>n</th>",
        "h_tasks": "과제별 상세",
        "summary_detail": "전체 {n}개 과제 × {m} arm 상세 표 펼치기",
        "th_task3": "<th rowspan='2'>과제</th><th rowspan='2'>분류</th><th rowspan='2'>난이도</th>",
        "cjc": "<th>비용</th><th>judge</th><th>체크</th>",
        "undelivered_dot": "미배달 · ",
        "session_fail": "세션 실패",
        "h_method": "방법론 요약",
        "method": [
            "모든 arm 은 동일 게이트웨이·동일 프롬프트·동일 토큰 한도로 실행. 멀티턴 과제는 각 arm 이 자기 응답을 이어받음.",
            "재시도 없는 1회 호출 정책 — 거부·생성 중단으로 답이 도착하지 않아도 그대로 감점(실서비스 배달 품질 기준). 해당 케이스는 「응답 미배달」 섹션에 전수 공개하고, 능력 비교용으로 미배달 제외 평균을 병기.",
            "비용은 게이트웨이 서버측 계량(llm_calls, 판매가 KRW)이 정본 — 세 arm 모두 같은 기준. 실패·재시도까지 모든 시도의 비용을 전수 계상(무중복 attempt 원장은 analysis.json 에 동봉). judge 호출 비용은 arm 비용에서 제외.",
            "코딩 과제는 유닛 테스트/SQL 결과 대조 등 결정적 체크, 나머지는 가중 루브릭 기반 블라인드 LLM 심사(1~10).",
            "추론 깊이(reasoning_effort·thinking)는 지정하지 않고 각 모델의 기본값으로 실행 — 즉 게이트웨이 기본 설정으로 실제 배달되는 가성비를 측정한 것이며, 추론 깊이를 통일한 실험은 아님(모델별로 effort를 튜닝하면 비용·품질이 달라질 수 있음).",
            "스마트 라우팅 풀은 실행 시점 조직 정책을 따르며, 고정 arm 중 일부는 그 풀에 없음 — 라우터가 선택할 수 없는 모델과의 비교임을 감안(어느 arm 이 풀 밖인지는 회차의 라우팅 분포로 확인).",
            "벤치마크 하네스·과제 전문: SmartRoute-Bench (오픈소스 공개 예정).",
        ],
    },
    "en": {
        "title": "SmartRoute-Bench Report",
        "run_sub": ("run <code>{run}</code> · {date} ·\n"
                    "{n} tasks ({c} categories) × {m} arms · blind {j}-judge scoring + objective checks ·\n"
                    "cost = gateway server-side metering (list price, converted to USD)"),
        "no_data": "No data",
        "cases": "{t} tasks",
        "tile_cost_save": "Chat workload cost savings<br>(vs {label})",
        "completed_cmp": "(completed runs only)",
        "tile_quality": "Quality retention<br>(chat judge, vs best fixed model)",
        "out_of_10": "(out of 10)",
        "tile_agentic": "Agentic session success rate<br>(smart routing, current pool)",
        "ag_all_pass": "all {n} sessions completed & passed checks",
        "ag_fail": "{fail} of {n} sessions failed",
        "partial_quote": "“{x}”",
        "partial_join": ", ",
        "partial_note": ("{labels} are partial reruns for cause isolation and are "
                         "excluded from overall totals comparisons."),
        "h_overall": "Overall — cost & quality",
        "overall_sub": ("Total cost across all tasks (left) and mean judge score on chat tasks "
                        "(right, out of 10 · mean of {j} blind judges)"),
        "h_delivery": "Undelivered responses — refusals & interrupted generation",
        "delivery_prose": ("Cases where the model did not complete its answer, so no full response arrived\n"
                           "(provider safety-classifier refusal stops, empty responses, generation cut off\n"
                           "mid-stream). To measure real-world delivery quality this benchmark uses a\n"
                           "single-call, no-retry policy, so undelivered answers count in the headline scores\n"
                           "as measured. Because these are delivery failures rather than capability gaps,\n"
                           "means excluding undelivered tasks are shown alongside for capability comparison."),
        "dh_judge_all": "Chat judge mean<br>(all · headline)",
        "dh_judge_excl": "Mean excl. undelivered<br>(capability comparison)",
        "dh_undelivered": "Undelivered",
        "dh_tasks": "Affected tasks",
        "none": "None",
        "h_cat_cost": "Cost by category",
        "h_cat_quality": "Quality by category (judge mean)",
        "cat_quality_sub": ("Chat tasks only — quality of agentic tasks is assessed via deterministic "
                            "checks (tests · artifact verification)."),
        "h_routing": "Smart routing — model selection distribution",
        "routing_sub": ("Models the route arm actually picked per category "
                        "(from the x-bizrouter-routed-model response header)"),
        "h_difficulty": "By difficulty",
        "h_oracle": "Routing accuracy (two-sided)",
        "oracle_sub": ("Measured against the <b>cheapest model that would have done "
                       "the job</b> (the oracle), not against the pinned arms. An "
                       "always-cheapest router loses on under-routing and an "
                       "always-frontier router loses on over-routing, so doing well "
                       "on one side alone does not produce a good score."),
        "o_optimal": "Optimal selection",
        "o_over": "Over-routing",
        "o_under": "Under-routing",
        "o_costreg": "Cost regret",
        "o_qualreg": "Quality regret",
        "o_disc": "Discriminative share",
        "o_compared": "Tasks compared",
        "o_over_note": "succeeded, but something cheaper also would have",
        "o_under_note": "router failed where some pool model succeeded",
        "o_offpool": "routed to a model the matrix never measured",
        "o_nomatrix": "tasks absent from the matrix",
        "o_bands": "Empirical difficulty bands",
        "h_mode": "By mode (chat vs agentic)",
        "mode_chat": "Chat (single & multi-turn)",
        "mode_agentic": "Agentic (CLI sessions)",
        "h_flags": "Tasks needing attention (misrouting candidates)",
        "flags_sub": ("Tasks where smart routing did meaningfully worse than fixed models — "
                      "direct material for router improvement"),
        "flag_check_fail": "Check failed (fixed models passed)",
        "flag_quality_gap": "Quality gap (judge -1.5 or worse)",
        "flag_detail": "route {r} vs fixed-model min {m}",
        "fh": "<th>Task</th><th>Type</th><th>Routed models</th><th>Notes</th>",
        "no_flags": "No flagged tasks",
        "h_insights": "Findings & improvement proposals",
        "h_judges": "Judging reliability",
        "judge_prose": ("Three judges from different providers score independently and the mean is used "
                        "as the final score. Judging is blind (model identities hidden, with instructions "
                        "to ignore self-identification), and the table below lets you check directly for "
                        "same-vendor bias."),
        "h_judge_means": "Per-arm · per-judge means (chat tasks)",
        "h_judge_agree": "Inter-judge agreement",
        "th_mean_final": "Mean (final)",
        "pwh": "<th>Judge pair</th><th>Mean abs. diff</th><th>Pearson</th><th>n</th>",
        "h_tasks": "Per-task detail",
        "summary_detail": "Expand the full table — {n} tasks × {m} arms",
        "th_task3": "<th rowspan='2'>Task</th><th rowspan='2'>Category</th><th rowspan='2'>Difficulty</th>",
        "cjc": "<th>Cost</th><th>judge</th><th>Check</th>",
        "undelivered_dot": "Undelivered · ",
        "session_fail": "session failed",
        "h_method": "Methodology summary",
        "method": [
            "All arms run through the same gateway with identical prompts and token limits. "
            "On multi-turn tasks each arm continues from its own previous responses.",
            "Single-call, no-retry policy — if no answer arrives due to a refusal or interrupted "
            "generation, it is penalized as measured (real-world delivery-quality standard). "
            "All such cases are disclosed in the “Undelivered responses” section, and means "
            "excluding undelivered tasks are shown alongside for capability comparison.",
            "Cost is the gateway's server-side metering (llm_calls, list price) — the same basis for "
            "every arm; shown here in USD at 1,504 KRW per USD. Every attempt is costed, failures and "
            "retries included (the deduplication-free attempt ledger ships in analysis.json). "
            "Judge-call costs are excluded from arm costs.",
            "Coding tasks are scored with deterministic checks (unit tests, SQL result comparison, "
            "etc.); all others use weighted-rubric blind LLM judging (1–10).",
            "Reasoning depth (reasoning_effort / thinking) is not set — each model runs at its "
            "default. This measures cost-performance as delivered through the gateway at default "
            "settings, not an iso-effort comparison; tuning effort per model could shift cost and "
            "quality.",
            "The smart-routing pool follows the org policy at run time; some fixed arms are not "
            "in that pool — those comparisons are against a model the router cannot select. Check "
            "the run's routing distribution for which.",
            "Benchmark harness & full task set: SmartRoute-Bench (open-source release planned).",
        ],
    },
}


def esc(s):
    return html.escape(str(s if s is not None else ""))


def krw(v, nd=0):
    if v is None:
        return "–"
    return f"₩{v:,.{nd}f}"


def usd(v):
    if v is None:
        return "–"
    return f"${v / KRW_PER_USD:,.2f}"


def _color_class(prefix, key):
    return f"{prefix}-{''.join(ch if ch.isalnum() else '_' for ch in key)}"


def hbar_group(rows, arms, unit="", fmt=lambda v: f"{v:,.0f}", height_per=22,
               nodata="데이터 없음"):
    """rows: [(label, {arm_id: value})]; grouped horizontal bars, direct labels."""
    if not rows:
        return f"<p class='muted'>{nodata}</p>"
    vmax = max((v for _, d in rows for v in d.values() if v is not None), default=1) or 1
    label_w, val_w, bar_w = 170, 90, 420
    row_h = height_per * len(arms) + 14
    H = row_h * len(rows)
    W = label_w + bar_w + val_w
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px" role="img">']
    y = 0
    for label, d in rows:
        out.append(f'<text x="{label_w-10}" y="{y + row_h/2 + 4}" text-anchor="end" class="lbl">{esc(label)}</text>')
        by = y + 7
        for a in arms:
            v = d.get(a["id"])
            if v is not None:
                w = max(2, v / vmax * (bar_w - 8))
                out.append(f'<rect x="{label_w}" y="{by}" width="{w:.1f}" height="{height_per-6}" rx="3" class="f-{a["id"]}"/>')
                out.append(f'<text x="{label_w + w + 6}" y="{by + (height_per-6)/2 + 4}" class="val">{fmt(v)}{unit}</text>')
            by += height_per
        y += row_h
        if y < H:
            out.append(f'<line x1="0" x2="{W}" y1="{y-3}" y2="{y-3}" class="grid"/>')
    out.append("</svg>")
    return "".join(out)


def stacked_bar(rows, keys, key_colors_cls, fmt=lambda v: str(v),
                nodata="데이터 없음", total_fmt=lambda t: f"{t}건"):
    """rows: [(label, {key: count})] — 100% stacked horizontal bars."""
    if not rows:
        return f"<p class='muted'>{nodata}</p>"
    label_w, bar_w, H_row = 170, 430, 34
    W = label_w + bar_w + 60
    H = H_row * len(rows)
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px" role="img">']
    y = 0
    for label, d in rows:
        total = sum(d.get(k, 0) for k in keys) or 1
        out.append(f'<text x="{label_w-10}" y="{y + H_row/2 + 4}" text-anchor="end" class="lbl">{esc(label)}</text>')
        x = label_w
        for k in keys:
            n = d.get(k, 0)
            if not n:
                continue
            w = n / total * bar_w
            out.append(f'<rect x="{x:.1f}" y="{y+6}" width="{max(w-2,1):.1f}" height="{H_row-14}" rx="3" class="{key_colors_cls[k]}"/>')
            if w > 26:
                out.append(f'<text x="{x + w/2 - 1:.1f}" y="{y + H_row/2 + 4}" text-anchor="middle" class="seg">{fmt(n)}</text>')
            x += w
        out.append(f'<text x="{label_w + bar_w + 8}" y="{y + H_row/2 + 4}" class="val">{total_fmt(total)}</text>')
        y += H_row
    out.append("</svg>")
    return "".join(out)


def legend(items):
    return ("<div class='legend'>" +
            "".join(f"<span><i class='{cls}'></i>{esc(lab)}</span>" for lab, cls in items) +
            "</div>")


def build(analysis: dict, insights_html: str | None,
          annotations: dict | None = None, lang: str = "ko") -> str:
    annotations = annotations or {}
    t = STRINGS[lang]
    cat_label = CAT_LABEL if lang == "ko" else CAT_LABEL_EN
    diff_label = DIFF_LABEL if lang == "ko" else DIFF_LABEL_EN
    delivery_label = DELIVERY_LABEL if lang == "ko" else DELIVERY_LABEL_EN
    if lang == "ko":
        money = krw
        cost_fmt = lambda v: f"₩{v:,.0f}"
    else:
        money = lambda v, nd=0: usd(v)
        cost_fmt = usd
    arms = analysis["arms"]
    arm_ids = [a["id"] for a in arms]
    arm_label = {a["id"]: a["label"] for a in arms}
    ov = analysis["overall"]

    # ── css color classes ──────────────────────────────────────────────────
    css_fill = []
    for aid, (lc, dc) in ARM_COLORS.items():
        css_fill.append(f".f-{aid}{{fill:{lc}}} .i-{aid}{{background:{lc}}}")
        css_fill.append(f"@media (prefers-color-scheme: dark){{.f-{aid}{{fill:{dc}}} .i-{aid}{{background:{dc}}}}}")
    model_cls = {}
    for i, (m, (lc, dc)) in enumerate(MODEL_COLORS.items()):
        cls = f"f-m{i}"
        model_cls[m] = cls
        css_fill.append(f".{cls}{{fill:{lc}}} .i-m{i}{{background:{lc}}}")
        css_fill.append(f"@media (prefers-color-scheme: dark){{.{cls}{{fill:{dc}}} .i-m{i}{{background:{dc}}}}}")
    css_fill.append(f".f-other{{fill:{FALLBACK_COLOR[0]}}} .i-other{{background:{FALLBACK_COLOR[0]}}}")

    # ── hero tiles ─────────────────────────────────────────────────────────
    # headline numbers use the chat workload only: every arm completed every
    # chat task, so the comparison is apples-to-apples. (The route arm's
    # agentic sessions failed early and cheap — counting them would overstate
    # savings.) Agentic outcomes are reported separately below.
    def pct(a, b):
        return None if not a or not b else (1 - a / b) * 100

    chat = analysis["by_mode"].get("chat", {})
    route_cost = chat.get("route", {}).get("cost_total")
    tiles = []
    fixed_arms = [a for a in chat if a not in ("route", "route_ng")]
    for base in fixed_arms:
        s = pct(route_cost, chat.get(base, {}).get("cost_total"))
        if s is not None:
            tiles.append((t["tile_cost_save"].format(label=arm_label[base]), f"{s:.0f}%",
                          f"{money(route_cost)} vs {money(chat[base]['cost_total'])} {t['completed_cmp']}"))
    rj = chat.get("route", {}).get("judge_mean")
    fj = [chat[b]["judge_mean"] for b in fixed_arms
          if chat.get(b, {}).get("judge_mean")]
    if rj and fj:
        tiles.append((t["tile_quality"], f"{rj / max(fj) * 100:.0f}%",
                      f"{rj:.2f} vs {max(fj):.2f} {t['out_of_10']}"))
    ag = analysis["by_mode"].get("agentic", {}).get("route", {})
    if ag.get("n"):
        ag_sub = (t["ag_all_pass"].format(n=ag['n']) if not ag["fail_n"]
                  else t["ag_fail"].format(n=ag['n'], fail=ag['fail_n']))
        tiles.append((t["tile_agentic"], f"{(1 - ag['fail_n']/ag['n'])*100:.0f}%",
                      ag_sub))
    tiles_html = "".join(
        f"<div class='tile'><div class='t-label'>{t_}</div><div class='t-num'>{esc(v)}</div>"
        f"<div class='t-sub'>{esc(s)}</div></div>" for t_, v, s in tiles)

    # ── overall charts ─────────────────────────────────────────────────────
    # arms that ran the full task set; diagnostic partial arms (e.g. a pool-
    # variant rerun over a subset) are excluded from headline comparisons.
    full_n = max(ov[a]["n"] for a in arm_ids if a in ov)
    full_arms = [a for a in arm_ids if ov.get(a, {}).get("n") == full_n]
    partial_arms = [a for a in arm_ids if a in ov and ov[a].get("n") != full_n]
    if partial_arms:
        partial_labels = t["partial_join"].join(
            t["partial_quote"].format(x=esc(arm_label[a])) for a in partial_arms)
        cat_cost_note = f'<p class="sub">{t["partial_note"].format(labels=partial_labels)}</p>'
    else:
        cat_cost_note = ""

    def single_bars(values, fmt):
        rows = [(arm_label[a], a, values[a]) for a in full_arms if values.get(a) is not None]
        if not rows:
            return f"<p class='muted'>{t['no_data']}</p>"
        vmax = max(v for _, _, v in rows) or 1
        label_w, bar_w, rh = 170, 330, 30
        W, H = label_w + bar_w + 90, rh * len(rows)
        out = [f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px" role="img">']
        y = 0
        for lab, aid, v in rows:
            w = max(2, v / vmax * bar_w)
            out.append(f'<text x="{label_w-10}" y="{y+rh/2+4}" text-anchor="end" class="lbl">{esc(lab)}</text>')
            out.append(f'<rect x="{label_w}" y="{y+7}" width="{w:.1f}" height="{rh-14}" rx="3" class="f-{aid}"/>')
            out.append(f'<text x="{label_w+w+6}" y="{y+rh/2+4}" class="val">{fmt(v)}</text>')
            y += rh
        out.append("</svg>")
        return "".join(out)

    overall_cost_svg = single_bars({a: ov[a]["cost_total"] for a in full_arms},
                                   cost_fmt)
    overall_judge_svg = single_bars({a: ov[a].get("judge_mean") for a in full_arms},
                                    lambda v: f"{v:.2f}")

    # ── delivery failures (refusal / empty / truncated) ────────────────────
    # headline judge_mean charges undelivered answers as-is (1-shot, no-retry
    # policy); the table below separates "what arrived" from "what the model
    # can do" so a safety-classifier refusal isn't read as a capability gap.
    dfails = analysis.get("delivery_failures") or []
    delivery_section = ""
    if dfails:
        by_arm = {}
        for f in dfails:
            by_arm.setdefault(f["arm_id"], []).append(f)
        chat_n = {a: analysis["by_mode"].get("chat", {}).get(a, {}).get("n") or 0
                  for a in full_arms}
        rows = []
        for a in full_arms:
            o = ov.get(a, {})
            fs = by_arm.get(a, [])
            tasks_txt = ", ".join(
                f"<code>{esc(f['task_id'])}</code>"
                f"<span class='muted-t'> {esc(delivery_label.get(f['kind'], f['kind']))}</span>"
                for f in fs) or f"<span class='muted'>{t['none']}</span>"
            jm, jd = o.get("judge_mean"), o.get("judge_mean_delivered")
            delta = (f" <span class='muted-t'>(+{jd - jm:.2f})</span>"
                     if jm is not None and jd is not None and jd - jm >= 0.005 else "")
            rows.append(f"<tr><th>{esc(arm_label[a])}</th>"
                        f"<td>{jm if jm is not None else '–'}</td>"
                        f"<td><b>{jd if jd is not None else '–'}</b>{delta}</td>"
                        f"<td>{len(fs)}/{chat_n[a]}</td><td>{tasks_txt}</td></tr>")
        delivery_section = f"""
<h2>{t["h_delivery"]}</h2>
<p class="sub">{t["delivery_prose"]}</p>
<table><thead><tr><th>arm</th><th>{t["dh_judge_all"]}</th>
<th>{t["dh_judge_excl"]}</th><th>{t["dh_undelivered"]}</th><th>{t["dh_tasks"]}</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>"""

    # ── per-category ───────────────────────────────────────────────────────
    cats = [c for c in cat_label if c in analysis["by_category"]]
    cat_cost_svg = hbar_group(
        [(cat_label[c], {a: analysis["by_category"][c][a]["cost_total"] for a in arm_ids
                         if analysis["by_category"][c].get(a, {}).get("n")}) for c in cats],
        arms, fmt=cost_fmt, nodata=t["no_data"])
    judge_cats = [c for c in cats
                  if any(analysis["by_category"][c].get(a, {}).get("judge_mean")
                         for a in arm_ids)]
    judge_arms = [a for a in arms if a["id"] in full_arms]
    cat_judge_svg = hbar_group(
        [(cat_label[c], {a: analysis["by_category"][c][a]["judge_mean"] for a in arm_ids
                         if analysis["by_category"][c].get(a, {}).get("judge_mean")})
         for c in judge_cats],
        judge_arms, fmt=lambda v: f"{v:.2f}", nodata=t["no_data"])

    arm_legend = legend([(arm_label[a], f"i-{a}") for a in arm_ids
                         if ov.get(a, {}).get("n")])
    full_legend = legend([(arm_label[a], f"i-{a}") for a in full_arms])
    n_judges = len(analysis.get("per_judge_mean") or {}) or 2

    # ── routing distribution ───────────────────────────────────────────────
    rd = analysis.get("route_distribution", {})
    model_keys = list(MODEL_COLORS.keys())
    seen_models = {m for v in rd.values() for m in v}
    model_keys = [m for m in model_keys if m in seen_models] + \
                 sorted(m for m in seen_models if m not in MODEL_COLORS)
    cls_map = {m: model_cls.get(m, "f-other") for m in model_keys}
    route_rows = [(cat_label.get(k[4:], k[4:]), rd[k]) for k in rd if k.startswith("cat:")]
    route_svg = stacked_bar(route_rows, model_keys, cls_map, nodata=t["no_data"],
                            total_fmt=lambda tot: t["cases"].format(t=tot))
    model_legend = legend([(m.split("/")[-1], "i-m" + str(list(MODEL_COLORS).index(m)) if m in MODEL_COLORS else "i-other")
                           for m in model_keys])

    # ── oracle-referenced section (only when a matrix was supplied) ────────
    o = analysis.get("oracle")
    if not o:
        oracle_section = ""
    else:
        def cell(label, value, note=""):
            v = "—" if value is None else value
            sub = f"<div class='ok-note'>{esc(note)}</div>" if note else ""
            return (f"<div class='stat'><div class='k'>{esc(label)}</div>"
                    f"<div class='v'>{v}</div>{sub}</div>")

        pctf = lambda x: "—" if x is None else f"{x:.1f}%"
        tiles = "".join([
            cell(t["o_optimal"], pctf(o.get("optimal_selection_pct"))),
            cell(t["o_over"], pctf(o.get("over_route_pct")), t["o_over_note"]),
            cell(t["o_under"], pctf(o.get("under_route_pct")), t["o_under_note"]),
            cell(t["o_costreg"],
                 "—" if o.get("cost_regret_x") is None
                 else f"{o['cost_regret_x']:.2f}&times;"),
            cell(t["o_qualreg"],
                 "—" if o.get("quality_regret_points") is None
                 else f"{o['quality_regret_points']:+.2f}"),
            cell(t["o_disc"],
                 "—" if o.get("discriminative_share") is None
                 else f"{100 * o['discriminative_share']:.0f}%"),
        ])
        bands = "  ·  ".join(f"{esc(k)} {v}"
                            for k, v in sorted((o.get("difficulty_bands") or {}).items()))
        caveats = []
        # Said out loud: both of these silently narrow every number above.
        if o.get("off_pool_routes"):
            caveats.append(f"{t['o_offpool']}: " + ", ".join(
                f"{esc(k)} ({v})" for k, v in sorted(o["off_pool_routes"].items())))
        if o.get("n_tasks_without_matrix"):
            caveats.append(f"{o['n_tasks_without_matrix']} {t['o_nomatrix']}")
        caveat_html = ("<p class='sub'>" + " · ".join(caveats) + "</p>"
                       if caveats else "")
        oracle_section = (
            f"<h2>{t['h_oracle']}</h2><p class='sub'>{t['oracle_sub']}</p>"
            f"<div class='ogrid'>{tiles}</div>"
            f"<p class='sub'>{t['o_compared']}: {o.get('n_tasks_compared')}"
            f"  ·  {t['o_bands']} — {bands}</p>{caveat_html}")

    # ── difficulty table ───────────────────────────────────────────────────
    def agg_table(groups, label_map):
        head = "".join(f"<th colspan='3'>{esc(arm_label[a])}</th>" for a in arm_ids)
        sub = t["cjc"] * len(arm_ids)
        body = []
        for g, label in label_map.items():
            if g not in groups:
                continue
            cells = []
            for a in arm_ids:
                o = groups[g].get(a, {})
                if not o.get("n"):
                    cells.append("<td colspan='3' class='muted'>–</td>")
                    continue
                cp = o.get("check_pass")
                cells.append(f"<td>{money(o['cost_total'])}</td>"
                             f"<td>{o['judge_mean'] if o['judge_mean'] is not None else '–'}</td>"
                             f"<td>{f'{cp*100:.0f}%' if cp is not None else '–'}</td>")
            body.append(f"<tr><th>{esc(label)}</th>{''.join(cells)}</tr>")
        return (f"<table><thead><tr><th rowspan='2'></th>{head}</tr><tr>{sub}</tr></thead>"
                f"<tbody>{''.join(body)}</tbody></table>")

    diff_table = agg_table(analysis["by_difficulty"], diff_label)
    mode_table = agg_table(analysis["by_mode"], {"chat": t["mode_chat"], "agentic": t["mode_agentic"]})

    # ── flags ──────────────────────────────────────────────────────────────
    flag_rows = []
    for f in analysis.get("flags", []):
        kind = {"check_fail_vs_fixed": t["flag_check_fail"],
                "quality_gap": t["flag_quality_gap"]}.get(f["kind"], f["kind"])
        detail = ""
        if f["kind"] == "quality_gap":
            detail = t["flag_detail"].format(r=f['route_judge'], m=f['fixed_min'])
        routed = ", ".join(x.split("/")[-1] for x in (f.get("routed") or [])[:3]) or "?"
        flag_rows.append(f"<tr><td><code>{esc(f['task_id'])}</code></td><td>{esc(kind)}</td>"
                         f"<td>{esc(routed)}</td><td>{esc(detail)}</td></tr>")
    flags_html = (f"<table><thead><tr>{t['fh']}</tr></thead>"
                  f"<tbody>{''.join(flag_rows)}</tbody></table>" if flag_rows
                  else f"<p class='muted'>{t['no_flags']}</p>")

    # ── per-task detail ────────────────────────────────────────────────────
    trs = []
    for row in analysis["tasks"]:
        cells = []
        for a in arm_ids:
            c = row["arms"].get(a)
            if not c:
                cells.append("<td colspan='3' class='muted'>–</td>")
                continue
            ch = c.get("check_passed")
            chs = "✓" if ch else ("✗" if ch is False else "–")
            routed = ",".join(m.split("/")[-1].replace("claude-", "").replace("gemini-", "g")
                              for m in dict.fromkeys(c.get("routed_models") or []))
            j = c.get("judge_mean")
            extra = f"<div class='routed'>{esc(routed)}</div>" if routed and a.startswith("route") else ""
            anno = (annotations.get(row["task_id"]) or {}).get(a)
            if anno:
                extra += f"<div class='anno'>{esc(anno)}</div>"
            elif c.get("delivery") not in (None, "ok"):
                extra += (f"<div class='anno'>{t['undelivered_dot']}"
                          f"{esc(delivery_label.get(c['delivery'], c['delivery']))}</div>")
            err = "" if c.get("ok") else f" <span class='err'>{t['session_fail']}</span>"
            cells.append(f"<td>{money(c['cost_krw'], 1)}{err}</td>"
                         f"<td>{j if j is not None else '–'}</td><td>{chs}{extra}</td>")
        trs.append(f"<tr><td><code>{esc(row['task_id'])}</code></td>"
                   f"<td>{esc(cat_label.get(row['category'], row['category']))}</td>"
                   f"<td>{esc(diff_label.get(row['difficulty'], row['difficulty']))}</td>{''.join(cells)}</tr>")
    task_head = "".join(f"<th colspan='3'>{esc(arm_label[a])}</th>" for a in arm_ids)
    task_sub = t["cjc"] * len(arm_ids)
    task_table = (f"<table class='detail'><thead><tr>{t['th_task3']}"
                  f"{task_head}</tr><tr>{task_sub}</tr></thead>"
                  f"<tbody>{''.join(trs)}</tbody></table>")

    ja = analysis.get("judge_agreement") or {}
    n_tasks = len(analysis["tasks"])

    # ── judge section: per-arm x per-judge means + pairwise agreement ─────
    judge_ids = sorted({j for a in ov.values() for j in (a.get("judge_by") or {})})
    judge_section = ""
    if judge_ids:
        head = "".join(f"<th>{esc(JUDGE_LABEL.get(j, j))}</th>" for j in judge_ids)
        body = []
        for a in arm_ids:
            jb = ov.get(a, {}).get("judge_by") or {}
            if not jb:
                continue
            cells = "".join(f"<td>{jb.get(j, '–')}</td>" for j in judge_ids)
            mean = ov[a].get("judge_mean")
            body.append(f"<tr><th>{esc(arm_label[a])}</th>{cells}<td><b>{mean}</b></td></tr>")
        judge_table = (f"<table><thead><tr><th>arm \\ judge</th>{head}<th>{t['th_mean_final']}</th></tr></thead>"
                       f"<tbody>{''.join(body)}</tbody></table>")
        pw_rows = "".join(
            f"<tr><td>{esc(JUDGE_LABEL.get(p['judges'][0], p['judges'][0]))} ↔ "
            f"{esc(JUDGE_LABEL.get(p['judges'][1], p['judges'][1]))}</td>"
            f"<td>{p['mean_abs_diff']}</td><td>{p['pearson']}</td><td>{p['n']}</td></tr>"
            for p in (ja.get("pairwise") or []))
        pw_table = (f"<table><thead><tr>{t['pwh']}</tr></thead>"
                    f"<tbody>{pw_rows}</tbody></table>" if pw_rows else "")
        judge_section = (
            f"<p>{t['judge_prose']}</p>"
            f"<h3>{t['h_judge_means']}</h3>{judge_table}"
            f"<h3>{t['h_judge_agree']}</h3>{pw_table}")

    insights_section = ""
    if insights_html:
        insights_section = f"<section id='insights'><h2>{t['h_insights']}</h2>{insights_html}</section>"

    run_sub = t["run_sub"].format(run=esc(analysis['run_id']), date=date.today().isoformat(),
                                  n=n_tasks, c=len(analysis.get("by_category") or {}),
                                  m=len(arm_ids), j=n_judges)
    method_lis = "\n".join(f"<li>{it}</li>" for it in t["method"])

    return f"""<!DOCTYPE html>
<html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{t["title"]} — {esc(analysis['run_id'])}</title>
<style>
:root{{color-scheme:light dark;
 --surface:#fcfcfb;--surface2:#f3f2ef;--ink:#0b0b0b;--ink2:#52514e;--line:#e4e2dc;--accent:#2a78d6}}
@media (prefers-color-scheme: dark){{:root{{--surface:#1a1a19;--surface2:#242422;--ink:#fff;--ink2:#c3c2b7;--line:#3a3936;--accent:#3987e5}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--surface);color:var(--ink);
 font:15px/1.65 "Pretendard","Apple SD Gothic Neo",system-ui,sans-serif}}
main{{max-width:980px;margin:0 auto;padding:40px 24px 100px}}
h1{{font-size:26px;margin:0 0 6px}} h2{{font-size:19px;margin:52px 0 4px}}
h3{{font-size:15px;margin:26px 0 4px}}
.sub{{color:var(--ink2);margin:0 0 8px}}
.muted,.muted-t{{color:var(--ink2)}} .muted-t{{font-size:11px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:28px 0}}
.tile{{background:var(--surface2);border:1px solid var(--line);border-radius:10px;padding:16px 18px}}
.t-label{{font-size:12.5px;color:var(--ink2);line-height:1.45}}
.t-num{{font-size:32px;font-weight:700;letter-spacing:-.5px;margin:6px 0 2px}}
.t-sub{{font-size:12px;color:var(--ink2)}}
.lbl{{font-size:12px;fill:var(--ink)}} .val{{font-size:11.5px;fill:var(--ink);font-weight:600}}
.seg{{font-size:11px;fill:#fff;font-weight:600}}
.grid{{stroke:var(--line);stroke-width:1}}
.legend{{display:flex;gap:16px;flex-wrap:wrap;font-size:12.5px;color:var(--ink2);margin:8px 0 14px}}
.ogrid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,190px),1fr));gap:10px;margin:14px 0}}
.ostat{{border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0;word-break:keep-all;overflow-wrap:break-word}}
.ok-lbl{{font-size:12px;color:var(--ink2);margin-bottom:4px}}
.ok-val{{font-size:22px;font-weight:700;letter-spacing:-.02em}}
.ok-note{{font-size:11.5px;color:var(--ink2);margin-top:4px;line-height:1.5}}
.legend i{{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin:14px 0}}
th,td{{border:1px solid var(--line);padding:6px 9px;text-align:left;vertical-align:top}}
thead th{{background:var(--surface2);font-weight:600}}
tbody th{{background:var(--surface2);font-weight:600;white-space:nowrap}}
code{{font:12px ui-monospace,monospace;background:var(--surface2);padding:1px 5px;border-radius:4px}}
.err{{color:#c0392b;font-size:11px;font-weight:600}}
.routed{{font-size:10.5px;color:var(--ink2)}}
.anno{{font-size:10.5px;color:#c98500;font-weight:600;white-space:nowrap}}
.chart2{{display:grid;grid-template-columns:1fr 1fr;gap:24px}}
@media (max-width:800px){{.chart2{{grid-template-columns:1fr}}}}
.note{{background:var(--surface2);border-left:3px solid var(--accent);padding:12px 16px;border-radius:0 8px 8px 0;font-size:13.5px;margin:16px 0}}
details summary{{cursor:pointer;font-weight:600;margin:10px 0}}
.detail{{font-size:12px}}
section#insights .card{{background:var(--surface2);border:1px solid var(--line);border-radius:10px;padding:18px 22px;margin:14px 0}}
section#insights .card h3{{margin:0 0 6px}}
.sev{{display:inline-block;font-size:11px;font-weight:700;border-radius:5px;padding:1px 8px;margin-right:8px;vertical-align:1px}}
.sev.crit{{background:#e34948;color:#fff}} .sev.high{{background:#eb6834;color:#fff}}
.sev.mid{{background:#eda100;color:#fff}} .sev.info{{background:var(--accent);color:#fff}}
{''.join(css_fill)}
</style></head><body><main>
<h1>{t["title"]}</h1>
<p class="sub">{run_sub}</p>

<div class="tiles">{tiles_html}</div>

<h2>{t["h_overall"]}</h2>
<p class="sub">{t["overall_sub"].format(j=n_judges)}</p>
{full_legend}
<div class="chart2"><div>{overall_cost_svg}</div><div>{overall_judge_svg}</div></div>
{delivery_section}

<h2>{t["h_cat_cost"]}</h2>
{cat_cost_note}{arm_legend}
{cat_cost_svg}

<h2>{t["h_cat_quality"]}</h2>
<p class="sub">{t["cat_quality_sub"]}</p>
{full_legend}
{cat_judge_svg}

<h2>{t["h_routing"]}</h2>
<p class="sub">{t["routing_sub"]}</p>
{model_legend}
{route_svg}

{oracle_section}

<h2>{t["h_difficulty"]}</h2>
{diff_table}

<h2>{t["h_mode"]}</h2>
{mode_table}

<h2>{t["h_flags"]}</h2>
<p class="sub">{t["flags_sub"]}</p>
{flags_html}

{insights_section}

<h2>{t["h_judges"]}</h2>
{judge_section}

<h2>{t["h_tasks"]}</h2>
<details><summary>{t["summary_detail"].format(n=n_tasks, m=len(arm_ids))}</summary>{task_table}</details>

<h2>{t["h_method"]}</h2>
<ul>
{method_lis}
</ul>
</main></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--insights", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--arms", default=None,
                    help="accepted for CLI symmetry with analyze; unused here")
    ap.add_argument("--lang", choices=["ko", "en"], default="ko")
    args = ap.parse_args()

    d = os.path.join("results", args.run_id)
    analysis = json.load(open(os.path.join(d, "analysis.json"), encoding="utf-8"))
    insights_html = None
    ins_path = args.insights or os.path.join(d, "insights.html")
    if os.path.exists(ins_path):
        insights_html = open(ins_path, encoding="utf-8").read()
    annotations = None
    anno_path = os.path.join(d, "annotations.json")
    if os.path.exists(anno_path):
        annotations = json.load(open(anno_path, encoding="utf-8"))

    out = args.out or os.path.join(d, "report.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(build(analysis, insights_html, annotations, args.lang))
    print(f"-> {out}")


if __name__ == "__main__":
    main()
