#!/usr/bin/env python3
"""Emit an integrity manifest for one run's deliverables.

Lists every artifact of results/<run-id>/ with its SHA-256 and size, plus the
run's attempt accounting and the harness commit that produced it, so a
reviewer can verify raw attempts ↔ analysis ↔ evidence line up without
trusting the sender.

  python3 tools/make_run_manifest.py --run-id v4 > results/v4/manifest.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# deliverables, in reconciliation order: raw attempts first, derived last
ARTIFACTS = [
    ("responses.jsonl", "raw attempts — one line per attempt, failures/retries included, never deduplicated"),
    ("checks.jsonl", "objective check results (chat mode)"),
    ("judgments.jsonl", "blind 3-judge rubric scores"),
    ("server_costs.json", "authoritative server-metered per-session costs (KRW)"),
    ("analysis.json", "aggregation incl. attempt_ledger + attempt_accounting"),
    ("report.html", "rendered report"),
    ("capability_evidence.json", "schema v2 capability evidence for gateway ingest"),
]


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args()

    run_dir = os.path.join(ROOT, "results", args.run_id)
    with open(os.path.join(run_dir, "analysis.json"), encoding="utf-8") as f:
        analysis = json.load(f)

    files = []
    for name, role in ARTIFACTS:
        path = os.path.join(run_dir, name)
        if not os.path.exists(path):
            raise SystemExit(f"missing deliverable: results/{args.run_id}/{name}")
        entry = {
            "file": name,
            "role": role,
            "sha256": sha256_of(path),
            "bytes": os.path.getsize(path),
        }
        if name.endswith(".jsonl"):
            with open(path, encoding="utf-8") as f:
                entry["lines"] = sum(1 for line in f if line.strip())
        files.append(entry)

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    manifest = {
        "benchmark": "smartroute-bench",
        "run_id": analysis["run_id"],
        "harness_commit": commit + ("+dirty" if dirty else ""),
        "arms": [{"id": a["id"], "model": a["model"]} for a in analysis["arms"]],
        "attempt_accounting": analysis["attempt_accounting"],
        "files": files,
    }
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
