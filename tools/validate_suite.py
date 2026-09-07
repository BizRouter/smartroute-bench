#!/usr/bin/env python3
"""Bias-budget gate for the task suites.

Checks tasks/k and tasks/base against configs/suite_spec_v2.json — the
machine-readable form of docs/DESIGN_v2.md — and exits non-zero when the suite
drifts out of tolerance.

The point is that suite balance is an *invariant*, not something a human
re-audits by eye every time a task is added. A hand-checked balance rots on the
first commit.

    python3 tools/validate_suite.py                 # both suites, hard gate
    python3 tools/validate_suite.py --warn-only     # report, always exit 0
    python3 tools/validate_suite.py --suite tasks/k

During migration most v1 tasks lack the v2 fields. Those are counted and shown
as *unmigrated* rather than raising a per-task error, so the same command
doubles as a progress report.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import re
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC_PATH = os.path.join(ROOT, "configs", "suite_spec_v2.json")
DEFAULT_SUITES = ["tasks/base", "tasks/k"]


# ── loading ─────────────────────────────────────────────────────────────────

def load_tasks(suite_dir: str) -> list[dict]:
    """Every *.jsonl under the suite, including sub-directories (capability/)."""
    tasks = []
    pattern = os.path.join(suite_dir, "**", "*.jsonl")
    for path in sorted(glob.glob(pattern, recursive=True)):
        if os.sep + "_golden" + os.sep in path + os.sep:
            continue
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    t = json.loads(line)
                except json.JSONDecodeError as e:
                    raise SystemExit(f"{path}:{lineno}: unparseable task line: {e}")
                t["_path"] = os.path.relpath(path, ROOT)
                tasks.append(t)
    return tasks


def input_chars(t: dict) -> int:
    n = sum(len(x) for x in t.get("turns") or [])
    n += len(t.get("system") or "")
    for key in ("context", "attachment"):
        n += len(t.get(key) or "")
    return n


def is_migrated(t: dict, required: list[str]) -> bool:
    return all(t.get(f) not in (None, "") for f in required)


# ── report helpers ──────────────────────────────────────────────────────────

class Report:
    def __init__(self, warn_only: bool):
        self.warn_only = warn_only
        self.violations: list[str] = []
        self.notes: list[str] = []

    def fail(self, rule: str, msg: str) -> None:
        self.violations.append(f"[{rule}] {msg}")

    def note(self, msg: str) -> None:
        self.notes.append(msg)


def pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:5.1f}%" if whole else "    —"


def bar(actual: int, target: int, width: int = 18) -> str:
    if target <= 0:
        return ""
    filled = min(width, int(round(width * actual / target)))
    return "█" * filled + "·" * (width - filled)


# ── checks ──────────────────────────────────────────────────────────────────

def flat_targets(spec: dict) -> dict[str, int]:
    out = {}
    for _track, blocks in spec["composition"].items():
        for _block, cats in blocks.items():
            for cat, n in cats.items():
                out[cat] = n
    return out


def cat_block(spec: dict) -> dict[str, str]:
    out = {}
    for _track, blocks in spec["composition"].items():
        for block, cats in blocks.items():
            for cat in cats:
                out[cat] = block
    return out


def legacy_source(spec: dict) -> dict[str, str]:
    """v2 category -> the v1 category it will be carved out of (display only)."""
    out = {}
    for old, news in spec.get("legacy_category_map", {}).items():
        if old == "note":
            continue
        for new in news:
            if new != old:
                out[new] = old
    return out


def check_composition(tasks, spec, rep: Report, suite: str) -> None:
    targets = flat_targets(spec)
    tol = spec["category_tolerance"]
    actual = Counter(t.get("category") for t in tasks)
    total = len(tasks)
    legacy = legacy_source(spec)

    print(f"\n  composition ({total} tasks, target {spec['tasks_per_language']})")
    print(f"    {'category':<24}{'now':>5}{'target':>8}  progress             from v1")
    blocks = cat_block(spec)
    for cat, target in sorted(targets.items(), key=lambda kv: (blocks[kv[0]], kv[0])):
        now = actual.get(cat, 0)
        src = legacy.get(cat)
        pool = f"{src} ({actual.get(src, 0)})" if src and actual.get(src) else ""
        print(f"    {cat:<24}{now:>5}{target:>8}  {bar(now, target)}  {pool}")
        lo, hi = target * (1 - tol), target * (1 + tol)
        if now and not (lo <= now <= hi):
            rep.fail("composition",
                     f"{suite}: {cat} has {now}, target {target} (±{int(tol*100)}%)")
    unknown = {c: n for c, n in actual.items() if c not in targets}
    for cat, n in sorted(unknown.items()):
        tag = "v1, to be split" if cat in spec.get("legacy_category_map", {}) \
            else "not in spec"
        print(f"    {cat:<24}{n:>5}{'—':>8}  ({tag})")
        if cat not in spec.get("legacy_category_map", {}):
            rep.note(f"{suite}: category '{cat}' is not in the v2 spec ({n} tasks)")


def check_scoring(tasks, spec, rep: Report, suite: str) -> None:
    """B1 — deterministic share, and scoring declarations must match reality."""
    total = len(tasks)
    if not total:
        return
    det = sum(1 for t in tasks if t.get("scoring") in ("deterministic", "both"))
    # v1 fallback: a task with a check is deterministic in practice
    det_effective = sum(1 for t in tasks
                        if t.get("scoring") in ("deterministic", "both") or
                        (t.get("scoring") is None and t.get("check")))
    floor = spec["bias_budget"]["B1_scoring"]["min_deterministic_share"]
    print(f"\n  B1 scoring: deterministic {det_effective}/{total} "
          f"({pct(det_effective, total)}), floor {floor:.0%}")
    if det_effective / total < floor:
        rep.fail("B1", f"{suite}: deterministic share {det_effective/total:.0%} "
                       f"< floor {floor:.0%} — judge-only suites saturate")

    for t in tasks:
        sc = t.get("scoring")
        if sc in ("deterministic", "both") and not t.get("check"):
            rep.fail("B1", f"{suite}: {t['id']} declares scoring={sc} but has no check")
        if sc in ("judge", "both") and not t.get("rubric"):
            rep.fail("B1", f"{suite}: {t['id']} declares scoring={sc} but has no rubric")
    _ = det


def check_ceiling(tasks, spec, rep: Report, suite: str) -> None:
    """B2 — empirical difficulty bands from the score matrix.

    The band is what the candidate pool actually did on the task, computed with
    the same rule the oracle metrics use (src/smartroute_bench/oracle.py), so
    this gate and the report cannot disagree. Without a matrix for the suite the
    gate is reported as unmeasured rather than silently passed."""
    b2 = spec["bias_budget"].get("B2_ceiling")
    if not b2:
        return
    name = os.path.basename(suite.rstrip("/"))
    matrix_path = os.path.join(ROOT, "results", "_matrix", f"pool{name}.json")
    if not os.path.exists(matrix_path):
        print(f"\n  B2 ceiling: no score matrix at results/_matrix/pool{name}.json — unmeasured")
        rep.note(f"{suite}: B2 (discriminative share) unmeasured — build a pool matrix "
                 f"with tools/build_score_matrix.py")
        return
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from smartroute_bench import oracle  # noqa: E402
    with open(matrix_path, encoding="utf-8") as f:
        matrix = json.load(f)
    cells = matrix.get("tasks", {})
    bands: Counter = Counter()
    by_cat: dict[str, Counter] = defaultdict(Counter)
    unmeasured = 0
    for t in tasks:
        cell = cells.get(t["id"])
        if not cell:
            unmeasured += 1
            continue
        view = oracle.task_view(cell)
        costs = {n: v["cost"] for n, v in view["scored"].items()}
        cheapest = min(costs, key=lambda n: (costs[n], n)) if costs else None
        band = oracle.difficulty_band(view, cheapest)
        bands[band] += 1
        by_cat[t.get("category", "?")][band] += 1
    measured = sum(bands.values())
    if not measured:
        rep.note(f"{suite}: B2 unmeasured — matrix has none of this suite's tasks")
        return
    disc = bands.get("discriminative", 0) + bands.get("frontier-only", 0)
    unsolved = bands.get("unsolved", 0)
    floor = b2["min_discriminative_share"]
    cap = b2["max_unsolved_share"]
    print(f"\n  B2 ceiling (matrix {os.path.relpath(matrix_path, ROOT)} · "
          f"{measured} measured, {unmeasured} unmeasured):")
    for band in ("cheap-sufficient", "discriminative", "frontier-only", "unsolved", "unknown"):
        if bands.get(band):
            print(f"    {band:<18} {bands[band]:>4}  {pct(bands[band], measured)}")
    print(f"    discriminative+frontier-only {disc}/{measured} ({pct(disc, measured).strip()}), "
          f"floor {floor:.0%} · unsolved cap {cap:.0%}")
    worst = sorted(by_cat.items(),
                   key=lambda kv: -kv[1].get("cheap-sufficient", 0))[:6]
    print("    most cheap-sufficient categories: " +
          ", ".join(f"{c} {v.get('cheap-sufficient', 0)}/{sum(v.values())}" for c, v in worst))
    if disc / measured < floor:
        rep.fail("B2", f"{suite}: discriminative share {disc/measured:.0%} < floor {floor:.0%} "
                       f"— most tasks are solved by the cheapest model; the suite cannot rank routers")
    if unsolved / measured > cap:
        rep.fail("B2", f"{suite}: unsolved share {unsolved/measured:.0%} > cap {cap:.0%}")


def check_domain(tasks, spec, rep: Report, suite: str) -> None:
    """B6 — no category dominates; the code block is a declared exception."""
    total = len(tasks)
    if not total:
        return
    b6 = spec["bias_budget"]["B6_domain"]
    cats = Counter(t.get("category") for t in tasks)
    for cat, n in cats.most_common():
        if n / total > b6["max_category_share"]:
            rep.fail("B6", f"{suite}: category {cat} is {n/total:.0%} of the suite "
                           f"(cap {b6['max_category_share']:.0%})")
    blocks = Counter(t.get("block") for t in tasks if t.get("block"))
    print("\n  B6 domain:", ", ".join(f"{b} {pct(n, total).strip()}"
                                      for b, n in blocks.most_common()) or "—")
    for block, cap in b6["max_block_share"].items():
        n = blocks.get(block, 0)
        if n / total > cap:
            rep.fail("B6", f"{suite}: block {block} is {n/total:.0%} "
                           f"(declared cap {cap:.0%})")


def pair_max_chars(suites: dict[str, list[dict]]) -> dict[str, int]:
    """Longest input across a task's twins.

    A KO/EN pair shares one `length_band` (the twin invariant demands it), and
    tools/author_tasks.py assigns it from the *widest* side. Checking each suite
    in isolation would flag the shorter twin as mislabelled — the gate would be
    contradicting the emitter rather than checking it. Korean says the same
    thing in fewer characters, so this is the normal case, not an edge case."""
    out: dict[str, int] = {}
    for tasks in suites.values():
        for t in tasks:
            key = t.get("pair_id") or t["id"]
            out[key] = max(out.get(key, 0), input_chars(t))
    return out


def check_length(tasks, spec, rep: Report, suite: str,
                 pair_chars: dict[str, int] | None = None) -> None:
    """B7 — length band shares, and declared band must match measured input."""
    total = len(tasks)
    if not total:
        return
    b7 = spec["bias_budget"]["B7_length"]
    bands = spec["length_bands"]
    declared = Counter(t.get("length_band") for t in tasks if t.get("length_band"))
    print("\n  B7 length:", ", ".join(
        f"{b} {declared.get(b, 0)} ({pct(declared.get(b, 0), total).strip()}, "
        f"target {b7['target_shares'][b]:.0%})" for b in ("short", "mid", "long")))
    for band, want in b7["target_shares"].items():
        got = declared.get(band, 0) / total
        if declared and abs(got - want) > b7["tolerance"]:
            rep.fail("B7", f"{suite}: length band {band} is {got:.0%}, "
                           f"target {want:.0%} (±{b7['tolerance']:.0%})")
    for t in tasks:
        band = t.get("length_band")
        if not band:
            continue
        key = t.get("pair_id") or t["id"]
        n = (pair_chars or {}).get(key, input_chars(t))
        cap = bands[band]["max_input_chars"]
        lo = 0 if band == "short" else bands[
            {"mid": "short", "long": "mid"}[band]]["max_input_chars"]
        if not (lo < n <= cap):
            rep.fail("B7", f"{suite}: {t['id']} declares length_band={band} "
                           f"but its longest twin measures {n} chars")


def check_format(tasks, spec, rep: Report, suite: str) -> None:
    """B8 — multiple choice hands out free points, so cap its share and require
    enough options that a coin flip is not competitive."""
    if not tasks:
        return
    b8 = spec["bias_budget"]["B8_format"]
    mcq = [t for t in tasks if (t.get("check") or {}).get("format") == "mcq"]
    share = len(mcq) / len(tasks)
    cap = b8["max_mcq_share_of_suite"]
    print(f"\n  B8 format: MCQ {len(mcq)}/{len(tasks)} of the suite "
          f"({share:.0%}), cap {cap:.0%}")
    if share > cap:
        rep.fail("B8", f"{suite}: MCQ is {share:.0%} of the suite "
                       f"(cap {cap:.0%}) — the guessing floor inflates accuracy")
    floor = b8["min_mcq_options"]
    thin = []
    for t in mcq:
        # count the "A. …" lines the prompt actually offers
        n_opt = len(re.findall(r"^[A-J]\.\s", t["turns"][0] or "", re.M))
        if n_opt and n_opt < floor:
            thin.append((t["id"], n_opt))
    if thin:
        rep.fail("B8", f"{suite}: {len(thin)} MCQ task(s) offer fewer than "
                       f"{floor} options (e.g. {thin[0][0]} with {thin[0][1]}) — "
                       f"a coin flip would score as capability")


def check_split(tasks, spec, rep: Report, suite: str) -> None:
    """B9 — a held-out slice must exist, or 'we improved the router' is unfalsifiable."""
    labelled = [t for t in tasks if t.get("split")]
    if not labelled:
        return
    b9 = spec["bias_budget"]["B9_tuning"]
    test = sum(1 for t in labelled if t["split"] == "test")
    got = test / len(labelled)
    print(f"\n  B9 tuning: held-out test {test}/{len(labelled)} ({got:.0%}), "
          f"target {b9['test_share']:.0%}")
    if abs(got - b9["test_share"]) > b9["tolerance"]:
        rep.fail("B9", f"{suite}: test split is {got:.0%}, "
                       f"target {b9['test_share']:.0%} (±{b9['tolerance']:.0%})")


def check_twins(suites: dict[str, list[dict]], spec, rep: Report) -> None:
    """B4 — KO/EN twins must stay structurally identical, or one side rots."""
    names = list(suites)
    if len(names) < 2:
        return
    b4 = spec["bias_budget"]["B4_language"]
    fields = b4["twin_must_match"]
    by_pair: dict[str, dict[str, dict]] = defaultdict(dict)
    for suite, tasks in suites.items():
        for t in tasks:
            if t.get("pair_id"):
                by_pair[t["pair_id"]][suite] = t
    print()

    # Scoped per track: the work track must be paired (that is where language
    # changes the task, and where the English suite silently rotted). The
    # capability track is allowed a declared native slice.
    def counts(pred):
        total = sum(1 for tasks in suites.values() for t in tasks if pred(t))
        paired = sum(len(p) for pid, p in by_pair.items()
                     if len(p) == len(names) and pred(next(iter(p.values()))))
        return paired, total

    for label, pred, floor_key in (
            ("work", lambda t: t.get("track") == "work", "min_paired_share_work"),
            ("overall", lambda _t: True, "min_paired_share_overall")):
        paired, total = counts(pred)
        floor = b4[floor_key]
        print(f"  B4 language ({label}): paired {paired}/{total} "
              f"({pct(paired, total).strip()}), floor {floor:.0%}")
        if total and by_pair and paired / total < floor:
            rep.fail("B4", f"{label} paired share {paired/total:.0%} < floor "
                           f"{floor:.0%}")

    for pid, sides in sorted(by_pair.items()):
        if len(sides) < len(names):
            rep.fail("B4", f"pair_id {pid} exists only in {list(sides)}")
            continue
        ref_name, ref = next(iter(sides.items()))
        for other_name, other in list(sides.items())[1:]:
            for f in fields:
                a, b = _twin_value(ref, f), _twin_value(other, f)
                if a != b:
                    rep.fail("B4", f"pair_id {pid}: {f} differs "
                                   f"({ref_name}={a!r} vs {other_name}={b!r})")


def _twin_value(t: dict, field: str):
    if field == "turn_count":
        return len(t.get("turns") or [])
    if field == "check_type":
        return (t.get("check") or {}).get("type")
    if field == "max_tokens":
        return t.get("max_tokens", 4096)
    if field == "mode":
        return t.get("mode", "chat")
    return t.get(field)


def check_enums(tasks, spec, rep: Report, suite: str) -> None:
    for t in tasks:
        for field, allowed in spec["enums"].items():
            v = t.get(field)
            if v is not None and v not in allowed:
                rep.fail("schema", f"{suite}: {t['id']} has {field}={v!r}, "
                                   f"allowed {allowed}")


def check_ids(suites: dict[str, list[dict]], rep: Report) -> None:
    for suite, tasks in suites.items():
        seen = Counter(t.get("id") for t in tasks)
        for tid, n in seen.items():
            if n > 1:
                rep.fail("schema", f"{suite}: duplicate task id {tid} ({n}x)")


# ── main ────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default=SPEC_PATH)
    ap.add_argument("--suite", action="append", default=None,
                    help="suite dir (repeatable); default: tasks/base and tasks/k")
    ap.add_argument("--warn-only", action="store_true",
                    help="print the report but always exit 0")
    args = ap.parse_args()

    with open(args.spec, encoding="utf-8") as f:
        spec = json.load(f)
    suite_dirs = args.suite or DEFAULT_SUITES
    rep = Report(args.warn_only)

    suites: dict[str, list[dict]] = {}
    print(f"SmartRoute-Bench suite validator — spec {spec['spec_version']}")

    required = spec["required_fields_v2"]
    # Load every suite before checking any of them: the length-band rule is
    # defined across a KO/EN pair, not within one suite.
    for d in suite_dirs:
        path = d if os.path.isabs(d) else os.path.join(ROOT, d)
        if not os.path.isdir(path):
            rep.fail("setup", f"suite dir not found: {d}")
            continue
        suites[d] = load_tasks(path)
    pair_chars = pair_max_chars(suites)

    for d, tasks in suites.items():
        migrated = [t for t in tasks if is_migrated(t, required)]
        print(f"\n── {d} — {len(tasks)} tasks, "
              f"{len(migrated)} migrated to v2 schema "
              f"({pct(len(migrated), len(tasks)).strip()})")
        if len(migrated) < len(tasks):
            missing = Counter()
            for t in tasks:
                for f in required:
                    if t.get(f) in (None, ""):
                        missing[f] += 1
            print("    missing v2 fields: " +
                  ", ".join(f"{f} ({n})" for f, n in missing.most_common()))

        check_composition(tasks, spec, rep, d)
        check_scoring(tasks, spec, rep, d)
        check_ceiling(tasks, spec, rep, d)
        check_domain(tasks, spec, rep, d)
        check_length(tasks, spec, rep, d, pair_chars)
        check_format(tasks, spec, rep, d)
        check_split(tasks, spec, rep, d)
        check_enums(tasks, spec, rep, d)

    check_ids(suites, rep)
    check_twins(suites, spec, rep)

    print("\n" + "─" * 72)
    if rep.notes:
        print("notes:")
        for n in rep.notes:
            print("  ·", n)
    if rep.violations:
        print(f"{len(rep.violations)} violation(s):")
        for v in rep.violations[:60]:
            print("  ✗", v)
        if len(rep.violations) > 60:
            print(f"  … and {len(rep.violations) - 60} more")
        if args.warn_only:
            print("\n--warn-only: exiting 0 anyway")
            return 0
        return 1
    print("bias budget: all rules green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
