# OrganSMNIST Classification

**Task:** Train an image classifier for 11 abdominal organs from 28×28 grayscale CT slices. You edit only `model.py`, which holds both the architecture and the training recipe.

**Bar:** held-out test accuracy ≥ 0.70.

**Timeouts:** set every timeout to 600s across the board. Don't set random per-step timeouts.

## What's yours vs. frozen
- **Yours:** `model.py` — `build_model()` (architecture: `[N,1,28,28]` → `[N,11]` logits) and `train()` (optimizer, LR, epochs, weight decay, augmentation, scheduler, early stopping), plus your own interpretability code.
- **Frozen:** data, preprocessing, scorer (`eval.py`/`submit.py`), `base_model.py`, `compare.py`, `plot_curves.py`, and the `model.pt` save/load contract.

## Constraints
- You are training on a CPU. Keep the model small and training fast. There is a hard 600s timeout per terminal command. However, training is not likely to hit this limit if you keep the model compact.

## Data
~3,483 train images (deterministic 1/4 slice), 2,452 val (full), test scored on a deterministic 1/4 slice (2,207). 11 imbalanced classes; liver and pancreas dominate, small organs are easy to miss. CPU training is fine.

## The starting point
Starter `model.py` is a copy of the weak MLP baseline (~0.58 test). It discards spatial structure — replace it. A compact CNN (a few conv→BN→ReLU→pool blocks into a small head) clears 0.70 comfortably; well-tuned reaches ~0.78. Avoid oversized/pretrained backbones — they overfit 3.5k tiny images fast.


## Workflow
1. Edit `model.py` — architecture + recipe.
2. `python model.py` — trains, early-stops on val loss, writes `model.pt` and `runs/model.json`.
3. `python eval.py --model model.pt` — val accuracy, macro AUC, confusion matrix, per-class recall/precision → `last_result.json`. `--split train` checks overfit.
4. `python plot_curves.py` → `loss_curves.png`.
5. Compare vs. baseline: `python base_model.py --epochs 20`, `python model.py --epochs 20`, `python compare.py` → `comparison.png`. (Add `--patience 20` for full 20-epoch curves.)
6. Interpretability — your own attribution (one of saliency / integrated gradients / occlusion / Grad-CAM) on ~10 curated val examples + short writeup. Grad-CAM needs the eager model from `build_model()` with trained weights, not the TorchScript `model.pt`.
7. `python submit.py --model model.pt` — scores test, prints PASS/below-target vs. 0.70 → `submission.json`. Re-runnable, aggregate-only.

## Deliverables
1. `model.pt` (TorchScript, test acc ≥ 0.70) — input `float32 [N,1,28,28]`, normalized mean=0.5/std=0.5; output raw logits `[N,11]`.
2. `loss_curves.png` — train + val loss and val accuracy.
3. `comparison.png` — baseline vs. your model, val loss over 20 epochs.
4. Interpretability figures + writeup.
5. Metric report — val (`last_result.json`) and test (`submission.json`).