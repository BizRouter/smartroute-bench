#!/usr/bin/env python3
"""Deterministic generators for long-context tasks.

v1 has no long prompts at all — every one of its 57 tasks sits in the `short`
band — so length-proportional cost is invisible in every run so far. Fixing that
by hand would mean committing hundreds of thousands of characters of prompt, and
hand-counting the expected answer over 400 log lines is how a benchmark ends up
with a wrong golden nobody notices.

So the source keeps a compact spec (kind + seed + size + which question), and
this module materialises both the prompt and the expected answer from it. The
answer is computed from the same data the model sees, so it cannot drift from
the prompt.

Everything here is seeded — no clock, no unseeded randomness — because
`tools/author_tasks.py` must emit byte-identical suites on every run.
"""
from __future__ import annotations

import random

# ── shared vocabulary ───────────────────────────────────────────────────────

SERVICES = ["auth-api", "payment-api", "search-api", "media-worker",
            "notify-worker", "billing-cron"]
REGIONS_EN = ["Seoul", "Tokyo", "Singapore", "Frankfurt", "Oregon"]
REGIONS_KO = ["서울", "도쿄", "싱가포르", "프랑크푸르트", "오리건"]
PATHS = ["/v1/login", "/v1/token", "/v1/checkout", "/v1/search",
         "/v1/media/upload", "/v1/notify", "/v1/invoice"]


# ── log analysis ────────────────────────────────────────────────────────────

def _log_rows(seed: int, n: int) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    minute = 0
    for i in range(n):
        # walk the clock forward so the window questions have real structure
        minute += rng.choice([0, 0, 1, 1, 1, 2])
        hh, mm = 8 + minute // 60, minute % 60
        svc = rng.choice(SERVICES)
        # give one service a deliberately bad hour so there is a clear answer
        bad_hour = (hh == 9 and svc == "payment-api")
        roll = rng.random()
        if bad_hour and roll < 0.42:
            status = rng.choice([500, 502, 503])
        elif roll < 0.06:
            status = rng.choice([500, 502, 503])
        elif roll < 0.14:
            status = rng.choice([400, 401, 404, 429])
        else:
            status = 200
        rows.append({
            "i": i,
            "ts": f"2026-07-14T{hh:02d}:{mm:02d}:{rng.randrange(60):02d}Z",
            "hh": hh,
            "svc": svc,
            "path": rng.choice(PATHS),
            "status": status,
            "ms": rng.randrange(12, 4200),
            "trace": f"{rng.randrange(16**8):08x}",
        })
    return rows


def _render_log(rows: list[dict]) -> str:
    out = []
    for r in rows:
        level = "ERROR" if r["status"] >= 500 else (
            "WARN" if r["status"] >= 400 else "INFO")
        out.append(f'{r["ts"]} {level:<5} svc={r["svc"]} path={r["path"]} '
                   f'status={r["status"]} dur_ms={r["ms"]} trace={r["trace"]}')
    return "\n".join(out)


def log_analysis(spec: dict, lang: str) -> dict:
    rows = _log_rows(spec["seed"], spec["lines"])
    log = _render_log(rows)
    q = spec["question"]

    if q == "top_5xx_service_in_hour":
        hour = spec.get("hour", 9)
        counts: dict[str, int] = {}
        for r in rows:
            if r["hh"] == hour and r["status"] >= 500:
                counts[r["svc"]] = counts.get(r["svc"], 0) + 1
        svc = max(counts, key=lambda k: (counts[k], k))
        answer = f"{svc} {counts[svc]}"
        ask_ko = (f"아래 로그에서 {hour:02d}시대(**{hour:02d}:00~{hour:02d}:59**)에 "
                  f"5xx 응답이 가장 많았던 서비스 이름과 그 건수를 찾아 줘.")
        ask_en = (f"In the log below, find which service returned the most 5xx "
                  f"responses during the {hour:02d}:00-{hour:02d}:59 hour, and "
                  f"how many.")
        fmt_ko = "정답: <서비스이름> <건수>"
        fmt_en = "Answer: <service-name> <count>"

    elif q == "slowest_path_p_max":
        worst = max(rows, key=lambda r: (r["ms"], r["i"]))
        answer = f'{worst["path"]} {worst["ms"]}'
        ask_ko = "아래 로그에서 가장 오래 걸린 요청 한 건의 path 와 dur_ms 를 찾아 줘."
        ask_en = ("In the log below, find the single slowest request: its path "
                  "and its dur_ms.")
        fmt_ko = "정답: <path> <dur_ms>"
        fmt_en = "Answer: <path> <dur_ms>"

    elif q == "error_count_total":
        n = sum(1 for r in rows if r["status"] >= 500)
        answer = str(n)
        ask_ko = "아래 로그 전체에서 status 가 500 이상인 줄이 몇 개인지 세어 줘."
        ask_en = ("Count how many lines in the log below have a status of 500 "
                  "or above.")
        fmt_ko = "정답: <숫자>"
        fmt_en = "Answer: <number>"

    elif q == "trace_of_first_5xx":
        first = next(r for r in rows if r["status"] >= 500)
        answer = first["trace"]
        ask_ko = "아래 로그에서 시간순으로 가장 먼저 나온 5xx 줄의 trace 값을 찾아 줘."
        ask_en = ("In the log below, find the trace value of the earliest 5xx "
                  "line.")
        fmt_ko = "정답: <trace>"
        fmt_en = "Answer: <trace>"

    elif q == "service_with_no_errors":
        errored = {r["svc"] for r in rows if r["status"] >= 500}
        seen = {r["svc"] for r in rows}
        clean = sorted(seen - errored)
        if len(clean) != 1:
            raise ValueError(
                f"spec seed={spec['seed']} lines={spec['lines']} yields "
                f"{len(clean)} error-free services ({clean}); the question "
                f"needs exactly one so the answer is unambiguous")
        answer = clean[0]
        ask_ko = "아래 로그에서 5xx 가 한 번도 없는 서비스 이름을 찾아 줘."
        ask_en = ("In the log below, name the one service that never returned "
                  "a 5xx.")
        fmt_ko = "정답: <서비스이름>"
        fmt_en = "Answer: <service-name>"
    else:
        raise ValueError(f"unknown log question {q!r}")

    if lang == "ko":
        prompt = (f"{ask_ko}\n\n찾는 과정을 길게 쓰지 말고, 마지막 줄에 정확히 이 형식으로만 "
                  f"답해 줘:\n{fmt_ko}\n\n--- 로그 시작 ---\n{log}\n--- 로그 끝 ---")
        pattern = r"정답\s*[::]\s*([^\n]+)"
    else:
        prompt = (f"{ask_en}\n\nDo not narrate the search. Answer on the last "
                  f"line in exactly this format:\n{fmt_en}\n\n"
                  f"--- begin log ---\n{log}\n--- end log ---")
        pattern = r"Answer\s*[::]\s*([^\n]+)"

    return {"turns": [prompt],
            "check": {"type": "answer_match", "expected": [answer],
                      "pattern": pattern}}


# ── csv aggregation ─────────────────────────────────────────────────────────

def _csv_rows(seed: int, n: int, lang: str) -> list[dict]:
    rng = random.Random(seed)
    regions = REGIONS_KO if lang == "ko" else REGIONS_EN
    rows = []
    for i in range(n):
        rows.append({
            "order_id": f"O-{100000 + i}",
            "month": f"2026-{rng.randrange(1, 7):02d}",
            "region": rng.choice(regions),
            "qty": rng.randrange(1, 40),
            "unit": rng.randrange(1000, 90000),
            "status": rng.choices(["paid", "refunded", "cancelled"],
                                  weights=[80, 12, 8])[0],
        })
    return rows


def csv_aggregate(spec: dict, lang: str) -> dict:
    rows = _csv_rows(spec["seed"], spec["rows"], lang)
    header = "order_id,month,region,qty,unit_price,status"
    body = "\n".join(f'{r["order_id"]},{r["month"]},{r["region"]},{r["qty"]},'
                     f'{r["unit"]},{r["status"]}' for r in rows)
    table = header + "\n" + body
    q = spec["question"]

    if q == "revenue_for_region_paid":
        region = (REGIONS_KO if lang == "ko" else REGIONS_EN)[spec["region_idx"]]
        total = sum(r["qty"] * r["unit"] for r in rows
                    if r["region"] == region and r["status"] == "paid")
        answer = str(total)
        ask_ko = (f"아래 CSV 에서 region 이 \"{region}\" 이고 status 가 paid 인 행만 골라, "
                  f"qty × unit_price 의 합계를 구해 줘.")
        ask_en = (f"From the CSV below, take only the rows where region is "
                  f"\"{region}\" and status is paid, and sum qty x unit_price.")
    elif q == "refund_count_in_month":
        month = spec["month"]
        n = sum(1 for r in rows if r["month"] == month and r["status"] == "refunded")
        answer = str(n)
        ask_ko = (f"아래 CSV 에서 month 가 {month} 이고 status 가 refunded 인 행이 "
                  f"몇 개인지 세어 줘.")
        ask_en = (f"In the CSV below, count the rows where month is {month} and "
                  f"status is refunded.")
    elif q == "largest_single_order":
        best = max(rows, key=lambda r: (r["qty"] * r["unit"], r["order_id"]))
        answer = best["order_id"]
        ask_ko = ("아래 CSV 에서 qty × unit_price 가 가장 큰 주문의 order_id 를 찾아 줘 "
                  "(status 는 상관없어).")
        ask_en = ("In the CSV below, find the order_id with the largest "
                  "qty x unit_price, regardless of status.")
    else:
        raise ValueError(f"unknown csv question {q!r}")

    if lang == "ko":
        prompt = (f"{ask_ko}\n\n중간 계산을 길게 나열하지 말고, 마지막 줄에 정확히 이 형식으로만 "
                  f"답해 줘 (쉼표 없이):\n정답: <값>\n\n{table}")
        pattern = r"정답\s*[::]\s*([^\n]+)"
    else:
        prompt = (f"{ask_en}\n\nDo not list intermediate arithmetic. Answer on "
                  f"the last line in exactly this format (no thousands "
                  f"separators):\nAnswer: <value>\n\n{table}")
        pattern = r"Answer\s*[::]\s*([^\n]+)"

    numeric = q != "largest_single_order"
    check = {"type": "answer_match", "expected": [answer], "pattern": pattern}
    if numeric:
        check["format"] = "numeric"
    return {"turns": [prompt], "check": check}


# ── repo question answering ─────────────────────────────────────────────────

_HELPERS = [
    ("normalise_phone", "phone: str", "return phone.replace('-', '').strip()"),
    ("to_minor_units", "amount: float", "return int(round(amount * 100))"),
    ("chunk", "items: list, size: int",
     "return [items[i:i + size] for i in range(0, len(items), size)]"),
    ("safe_div", "a: float, b: float", "return a / b if b else 0.0"),
    ("clamp", "v: int, lo: int, hi: int", "return max(lo, min(hi, v))"),
    ("slugify", "text: str", "return text.lower().replace(' ', '-')"),
    ("dedupe", "items: list", "return list(dict.fromkeys(items))"),
    ("pct", "part: float, whole: float",
     "return 0.0 if not whole else 100.0 * part / whole"),
]


def repo_qa(spec: dict, lang: str) -> dict:
    """Synthesise a small multi-file Python package and ask a question whose
    answer can only be found by reading across the files."""
    rng = random.Random(spec["seed"])
    n_modules = spec["modules"]
    per_module = spec["functions_per_module"]

    modules: list[tuple[str, list[tuple[str, str, str]]]] = []
    pool = list(_HELPERS)
    for m in range(n_modules):
        funcs = []
        for f in range(per_module):
            name, sig, body = pool[(m * per_module + f) % len(pool)]
            suffix = f"_{m}{f}"
            funcs.append((name + suffix, sig, body))
        modules.append((f"app/util_{m:02d}.py", funcs))

    # plant exactly one function that is defined twice in the same module —
    # the thing a reviewer is supposed to spot by reading, not by guessing
    dup_m = rng.randrange(n_modules)
    dup_name = modules[dup_m][1][rng.randrange(per_module)][0]
    modules[dup_m][1].append((dup_name, "x: int", "return x * 2"))

    files = []
    for path, funcs in modules:
        lines = [f"# {path}", ""]
        for name, sig, body in funcs:
            lines += [f"def {name}({sig}):", f"    {body}", ""]
        files.append(f"===== {path} =====\n" + "\n".join(lines))
    repo = "\n".join(files)

    q = spec["question"]
    if q == "duplicate_definition":
        answer = dup_name
        ask_ko = ("아래는 한 파이썬 패키지의 파일들이야. **같은 파일 안에서 같은 이름으로 두 번 "
                  "정의된 함수**가 딱 하나 있어. 그 함수 이름을 찾아 줘.")
        ask_en = ("Below are the files of a Python package. Exactly one function "
                  "is defined twice with the same name inside the same file. "
                  "Name that function.")
    elif q == "function_count":
        answer = str(sum(len(f) for _p, f in modules))
        ask_ko = "아래 파이썬 패키지 파일들에 정의된 함수(def)가 전부 몇 개인지 세어 줘."
        ask_en = ("Count how many functions (def) are defined across the Python "
                  "package files below.")
    elif q == "module_of_duplicate":
        answer = modules[dup_m][0]
        ask_ko = ("아래 파이썬 패키지 파일들 중, 같은 이름의 함수가 두 번 정의된 파일의 "
                  "경로를 찾아 줘.")
        ask_en = ("Among the Python package files below, give the path of the "
                  "file that defines the same function name twice.")
    else:
        raise ValueError(f"unknown repo question {q!r}")

    if lang == "ko":
        prompt = (f"{ask_ko}\n\n설명은 두 문장 안으로 줄이고, 마지막 줄에 정확히 이 형식으로만 "
                  f"답해 줘:\n정답: <값>\n\n{repo}")
        pattern = r"정답\s*[::]\s*([^\n]+)"
    else:
        prompt = (f"{ask_en}\n\nKeep any explanation to two sentences, then "
                  f"answer on the last line in exactly this format:\n"
                  f"Answer: <value>\n\n{repo}")
        pattern = r"Answer\s*[::]\s*([^\n]+)"

    check = {"type": "answer_match", "expected": [answer], "pattern": pattern}
    if q == "function_count":
        check["format"] = "numeric"
    return {"turns": [prompt], "check": check}


# ── meeting transcript -> action items ──────────────────────────────────────

_PEOPLE_KO = ["김민수", "이서연", "박도윤", "최지우", "정하늘", "강예린", "윤성호"]
_PEOPLE_EN = ["Alex Reed", "Sam Cole", "Dana Woo", "Riley Park", "Jordan Lee",
              "Casey Kim", "Morgan Choi"]
_TOPICS_KO = ["결제 실패 재시도", "온보딩 화면 개편", "월 정산 리포트", "검색 색인 재구축",
              "고객 문의 분류 자동화", "가격표 개정", "장애 대응 매뉴얼", "베타 초대 메일"]
_TOPICS_EN = ["payment retry logic", "onboarding screen revamp",
              "monthly settlement report", "search index rebuild",
              "support ticket triage", "price list revision",
              "incident runbook", "beta invite emails"]

# Chatter that is deliberately NOT an action item. A model that pattern-matches
# on "I will look into it" instead of reading for an owner and a date picks these
# up, which is exactly the difference we want measured.
_NOISE_KO = [
    "{a}: 그건 나중에 한번 얘기해 보면 좋겠네요.",
    "{a}: 저도 같은 생각이에요.",
    "{a}: 지난주 수치는 큰 변화 없었어요.",
    "{a}: 일단 상황 보고 판단하는 게 좋을 것 같아요.",
    "{a}: 그 부분은 {b}님이 더 잘 아실 것 같은데요.",
    "{a}: 관련해서 자료 있으면 공유해 주세요.",
    "{a}: 시간 되면 제가 한번 봐 볼게요.",
]
_NOISE_EN = [
    "{a}: We should talk about that at some point.",
    "{a}: I think so too.",
    "{a}: Last week's numbers were basically flat.",
    "{a}: Let's wait and see before deciding.",
    "{a}: {b} probably knows that area better than I do.",
    "{a}: Share any docs you have on that.",
    "{a}: I can take a look if I get time.",
]


def meeting_actions(spec: dict, lang: str) -> dict:
    """Synthesise a meeting transcript with planted action items and decoys.

    The decoys carry the measurement: vague volunteering with no date, and an
    action item that is explicitly cancelled later in the same meeting. Both
    look like action items to anything skimming for intent, and both must be
    excluded.
    """
    rng = random.Random(spec["seed"])
    people = _PEOPLE_KO if lang == "ko" else _PEOPLE_EN
    topics = _TOPICS_KO if lang == "ko" else _TOPICS_EN
    noise = _NOISE_KO if lang == "ko" else _NOISE_EN
    n_actions = spec["actions"]
    n_noise = spec["noise_lines"]
    n_cancelled = spec.get("cancelled", 1)

    total = n_actions + n_cancelled
    # Cycle a shuffled list instead of sampling: more items than people is a
    # legitimate meeting (one person can own two things), and rng.sample would
    # just raise.
    def cycle(pool):
        shuffled = pool[:]
        rng.shuffle(shuffled)
        return [shuffled[i % len(shuffled)] for i in range(total)]

    owners = cycle(people)
    picked_topics = cycle(topics)
    # Distinct dates keep the sort order unambiguous, so the expected list has
    # exactly one correct ordering. The pool spans three months so a long
    # transcript with 30 items does not exhaust it.
    pool = [f"2026-{m:02d}-{d:02d}" for m in (9, 10, 11) for d in range(1, 29)]
    if total > len(pool):
        raise ValueError(f"{total} items exceeds the {len(pool)} distinct dates "
                         f"available; widen the pool before asking for more")
    dates = rng.sample(pool, total)

    lines: list[str] = []
    expected: list[dict] = []
    cancelled_idx = set(range(n_actions, n_actions + n_cancelled))

    for i in range(n_actions + n_cancelled):
        owner, topic, due = owners[i], picked_topics[i], dates[i]
        if lang == "ko":
            lines.append(f"{owner}: {topic} 건은 제가 맡을게요. 마감은 {due} 로 잡죠.")
        else:
            lines.append(f"{owner}: I'll own the {topic} item. Let's set the "
                         f"deadline at {due}.")
        if i not in cancelled_idx:
            expected.append({"owner": owner, "due": due})
        # scatter chatter between the real items
        for _ in range(max(1, n_noise // (n_actions + n_cancelled))):
            tpl = rng.choice(noise)
            a, b = rng.choice(people), rng.choice(people)
            lines.append(tpl.format(a=a, b=b))

    # the cancellations land near the end, after their item was assigned
    for i in sorted(cancelled_idx):
        owner, topic = owners[i], picked_topics[i]
        if lang == "ko":
            lines.append(f"{rng.choice(people)}: 아, {topic} 건은 이번 분기에 "
                         f"안 하기로 정리됐어요. {owner}님 그건 빼시면 돼요.")
        else:
            lines.append(f"{rng.choice(people)}: Oh — the {topic} item is "
                         f"dropped for this quarter. {owner}, take that off "
                         f"your list.")

    # one vague volunteer with no date: intent without a commitment
    vague = rng.choice(people)
    lines.append(f"{vague}: 그건 제가 한번 알아볼게요." if lang == "ko"
                 else f"{vague}: I'll look into that one.")

    expected.sort(key=lambda x: (x["due"], x["owner"]))
    seen = {(e["owner"], e["due"]) for e in expected}
    if len(seen) != len(expected):
        raise ValueError(f"seed {spec['seed']} produced a duplicate "
                         f"(owner, due) pair; the expected list would be "
                         f"ambiguous")
    body = "\n".join(lines)

    if lang == "ko":
        prompt = (
            "아래 회의록에서 **확정된 액션 아이템만** 뽑아 JSON 으로 줘.\n\n"
            "규칙:\n"
            "- 담당자와 마감일이 **둘 다** 분명한 것만 액션 아이템이야.\n"
            "- 회의 중에 취소·보류된 건은 넣지 마.\n"
            "- 「한번 알아볼게요」처럼 마감 없는 말은 액션 아이템이 아니야.\n"
            "- 형식: `[{\"owner\": 이름, \"due\": \"YYYY-MM-DD\"}, …]`\n"
            "- `due` 오름차순, 같으면 `owner` 오름차순으로 정렬해.\n"
            "- 응답 전체가 JSON 하나여야 해. 설명 붙이지 마.\n\n"
            f"--- 회의록 ---\n{body}\n--- 끝 ---")
    else:
        prompt = (
            "Extract **only the confirmed action items** from the meeting notes "
            "below, as JSON.\n\n"
            "Rules:\n"
            "- An action item needs **both** a named owner and a clear deadline.\n"
            "- Exclude anything cancelled or dropped during the meeting.\n"
            "- Vague offers like \"I'll look into that\" with no date are not "
            "action items.\n"
            "- Shape: `[{\"owner\": name, \"due\": \"YYYY-MM-DD\"}, …]`\n"
            "- Sort by `due` ascending, then `owner` ascending.\n"
            "- The entire response must be one JSON value. No prose.\n\n"
            f"--- notes ---\n{body}\n--- end ---")

    return {"turns": [prompt],
            "check": {"type": "json_match", "expected": expected}}


GENERATORS = {
    "log_analysis": log_analysis,
    "csv_aggregate": csv_aggregate,
    "repo_qa": repo_qa,
    "meeting_actions": meeting_actions,
}


def build(spec: dict, lang: str) -> dict:
    kind = spec["kind"]
    fn = GENERATORS.get(kind)
    if fn is None:
        raise SystemExit(f"unknown generator kind {kind!r}")
    return fn(spec, lang)
