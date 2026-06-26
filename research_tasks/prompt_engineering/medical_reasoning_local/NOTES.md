# Medical Reasoning (MedXpertQA) — Prompt-Engineering Notes

Goal: maximize accuracy on the fixed, seeded MedXpertQA (Text) questions for the
**local Qwen3.5-27B solver**, **editing only the system prompt**. The harness
(`eval_qwen.py`) is frozen: it enforces a JSON `{reasoning, answer}` structured
output (vLLM `json_schema`/`guided_json`) and constrains `answer` to the valid
option letters. Output formatting is therefore already solved — the prompt's only
job is to **improve the quality of the clinical reasoning**. The system prompt is
optimized **by hand**: the working agent scores a prompt, reads the per-question
failures, revises, and re-scores, looping for many rounds. Scoring always runs on
the local Qwen solver; no Anthropic key is involved anywhere.

## Files (nothing frozen was modified)

| File | Purpose |
| --- | --- |
| `baseline_prompt.txt` | The starting prompt to beat. |
| `eval_qwen.py` | FROZEN scorer — local Qwen solver; do not edit while iterating. |
| `run_iter.py` | The by-hand loop: score one prompt (dev every call, test on cadence), log the dashboard, write `results/last_dev_result.json`, and print the misses to fix next. |
| `results_log.py` | Leaderboard + live dashboard (`results/index.html`) writer used by `run_iter.py`. |
| `check_tunnel.py` | Scans the likely host:port combos to find a live Qwen tunnel and prints the `QWEN_BASE_URL` to export (port defaults to 8010 but can move). |
| `prompts/` | Saved candidate prompt variants (for your own provenance). |
| `results/` | Per-iteration result JSON + leaderboard + dashboard (created on run). |

## Dataset shape (drives the prompt design)

20 questions, 10 options each:
- **medical_task**: Treatment 10, Diagnosis 5, Basic Science 5.
- **body_system**: Reproductive 4, Nervous 4, then Cardiovascular/Digestive/
  Lymphatic/Other 2 each, and several singletons.
- **question_type**: mix of "Reasoning" and "Understanding".

Treatment/management is the largest bucket and includes several unstable patients
(shock, esophageal perforation, opioid toxidrome, sepsis in an asplenic patient),
plus obstetric cases where gestational age / Rh status / GBS status change the
answer. This is why the prompt emphasizes (a) honoring the exact lead-in qualifier,
(b) clinical sequencing / stabilize-first, and (c) patient-specific modifiers.

## Design rationale — failure modes targeted

MedXpertQA distractors are deliberately plausible. The prompt installs an explicit
reasoning discipline aimed at the highest-yield error modes for expert MCQs:

1. **Answer the question actually asked.** "Most likely" vs "best initial step" vs
   "definitive treatment" vs "most common association" change the answer; the model
   is told to lock onto the lead-in qualifier first.
2. **Problem representation before answering** — turn raw vitals/labs into meaning
   and surface the one decisive modifier (pregnancy, allergy, renal/hepatic
   impairment, age extremes, asplenia/immunocompromise, instability, or what was
   already done).
3. **Option-by-option elimination** — name the confirming or disqualifying feature
   for each choice rather than picking on gestalt; the best answer must beat all 10.
4. **Clinical sequencing** for management — stabilize the unstable first; pick the
   most informative low-risk test when uncertain, but act when the diagnosis is
   clear; use guideline first-line therapy that is safe for *this* patient.
5. **Explicit trap list** — textbook-right-but-wrong-for-this-patient, defaulting to
   the most aggressive/comprehensive option, and detail anchoring.
6. **Calibrate and commit** — reason hard, but don't talk yourself out of a
   well-supported answer; always return a single best choice.

## How to run (requires solver access)

```bash
cd prompt_engineering/medical_reasoning_local

# 1. Open the SSH tunnel on the HOST (default: host :8010 -> remote vLLM):
#    ssh -L 8010:127.0.0.1:8111 <user>@<gpu-host>
# 2. From inside the Docker sandbox, QWEN_BASE_URL reaches it via the host gateway
#    (8010 is the default, so usually nothing to set):
export QWEN_BASE_URL=http://host.docker.internal:8010/v1
# If 8010 is down the port may have moved — poke around for it; check_tunnel.py
# scans the likely host:port combos and prints the QWEN_BASE_URL to export:
python check_tunnel.py

# Score one prompt with the frozen harness (local Qwen solver):
python eval_qwen.py --prompt-file baseline_prompt.txt

# Run one iteration of the by-hand loop (scores dev + test-on-cadence, logs the
# dashboard, prints the misses to fix next). Repeat for many rounds:
python run_iter.py baseline_prompt.txt
```

`run_iter.py` appends each run to `results/leaderboard.jsonl`, rebuilds the live
dashboard (`results/index.html`), and writes the full per-question dev log to
`results/last_dev_result.json`. Inspect the per-question `reasoning` of any ✗ to
find the next prompt refinement.

## Status / open item

The scored eval has **not been executed yet**: this environment had no reachable
Qwen endpoint (SSH tunnel down / `host.docker.internal:8010` not listening), so no
accuracy numbers were produced. Everything is wired to run the moment the tunnel is
up (verify with `check_tunnel.py`). The natural next iteration is data-driven: run
the baseline, read the per-question `reasoning` for every miss, and tighten the
prompt against the specific mistakes (a 1/20 swing is ~5 points on the dev set, so
treat small dev moves as noise and confirm on the n=100 test set).
