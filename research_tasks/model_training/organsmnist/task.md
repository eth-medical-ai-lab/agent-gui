# OrganSMNIST Classification (model training)

You are working on the **OrganSMNIST** model-training task. Train an image
classifier that identifies which of **11 abdominal organs** is shown in a 28×28
CT slice. You write **`model.py`** — the only file you edit — and it holds **both
your architecture _and_ your training recipe**. Deliver the trained `model.pt`
plus loss curves, a base-vs-advanced comparison, interpretability, and a val/test
metric report. The bar to clear: **held-out test accuracy ≥ 0.70**. Narrate what
you're trying as you go.

What's frozen is only the parts that make scores comparable: the **data,
preprocessing, and scorer** (`eval.py` / `submit.py`) and the **`model.pt`
save/load contract**. _How_ you model and train — the architecture, the
optimizer, the learning rate, augmentation, the epoch budget, early stopping — is
entirely yours.

## Setup

```bash
pip install -r requirements.txt     # torch, numpy, scikit-learn, matplotlib
python prepare_data.py              # verify the vendored splits (offline)
```

> Install a CPU build of PyTorch and train on CPU — this task is small and fast.

## The goal & the bar

- **Pass condition:** `submit.py` reports **test accuracy ≥ 0.70**. You may submit
  **as many times as you like** — keep improving `model.py`, retrain, and re-run
  `submit.py` until you clear the bar.
- **The starter is a deliberately weak baseline.** Out of the box `model.py` is an
  exact copy of `base_model.py` — a plain **MLP** (fully-connected on flattened
  pixels) trained with a vanilla recipe — and it scores **~0.58 on the held-out
  test split**. It throws away all spatial structure, which is exactly the wrong
  thing to do with images. That is your floor; replace it with a real model.
- **Mind the val→test gap.** OrganSMNIST's val and test splits differ noticeably,
  so val scores run **optimistic** — this MLP looks like ~0.75 on val but only
  ~0.58 on test. Select and early-stop on val, but remember the bar is on **test**
  and your val number will overstate it. (`submit.py` only ever shows the
  aggregate test number, so you can re-check but not memorize individual items.)
- **There is real headroom.** A small **CNN** already clears ~0.70+ on test, and a
  well-tuned one reaches **~0.78** (the published ResNet-18 reference — context,
  not a target). Convolutions exploit locality and translation that the MLP can't.
- **Primary metric:** accuracy (top-1 over 11 classes; chance is ~0.09). Macro
  one-vs-rest **AUC** and per-class recall/precision are reported alongside —
  watch the per-class recalls, since the classes are imbalanced (liver and
  pancreas dominate; the small organs are easy to miss).

> **Rule:** train on `train`, tune/select on `val`; `test` is the bar — you may
> re-check it (aggregate-only) and resubmit until you clear 0.70.
> **Rule:** if you are setting timeout for terminal commands, set timeout >= 10 minutes for `model.py` / `base_model.py`, and >= 5 minutes for the rest.

## Sizing the model (dataset scale)

This is a **small-data, small-image** regime — pick an architecture and recipe to
match, not an oversized one:

- **~3,483** training images (a frozen 1/4 slice of the train split), **28×28
  grayscale**, **11 imbalanced** classes; `val` = 2,452 (scored in full).
- A **compact CNN** — a few conv blocks (conv → BN → ReLU → pool) into a small
  classifier head — is the sweet spot and clears the bar comfortably. Large or
  ImageNet-pretrained backbones overfit ~3.5k tiny grayscale images quickly; if
  you use one, lean hard on augmentation, weight decay, dropout, and early
  stopping.
- A bigger or differently-shaped network usually wants a **different recipe** than
  the baseline MLP (learning rate, batch size, epoch budget, weight decay, maybe a
  scheduler). That's the whole point of owning `train()` — tune the recipe _with_
  the architecture, and watch the train-vs-val gap (`plot_curves.py`) for
  overfitting.

## Workflow

The starter `model.py` ships as a _copy of the weak MLP baseline_ (`base_model.py`,
architecture **and** recipe), so the pipeline runs out of the box and scores ~0.58
on test. Your job is to replace both halves with something that works.

1. **Edit `model.py`.** Rewrite `build_model()` to return your architecture
   (`[N, 1, 28, 28]` → `[N, 11]` logits) **and** tune the training recipe (the
   `EPOCHS` / `BATCH_SIZE` / `LR` / `WEIGHT_DECAY` / … knobs and the `train()` loop
   itself — add augmentation or a scheduler if you like). This is the **only file
   you edit**.
2. **Train.** `python model.py` — trains `model.py`'s `build_model()` with _your_
   recipe, early-stops on val loss, writes `model.pt` and the loss history
   (`runs/model.json`).
3. **Iterate against val.** `python eval.py --model model.pt` — prints accuracy,
   macro AUC, an 11×11 confusion matrix, and per-class recall/precision, and
   writes a full per-item log to `last_result.json` (use the misclassified items
   to decide what to change). `--split train` checks for overfit.
4. **Plot the loss curves.** `python plot_curves.py` → `loss_curves.png`
   (your model's train vs. val loss + val accuracy).
5. **Compare against the baseline.** Train each model, then plot — `compare.py`
   only *plots* (it does not train):
   ```bash
   python base_model.py --epochs 20    # baseline curve -> runs/base.json
   python model.py      --epochs 20    # your model     -> runs/model.json (+ model.pt)
   python compare.py                   # overlay both val-loss curves -> comparison.png
   ```
   Required deliverable: it shows your approach actually beats the MLP. Order
   doesn't matter (`base_model.py` never writes `model.pt`, so it can't clobber
   yours); both honor early stopping, so add `--patience 99` to each training
   command if you want both curves to span the full 20 epochs.
6. **Interpretability.** Produce your own attribution analysis of the trained
   model on a handful of curated val examples — implement it yourself (saliency /
   integrated gradients / occlusion / **Grad-CAM** — your choice) and save the
   figures + a short writeup. Mind *where* you run it: input-space methods
   (occlusion, integrated gradients) work on the TorchScript `model.pt` directly,
   but **Grad-CAM** and other layer methods must hook an internal conv layer —
   which TorchScript doesn't expose — so run those on your *eager* model from
   `build_model()` with the trained weights (you own `train()`, so save a
   `state_dict` there and reload it, or compute the attribution in-process before
   serializing). (Use val examples — you only ever get the aggregate test number,
   not per-item data.)
7. **Submit** against the held-out **test** split.
   `python submit.py --model model.pt` — scores `model.pt` on `test`, prints a
   **PASS/below-target** verdict against 0.70, and writes the result to
   `submission.json`. You may run it **as many times as you like** (each run
   overwrites `submission.json` and bumps an attempt counter; exit code is 0 on
   PASS, 1 below target). Output is **aggregate-only** — no per-item test
   predictions, so you see the test number but not which individual items missed.

## Deliverables

1. **`model.pt`** — TorchScript model (from your `model.py`) with **test
   accuracy ≥ 0.70**.
2. **Loss curves (train + val)** — `loss_curves.png` (overfitting view + val
   accuracy).
3. **Base-vs-advanced comparison** — `comparison.png` (`python compare.py`): the
   baseline MLP's and your model's **val loss over 20 epochs** on one graph.
4. **Interpretability** — your own attribution analysis (saliency / integrated
   gradients / occlusion / Grad-CAM — your choice) on ~10 curated val examples,
   showing where the model looks: the figures + a short writeup. No template is
   provided. (Grad-CAM needs the eager model from `build_model()`, not the
   TorchScript `model.pt` — see workflow step 6.)
5. **Metric report** — **val** metrics (`eval.py` → `last_result.json`, rich
   per-class log) and the **test** metrics (`submit.py` → `submission.json`).

## The dataset

[OrganSMNIST](https://medmnist.com) — abdominal CT, **sagittal** views, **11-class**
organ classification (bladder, femur-left, femur-right, heart, kidney-left,
kidney-right, liver, lung-left, lung-right, pancreas, spleen), 28×28 grayscale.
Official splits, **vendored** as `data/organsmnist.npz` and read directly from
that file (no download, no `medmnist` package):

| split | n     | use                                                                                          |
| ----- | ----- | -------------------------------------------------------------------------------------------- |
| train | 13932 | train your model (a frozen **1/4 subsample** is used, → 3483, for speed)                     |
| val   | 2452  | model selection / early stopping / overfit check (`eval.py`) — **full**                      |
| test  | 8827  | the bar — score against 0.70 (`submit.py`, re-runnable) — a frozen **1/4 subsample**, → 2207 |

Training uses a deterministic quarter of the train split (`eval.TRAIN_SUBSAMPLE`)
and scores against a deterministic quarter of the test split
(`eval.TEST_SUBSAMPLE`) so runs are fast on CPU and stay under the exec-env
timeout; `val` is always scored in full. Both 1/4 slices are deterministic and
preserve class balance, so the 0.70 bar is unchanged. A CUDA GPU is used
automatically if present.

## What you change vs. what is frozen

| You change                                                                                                                                                                                                                                                                                                                           | Frozen (do not touch)                                                                                                                                                                                                                                                                                                                       |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`model.py`** — your architecture (`build_model()`: conv stacks, normalization, dropout, residual blocks, transfer learning, …) **and** your training recipe (the `train()` loop: optimizer, lr, epochs, weight decay, augmentation, scheduler, early stopping) — **plus** your own interpretability code (no template is provided) | `base_model.py` (the reference baseline — architecture **and** recipe — that produced the `model.pt` floor), `compare.py`, the data splits + preprocessing (`eval.py`, read from `data/*.npz`), the scorer (`eval.py` / `submit.py`), the `model.pt` save/load contract (`eval.save_torchscript` / `eval.load_model`), and `plot_curves.py` |

Keeping the **data + preprocessing + metric** fixed is what makes the held-out
score a fair bar and two submissions directly comparable. The base-vs-advanced
comparison now pits the baseline's _whole approach_ (its architecture + recipe)
against _yours_ — the honest comparison of the thing you actually built.

Two small outputs let the frozen tooling keep working — everything else in
`model.py` (all of `train()`) is yours:

- **`build_model()`** returns an `nn.Module` mapping `[N, 1, 28, 28]` → `[N, 11]`.
- **Running `python model.py`** must write the TorchScript `model.pt` (via
  `eval.save_torchscript`) and the per-epoch loss history `runs/model.json`
  (`epoch` / `train_loss` / `val_loss` / `val_acc`). `compare.py` overlays that
  history against `runs/base.json` and `plot_curves.py` plots it.

## The model artifact contract

`model.pt` must be a **TorchScript** module (`torch.jit.save`) where:

- **input** — `float32` tensor `[N, 1, 28, 28]`, normalized with `mean=0.5,
std=0.5` (the canonical MedMNIST transform produced by `eval.preprocess()` /
  `eval.load_dataset()`).
- **output** — raw class logits `[N, 11]` (the harness applies softmax/argmax).

You return an `nn.Module` from `build_model()`; your `train()` serializes the best
checkpoint to TorchScript for you via the frozen `eval.save_torchscript`
(`torch.jit.script`, falling back to `trace`). Any architecture is fine as long as
it honors this input/output contract and is TorchScript-serializable (standard
layers are).

```python
from eval import evaluate
result = evaluate("model.pt")          # val split by default
print(result["accuracy"], result["auc"])
print(result["per_class"], result["confusion"])
```

`evaluate(model_path, split="test")` returns the same headline numbers
**without** any per-item log — aggregate-only, by design (use `submit.py` for the
held-out test measurement).

## Scoring

Accuracy and AUC are computed locally with scikit-learn using the MedMNIST
leaderboard protocol (top-1 accuracy; AUC = macro one-vs-rest `roc_auc_score`
over the 11 classes). No network, no `medmnist` package.

## Files

```
organsmnist/
├── data/organsmnist.npz      # official splits, VENDORED — read directly, offline
├── model.py            # YOUR FILE — build_model() + train() (ships as the weak MLP baseline)
├── base_model.py       # FROZEN: the baseline — architecture + recipe; produced the model.pt floor
├── compare.py          # FROZEN: plots base-vs-advanced val-loss from runs/ (does NOT train)
├── prepare_data.py     # FROZEN: verify the vendored splits (numpy-only)
├── eval.py             # FROZEN iteration harness (val split) + model.pt save/load contract — do not edit
├── submit.py           # FROZEN held-out scorer (test split, re-runnable) + 0.70 check
├── plot_curves.py      # FROZEN: train-vs-val loss curve + val-accuracy plot
├── requirements.txt    # pip deps (no conda)
└── LICENSE.md          # dataset license + citation
```
