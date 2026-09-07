# SmartRoute-Bench

**English** · [한국어](README.ko.md)

A benchmark for **LLM smart-routing gateways**: does automatic model routing
deliver comparable quality at a significantly lower cost than pinning a
frontier model — and when it spends more, did it need to?

Most LLM benchmarks measure *models*. This one measures *routers* — the layer
that decides, per request, which model should handle it. It compares N arms
(e.g. pinned frontier models vs. a smart-routing virtual model) over a
realistic, mixed workload and reports **cost, quality, latency, and routing
behavior** side by side. Since v2 it also scores the router against an
**oracle** — the cheapest model in the pool that would have passed each task —
so that over-routing (paying for a frontier model where a cheap one sufficed)
and under-routing (sending a task to a model that fails it) are both visible.

## Two suites, two tracks

The benchmark ships two parallel task suites with the same structure, so you
can measure routing on whichever workload matches your users:

- **`tasks/base/` — SmartRoute-Bench (English).** The default suite.
- **`tasks/k/` — K-SmartRoute-Bench (Korean).** The same tasks re-authored as
  Korean real-work tasks (Korean business writing, KO↔EN translation, Korean
  personas, Korean knowledge sources). Run it with `--tasks tasks/k`.

Each suite is **253 tasks in 22 categories**, split into two tracks that are
**reported separately and never averaged together**:

- **Track A — Work (158 tasks, authored here, headline).** Real work a
  customer sends through a gateway: coding, debugging, tool use, agentic
  sessions, business writing, extraction, structured generation,
  instruction following, safety boundaries, clarifying questions. Authored
  bilingually from one source (`tasks/_src/`) so the two suites cannot drift
  apart; each task carries a `pair_id` linking its KO/EN twin.
- **Track B — Capability (95 tasks, sampled from public datasets,
  supporting).** Short, objectively graded knowledge/math/logic/long-context
  questions that give the suite its difficulty spread and make the numbers
  comparable to global router benchmarks. **This repository ships pointers,
  not rows**: `configs/track_b_manifest.json` + `track_b_lock.json` name the
  dataset, split, index, and license of every item, and
  `tools/fetch_track_b.py` materialises `tasks/*/capability/*.jsonl` locally
  (gitignored). Items that collide with the RouterArena evaluation set are
  rejected at sampling time.

The 57 tasks of the v1 suite survive **byte-identical** inside the current
suite (marked `v1_category`), so runs on the old and new suite can still be
compared on that overlap — `tools/check_v1_continuity.py` fails if anyone
edits one of them.

## Workload (253 tasks per language, 22 categories)

| Track | Category | Tasks | Mode | Objective checks |
|---|---|---:|---|---|
| A | `code-write` | 29 | chat | unit tests (Python/JS/Go/Rust/SQL) |
| A | `code-debug` | 23 | chat | failing test must go green |
| A | `code-longcontext` | 6 | chat | exact answer over an 8–40k-char repo/log/diff |
| A | `tool-use` | 12 | chat | tool name + arguments exact match |
| A | `agentic-coding` | 5 | agentic | test suites run in the workspace |
| A | `agentic-artifact` | 3 | agentic | file/PPTX/PDF/CSV validation |
| A | `business-writing` | 8 | chat | LLM judge (+numeric reference for finance) |
| A | `summarization` | 3 | chat | LLM judge + key-fact containment |
| A | `translation` | 3 | chat | LLM judge (base EN↔ES/FR · K KO↔EN) |
| A | `research-analysis` | 4 | chat | LLM judge |
| A | `daily-chat` | 6 | chat | LLM judge |
| A | `character-multiturn` | 4 | chat, multi-turn | LLM judge (persona consistency) |
| A | `extraction` | 15 | chat | deterministic JSON field match |
| A | `json-schema-gen` | 5 | chat | deterministic JSON match (incl. computed values) |
| A | `instruction-following` | 12 | chat | deterministic constraint checks (IFEval-style) |
| A | `safety-boundary` | 4 | chat | rule: benign twin must be answered, harmful twin refused |
| A | `ambiguity-clarify` | 3 | chat | rule: must ask before answering |
| B | `knowledge-mcq` | 40 | chat | final-answer match (MMLU-Pro/ARC · KMMLU/CLIcK/HAE-RAE) |
| B | `math` | 27 | chat | final-answer match (GSM8K/MATH · KMMLU-math) |
| B | `logic-commonsense` | 21 | chat | final-answer match (SuperGLUE · KoBEST) |
| B | `domain-pro` | 10 | chat | final-answer match (MedMCQA/FinQA/law · KMMLU) |
| B | `longcontext-qa` | 10 | chat | exact answer over a generated long document |

228 of the 253 tasks carry a deterministic check; the rest are judged against
weighted rubrics. Tasks span difficulty (easy/medium/hard — an authored prior
that the score matrix re-labels empirically, see below) and input length
bands (short/mid/long). Multi-turn tasks feed each arm its **own** previous
answers, so routing decisions across turns are part of what's measured.

Two categories are new in kind. `safety-boundary` pairs a benign request with
a genuinely harmful near-twin: a model that refuses both is not "safe", and a
router that sends every borderline prompt to the most conservative model is
paying for false refusals. `ambiguity-clarify` rewards asking a clarifying
question — under v1's rubrics, asking instead of answering scored *worse*,
which biased the whole suite toward confident guessing.

Task definitions live in `tasks/base/*.jsonl` and `tasks/k/*.jsonl`, compiled
from the bilingual source `tasks/_src/*.json` by `tools/author_tasks.py`.
Long-context tasks are produced by deterministic generators
(`tools/generators.py`) from a compact seed rather than committed as raw text.
Agentic tasks ship with seed repositories under each suite's `fixtures/`.

## Evolved tasks — why 35 tasks were added on 2026-09-07

The score matrix (every candidate model × every task, see *The oracle* below)
labels each task by what the pool actually did. On the v12 suite the Korean
matrix said **15.5% of measured tasks were `discriminative` or `frontier-only`**
(28/181); the design gate B2 asks for ≥40%. Worse, the coding block was
**100% `cheap-sufficient`** — every one of `code-write` 20, `code-debug` 11 and
`tool-use` 7 was solved by the cheapest model, so those tasks carried no
information about routing at all. A benchmark like that cannot rank routers;
it can only reward "always pick the cheapest".

Instead of hand-writing harder tasks (an author's difficulty guess is what the
matrix keeps proving wrong), `tools/evolve_tasks.py` adapts *environment
evolution* (Fan et al., arXiv:2609.04128, Sep 2026) to chat-mode benchmark
items. A seed task is read as a sequence of (scenario, skill) steps and edited
along **one** of three directions per generation — `length` (add dependent
steps), `scenario` (same skill, less common setting), `skill` (same setting,
rarer technique). A descendant is kept only if it survives, in both languages:

1. **oracle** — the new golden answer passes the new check;
2. **invalid** — a plausible wrong answer fails it, and so does an empty reply;
3. **harder** — the *seed's* correct answer fails the new check, i.e. the edit
   raised the bar rather than rewording the prompt;
4. **rubric** — a reviewer from a different vendor than the synthesiser
   (GPT-5.6 Sol reviewing Claude Opus 5) accepts the plan before and the task
   after, against fixed rubrics (self-contained, unique answer, KO/EN twins,
   realistic request, no trick or trivia);
5. **crossover** — the two cheapest pool models attempt it once each; if both
   still pass, the child is not discriminative yet and evolves another
   generation (up to three).

Accepted descendants were then calibrated on the full 8-model pool exactly like
every other task. Result on the Korean suite: **discriminative + frontier-only
28/181 (15.5%) → 46/216 (21.3%)**; the coding block went from 0 to 13
discriminative tasks. Still below the 40% gate — `tools/validate_suite.py` now
measures B2 from the matrix and reports it as a violation until it is met.

What this means for you as a reader of the numbers:

- 35 tasks per language were **added**; none of the 218 existing tasks changed
  (`tools/author_tasks.py --verify` is byte-identical, and the v1 overlap is
  still frozen). Their ids end in `-g<N>` (generation) and their full lineage
  — seed, direction, generation, the cheap-model probe, and a note on what the
  new tests cover that the seed's did not — is in `tasks/_src/evolved.json`.
- The empirical band of every measured task is published in
  `tasks/_calibration/` (Korean full matrix; English for the evolved tasks —
  the English pool matrix is otherwise not built yet).
- The `v12` reference results below were run on the 218-task suite **before**
  these tasks existed. A run on the 253-task suite is comparable to `v12`
  only on the common tasks; `internal/compare_runs.py` does that
  automatically when task sets differ. A fresh baseline on the full suite is
  the next scheduled run.
- Evolution is off-policy: nothing in the pipeline reads the router. Tuning a
  router against these specific tasks would defeat the benchmark — use the
  `dev`/`test` split and the band distribution, not the task texts.

## Suite invariants

Suite balance is enforced, not eyeballed. `tools/validate_suite.py` checks
both suites against `configs/suite_spec_v2.json` and exits non-zero when the
suite drifts: ≥70% deterministically scored; no category above 15% (the
coding block is capped at 35%); multiple choice capped at 30% with ≥3 options
per item; declared input-length shares; ≥90% of Track A tasks KO/EN-paired;
judge means and objective pass rates never averaged together; every quality
claim must report over-routing and under-routing side by side. The current
suite is still growing toward the 300-per-language target, so the validator
reports the remaining composition gaps as known violations.

## Fairness rules

- **No preordained winner.** Results are whatever the run produces; checks are
  deterministic and judge prompts are blind.
- **Blind judging.** Judges never learn which arm produced a response and are
  instructed to ignore self-identification. Three independent judge models
  **from different providers** (default: Claude Opus 5, GPT-5.6 Sol,
  Gemini 3.1 Pro) score weighted rubrics (1–10) and their mean is the final
  score; the report exposes per-judge × per-arm means so same-vendor bias is
  auditable, plus pairwise inter-judge agreement.
- **Same wire, same prompts.** All arms run through the same gateway endpoint
  with identical prompts and token limits.
- **Default reasoning depth.** `reasoning_effort` / `thinking` are not set —
  each model runs at its own default. This measures cost-performance as
  delivered through the gateway at default settings (the same way the router
  itself calls models), not an iso-effort lab comparison.
- **Costs from one source of truth.** Per-session costs come from gateway-side
  metering (or response `usage.cost`), never from self-reported estimates.
- **Delivery counts, and is reported separately.** Calls are 1-shot with no
  retry: a refusal, empty body, or mid-stream truncation scores as-is in the
  headline mean — that is what a real caller received. Every such failure is
  also listed in the report's delivery-failure table and a
  `judge_mean_delivered` (failures excluded) is reported alongside, so a
  safety-classifier false positive can't masquerade as model quality.
- **Two-sided routing metric.** See below — a router is never rewarded for
  simply being cheapest or simply being best.

## The oracle: over-routing and under-routing

Every v1 metric was *router vs. pinned arms*. v2 adds *router vs. the cheapest
model that would have worked*.

1. **Score matrix.** Run every non-agentic task once against every model in the
   router's candidate pool (a "pool run": one arm per concrete model), then
   `tools/build_score_matrix.py --from-run <pool-run>` reshapes it into
   task → model → {pass, judge, cost, latency}. The matrix is cached and only
   rebuilt when the pool changes.
2. **Per task**, the oracle is the cheapest pool model that passes.
3. **Per run**, `analyze --matrix` reports:

| Metric | Definition | Punishes |
|---|---|---|
| **Optimal selection %** | router picked exactly the oracle model | both directions |
| **Over-route %** | router succeeded, but a strictly cheaper model also would have | always-frontier |
| **Under-route %** | router failed where some pool model succeeded | always-cheapest |
| **Cost regret ×** | router cost ÷ oracle cost | always-frontier |

The matrix also assigns each task an **empirical difficulty band** —
`cheap-sufficient` (the cheapest model passes), `discriminative` (some pass,
some fail — where routing earns its money), `frontier-only`, `unsolved` — and
the report states what share of the suite is discriminative, because a suite
that every model passes cannot rank routers. `tools/selftest_oracle.py` proves
on a synthetic matrix that an always-cheapest and an always-frontier router
both score badly.

## Running

```bash
export SRB_API_KEY=...            # gateway API key

# 0. materialise Track B locally (pointers -> rows; gitignored)
python3 tools/fetch_track_b.py
python3 tools/validate_suite.py   # suite invariants (composition gaps are reported, not fatal)

# 1. chat-mode responses (all tasks x all arms)
PYTHONPATH=src python3 -m smartroute_bench.runner --run-id r1 --skip-done

# 2. objective checks
PYTHONPATH=src python3 -m smartroute_bench.run_checks --run-id r1

# 3. blind judging (3-provider judge panel, rubric-bearing tasks only)
PYTHONPATH=src python3 -m smartroute_bench.judge --run-id r1 --skip-done

# 4. aggregate + report   (add --matrix results/_matrix/<pool>.json for oracle metrics)
PYTHONPATH=src python3 -m smartroute_bench.analyze --run-id r1
PYTHONPATH=src python3 -m smartroute_bench.report --run-id r1 --lang en   # -> report.html
```

The commands above run the English base suite. For the Korean suite, add
`--tasks tasks/k` to every step (`runner`, `run_checks`, `judge`, `analyze`)
and use a distinct `--run-id`. Every step is resumable with `--skip-done`, so
a network drop continues instead of paying twice. Never reuse a `--run-id`
for a new measurement — results are append-only per run.

To get the oracle metrics, run the pool once (`configs/arms.json` with one arm
per candidate model, a dedicated run-id), build the matrix with
`tools/build_score_matrix.py`, and pass it to `analyze --matrix`. The oracle
is defined relative to the pool: a matrix built on a different pool answers a
different question, so rebuild it when the pool changes.

For **periodic re-benchmarking** (every router change), keep the arm set and
judge panel fixed between runs and diff a new run against the previous one on
the same suite. If either the arms or the judges change, compare only the
common judge subset rather than the headline judge mean, and treat the first
run after the change as a new baseline.

Arms, endpoint, and judges are configured in `configs/arms.json`. Any
OpenAI-compatible (`/chat/completions`) or Anthropic-compatible (`/messages`)
gateway works; a routing arm is just an arm whose `model` is the gateway's
routing pseudo-model. Routing decisions are read from
`x-bizrouter-routed-model`-style response headers when present.

Agentic-mode tasks need a coding-agent CLI that can run headless in a
workspace directory. `adapters/agentic_bizcoder.py` drives the
[BizCoder](https://bizcoder.ai) CLI; copy it and swap the subprocess call to
use another agent CLI.

No third-party Python dependencies (standard library only). Objective checks
use whatever toolchains are on PATH (`node`, `go`, `rustc`, `sqlite3`) and
skip gracefully when missing. `tools/make_run_manifest.py` emits a SHA-256
manifest of a run's artifacts so a reviewer can verify raw attempts ↔
analysis ↔ evidence without trusting the sender.

## Output

`results/<run-id>/analysis.json` holds per-task and aggregate numbers:
cost/quality/latency per arm, per category, per difficulty, per mode; the
router's model-selection distribution; inter-judge agreement; oracle metrics
when a matrix was supplied; and flagged cases where the routed answer was
measurably worse than the pinned arms (mis-route candidates). `report.html`
renders the same as a shareable page.

## Feeding results back into a router

A run's category scores are *measured* capability evidence. Export them as a
capability-proposal payload:

```bash
python3 tools/export_capability_evidence.py --run-id r1 --measured-at 2026-08-27 \
    > results/r1/capability_evidence.json
```

Only attributable measurements are exported: fixed arms measure their own
model, and the routing arm's scores are attributed to a concrete model only
when routing concentrated (≥95%) on it. BizRouter ingests this file as
*pending* capability proposals that an administrator reviews before anything
affects live routing; other gateways can consume the same JSON.

## Reference results — run `v12`, 2026-08-27

> Measured on the 218-task suite, before the 35 evolved tasks were added (see above).

Both suites, all 210 chat-mode tasks (the 8 agentic tasks were not run in this
round), 4 arms, 3-judge blind panel (Claude Opus 5 · GPT-5.6 Sol · Gemini 3.1
Pro). The routing arm ran an unmodified cost-quality-balanced policy. Costs
are gateway-metered list prices; base (English) is shown in USD (₩1,504/$),
the Korean suite in KRW. "Objective checks" is the pass rate over the tasks
that carry a deterministic check; "LLM judge" is the headline mean over the
rubric-bearing tasks (delivered-only mean in parentheses where it differs).

**Base suite (English) — `runs/base/`**

| arm | total cost | LLM judge (55 tasks) | objective checks (183–185) | mean latency |
|---|---|---|---|---|
| GPT-5.6 Sol (pinned) | $2.83 | **9.33** | **95.6%** | 5.6s |
| Claude Opus 5 (pinned) | $6.18 | 9.17 (9.22) | 91.4% | 10.6s |
| Claude Opus 4.8 (pinned) | $3.77 | 9.30 | 90.3% | 5.9s |
| **Smart routing** | **$1.34** | 9.14 | 92.4% | **5.5s** |

**K-SmartRoute-Bench (Korean) — `runs/k/`**

| arm | total cost | LLM judge (57 tasks) | objective checks (183–185) | mean latency |
|---|---|---|---|---|
| GPT-5.6 Sol (pinned) | ₩5,035 | **9.22** | **89.1%** | 6.2s |
| Claude Opus 5 (pinned) | ₩10,885 | 8.66 (8.85) | 80.0% | 13.6s |
| Claude Opus 4.8 (pinned) | ₩6,880 | 9.12 | 82.7% | 7.5s |
| **Smart routing** | **₩2,490** | 9.19 | 87.5% | **6.1s** |

Smart routing was the cheapest arm in both languages — 53% below the cheapest
pinned frontier model (GPT-5.6 Sol) in English and 51% below it in Korean —
while landing within 0.2 judge points and 3.2 percentage points of checks of
the best pinned arm, and fastest on mean latency. Routing distribution:
Korean `openai/gpt-5.6-sol` 147 · `openai/gpt-5.6-luna` 54 ·
`upstage/solar-pro-2` 7 · `anthropic/claude-opus-5` 1; English
`openai/gpt-5.6-luna` 93 · `openai/gpt-5.6-sol` 77 ·
`google/gemini-3.1-flash-image` 28 · `anthropic/claude-opus-5` 10. Claude
Opus 5's lower Korean headline is mostly delivery: 26 responses came back
empty or truncated and are listed in the report's delivery-failure table.

**Oracle metrics (Korean suite, 170 tasks covered by the score matrix)**

| optimal selection | over-route | under-route | cost regret | discriminative share |
|---|---|---|---|---|
| 27.1% | 85.3% | 8.8% | 1.82× | 13.8% |

Read together: the router rarely under-routes (it seldom fails where a pool
model would have passed), but on most tasks it succeeded with a model that was
not the cheapest passing one, paying 1.8× the oracle's cost. Only 13.8% of the
covered tasks are discriminative on this pool — most are passed by the cheapest
model — which is the main reason the suite is still being expanded toward
harder, longer work tasks. No English score matrix has been built yet, so the
English run reports no oracle row.

Both runs are published under [`runs/base/`](runs/base/) and
[`runs/k/`](runs/k/) — per-task responses, all 3 judges' scores, deterministic
check outcomes, the aggregate `analysis.json`, and the rendered `report.html`.
The v1-suite runs (57 tasks per language: `base-v1` 2026-07-21 and `v7`
2026-07-31, judged by a different panel) are kept under
[`runs/archive-v1/`](runs/archive-v1/) for reference. Numbers depend on the
gateway's policy, model pool, and prices at measurement time — treat them as
one reference point, not a universal ranking.

## License

Apache-2.0
