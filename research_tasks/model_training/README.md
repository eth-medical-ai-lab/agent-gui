# Research Tasks — Model Training

A small library of self-contained **model-training** tasks. **One folder = one
task.** The agent writes a training script; the task's fixed harness trains-then-
scores the resulting model on a held-out split and returns a metric plus a
detailed log.

These are the training-task counterpart of `../prompt_engineering/` — same
iterate-on-val / clear-the-bar-on-test discipline, but the thing being optimized
is a **trained model** (a saved artifact) rather than a prompt string. Each task
sets an **absolute bar** on the held-out metric (e.g. test accuracy ≥ 0.70) and
asks for the standard supporting artifacts: train-vs-val loss curves,
interpretability, and a val+test metric report.

## Convention (so the selector can discover tasks)

Every task folder contains a `task.md` spec, a fixed `eval.py`, and a fixed
`submit.py`:

```
model_training/
└── <task_id>/
    ├── task.md           # the task spec — paste into the agent window (goal, bar, workflow, deliverables, contract)
    ├── data/<id>.npz     # the dataset splits, VENDORED — read directly, offline
    ├── model.py          # the agent's FILE — build_model() + train() (ships as the weak baseline)
    ├── base_model.py     # FIXED: the baseline — architecture + recipe; reference for compare.py
    ├── compare.py        # FIXED: plots base-vs-advanced val-loss from runs/ (train each first; no training here)
    ├── prepare_data.py   # FIXED: verify the vendored splits (numpy-only, run once)
    ├── eval.py           # FIXED harness: evaluate(model_path, *, split="val") -> dict; model.pt save/load contract
    ├── submit.py         # FIXED held-out scorer (test split, re-runnable) + pass/fail bar
    ├── plot_curves.py    # FIXED: train-vs-val loss curve from runs/<tag>.json
    └── requirements.txt  # pip deps (no conda; `pip install -r requirements.txt`)
```

Interpretability is a **required deliverable** but no template ships with the
task — the agent implements it.

Contract every task honors:

- **Input:** a trained **model artifact** (e.g. a TorchScript `model.pt`) produced
  by training the agent's `model.py`, which now owns **both** the architecture
  (`build_model()`) and the training recipe (`train()`). The artifact's
  input/output tensor contract is documented in `eval.py` and `task.md`.
- **Entry point:** `evaluate(model_path: str, *, split="val") -> dict` in
  `eval.py`, also runnable as `python eval.py --model <file>`.
- **Iterate vs. report:** `eval.py` scores the **val** split (model selection,
  early stopping, overfitting diagnosis) and returns a rich per-item / per-class
  log; `submit.py` scores the **held-out test** split (**re-runnable** — submit as
  many times as you like) and returns aggregate-only numbers with a pass/fail
  against the task's bar (written to `submission.json`).
- **Success criterion:** an **absolute threshold** on the held-out metric (no
  baseline to beat) — e.g. `test accuracy ≥ 0.70` — declared in the task's
  `task.md`.
- **Deliberately weak starter:** `model.py` ships as an exact copy of
  `base_model.py` — a plain MLP trained with a vanilla recipe — that scores well
  below the bar by design (e.g. ~0.58 on the 11-class organ task), so there is
  genuine work to do; the agent replaces both the architecture and the recipe with
  a real model (a CNN) to clear the bar. A required deliverable trains the
  baseline and the agent's model separately (each writes its own `runs/<tag>.json`)
  and `compare.py` then overlays the two val-loss curves.
- **Output:** a dict including the headline `metric` (e.g. `accuracy`), secondary
  metrics (`auc`, …), `n`, and — on val — a per-item `samples` log.
- **Fixed:** the dataset splits, preprocessing, scoring, and the `model.pt`
  save/load contract are frozen; the agent's `model.py` owns **both** the
  architecture *and* the training recipe (`base_model.py` is the frozen reference
  baseline for both).

A selector can enumerate tasks by globbing `research_tasks/*/*/task.md`
(these model-training tasks deliver a trained artifact, vs. the prompt tasks).

## Available tasks

| id              | description                                                                                                                                  |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `organsmnist`   | Train a classifier on MedMNIST OrganSMNIST (11-class abdominal-CT organ ID). `model.py` ships as a weak MLP (~0.58 test); a CNN clears the bar. Iterate on val (model selection / early stopping / overfit check); check on held-out test (re-runnable). **Bar: test accuracy ≥ 0.70.** Deliver loss curves + a base-vs-advanced comparison + interpretability + a val/test metric report. |

To stamp out a sibling MedMNIST task (PathMNIST, OrganAMNIST, DermaMNIST, …), copy
the `organsmnist/` folder, vendor that dataset's `.npz` under `data/`, and change
the frozen config block at the top of `eval.py` (`DATASET`, `N_CHANNELS`,
`N_CLASSES`, `LABELS`, `SPLIT_SIZES`) plus `task.md` (incl. the bar). The harness,
scorer, starter, and reporting helpers are otherwise dataset-agnostic.

> **Attribution:** these tasks use the [MedMNIST](https://medmnist.com) datasets.
> If you use them, cite MedMNIST **and** each subset's source dataset — the
> citation is in each task's `LICENSE.md` (e.g. [`organsmnist/LICENSE.md`](organsmnist/LICENSE.md)).

See each task's `task.md` for setup and usage.
