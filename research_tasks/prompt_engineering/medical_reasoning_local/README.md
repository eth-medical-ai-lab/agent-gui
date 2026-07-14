# Research Task: Medical Reasoning (MedXpertQA) — Local Solver

> **Local-solver variant.** The solver model here is **Qwen3.5-27B served locally**
> (a remote GPU server reached over an SSH tunnel), scored by `eval_qwen.py`. A
> sibling task (`medical_reasoning_api`) uses an **API (Anthropic) model** as the
> *solver*; this folder is the *local* one, so **scoring never calls an Anthropic
> key** — it only talks to the local Qwen server.
>
> Prompt iteration is **by hand**: *you* (the working agent) are the prompt
> engineer. The protocol: seal the baseline's test number first
> (`submit.py --baseline`), iterate on dev **exactly 20 times**
> (`run_iter.py`, selection by dev only), then seal the final test number
> (`submit.py --prompt-file <best>`) and write RESULTS.md. The held-out test
> is scored exactly twice, both one-shot and aggregate-only. `eval_qwen.py`
> is the underlying frozen scorer. No Anthropic key is involved anywhere —
> the local Qwen solver does all the scoring.

A self-contained prompt-engineering task. You iterate on a **system prompt**; a
**fixed** harness scores it with the local Qwen solver against fixed MedXpertQA
questions and returns **accuracy + a detailed per-question log**.

```
medical_reasoning_local/
├── task.json            # machine-readable task manifest (for the task selector)
├── data.jsonl           # 20 fixed dev samples (checked in — runs offline)
├── data_test.jsonl      # 100 fixed held-out test samples (report-only)
├── eval_qwen.py         # FROZEN scorer (Qwen solver) — do not edit while iterating
├── run_iter.py          # the by-hand loop: score one prompt on dev, log the dashboard, print the misses (dev ONLY)
├── submit.py            # FROZEN sealed test submissions: --baseline first, --prompt-file <best> at the end (each once)
├── results_log.py       # leaderboard + live dashboard (results/index.html) writer used by run_iter.py
├── check_tunnel.py      # quick "can I reach the Qwen server?" probe
├── baseline_prompt.txt  # a starting prompt to beat
├── prepare_data.py      # regenerate data.jsonl (dev) from the source dataset
├── prepare_test.py      # regenerate data_test.jsonl (test) from the source dataset
├── requirements.txt     # (none required — eval talks to Qwen over the stdlib)
└── README.md
```

## What you change vs. what is frozen

| You change            | Frozen (do not touch)                                                  |
| --------------------- | --------------------------------------------------------------------- |
| The **system prompt** | Solver model (auto-detected Qwen), the samples, answer parsing, scoring |

Keeping everything except the prompt fixed is what makes two prompt revisions
directly comparable.

**The prompt must generalize, not memorize.** `run_iter.py` shows the dev misses
with their gold labels so you can fix *reasoning* failures — do **not** hardcode
answers to specific dev questions into the prompt. That spikes dev accuracy but
does not transfer to the held-out test (the number that counts). Keep the prompt
about clinical reasoning, output formatting, and general decision rules.

## Connecting to the local Qwen solver

The Qwen3.5-27B server (OpenAI-compatible vLLM) lives on a remote GPU box. Open an
SSH tunnel **on the host**, forwarding **local port 8111** to the remote vLLM:

```bash
ssh -L 8111:127.0.0.1:8111 <user>@<gpu-host>   # host :8111 -> remote vLLM
```

This task runs inside the per-desk **Docker sandbox**, which cannot see the host's
`127.0.0.1`. Reach the host's tunnel through Docker's host gateway —
**`host.docker.internal`** — which is the default `QWEN_BASE_URL`:

```bash
# default; no need to set it when the tunnel is on 8111 and you're in the sandbox
export QWEN_BASE_URL=http://host.docker.internal:8111/v1

# only if you run the harness directly on the host (not in a container):
export QWEN_BASE_URL=http://127.0.0.1:8111/v1
```

**If the tunnel isn't on 8111** (the port sometimes changes between sessions),
don't assume it — poke around for it. `check_tunnel.py` scans the likely
host:port combinations and prints a working base URL to export:

```bash
python check_tunnel.py
# -> TUNNEL OK  http://host.docker.internal:8111/v1   models=[...]
#    Use it:  export QWEN_BASE_URL=http://host.docker.internal:8111/v1
export QWEN_BASE_URL=http://host.docker.internal:8111/v1   # whatever it found
```

## Run

```bash
# Score the baseline prompt against the dev set (n=20)
python eval_qwen.py --prompt-file baseline_prompt.txt

# Score your own prompt
python eval_qwen.py --prompt-file my_prompt.txt
python eval_qwen.py --prompt "You are a careful diagnostician. ..."

# The held-out test set (data_test.jsonl) is OFF-LIMITS during iteration.
# It is scored exactly twice, both sealed one-shots via submit.py:
#   python submit.py --baseline              (before iterating)
#   python submit.py --prompt-file <best>    (after iteration 20)
```

Output: a summary (overall accuracy + a breakdown by `medical_task`), a per-sample
line (`✓`/`✗`/`!`), and the full result JSON (use `--out` to save it).

### The protocol (by hand) — baseline submit → 20 dev iterations → final submit

**Step 1 — seal the baseline's held-out number first** (one-shot, aggregate-only):

```bash
python submit.py --baseline
```

**Step 2 — iterate on dev EXACTLY 20 times.** Each round, run one iteration and
let the failures drive the next edit (selection is by **dev** accuracy only):

```bash
python run_iter.py my_prompt.txt
```

`run_iter.py` scores the prompt on **dev (n=20)** every call, appends to
`results/leaderboard.jsonl`, rebuilds the live dashboard (`results/index.html`),
writes the full per-question log to `results/last_dev_result.json`, and prints
the questions still being missed (gold vs. predicted + the solver's own
reasoning). It NEVER touches the test set. Read the misses, revise
`my_prompt.txt` to fix them, and run the next iteration — all 20.

**Step 3 — submit the dev-best prompt** (the second and last sealed test eval):

```bash
python submit.py --prompt-file <your_best_prompt_file>
```

`submit.py` refuses to re-run a sealed slot, and reports aggregates only (no
per-question test log), so the held-out numbers stay meaningful.

**Step 4 — write `RESULTS.md`**: baseline vs best dev accuracy, the two sealed
TEST numbers (baseline vs final — the delta is the headline), which prompt
ideas helped, and which dev questions are still missed.

A 1/20 swing is ~5 points on dev, so treat small dev moves as noise — the
held-out test set (n=100) is where transfer is judged.

### From Python

```python
from eval_qwen import evaluate

result = evaluate(open("my_prompt.txt").read())
print(result["accuracy"])              # e.g. 0.75
print(result["accuracy_by_medical_task"])
print(result["samples"][0]["reasoning"])
```

`evaluate(prompt)` returns:

```jsonc
{
  "model": "<auto-detected qwen model id>",
  "solver": "qwen (remote vLLM via host SSH tunnel, reached at host.docker.internal)",
  "n": 20, "n_correct": 15, "accuracy": 0.75,
  "accuracy_by_medical_task": { "Diagnosis": 0.8, "Treatment": 0.7, ... },
  "samples": [
    { "id": "Text-102", "predicted": "G", "correct": "G", "is_correct": true,
      "reasoning": "...", "medical_task": "...", "latency_s": 6.1, "usage": {...} }
  ]
}
```

## How scoring works

For each question the harness sends your system prompt plus the question and its
lettered options. The model answers via **structured outputs** — JSON
`{"reasoning", "answer"}` where `answer` is constrained to that question's valid
option letters. The predicted letter is compared to the gold label;
`accuracy = correct / n`. Because the output format is enforced by the harness,
your prompt only needs to improve the *reasoning* — you never have to fight with
output formatting.

## Data provenance

- **Dataset:** [`TsinghuaC3I/MedXpertQA`](https://huggingface.co/datasets/TsinghuaC3I/MedXpertQA), config `Text` (text-only reasoning MCQs, 10 options each).
- **Subsample:** drawn deterministically with seed 42 from the `test` split — *fixed but random*. `data.jsonl` is the **dev** set (n=20, used for iteration/selection); `data_test.jsonl` is the **held-out test** set (n=100, report-only).
- **Why not the released `dev` split?** MedXpertQA's `dev` split has only **5** items (few-shot exemplars), too few for an eval. We carve fixed, seeded held-out subsets from the benchmark and iterate strictly on the dev subset.

Regenerate (or change seed/size/split) with `python prepare_data.py` (dev) and
`python prepare_test.py` (test) — they use only the standard library via the HF
datasets-server REST API (no `datasets` package needed).
