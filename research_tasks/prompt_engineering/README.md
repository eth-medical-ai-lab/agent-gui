# Research Tasks

A small library of self-contained prompt-engineering tasks. **One folder = one
task.** The user (or the app's task selector) picks a task; the user supplies a
prompt; the task's fixed harness scores it and returns a metric plus a detailed
log.

> ⚠️ **Tasks that call a Claude API (e.g. `medical_reasoning_api`) need `ANTHROPIC_API_KEY` *inside* the sandbox.** Whichever agent you assign runs in its Docker sandbox, so a host-only key is invisible to it. The operator must (1) set `ANTHROPIC_API_KEY` in the environment **and** (2) forward it into the sandbox via that agent's Hermes profile config:
>
> ```yaml
> terminal:
>   docker_forward_env:
>     - "ANTHROPIC_API_KEY"
> ```
>
> The solving agent can't set this for itself from inside the sandbox — configure it on the profile before the desk runs.

## Convention (so the selector can discover tasks)

Every task folder contains a `task.json` manifest and a fixed `eval.py`:

```
research_tasks/
└── <task_id>/
    ├── task.json         # manifest: id, name, description, metric, entrypoint, ...
    ├── train.*           # the (small, fixed) train split — iterate here
    ├── test.*            # the disjoint held-out split — report here, once
    ├── eval.py           # FIXED harness exposing `evaluate(prompt: str) -> dict`
    ├── submit.py         # FIXED one-shot held-out scorer (test split)
    ├── baseline_prompt.txt
    ├── requirements.txt
    └── README.md
```

Contract every task honors:

- **Input:** a single prompt string.
- **Entry point:** `evaluate(prompt: str, *, split="train") -> dict` in `eval.py`,
  also runnable as `python eval.py --prompt-file <file>`.
- **Iterate vs. report:** `eval.py` scores the **train** split and returns a rich
  per-item log; `submit.py` scores the **held-out test** split **once** and
  returns aggregate-only numbers (sealed in `submission.json`).
- **Output:** a dict including at least `metric`/`accuracy`, `n`, and — on the
  train split — a `samples` list with the per-item log.
- **Fixed:** model, datasets, and scoring are frozen in `eval.py`; only the prompt
  varies between runs.

A selector can enumerate tasks by globbing `research_tasks/*/task.json`.

## Available tasks

| id                 | description                                                  |
| ------------------ | ------------------------------------------------------------ |
| `medical_reasoning`| Prompt-engineer a fixed Claude model on MedXpertQA MCQs: iterate on 20 train items, report once on 20 disjoint held-out test items; metric = accuracy. |

See each task's `README.md` for setup and usage.
