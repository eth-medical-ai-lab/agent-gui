# Research Task: Medical Reasoning (MedXpertQA) with API model

A self-contained prompt-engineering task with a proper **train/test split**. You
iterate on a **system prompt** against the **train** split (rich per-question
feedback), then submit it **once** for a held-out score on the disjoint **test**
split. The model and scoring are **fixed**; only your prompt changes.

Crucially, for running this experiment, you are expected to have the ANTHROPIC_API_KEY defined as environment
variable. If you're running this from a docker environment via a hermes profile, you need to configure the profile accordingly:
terminal:  
docker_forward_env:   
    - "ANTHROPIC_API_KEY"  


```
medical_reasoning/
├── task.json            # machine-readable task manifest (for the task selector)
├── train.jsonl          # 20 fixed samples for ITERATION (checked in, offline)
├── test.jsonl           # 20 fixed HELD-OUT samples — never tune against these
├── eval.py              # FIXED iteration harness (train split) — do not edit
├── submit.py            # FIXED one-shot held-out scorer (test split) — do not edit
├── baseline_prompt.txt  # a starting prompt to beat
├── prepare_data.py      # regenerate train/test from the source dataset
├── requirements.txt     # anthropic
└── README.md
```

## Workflow (for an agent)

1. **Iterate** on the system prompt with `eval.py` against `train.jsonl`. It
   returns accuracy, a per-`medical_task` breakdown, and a full per-question log
   (each item's reasoning, prediction, and gold answer) — use the failures to
   decide what to change. Repeat as many times as you like.
2. **Submit once** with `submit.py`. It scores the prompt on the held-out
   `test.jsonl` and seals the result in `submission.json`. It refuses to run a
   second time and reports **aggregate-only** numbers (no per-question test log),
   so the held-out set can't be tuned against.

## What you change vs. what is frozen

| You change            | Frozen (do not touch)                                      |
| --------------------- | --------------------------------------------------------- |
| The **system prompt** | Model (`claude-opus-4-8`), the train/test samples, answer parsing, scoring |

Keeping everything except the prompt fixed is what makes two prompt revisions
directly comparable.

> **Rule:** tune only on `train.jsonl`. Never read or optimize against
> `test.jsonl` — it exists solely for the single honest measurement from
> `submit.py`. (The two splits are disjoint by construction; see *Data provenance*.)

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
```

## Run — iterate (train split)

```bash
# Score the baseline prompt
python eval.py --prompt-file baseline_prompt.txt

# Score your own prompt
python eval.py --prompt-file my_prompt.txt
python eval.py --prompt "You are a careful diagnostician. ..."
```

Output: a summary (split + overall accuracy + a breakdown by `medical_task`), a
per-sample line (`✓`/`✗`/`!`), and the full result JSON written to
`last_result.json`. `eval.py` always uses the **train** split.

## Submit — final held-out score (test split, one-shot)

```bash
python submit.py --prompt-file my_prompt.txt
```

Scores the prompt on `test.jsonl` **once** and writes a sealed `submission.json`
(prompt, its SHA-256, timestamp, accuracy). Running it again is refused. Output is
**aggregate-only** — no per-question test log is ever shown.

### From Python

```python
from eval import evaluate

result = evaluate(open("my_prompt.txt").read())   # split="train" by default
print(result["accuracy"])              # e.g. 0.75
print(result["accuracy_by_medical_task"])
print(result["samples"][0]["reasoning"])
```

`evaluate(prompt)` returns (train split):

```jsonc
{
  "model": "claude-opus-4-8",
  "split": "train",
  "n": 20, "n_correct": 15, "accuracy": 0.75,
  "accuracy_by_medical_task": { "Diagnosis": 0.8, "Treatment": 0.7, ... },
  "samples": [
    { "id": "Text-102", "predicted": "G", "correct": "G", "is_correct": true,
      "reasoning": "...", "medical_task": "...", "latency_s": 6.1, "usage": {...} }
  ]
}
```

`evaluate(prompt, split="test")` returns the same shape **without** the `samples`
list — aggregate-only, by design.

## Live progress dashboard

A self-contained `results/dashboard.html` shows accuracy per iteration (dev +
test), a live status banner, and a runs table. It auto-refreshes every 5s.

**Logging is automatic — the agent does nothing extra.** Every `evaluate()` call
records one row to `results/leaderboard.jsonl`, updates `results/status.json`, and
rebuilds `results/dashboard.html`. Iteration numbers and a stable per-prompt id are
derived inside `eval.py`; the iteration (train) split is recorded as `dev`. So the
loop is just:

```python
from eval import evaluate

for it in range(N):
    res = evaluate(prompt, split="train")   # logs a dev row + refreshes dashboard
    # ...refine the prompt using res["samples"]...

evaluate(prompt, split="test")              # logs a test checkpoint (or use submit.py)
```

(`submit.py` also goes through `evaluate`, so the final held-out run shows up too.)
Pass `evaluate(..., log=False)` to opt out. Run from **inside this task directory**
so `eval.py` can import `make_dashboard` to rebuild the HTML.

> If the dashboard stays empty/"idle", the agent isn't calling `evaluate()` from
> this directory — nothing else populates the page.

**Viewing.** The first logged run tries to open `results/dashboard.html` in your
default browser automatically (once; set `DASHBOARD_AUTO_OPEN=0` to disable). That
works on a host with a desktop browser — inside a headless GUI sandbox there is
nothing to launch, so open it yourself:
- *In the GUI:* preview `team_files/.../medical_reasoning_api/results/dashboard.html`.
  It loads in an iframe over HTTP and meta-refreshes every 5s, tracking the agent's
  writes (which land in the shared team-file repo the preview reads). If a view
  looks frozen, close and re-open the preview.
- *On the host / CLI:* `open results/dashboard.html` (auto-refreshes). The optional
  `python make_dashboard.py --watch` is only needed if something other than
  `evaluate()` is appending to the ledger.

(`results/dashboard.html`, `leaderboard.jsonl`, `status.json` are git-ignored —
generated per run.)

## How scoring works

For each question the harness sends your system prompt plus the question and its
lettered options, with **adaptive thinking** on. The model answers via
**structured outputs** — JSON `{"reasoning", "answer"}` where `answer` is
constrained to that question's valid option letters. The predicted letter is
compared to the gold label; `accuracy = correct / 20`. Because the output format
is enforced by the harness, your prompt only needs to improve the *reasoning* —
you never have to fight with output formatting.

## Data provenance

- **Dataset:** [`TsinghuaC3I/MedXpertQA`](https://huggingface.co/datasets/TsinghuaC3I/MedXpertQA), config `Text` (text-only reasoning MCQs, 10 options each).
- **Subsample:** a single seeded draw of **40 distinct** items,
  `random.Random(42).sample(range(2450), 40)`, partitioned into **disjoint**
  `train.jsonl` (first 20) and `test.jsonl` (last 20). Drawing once then splitting
  guarantees no item can appear in both — *fixed but random*, deterministic given
  the seed.
- **Why not the "validation"/`dev` split?** MedXpertQA's released `dev` split has only **5** items (few-shot exemplars), too few. We carve fixed, seeded held-out subsamples from the benchmark: train for iteration, test for the single reported number. Leave the full `test` set untouched for anything you intend to *publish*.

Regenerate (or change seed/size/split) with `python prepare_data.py` — it uses
only the standard library via the HF datasets-server REST API (no `datasets`
package needed). Fetched rows are cached under `.row_cache/` so reruns resume
without re-downloading.
