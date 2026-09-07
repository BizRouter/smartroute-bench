#!/usr/bin/env python3
"""The v1 tasks must stay byte-identical, or the history stops meaning anything.

    python3 tools/check_v1_continuity.py

Runs v1..v10 were scored on 57 tasks per language. v2 grew the suite to 218,
and the only reason those old numbers are still usable is that the original 57
survived the migration unchanged and carry a `v1_category` marker, so a run can
be re-scored on just that overlap (the "continuity strip" in the report).

That property is fragile in a way nothing else catches. Rewording one prompt or
loosening one check still passes every other gate — the suite validator counts
tasks, the self-tests verify the checker agrees with itself — while quietly
turning "the router improved since July" into a comparison between two
different questions. Worse, it fails in the flattering direction: an easier
reworded task raises the score.

So compare against git, not against a copy of the file that could drift with it:

  · Korean  — tasks/k    at 44604a0 (the last commit before the v2 migration,
              i.e. exactly the suite that run v10 executed)
  · English — tasks/base at 5db672f (the commit that introduced the English 57)

Exit 1 on any drift. If a change to a v1 task is genuinely intended, retire the
task instead: drop `v1_category` so it leaves the strip honestly, rather than
editing it in place and keeping the marker.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (suite dir, commit that pinned that suite's v1-era content)
BASELINES = [("tasks/k", "44604a0"), ("tasks/base", "5db672f")]

# Fields that define the question being asked and how it is graded. `category`
# is deliberately absent: v2 renames categories (coding -> code-write) and that
# is a relabel, not a change to the task. `v1_category` preserves the old name.
FROZEN = ("turns", "check", "system", "max_tokens", "mode", "difficulty",
          "language", "locale", "rubric", "reference")


def at_commit(rev: str, path: str) -> dict:
    names = subprocess.run(["git", "-C", ROOT, "ls-tree", "-r", "--name-only", rev, path],
                           capture_output=True, text=True, check=True).stdout.split()
    out = {}
    for name in names:
        if not name.endswith(".jsonl") or "_golden" in name:
            continue
        blob = subprocess.run(["git", "-C", ROOT, "show", f"{rev}:{name}"],
                              capture_output=True, text=True, check=True).stdout
        for line in blob.splitlines():
            if line.strip():
                t = json.loads(line)
                out[t["id"]] = t
    return out


def on_disk(path: str) -> dict:
    out = {}
    for f in glob.glob(os.path.join(ROOT, path, "**", "*.jsonl"), recursive=True):
        if "_golden" in f:
            continue
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    t = json.loads(line)
                    out[t["id"]] = t
    return out


def canon(task: dict) -> str:
    return json.dumps({k: task.get(k) for k in FROZEN},
                      ensure_ascii=False, sort_keys=True)


def main() -> int:
    problems = []
    for path, rev in BASELINES:
        old, new = at_commit(rev, path), on_disk(path)
        missing = sorted(set(old) - set(new))
        changed = [i for i in sorted(set(old) & set(new)) if canon(old[i]) != canon(new[i])]
        # A surviving v1 task that lost its marker drops out of the continuity
        # strip without any diff to the task itself — same broken history,
        # quieter.
        unmarked = [i for i in sorted(set(old) & set(new))
                    if not new[i].get("v1_category")]

        print(f"{path} vs {rev}: {len(old)} v1 tasks · "
              f"{len(set(old) & set(new))} still present · "
              f"{len(changed)} changed · {len(unmarked)} unmarked")
        for tag, ids in (("MISSING", missing), ("CHANGED", changed),
                         ("UNMARKED", unmarked)):
            if ids:
                problems.append(f"{path}: {tag} {ids}")
                print(f"  ✗ {tag}: {', '.join(ids[:12])}"
                      + (" …" if len(ids) > 12 else ""))

    if problems:
        print("\nv1 continuity BROKEN — runs v1..v10 can no longer be compared "
              "to new runs on the overlap.")
        return 1
    print("\nv1 continuity intact — the strip against v1..v10 is valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
