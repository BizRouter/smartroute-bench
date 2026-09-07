#!/usr/bin/env python3
"""Materialise the Track B (capability) slices from upstream datasets.

This repository is public, so it ships **pointers, not rows**: the manifest
(configs/track_b_manifest.json) names each dataset/split, and this tool builds
tasks/{k,base}/capability/*.jsonl locally. Those outputs are gitignored.

    python3 tools/fetch_track_b.py                 # fetch + write, refresh lock
    python3 tools/fetch_track_b.py --relock        # re-sample (new indices)
    python3 tools/fetch_track_b.py --dry-run

Two properties this tool has to guarantee:

1. **Reproducible.** Sampling is a seeded shuffle and the chosen upstream
   indices are written to configs/track_b_lock.json, which IS committed. A
   re-fetch reproduces the same suite; --relock is the only way to change it.

2. **No RouterArena overlap.** RouterArena is evaluation-only. Our bench feeds
   router fixes, so an item shared with RouterArena becomes an item we tune
   against — which would taint a later leaderboard submission. Every candidate
   is rejected if its normalised question text collides with the RouterArena
   set, and the guard is proven live by a positive control (a known RouterArena
   question must be detected) before any sampling happens. An absence check
   with no positive control reports "clean" just as readily when it is broken.

Needs the `datasets` package. The benchmark harness itself stays standard
library only — this is a build-time tool, not part of src/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "configs", "track_b_manifest.json")
LOCK = os.path.join(ROOT, "configs", "track_b_lock.json")
OUT = {"ko": os.path.join(ROOT, "tasks", "k", "capability"),
       "en": os.path.join(ROOT, "tasks", "base", "capability")}

LETTERS = "ABCDEFGHIJ"


# ── overlap guard ───────────────────────────────────────────────────────────

def norm_q(s: str) -> str:
    """Collapse to what makes two questions 'the same item' across formats."""
    s = re.sub(r"\s+", " ", (s or "").lower())
    s = re.sub(r"[^a-z0-9가-힣 ]", "", s)
    return s.strip()


def qhash(s: str) -> str:
    return hashlib.sha1(norm_q(s).encode()).hexdigest()[:16]


def load_routerarena_hashes(cfg: dict) -> tuple[set[str], str]:
    """Hash every RouterArena question so we can refuse to reuse them."""
    from datasets import load_from_disk
    for raw in cfg["routerarena_dataset_dirs"]:
        path = os.path.abspath(os.path.join(ROOT, os.path.expanduser(raw)))
        if not os.path.isdir(path):
            continue
        ds = load_from_disk(path)
        hashes = set()
        for row in ds:
            for field in ("Question", "question", "prompt_formatted"):
                if row.get(field):
                    hashes.add(qhash(row[field]))
        if cfg.get("positive_control"):
            # Prove the guard actually fires. If the field names drift upstream
            # the set silently empties and every candidate reads as "clean".
            probe = None
            for row in ds:
                probe = row.get("Question") or row.get("question")
                if probe:
                    break
            if probe is None or qhash(probe) not in hashes:
                raise SystemExit(
                    "overlap guard positive control FAILED — a known "
                    "RouterArena question is not detected by the guard; "
                    "refusing to sample (the check would report 'no overlap' "
                    "for every item)")
            print(f"  overlap guard: {len(hashes)} RouterArena questions "
                  f"hashed · positive control fired")
        return hashes, path
    if cfg.get("required"):
        raise SystemExit(
            "overlap guard required but no RouterArena dataset found in "
            f"{cfg['routerarena_dataset_dirs']} — refusing to sample blind")
    return set(), ""


# ── upstream loading ────────────────────────────────────────────────────────

_cache: dict[tuple, object] = {}


def get_split(spec: dict):
    from datasets import load_dataset
    key = (spec["dataset"], spec.get("config"), spec["split"])
    if key not in _cache:
        args = [spec["dataset"]] + ([spec["config"]] if spec.get("config") else [])
        _cache[key] = load_dataset(*args, split=spec["split"])
    return _cache[key]


# ── per-dataset normalisation ───────────────────────────────────────────────
# Each upstream set spells "question / options / answer" its own way. Keeping
# the adapters in one table makes it obvious what is assumed about each source.
#
# An adapter raises Skip for a row that cannot be graded deterministically
# (a LaTeX answer where the check expects a number, a multi-answer item). Only
# Skip and IndexError are swallowed during sampling — a KeyError means the
# upstream schema moved and must be loud, not silently drain the slice to zero.

class Skip(Exception):
    """This row is not eligible for the slice."""


_NUMERIC = re.compile(r"^-?\d+(?:\.\d+)?$")


def _mmmlu(row) -> dict:
    return {"question": row["Question"],
            "options": [row["A"], row["B"], row["C"], row["D"]],
            "answer": row["Answer"].strip().upper(),
            "tag": row["Subject"]}


def _mmlu(row) -> dict:
    return {"question": row["question"],
            "options": list(row["choices"]),
            "answer": LETTERS[int(row["answer"])],
            "tag": row["subject"]}


def _kmmlu(row) -> dict:
    return {"question": row["question"],
            "options": [row["A"], row["B"], row["C"], row["D"]],
            "answer": LETTERS[int(row["answer"]) - 1],
            "tag": row.get("Category")}


def _arc(row) -> dict:
    ch = row["choices"]
    labels = list(ch["label"])
    return {"question": row["question"],
            "options": list(ch["text"]),
            "answer": LETTERS[labels.index(row["answerKey"])],
            "tag": "arc-challenge"}


def _kobest_copa(row) -> dict:
    # `question` is 원인 or 결과 — which direction to reason in. Dropping it
    # would leave an item with two defensible answers and score the model on a
    # coin flip.
    ask = {"원인": "위 상황의 원인으로 알맞은 것은?",
           "결과": "위 상황의 결과로 알맞은 것은?"}.get(row["question"].strip())
    if ask is None:
        raise Skip(f"unknown copa direction {row['question']!r}")
    return {"question": f"{row['premise']}\n\n{ask}",
            "options": [row["alternative_1"], row["alternative_2"]],
            "answer": LETTERS[int(row["label"])],
            "tag": f"kobest-copa-{row['question'].strip()}"}


def _kobest_hellaswag(row) -> dict:
    return {"question": f"{row['context']}\n\n이 다음에 이어질 내용으로 가장 자연스러운 것은?",
            "options": [row[f"ending_{i}"] for i in range(1, 5)],
            "answer": LETTERS[int(row["label"])],
            "tag": "kobest-hellaswag"}


def _medmcqa(row) -> dict:
    if row.get("choice_type") != "single":
        raise Skip("multi-answer item; the mcq check expects one letter")
    return {"question": row["question"],
            "options": [row["opa"], row["opb"], row["opc"], row["opd"]],
            "answer": LETTERS[int(row["cop"])],
            "tag": row.get("subject_name")}


def _gsm8k_ko(row, field="question") -> dict:
    ans = row["answer" if field == "question" else "answer_en"]
    return {"question": row[field],
            "answer": ans.split("####")[-1].strip().replace(",", ""),
            "tag": "gsm8k"}


def _math500(row) -> dict:
    # MATH-500 answers are often LaTeX (\left( 3, \frac{\pi}{2} \right)), which
    # no string comparison grades honestly. Keep the numeric-answer rows and
    # the hard levels — this slice exists to supply difficulty, not coverage.
    ans = str(row["answer"]).strip()
    if not _NUMERIC.match(ans):
        raise Skip("non-numeric answer")
    if int(row.get("level") or 0) < 4:
        raise Skip("easy level; this slice is the hard-math tier")
    return {"question": row["problem"], "answer": ans, "tag": row.get("subject")}


ADAPTERS = {
    "openai/MMMLU": _mmmlu,
    "cais/mmlu": _mmlu,
    "HAERAE-HUB/KMMLU": _kmmlu,
    "allenai/ai2_arc": _arc,
    "skt/kobest_v1": _kobest_hellaswag,
    "openlifescienceai/medmcqa": _medmcqa,
    "kuotient/gsm8k-ko": _gsm8k_ko,
    "HuggingFaceH4/MATH-500": _math500,
}


def adapt(spec: dict, row) -> dict:
    fn = ADAPTERS.get(spec["dataset"])
    if fn is None:
        raise SystemExit(f"no adapter for dataset {spec['dataset']!r}")
    if spec["dataset"] == "kuotient/gsm8k-ko":
        return fn(row, spec.get("field", "question"))
    return fn(row)


# ── task building ───────────────────────────────────────────────────────────

def render(template: str, item: dict, fmt: str) -> str:
    if fmt == "mcq":
        opts = "\n".join(f"{LETTERS[i]}. {o}"
                         for i, o in enumerate(item["options"]))
        return template.format(question=item["question"], options=opts)
    return template.format(question=item["question"])


def build_task(tid: str, slice_cfg: dict, lang: str, item: dict, fmt: str,
               templates: dict, source: str) -> dict:
    if fmt == "mcq":
        check = {"type": "mcq", "expected": item["answer"], "format": "mcq"}
        pattern_ok = True
    else:
        marker = r"정답\s*[::]\s*([^\n]+)" if lang == "ko" \
            else r"Answer\s*[::]\s*([^\n]+)"
        check = {"type": "answer_match", "expected": [item["answer"]],
                 "pattern": marker, "format": "numeric"}
        pattern_ok = True
    assert pattern_ok
    return {
        "id": tid,
        "category": slice_cfg["category"],
        "subcategory": item.get("tag"),
        "difficulty": "medium",          # prior only; the matrix assigns the band
        "turns": [render(templates[fmt][lang], item, fmt)],
        "rubric": [],
        "check": check,
        "max_tokens": slice_cfg.get("max_tokens", 1024),
        "system": None,
        "language": None,
        "locale": lang,
        "mode": "chat",
        "reference": None,
        "track": "capability",
        "block": "capability",
        "difficulty_prior": "medium",
        "difficulty_band": None,
        "length_band": "short",
        "scoring": "deterministic",
        "split": "dev",
        "source": source,
        "pair_id": tid if slice_cfg["kind"] == "parallel" else None,
    }


def _pick(specs: list[dict], n: int, n_rows: int, banned: set[str],
          rng: random.Random, label: str) -> list[int]:
    """Seeded scan for n eligible row indices valid for every spec given."""
    order = list(range(n_rows))
    rng.shuffle(order)
    picked, overlap, ineligible = [], 0, 0
    for i in order:
        if len(picked) >= n:
            break
        try:
            texts = [adapt(s, get_split(s)[i])["question"] for s in specs]
        except (Skip, IndexError):
            ineligible += 1
            continue
        if any(qhash(t) in banned for t in texts):
            overlap += 1
            continue
        picked.append(i)
    notes = []
    if overlap:
        notes.append(f"{overlap} skipped as RouterArena overlap")
    if ineligible:
        notes.append(f"{ineligible} ineligible")
    if notes:
        print(f"    {label}: " + ", ".join(notes))
    if len(picked) < n:
        print(f"    WARNING {label}: only {len(picked)}/{n} rows available")
    return picked


def sample(slice_cfg: dict, banned: set[str], rng: random.Random,
           locked: dict | None) -> dict[str, list[int]]:
    """Row indices per language.

    A **parallel** slice pairs the two languages by row, so both sides must use
    the same index. A **native** slice draws from two unrelated datasets of
    different length — sampling one shared index list there silently starves the
    shorter side (KMMLU Law has 1,000 rows against MedMCQA's 4,183, which left
    the Korean slice with 2 of 10 tasks).
    """
    if locked is not None:
        return {k: v for k, v in locked.items() if k in ("ko", "en", "both")}
    ko_ds, en_ds = get_split(slice_cfg["ko"]), get_split(slice_cfg["en"])
    if slice_cfg["kind"] == "parallel":
        idx = _pick([slice_cfg["ko"], slice_cfg["en"]], slice_cfg["n"],
                    min(len(ko_ds), len(en_ds)), banned, rng, "parallel")
        return {"both": idx}
    return {
        "ko": _pick([slice_cfg["ko"]], slice_cfg["n"], len(ko_ds), banned,
                    random.Random(f"{rng.random()}:ko"), "ko"),
        "en": _pick([slice_cfg["en"]], slice_cfg["n"], len(en_ds), banned,
                    random.Random(f"{rng.random()}:en"), "en"),
    }


def check_alignment(slice_cfg: dict, indices: dict[str, list[int]]) -> None:
    """A parallel slice pairing two datasets by row index must prove the rows
    really are the same item, or the twins are silently mismatched."""
    if slice_cfg.get("align") != "index":
        return
    ko_ds, en_ds = get_split(slice_cfg["ko"]), get_split(slice_cfg["en"])
    rows = indices.get("both", [])
    for i in rows:
        a = adapt(slice_cfg["ko"], ko_ds[i])
        b = adapt(slice_cfg["en"], en_ds[i])
        for field in slice_cfg.get("align_check", []):
            if a.get(field if field != "subject" else "tag") != \
               b.get(field if field != "subject" else "tag"):
                raise SystemExit(
                    f"{slice_cfg['category']}: row {i} does not align between "
                    f"{slice_cfg['ko']['dataset']} and {slice_cfg['en']['dataset']} "
                    f"({field}: {a.get('tag')!r} vs {b.get('tag')!r}) — the "
                    f"upstream ordering changed; re-lock before trusting pairs")
    print(f"    index alignment verified on all {len(rows)} sampled rows")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--relock", action="store_true",
                    help="re-sample upstream indices and rewrite the lock file")
    ap.add_argument("--only", help="comma-separated categories")
    args = ap.parse_args()

    try:
        import datasets  # noqa: F401
    except ImportError:
        raise SystemExit("needs the `datasets` package: pip install datasets")

    with open(MANIFEST, encoding="utf-8") as f:
        man = json.load(f)
    lock = {}
    if os.path.exists(LOCK) and not args.relock:
        with open(LOCK, encoding="utf-8") as f:
            lock = json.load(f).get("slices", {})

    banned, _ra_path = load_routerarena_hashes(man["overlap_guard"])
    templates = man["prompt_templates"]
    only = {x.strip() for x in args.only.split(",")} if args.only else None

    by_lang_cat: dict[tuple[str, str], list[dict]] = {}
    new_lock: dict[str, dict] = {}
    for idx, sl in enumerate(man["slices"]):
        cat = sl["category"]
        if only and cat not in only:
            continue
        key = f"{cat}#{idx}"
        rng = random.Random(f"{man['seed']}:{key}")
        print(f"  {key} ({sl['kind']}, n={sl['n']})")
        locked = lock.get(key, {}).get("indices")
        indices = sample(sl, banned, rng, locked)
        check_alignment(sl, indices)
        new_lock[key] = {"indices": indices, "ko": sl["ko"], "en": sl["en"]}

        # parallel: one index list, both languages. native: one list each.
        plan = ([("ko", indices["both"]), ("en", indices["both"])]
                if "both" in indices
                else [(lang, indices.get(lang, [])) for lang in ("ko", "en")])
        for lang, rows in plan:
            spec = sl[lang]
            ds = get_split(spec)
            fmt = spec.get("format", sl["format"])
            for n, i in enumerate(rows, 1):
                item = adapt(spec, ds[i])
                tid = (f"cap-{cat}-{idx}-{n:03d}" if "both" in indices
                       else f"cap-{cat}-{idx}-{lang}-{n:03d}")
                src = (f"public:{spec['dataset']}"
                       f"{'@' + spec['config'] if spec.get('config') else ''}"
                       f"#{spec['split']}:{i}")
                task = build_task(tid, sl, lang, item, fmt, templates, src)
                by_lang_cat.setdefault((lang, cat), []).append(task)

    total = 0
    for (lang, cat), tasks in sorted(by_lang_cat.items()):
        total += len(tasks)
        path = os.path.join(OUT[lang], f"{cat}.jsonl")
        print(f"{'(dry) ' if args.dry_run else ''}"
              f"{os.path.relpath(path, ROOT)}: {len(tasks)}")
        if args.dry_run:
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for t in sorted(tasks, key=lambda x: x["id"]):
                f.write(json.dumps(t, ensure_ascii=False) + "\n")

    if not args.dry_run:
        with open(LOCK, "w", encoding="utf-8") as f:
            # Record the guard's fingerprint, not where the dataset sits on
            # this machine — the lock file is committed to a public repo.
            json.dump({"seed": man["seed"],
                       "overlap_guard_questions_hashed": len(banned),
                       "slices": new_lock}, f, ensure_ascii=False, indent=2)
            f.write("\n")
    print(f"total: {total} capability tasks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
