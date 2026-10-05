"""Draft the ADR-0149 software expansion: 20 bilingual items -> tasks/_src/code-expansion.json.

python_tests items carry a reference solution (not emitted) and, for debugging items, the buggy code;
`verify()` runs the tests against both: the reference must pass and the buggy code must fail. Explain
and test/review items derive their answers by executing Python here. Status: DRAFT, not yet reviewed.
"""
import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tasks/_src/code-expansion.json"
EN_PATTERN = r"Answer\s*[::]\s*(.+)"
ITEMS, VERIFY = [], []
KO_CODE = "\n\npython 코드블록 하나로 전체 코드를 줘."
EN_CODE = "\n\nReturn the complete code in a single python code block."


def code_item(id_, sub, prior, ko, en, tests, reference, buggy=None):
    tests = textwrap.dedent(tests).lstrip()
    ITEMS.append({
        "id": id_, "pair_id": id_, "track": "work", "block": "code", "category": "code-expansion",
        "subcategory": sub, "difficulty_prior": prior, "mode": "chat", "language": "python", "max_tokens": 4096,
        "scoring": "deterministic", "split": "dev", "source": "authored",
        "check": {"type": "python_tests", "tests": tests},
        "ko": {"turns": [ko + KO_CODE]}, "en": {"turns": [en + EN_CODE]},
    })
    VERIFY.append((id_, tests, textwrap.dedent(reference).lstrip(), textwrap.dedent(buggy).lstrip() if buggy else None))


def choice_item(id_, sub, prior, ko, en, ko_opts, en_opts, answer):
    letters = "ABCD"
    ko_o = "\n".join(f"{letters[i]}) {o}" for i, o in enumerate(ko_opts))
    en_o = "\n".join(f"{letters[i]}) {o}" for i, o in enumerate(en_opts))
    ITEMS.append({
        "id": id_, "pair_id": id_, "track": "work", "block": "code", "category": "code-expansion",
        "subcategory": sub, "difficulty_prior": prior, "mode": "chat", "language": "python", "max_tokens": 1536,
        "scoring": "deterministic", "split": "dev", "source": "authored",
        "ko": {"turns": [f"{ko}\n\n{ko_o}\n\n근거를 짧게 쓰고, 마지막 줄에 정확히 이 형식으로 답해:\n정답: <A~D 중 하나>"], "check": {"type": "mcq", "expected": answer}},
        "en": {"turns": [f"{en}\n\n{en_o}\n\nGive brief reasons, then end with exactly this line:\nAnswer: <one of A-D>"], "check": {"type": "mcq", "expected": answer}},
    })


def output_item(id_, prior, ko, en, snippet, fmt_ko, fmt_en, expected):
    ITEMS.append({
        "id": id_, "pair_id": id_, "track": "work", "block": "code", "category": "code-expansion",
        "subcategory": "explain-output", "difficulty_prior": prior, "mode": "chat", "language": "python", "max_tokens": 1536,
        "scoring": "deterministic", "split": "dev", "source": "authored",
        "ko": {"turns": [f"{ko}\n\n```python\n{snippet}\n```\n\n이유를 짧게 설명하고, 마지막 줄에 정확히 이 형식으로 답해:\n정답: {fmt_ko}"], "check": {"type": "answer_match", "expected": expected}},
        "en": {"turns": [f"{en}\n\n```python\n{snippet}\n```\n\nExplain briefly, then end with exactly this line:\nAnswer: {fmt_en}"], "check": {"type": "answer_match", "pattern": EN_PATTERN, "expected": expected}},
    })


def run_tests(tests, solution):
    with tempfile.TemporaryDirectory() as d:
        Path(d, "solution.py").write_text(solution)
        Path(d, "test_solution.py").write_text(tests)
        r = subprocess.run([sys.executable, "-m", "unittest", "-q", "test_solution"], cwd=d, capture_output=True, text=True, timeout=60)
        return r.returncode == 0


def run_snippet(snippet):
    r = subprocess.run([sys.executable, "-c", snippet], capture_output=True, text=True, timeout=30)
    return r.stdout.strip()


# ---- debugging (fix the given code) -------------------------------------------------------------
code_item("swx-debug-01", "debug", "medium",
    "아래 페이지네이션 함수는 1페이지부터 세는데, 1페이지를 요청하면 두 번째 묶음이 나온다. 원인을 짚고 고친 전체 코드를 줘.\n\n```python\ndef paginate(items, page, size):\n    \"\"\"page는 1부터 센다. 범위를 벗어나면 빈 리스트.\"\"\"\n    start = page * size\n    return items[start:start + size]\n```",
    "This pagination function counts pages from 1, but asking for page 1 returns the second batch. Identify the cause and give the complete fixed code.\n\n```python\ndef paginate(items, page, size):\n    \"\"\"page counts from 1. Out of range returns an empty list.\"\"\"\n    start = page * size\n    return items[start:start + size]\n```",
    """
    import unittest
    from solution import paginate
    class T(unittest.TestCase):
        def test_first(self): self.assertEqual(paginate(list(range(10)), 1, 3), [0, 1, 2])
        def test_last(self): self.assertEqual(paginate(list(range(10)), 4, 3), [9])
        def test_out(self): self.assertEqual(paginate(list(range(10)), 5, 3), [])
    """,
    "def paginate(items, page, size):\n    start = (page - 1) * size\n    return items[start:start + size]\n",
    "def paginate(items, page, size):\n    start = page * size\n    return items[start:start + size]\n")
code_item("swx-debug-02", "debug", "medium",
    "태그를 붙이는 함수인데, 서로 다른 글에 붙인 태그가 다음 호출에 섞여 나온다. 원인을 짚고 고친 전체 코드를 줘.\n\n```python\ndef add_tag(tag, tags=[]):\n    tags.append(tag)\n    return tags\n```",
    "This tagging helper leaks tags between calls for different posts. Identify the cause and give the complete fixed code.\n\n```python\ndef add_tag(tag, tags=[]):\n    tags.append(tag)\n    return tags\n```",
    """
    import unittest
    from solution import add_tag
    class T(unittest.TestCase):
        def test_independent(self):
            self.assertEqual(add_tag("a"), ["a"])
            self.assertEqual(add_tag("b"), ["b"])
        def test_given_list(self):
            base = ["x"]
            self.assertEqual(add_tag("y", base), ["x", "y"])
    """,
    "def add_tag(tag, tags=None):\n    tags = [] if tags is None else tags\n    tags.append(tag)\n    return tags\n",
    "def add_tag(tag, tags=[]):\n    tags.append(tag)\n    return tags\n")
code_item("swx-debug-03", "debug", "hard",
    "두 날짜 문자열(YYYY-MM-DD) 사이의 일수를 구하는 함수가 어떤 입력에서는 틀린 값을 내고 어떤 입력에서는 ValueError를 낸다. 원인을 짚고 고친 전체 코드를 줘. 결과는 b가 a보다 이르면 음수다.\n\n```python\nfrom datetime import datetime\n\ndef days_between(a, b):\n    fmt = \"%Y-%d-%m\"\n    return (datetime.strptime(b, fmt) - datetime.strptime(a, fmt)).days\n```",
    "A function that counts days between two YYYY-MM-DD strings gives wrong values for some inputs and raises ValueError for others. Identify the cause and give the complete fixed code. The result is negative when b is earlier than a.\n\n```python\nfrom datetime import datetime\n\ndef days_between(a, b):\n    fmt = \"%Y-%d-%m\"\n    return (datetime.strptime(b, fmt) - datetime.strptime(a, fmt)).days\n```",
    """
    import unittest
    from solution import days_between
    class T(unittest.TestCase):
        def test_basic(self): self.assertEqual(days_between("2026-01-30", "2026-02-02"), 3)
        def test_leap(self): self.assertEqual(days_between("2028-02-28", "2028-03-01"), 2)
        def test_negative(self): self.assertEqual(days_between("2026-10-05", "2026-10-01"), -4)
    """,
    "from datetime import date\n\ndef days_between(a, b):\n    return (date.fromisoformat(b) - date.fromisoformat(a)).days\n",
    "from datetime import datetime\n\ndef days_between(a, b):\n    fmt = \"%Y-%d-%m\"\n    return (datetime.strptime(b, fmt) - datetime.strptime(a, fmt)).days\n")
code_item("swx-debug-04", "debug", "hard",
    "더치페이 금액을 나누는 함수인데, 나눈 금액의 합이 총액과 안 맞을 때가 있다. 요구사항: 원 단위 정수로 나누고, 나머지는 앞사람부터 1원씩 더 낸다. 합은 반드시 총액과 같아야 한다. 고친 전체 코드를 줘.\n\n```python\ndef split_bill(total_krw, people):\n    share = round(total_krw / people)\n    return [share] * people\n```",
    "This bill-splitting function sometimes returns shares that do not add up to the total. Requirements: whole-won integers; the remainder is paid 1 won extra each by the first people in order; the sum must equal the total. Give the complete fixed code.\n\n```python\ndef split_bill(total_krw, people):\n    share = round(total_krw / people)\n    return [share] * people\n```",
    """
    import unittest
    from solution import split_bill
    class T(unittest.TestCase):
        def test_even(self): self.assertEqual(split_bill(30000, 3), [10000, 10000, 10000])
        def test_remainder(self): self.assertEqual(split_bill(10000, 3), [3334, 3333, 3333])
        def test_two_extra(self): self.assertEqual(split_bill(10001, 3), [3334, 3334, 3333])
        def test_sum(self):
            for total, n in ((99999, 7), (5, 3), (12345, 4)):
                self.assertEqual(sum(split_bill(total, n)), total)
    """,
    "def split_bill(total_krw, people):\n    base, extra = divmod(total_krw, people)\n    return [base + 1 if i < extra else base for i in range(people)]\n",
    "def split_bill(total_krw, people):\n    share = round(total_krw / people)\n    return [share] * people\n")
code_item("swx-debug-05", "debug", "medium",
    "만료된 세션을 지우는 함수가 실행 중에 `RuntimeError: dictionary changed size during iteration`을 낸다. 고친 전체 코드를 줘. 입력 딕셔너리를 제자리에서 수정하고, 지운 세션 수를 돌려준다.\n\n```python\ndef remove_expired(sessions, now):\n    removed = 0\n    for sid, expires_at in sessions.items():\n        if expires_at <= now:\n            del sessions[sid]\n            removed += 1\n    return removed\n```",
    "A function that removes expired sessions raises `RuntimeError: dictionary changed size during iteration`. Give the complete fixed code. It must modify the dict in place and return the number of sessions removed.\n\n```python\ndef remove_expired(sessions, now):\n    removed = 0\n    for sid, expires_at in sessions.items():\n        if expires_at <= now:\n            del sessions[sid]\n            removed += 1\n    return removed\n```",
    """
    import unittest
    from solution import remove_expired
    class T(unittest.TestCase):
        def test_in_place(self):
            s = {"a": 5, "b": 20, "c": 10}
            self.assertEqual(remove_expired(s, 10), 2)
            self.assertEqual(s, {"b": 20})
        def test_none(self):
            s = {"a": 50}
            self.assertEqual(remove_expired(s, 10), 0)
            self.assertEqual(s, {"a": 50})
    """,
    "def remove_expired(sessions, now):\n    expired = [sid for sid, t in sessions.items() if t <= now]\n    for sid in expired:\n        del sessions[sid]\n    return len(expired)\n",
    "def remove_expired(sessions, now):\n    removed = 0\n    for sid, expires_at in sessions.items():\n        if expires_at <= now:\n            del sessions[sid]\n            removed += 1\n    return removed\n")
code_item("swx-debug-06", "debug", "hard",
    "격자에서 오른쪽·아래로만 움직여 왼쪽 위에서 오른쪽 아래까지 가는 경로 수를 세는 함수야(1은 막힌 칸). 메모이제이션을 넣은 뒤로 결과가 틀린다. 원인을 짚고 고친 전체 코드를 줘.\n\n```python\ndef count_paths(grid):\n    rows, cols = len(grid), len(grid[0])\n    memo = {}\n    def go(r, c):\n        if r >= rows or c >= cols or grid[r][c] == 1:\n            return 0\n        if (r, c) == (rows - 1, cols - 1):\n            return 1\n        if r in memo:\n            return memo[r]\n        memo[r] = go(r + 1, c) + go(r, c + 1)\n        return memo[r]\n    return go(0, 0)\n```",
    "This function counts paths from the top-left to the bottom-right of a grid moving only right or down (1 marks a blocked cell). It went wrong after memoization was added. Identify the cause and give the complete fixed code.\n\n```python\ndef count_paths(grid):\n    rows, cols = len(grid), len(grid[0])\n    memo = {}\n    def go(r, c):\n        if r >= rows or c >= cols or grid[r][c] == 1:\n            return 0\n        if (r, c) == (rows - 1, cols - 1):\n            return 1\n        if r in memo:\n            return memo[r]\n        memo[r] = go(r + 1, c) + go(r, c + 1)\n        return memo[r]\n    return go(0, 0)\n```",
    """
    import unittest
    from solution import count_paths
    class T(unittest.TestCase):
        def test_open(self): self.assertEqual(count_paths([[0, 0, 0], [0, 0, 0], [0, 0, 0]]), 6)
        def test_blocked(self): self.assertEqual(count_paths([[0, 0, 0], [0, 1, 0], [0, 0, 0]]), 2)
        def test_start_blocked(self): self.assertEqual(count_paths([[1, 0], [0, 0]]), 0)
    """,
    "def count_paths(grid):\n    rows, cols = len(grid), len(grid[0])\n    memo = {}\n    def go(r, c):\n        if r >= rows or c >= cols or grid[r][c] == 1:\n            return 0\n        if (r, c) == (rows - 1, cols - 1):\n            return 1\n        if (r, c) not in memo:\n            memo[(r, c)] = go(r + 1, c) + go(r, c + 1)\n        return memo[(r, c)]\n    return go(0, 0)\n",
    "def count_paths(grid):\n    rows, cols = len(grid), len(grid[0])\n    memo = {}\n    def go(r, c):\n        if r >= rows or c >= cols or grid[r][c] == 1:\n            return 0\n        if (r, c) == (rows - 1, cols - 1):\n            return 1\n        if r in memo:\n            return memo[r]\n        memo[r] = go(r + 1, c) + go(r, c + 1)\n        return memo[r]\n    return go(0, 0)\n")

# ---- implementation ------------------------------------------------------------------------------
code_item("swx-impl-01", "implement", "hard",
    "`merge_labeled(intervals)`를 써 줘. 입력은 (start, end, label) 튜플 리스트이고 구간은 닫힌 구간 [start, end]다. 겹치거나 끝점이 맞닿는 구간을 합치고, 합친 구간마다 라벨 집합을 정렬된 리스트로 붙여 (start, end, labels) 튜플을 시작점 순서로 돌려줘.",
    "Write `merge_labeled(intervals)`. The input is a list of (start, end, label) tuples; intervals are closed [start, end]. Merge intervals that overlap or touch at an endpoint, attach the union of labels as a sorted list, and return (start, end, labels) tuples ordered by start.",
    """
    import unittest
    from solution import merge_labeled
    class T(unittest.TestCase):
        def test_merge(self):
            self.assertEqual(merge_labeled([(1, 3, "b"), (2, 5, "a"), (7, 8, "c")]), [(1, 5, ["a", "b"]), (7, 8, ["c"])])
        def test_touching(self):
            self.assertEqual(merge_labeled([(5, 6, "x"), (1, 5, "x")]), [(1, 6, ["x"])])
        def test_empty(self):
            self.assertEqual(merge_labeled([]), [])
    """,
    "def merge_labeled(intervals):\n    out = []\n    for s, e, l in sorted(intervals):\n        if out and s <= out[-1][1]:\n            ps, pe, pl = out[-1]\n            out[-1] = (ps, max(pe, e), pl | {l})\n        else:\n            out.append((s, e, {l}))\n    return [(s, e, sorted(l)) for s, e, l in out]\n")
code_item("swx-impl-02", "implement", "hard",
    "한국어 금액 표기를 정수로 바꾸는 `parse_krw(text)`를 써 줘. 예: \"1억 2천3백만 5천원\" → 123005000, \"3만원\" → 30000, \"12,500원\" → 12500, \"1억원\" → 100000000. 단위는 천·백·십(만 아래)과 만·억이다. 공백과 쉼표는 무시하고, 끝의 \"원\"은 있어도 없어도 된다. 해석할 수 없는 입력은 ValueError를 던져.",
    "Write `parse_krw(text)` that turns a Korean won amount into an integer. Examples: \"1억 2천3백만 5천원\" → 123005000, \"3만원\" → 30000, \"12,500원\" → 12500, \"1억원\" → 100000000. Units are 천/백/십 (below 만) and 만/억. Ignore spaces and commas; a trailing \"원\" is optional. Raise ValueError for input that cannot be parsed.",
    """
    import unittest
    from solution import parse_krw
    class T(unittest.TestCase):
        def test_examples(self):
            self.assertEqual(parse_krw("1억 2천3백만 5천원"), 123005000)
            self.assertEqual(parse_krw("3만원"), 30000)
            self.assertEqual(parse_krw("12,500원"), 12500)
            self.assertEqual(parse_krw("1억원"), 100000000)
        def test_mixed(self):
            self.assertEqual(parse_krw("2억 50만"), 200500000)
            self.assertEqual(parse_krw("7천"), 7000)
        def test_bad(self):
            with self.assertRaises(ValueError):
                parse_krw("abc")
    """,
    textwrap.dedent('''
    import re

    SMALL = {"천": 1000, "백": 100, "십": 10}
    BIG = {"억": 10 ** 8, "만": 10 ** 4}

    def _small(part):
        if not part:
            return 0
        total, num = 0, ""
        for ch in part:
            if ch.isdigit():
                num += ch
            elif ch in SMALL:
                total += int(num or "1") * SMALL[ch]
                num = ""
            else:
                raise ValueError(part)
        return total + (int(num) if num else 0)

    def parse_krw(text):
        s = re.sub(r"[\\s,]", "", text or "")
        if s.endswith("원"):
            s = s[:-1]
        if not s or not re.fullmatch(r"[0-9천백십만억]+", s):
            raise ValueError(text)
        total = 0
        for unit in ("억", "만"):
            if unit in s:
                head, s = s.split(unit, 1)
                total += (_small(head) or 1) * BIG[unit]
        return total + _small(s)
    '''))
code_item("swx-impl-03", "implement", "hard",
    "슬라이딩 윈도 레이트 리미터 `allow_requests(timestamps, limit, window)`를 써 줘. timestamps는 오름차순 초 단위 정수 리스트다. 각 요청 시각 t에서 (t - window, t] 구간에 이미 허용된 요청이 limit개 미만이면 허용한다. 거부된 요청은 개수에 넣지 않는다. 요청마다 허용 여부(bool) 리스트를 돌려줘.",
    "Write a sliding-window rate limiter `allow_requests(timestamps, limit, window)`. timestamps is an ascending list of integer seconds. A request at time t is allowed if fewer than limit already-allowed requests fall in (t - window, t]. Rejected requests do not count. Return a list of booleans, one per request.",
    """
    import unittest
    from solution import allow_requests
    class T(unittest.TestCase):
        def test_basic(self):
            self.assertEqual(allow_requests([0, 1, 2, 3], 2, 10), [True, True, False, False])
        def test_window_slides(self):
            self.assertEqual(allow_requests([0, 5, 10, 11, 15], 2, 10), [True, True, True, False, True])
        def test_rejected_do_not_count(self):
            self.assertEqual(allow_requests([0, 0, 0, 10], 1, 10), [True, False, False, True])
    """,
    "from collections import deque\n\ndef allow_requests(timestamps, limit, window):\n    allowed, out = deque(), []\n    for t in timestamps:\n        while allowed and allowed[0] <= t - window:\n            allowed.popleft()\n        ok = len(allowed) < limit\n        if ok:\n            allowed.append(t)\n        out.append(ok)\n    return out\n")
code_item("swx-impl-04", "implement", "medium",
    "`business_days(start, end, holidays)`를 써 줘. start와 end는 YYYY-MM-DD 문자열, holidays는 같은 형식 문자열 집합이다. start 이상 end 미만인 날 중 토·일과 holidays를 뺀 영업일 수를 돌려줘. end가 start보다 같거나 이르면 0.",
    "Write `business_days(start, end, holidays)`. start and end are YYYY-MM-DD strings; holidays is a set of strings in the same format. Return the number of business days from start (inclusive) to end (exclusive), excluding Saturdays, Sundays and holidays. Return 0 when end is not after start.",
    """
    import unittest
    from solution import business_days
    class T(unittest.TestCase):
        def test_week(self): self.assertEqual(business_days("2026-10-05", "2026-10-12", set()), 5)
        def test_holiday(self): self.assertEqual(business_days("2026-10-05", "2026-10-12", {"2026-10-09"}), 4)
        def test_weekend_holiday(self): self.assertEqual(business_days("2026-10-05", "2026-10-12", {"2026-10-10"}), 5)
        def test_empty(self): self.assertEqual(business_days("2026-10-12", "2026-10-05", set()), 0)
    """,
    "from datetime import date, timedelta\n\ndef business_days(start, end, holidays):\n    d, e, n = date.fromisoformat(start), date.fromisoformat(end), 0\n    while d < e:\n        if d.weekday() < 5 and d.isoformat() not in holidays:\n            n += 1\n        d += timedelta(days=1)\n    return n\n")

# ---- review (pick the most serious problem) ------------------------------------------------------
choice_item("swx-review-01", "review", "medium",
    "다음 코드를 검토해서 가장 심각한 문제를 골라 줘.\n\n```python\ndef find_user(conn, email):\n    cur = conn.cursor()\n    cur.execute(f\"SELECT id, name FROM users WHERE email = '{email}'\")\n    return cur.fetchone()\n```",
    "Review this code and pick the most serious problem.\n\n```python\ndef find_user(conn, email):\n    cur = conn.cursor()\n    cur.execute(f\"SELECT id, name FROM users WHERE email = '{email}'\")\n    return cur.fetchone()\n```",
    ["커서를 닫지 않아 성능이 떨어진다", "email에 인덱스가 없을 수 있다", "사용자 입력을 문자열로 이어 붙여 SQL 인젝션에 취약하다", "결과가 여러 줄일 수 있는데 하나만 돌려준다"],
    ["The cursor is never closed, which hurts performance", "email may not be indexed", "User input is interpolated into the SQL string, allowing SQL injection", "There may be several rows but only one is returned"],
    "C")
choice_item("swx-review-02", "review", "medium",
    "회원가입 코드야. 가장 심각한 문제를 골라 줘.\n\n```python\nimport hashlib\n\ndef store_password(db, user_id, password):\n    digest = hashlib.md5(password.encode()).hexdigest()\n    db.execute(\"UPDATE users SET pw = ? WHERE id = ?\", (digest, user_id))\n```",
    "This is sign-up code. Pick the most serious problem.\n\n```python\nimport hashlib\n\ndef store_password(db, user_id, password):\n    digest = hashlib.md5(password.encode()).hexdigest()\n    db.execute(\"UPDATE users SET pw = ? WHERE id = ?\", (digest, user_id))\n```",
    ["솔트 없는 빠른 해시(MD5)라 유출 시 비밀번호가 쉽게 복원된다", "SQL 인젝션에 취약하다", "인코딩을 지정하지 않아 한글 비밀번호가 깨진다", "UPDATE 대신 INSERT를 써야 한다"],
    ["An unsalted fast hash (MD5) makes leaked passwords easy to recover", "It is vulnerable to SQL injection", "No encoding is given, so non-ASCII passwords break", "It should use INSERT instead of UPDATE"],
    "A")
choice_item("swx-review-03", "review", "hard",
    "여러 요청이 동시에 처리되는 출금 핸들러야. 가장 심각한 문제를 골라 줘.\n\n```python\ndef withdraw(db, account_id, amount):\n    balance = db.get_balance(account_id)\n    if balance < amount:\n        raise ValueError(\"insufficient\")\n    db.set_balance(account_id, balance - amount)\n```",
    "This withdrawal handler serves many requests concurrently. Pick the most serious problem.\n\n```python\ndef withdraw(db, account_id, amount):\n    balance = db.get_balance(account_id)\n    if balance < amount:\n        raise ValueError(\"insufficient\")\n    db.set_balance(account_id, balance - amount)\n```",
    ["음수 금액을 검사하지 않는다", "잔액 확인과 갱신 사이에 다른 요청이 끼어들어 이중 출금이 될 수 있다", "예외 메시지가 사용자에게 불친절하다", "함수 이름이 동사로 시작하지 않는다"],
    ["It does not reject negative amounts", "Another request can slip in between the balance check and the update, allowing a double withdrawal", "The exception message is unfriendly", "The function name is not a verb"],
    "B")
choice_item("swx-review-04", "review", "hard",
    "로그 파일 수천 개를 처리하는 코드야. 장시간 돌리면 결국 실패한다. 가장 가능성 높은 원인을 골라 줘.\n\n```python\ndef count_errors(paths):\n    total = 0\n    for p in paths:\n        f = open(p, encoding=\"utf-8\")\n        for line in f:\n            if \"ERROR\" in line:\n                total += 1\n    return total\n```",
    "This processes thousands of log files and eventually fails on long runs. Pick the most likely cause.\n\n```python\ndef count_errors(paths):\n    total = 0\n    for p in paths:\n        f = open(p, encoding=\"utf-8\")\n        for line in f:\n            if \"ERROR\" in line:\n                total += 1\n    return total\n```",
    ["대소문자를 구분해 error를 놓친다", "파일을 닫지 않아 파일 디스크립터가 고갈될 수 있다", "utf-8이 아닌 파일에서 느려진다", "total이 정수 범위를 넘는다"],
    ["It is case-sensitive and misses 'error'", "Files are never closed, so file descriptors can run out", "It slows down on non-UTF-8 files", "total overflows the integer range"],
    "B")

# ---- explain (predict output; computed here) -------------------------------------------------------
snippet1 = "fs = [lambda: i for i in range(3)]\nprint([f() for f in fs])"
assert run_snippet(snippet1) == "[2, 2, 2]"
output_item("swx-explain-01", "medium", "이 코드는 무엇을 출력해?", "What does this code print?", snippet1, "<출력 그대로>", "<the exact output>", ["[2, 2, 2]"])
snippet2 = "scores = {\"kim\": 82, \"lee\": 95, \"park\": 88, \"choi\": 95}\nprint(sorted(scores, key=scores.get, reverse=True)[:2])"
assert run_snippet(snippet2) == "['lee', 'choi']"
output_item("swx-explain-02", "hard", "이 코드는 무엇을 출력해? (정렬은 안정 정렬이다)", "What does this code print? (the sort is stable)", snippet2, "<출력 그대로>", "<the exact output>", ["['lee', 'choi']"])
snippet3 = "print(round(2.5), round(3.5), -7 // 2, -7 % 3)"
assert run_snippet(snippet3) == "2 4 -4 2"
output_item("swx-explain-03", "hard", "파이썬 3에서 이 코드는 무엇을 출력해?", "What does this print in Python 3?", snippet3, "<출력 그대로>", "<the exact output>", ["2 4 -4 2"])

# ---- test design (which input exposes the bug; computed here) ---------------------------------------
def is_leap_buggy(y): return y % 4 == 0 and y % 100 != 0
def is_leap(y): return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)
opts = [2024, 1900, 2000, 2023]
catching = [chr(65 + i) for i, y in enumerate(opts) if is_leap(y) != is_leap_buggy(y)]
assert catching == ["C"]
choice_item("swx-test-01", "test", "medium",
    "윤년 판정 함수가 아래처럼 구현돼 있다. 이 구현의 버그를 드러내는 테스트 입력은 어느 것?\n\n```python\ndef is_leap(y):\n    return y % 4 == 0 and y % 100 != 0\n```",
    "A leap-year function is implemented as below. Which test input exposes its bug?\n\n```python\ndef is_leap(y):\n    return y % 4 == 0 and y % 100 != 0\n```",
    [f"is_leap({y})" for y in opts], [f"is_leap({y})" for y in opts], catching[0])
def clamp_buggy(x, lo, hi): return max(min(x, lo), hi)
def clamp(x, lo, hi): return min(max(x, lo), hi)
copts = [(0, 0, 0), (10, 0, 10), (5, 5, 5), (3, 0, 10)]
catching = [chr(65 + i) for i, a in enumerate(copts) if clamp(*a) != clamp_buggy(*a)]
assert catching == ["D"]
choice_item("swx-test-02", "test", "medium",
    "x를 [lo, hi] 범위로 자르는 함수가 아래처럼 잘못 구현됐다. 아래 테스트 중 이 버그를 잡아내는 것은 하나뿐이다. 어느 것?\n\n```python\ndef clamp(x, lo, hi):\n    return max(min(x, lo), hi)\n```",
    "A function meant to clamp x into [lo, hi] is implemented incorrectly as below. Exactly one of these tests catches the bug. Which one?\n\n```python\ndef clamp(x, lo, hi):\n    return max(min(x, lo), hi)\n```",
    [f"clamp{a} == {clamp(*a)}" for a in copts], [f"clamp{a} == {clamp(*a)}" for a in copts], catching[0])
def dedupe_buggy(xs): return list(set(xs))
def dedupe(xs): return list(dict.fromkeys(xs))
dopts = [[], [1], [1, 1, 1], [3, 1, 3, 2]]
catching = [chr(65 + i) for i, a in enumerate(dopts) if dedupe(a) != dedupe_buggy(a)]
assert catching == ["D"]
choice_item("swx-test-03", "test", "hard",
    "중복을 지우되 처음 나온 순서를 지켜야 하는 함수가 아래처럼 구현됐다(CPython 3). 아래 테스트 중 이 버그를 잡아내는 것은 어느 것?\n\n```python\ndef dedupe(xs):\n    return list(set(xs))\n```",
    "A function must remove duplicates while keeping first-occurrence order; it is implemented as below (CPython 3). Which of these tests catches the bug?\n\n```python\ndef dedupe(xs):\n    return list(set(xs))\n```",
    [f"dedupe({a}) == {dedupe(a)}" for a in dopts], [f"dedupe({a}) == {dedupe(a)}" for a in dopts], catching[0])


def verify():
    failures = []
    for id_, tests, reference, buggy in VERIFY:
        if not run_tests(tests, reference):
            failures.append(f"{id_}: reference fails")
        if buggy is not None and run_tests(tests, buggy):
            failures.append(f"{id_}: buggy code passes")
    return failures


if __name__ == "__main__":
    problems = verify()
    if problems:
        sys.exit("verification failed: " + "; ".join(problems))
    OUT.write_text(json.dumps(ITEMS, ensure_ascii=False, indent=1) + "\n")
    print(f"verified {len(VERIFY)} test suites; wrote {len(ITEMS)} items ({len(ITEMS) * 2} tasks) to {OUT.relative_to(ROOT)}")
