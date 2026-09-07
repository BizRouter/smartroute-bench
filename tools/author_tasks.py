#!/usr/bin/env python3
"""Emit both task suites from one bilingual source.

Source of truth: tasks/_src/<category>.json — a list of bilingual task records.
Each record carries the shared skeleton once (id, category, mode, max_tokens,
check type, …) and a `ko` / `en` block for the parts that must differ (prompt
text, rubric wording, locale-specific expected values).

    tasks/_src/*.json ──► tasks/k/*.jsonl        Korean, split=dev   (public)
                      ├─► tasks/base/*.jsonl     English, split=dev  (public)
                      └─► internal/tasks/{k,base}/*.jsonl   split=test (held out)

Why one source: the two suites used to be maintained separately, and the English
one silently rotted — it was last run 2026-07-21 while the Korean one kept
going. A twin that cannot be edited on one side alone cannot drift.

    python3 tools/author_tasks.py                # emit
    python3 tools/author_tasks.py --verify       # re-emit and diff, write nothing
    python3 tools/author_tasks.py --stats

Emission fails loudly on a twin whose structure diverges (different check type,
turn count, max_tokens, …) — that is the invariant the single source buys.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generators  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "tasks", "_src")

# suite key -> (public dir, held-out dir, default locale)
SUITES = {
    "ko": ("tasks/k", "internal/tasks/k", "ko"),
    "en": ("tasks/base", "internal/tasks/base", "en"),
}

# emitted field order — v1 fields first so diffs against old runs stay readable
FIELD_ORDER = ("id", "category", "subcategory", "difficulty", "turns", "rubric",
               "check", "max_tokens", "system", "language", "locale", "mode",
               "reference", "track", "block", "difficulty_prior",
               "difficulty_band", "length_band", "scoring", "split", "source",
               "pair_id", "v1_category")

SHARED_KEYS = ("id", "pair_id", "track", "block", "category", "subcategory",
               "difficulty_prior", "mode", "language", "max_tokens", "scoring",
               "split", "source", "length_band", "difficulty_band",
               "v1_category")
LANG_KEYS = ("turns", "rubric", "system", "check", "reference", "locale")

# a twin may differ only in wording — these must match on both sides
TWIN_INVARIANTS = ("mode", "max_tokens", "turn_count", "check_type",
                   "rubric_weights")

BAND_CAPS = (("short", 4000), ("mid", 32000), ("long", 260000))


# ── source ──────────────────────────────────────────────────────────────────

def load_source() -> list[dict]:
    recs = []
    for path in sorted(glob.glob(os.path.join(SRC_DIR, "*.json"))):
        with open(path, encoding="utf-8") as f:
            try:
                items = json.load(f)
            except json.JSONDecodeError as e:
                raise SystemExit(f"{os.path.relpath(path, ROOT)}: {e}")
        if not isinstance(items, list):
            raise SystemExit(f"{os.path.relpath(path, ROOT)}: expected a JSON list")
        for rec in items:
            rec["_src"] = os.path.relpath(path, ROOT)
            expand_generated(rec)
            recs.append(rec)
    return recs


def expand_generated(rec: dict) -> None:
    """Materialise `generate` specs into real turns and checks.

    Long-context prompts are built from a compact spec rather than committed as
    prose: hand-writing a 40k-character log and then hand-counting the answer
    over it is how a suite acquires a wrong golden that nobody catches. The
    generator computes the expected answer from the same data it renders, so the
    two cannot disagree.
    """
    for side in ("ko", "en"):
        spec = (rec.get(side) or {}).get("generate")
        if not spec:
            continue
        built = generators.build(spec, side)
        # An explicit field in the source still wins, so a generated task can be
        # tweaked without forking the generator.
        for key, value in built.items():
            rec[side].setdefault(key, value)
        rec[side].pop("generate", None)


def measured_band(rec: dict) -> str:
    """Widest twin decides — a band has to hold for both languages."""
    n = 0
    for side in ("ko", "en"):
        v = rec.get(side) or {}
        n = max(n, sum(len(x) for x in v.get("turns") or []) +
                len(v.get("system") or ""))
    for band, cap in BAND_CAPS:
        if n <= cap:
            return band
    return "long"


def twin_signature(rec: dict, side: str) -> dict:
    v = rec.get(side) or {}
    check = v.get("check") if v.get("check") is not None else rec.get("check")
    rubric = v.get("rubric") or rec.get("rubric") or []
    return {
        "mode": rec.get("mode", "chat"),
        "max_tokens": rec.get("max_tokens", 4096),
        "turn_count": len(v.get("turns") or []),
        "check_type": (check or {}).get("type"),
        "rubric_weights": [round(float(r["weight"]), 6) for r in rubric],
    }


def build(rec: dict, side: str) -> dict:
    """One emitted task for one suite."""
    _pub, _held, default_locale = SUITES[side]
    v = rec.get(side) or {}
    out = {}
    for key in SHARED_KEYS:
        if key in rec:
            out[key] = rec[key]
    for key in LANG_KEYS:
        if key in v:
            out[key] = v[key]
        elif key in rec:              # shared fallback (identical in both twins)
            out[key] = rec[key]
    out.setdefault("locale", default_locale)
    out.setdefault("system", None)
    out.setdefault("reference", None)
    out.setdefault("check", None)
    out.setdefault("language", None)
    out.setdefault("subcategory", None)
    out.setdefault("mode", "chat")
    out.setdefault("max_tokens", 4096)
    # `difficulty` stays the authored prior so v1..v10 reporting keeps working;
    # the empirical band from the score matrix lands in `difficulty_band`.
    out["difficulty"] = rec["difficulty_prior"]
    out.setdefault("difficulty_band", None)
    out["length_band"] = rec.get("length_band") or measured_band(rec)
    out.setdefault("pair_id", rec["id"])
    ordered = {k: out[k] for k in FIELD_ORDER if k in out}
    ordered.update({k: v2 for k, v2 in out.items() if k not in ordered})
    return ordered


def validate(recs: list[dict]) -> list[str]:
    errs = []
    seen = {}
    for rec in recs:
        tid = rec.get("id")
        if not tid:
            errs.append(f"{rec.get('_src')}: record without an id")
            continue
        if tid in seen:
            errs.append(f"duplicate id {tid} ({seen[tid]} and {rec['_src']})")
        seen[tid] = rec["_src"]
        for req in ("track", "block", "category", "difficulty_prior",
                    "scoring", "split", "source"):
            if not rec.get(req):
                errs.append(f"{tid}: missing required field '{req}'")
        for side in ("ko", "en"):
            if not (rec.get(side) or {}).get("turns"):
                errs.append(f"{tid}: {side} side has no turns")
        sig_ko, sig_en = twin_signature(rec, "ko"), twin_signature(rec, "en")
        for key in TWIN_INVARIANTS:
            if sig_ko[key] != sig_en[key]:
                errs.append(f"{tid}: twin invariant '{key}' differs "
                            f"(ko={sig_ko[key]!r} en={sig_en[key]!r})")
        declared = rec.get("length_band")
        if declared and declared != measured_band(rec):
            errs.append(f"{tid}: length_band={declared} but the prompts measure "
                        f"{measured_band(rec)}")
        if rec.get("scoring") in ("deterministic", "both"):
            for side in ("ko", "en"):
                if not ((rec.get(side) or {}).get("check") or rec.get("check")):
                    errs.append(f"{tid}: scoring={rec['scoring']} but the {side} "
                                f"side has no check")
        if rec.get("scoring") in ("judge", "both"):
            for side in ("ko", "en"):
                if not ((rec.get(side) or {}).get("rubric") or rec.get("rubric")):
                    errs.append(f"{tid}: scoring={rec['scoring']} but the {side} "
                                f"side has no rubric")
    return errs


# ── emit ────────────────────────────────────────────────────────────────────

def group(recs: list[dict], side: str, split: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for rec in recs:
        if rec.get("split") != split:
            continue
        out.setdefault(rec["category"], []).append(build(rec, side))
    for items in out.values():
        items.sort(key=lambda t: t["id"])
    return out


def write_suite(dirpath: str, by_cat: dict[str, list[dict]], dry: bool) -> int:
    """Rewrite the suite's *.jsonl. Only files this tool owns are removed —
    fixtures/ and _golden/ are directories and are left alone."""
    if not by_cat:
        return 0
    if not dry:
        os.makedirs(dirpath, exist_ok=True)
        for stale in glob.glob(os.path.join(dirpath, "*.jsonl")):
            os.remove(stale)
    n = 0
    for cat, items in sorted(by_cat.items()):
        n += len(items)
        if dry:
            continue
        with open(os.path.join(dirpath, f"{cat}.jsonl"), "w",
                  encoding="utf-8") as f:
            for t in items:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")
    return n


def verify(recs: list[dict]) -> int:
    """Re-emit and diff against what is on disk, field by field.

    `category` is expected to change (the v2 spec splits `coding` four ways and
    moves the authored reasoning tasks into the capability track), and the v2
    bookkeeping fields are new. Everything a model actually sees — prompts,
    rubrics, checks, token limits — must be byte-identical, or the migration
    lost something.
    """
    ignore = {"category", "track", "block", "difficulty_prior", "difficulty_band",
              "length_band", "scoring", "split", "source", "pair_id",
              "v1_category"}
    problems = 0
    for side, (pub, _held, _loc) in SUITES.items():
        disk = {}
        for path in sorted(glob.glob(os.path.join(ROOT, pub, "*.jsonl"))):
            for line in open(path, encoding="utf-8"):
                if line.strip():
                    t = json.loads(line)
                    disk[t["id"]] = t
        fresh = {t["id"]: t for cat in group(recs, side, "dev").values()
                 for t in cat}
        only_disk, only_fresh = set(disk) - set(fresh), set(fresh) - set(disk)
        if only_disk or only_fresh:
            print(f"  {pub}: ids on disk only {sorted(only_disk)}, "
                  f"emitted only {sorted(only_fresh)}")
            problems += len(only_disk) + len(only_fresh)
        for tid in sorted(set(disk) & set(fresh)):
            for key in sorted(set(disk[tid]) | set(fresh[tid])):
                if key in ignore:
                    continue
                a, b = disk[tid].get(key), fresh[tid].get(key)
                if a != b:
                    print(f"  {pub} {tid}: {key} differs")
                    print(f"      disk: {json.dumps(a, ensure_ascii=False)[:180]}")
                    print(f"      emit: {json.dumps(b, ensure_ascii=False)[:180]}")
                    problems += 1
    return problems


def stats(recs: list[dict]) -> None:
    from collections import Counter
    print(f"source: {len(recs)} bilingual pairs -> {len(recs) * 2} tasks")
    for field in ("track", "block", "split", "scoring", "length_band", "mode"):
        c = Counter(r.get(field) for r in recs)
        print(f"  {field:<12} " + "  ".join(f"{k}={v}" for k, v in c.most_common()))
    c = Counter(r.get("category") for r in recs)
    print("  categories   " + "  ".join(f"{k}={v}" for k, v in sorted(c.items())))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true",
                    help="re-emit and diff against the suites on disk; write nothing")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    recs = load_source()
    if not recs:
        raise SystemExit(f"no sources found under {os.path.relpath(SRC_DIR, ROOT)}")

    errs = validate(recs)
    if errs:
        print(f"{len(errs)} source error(s):", file=sys.stderr)
        for e in errs[:40]:
            print("  ✗", e, file=sys.stderr)
        if len(errs) > 40:
            print(f"  … and {len(errs) - 40} more", file=sys.stderr)
        return 1

    if args.stats:
        stats(recs)
        return 0

    if args.verify:
        print("verifying re-emission against the suites on disk …")
        problems = verify(recs)
        if problems:
            print(f"\n{problems} difference(s) — the source is NOT lossless")
            return 1
        print("  every prompt, rubric, check and token limit is identical")
        return 0

    for side, (pub, held, _loc) in SUITES.items():
        n_dev = write_suite(os.path.join(ROOT, pub),
                            group(recs, side, "dev"), args.dry_run)
        n_test = write_suite(os.path.join(ROOT, held),
                             group(recs, side, "test"), args.dry_run)
        tag = "(dry) " if args.dry_run else ""
        print(f"{tag}{pub}: {n_dev} dev" +
              (f"  ·  {held}: {n_test} held out" if n_test else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
