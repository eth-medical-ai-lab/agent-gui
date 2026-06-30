"""YOUR model AND training — EDIT THIS FILE (this is the only file you change).

You own the whole approach here: the **architecture** (``build_model``) *and* the
**training recipe** (the hyperparameters + the ``train`` loop below). A real CNN
usually needs a different recipe than the baseline MLP — a different learning
rate, batch size, epoch budget, weight decay, maybe augmentation or an LR
scheduler — so both halves are yours to rewrite.

This file ships as an exact **copy of the baseline** (``base_model.py``): a plain
MLP trained with a vanilla recipe, so the pipeline runs out of the box and scores
**~0.58 on test**. That is the floor. Replace the MLP with an architecture that
exploits image structure (a small **CNN** already clears the **test >= 0.70**
bar; a well-tuned one reaches ~0.78) and tune the recipe to match.

Dataset scale — size the model sensibly:
  * **~3,483** training images (a frozen 1/4 slice of OrganSMNIST's train split),
    **28x28 grayscale**, **11 imbalanced** organ classes; val = 2,452 (full).
  * This is a *small-data, small-image* regime. A **compact CNN** — a few conv
    blocks (conv -> BN -> ReLU -> pool) into a small classifier head — is the
    sweet spot. Large or ImageNet-pretrained backbones overfit ~3.5k tiny
    grayscale images fast; if you go that route, lean hard on augmentation,
    weight decay, dropout, and early stopping. Watch the train-vs-val gap
    (``plot_curves.py``) — when val loss rises while train loss keeps falling,
    you are overfitting.

How it runs (you do NOT edit the data, preprocessing, or scorer):
    python model.py          # trains build_model() with YOUR recipe -> model.pt (+ runs/model.json)
    python base_model.py     # trains the baseline -> runs/base.json (for the comparison)
    python compare.py        # overlays runs/base.json vs runs/model.json -> comparison.png
    python eval.py  --model model.pt    # val metrics (rich per-class log)
    python submit.py --model model.pt   # held-out test score + 0.70 check (re-runnable)

Two contracts to keep (everything else — including all of ``train()`` — is free):
  1. ``build_model()`` returns an ``nn.Module`` mapping a normalized batch
     ``[N, 1, 28, 28]`` -> class logits ``[N, 11]`` (TorchScript-serializable —
     standard layers are).
  2. Running ``python model.py`` writes BOTH (a) the TorchScript ``model.pt`` (via
     the frozen ``eval.save_torchscript``) and (b) the per-epoch loss history to
     ``runs/model.json`` (``epoch`` / ``train_loss`` / ``val_loss`` / ``val_acc``)
     — that is what ``compare.py`` overlays and ``plot_curves.py`` plots. Keep
     those two outputs and you can rewrite ``train()`` however you like (optimizer,
     scheduler, augmentation, loss, …).
"""

from __future__ import annotations

import argparse
import copy
import json

import torch
import torch.nn as nn
from torch.utils.data import DataLoader

# Frozen data + preprocessing + the model.pt save/load contract live in eval.py,
# so training and scoring see byte-identical inputs and a compatible artifact.
from eval import (
    IMG_SIZE,
    MODEL_FILENAME,
    N_CHANNELS,
    N_CLASSES,
    TASK_DIR,
    evaluate,
    load_dataset,
    pick_device,
    save_torchscript,
)
from prepare_data import ensure_data


# --- Your architecture --------------------------------------------------------
def build_model() -> nn.Module:
    """Return your model. TODO: replace this MLP with a real architecture (a CNN)."""
    in_dim = N_CHANNELS * IMG_SIZE * IMG_SIZE
    return nn.Sequential(
        nn.Flatten(),
        nn.Linear(in_dim, 256), nn.ReLU(inplace=True),
        nn.Linear(256, 128), nn.ReLU(inplace=True),
        nn.Linear(128, N_CLASSES),
    )


# --- Your training recipe -----------------------------------------------------
# TODO: tune these for YOUR architecture. A CNN typically wants a different lr /
# batch size / epoch budget / weight decay than this MLP, and you may add data
# augmentation or an LR scheduler inside train() below.
EPOCHS = 20
BATCH_SIZE = 128
LR = 1e-3
WEIGHT_DECAY = 0.0
PATIENCE = 8        # early-stop after this many epochs with no val-loss improvement
SEED = 0


@torch.no_grad()
def _val_loss(model, loader, criterion, device) -> tuple[float, float]:
    """Mean val loss + val accuracy over one pass (drives early stopping)."""
    model.eval()
    total_loss, correct, seen = 0.0, 0, 0
    for x, y in loader:
        x = x.to(device).float()
        y = torch.as_tensor(y).reshape(-1).long().to(device)
        logits = model(x)
        total_loss += criterion(logits, y).item() * x.size(0)
        correct += int((logits.argmax(1) == y).sum().item())
        seen += x.size(0)
    return total_loss / seen, correct / seen


def train(
    *,
    out=None,
    tag: str = "model",
    epochs: int = EPOCHS,
    batch_size: int = BATCH_SIZE,
    lr: float = LR,
    weight_decay: float = WEIGHT_DECAY,
    patience: int = PATIENCE,
    seed: int = SEED,
    write_runs: bool = True,
    verbose: bool = True,
) -> list[dict]:
    """Train ``build_model()`` with YOUR recipe; return the per-epoch history.

    This loop is yours to change (optimizer, scheduler, augmentation, loss, …).
    Keep the keyword-argument signature and the returned history shape so
    ``compare.py`` / ``plot_curves.py`` keep working. Seeds *before* building (so
    weight init + training reproduce), records ``train_loss`` / ``val_loss`` /
    ``val_acc`` each epoch, keeps the lowest-val-loss checkpoint, and early-stops
    after ``patience`` epochs without improvement. With ``out`` set, saves the
    best checkpoint as the TorchScript ``model.pt`` (via the frozen
    ``eval.save_torchscript``); with ``write_runs`` set, writes the history to
    ``runs/<tag>.json`` (read by ``plot_curves.py``).
    """
    torch.manual_seed(seed)
    ensure_data()
    device = pick_device()
    if verbose:
        print(f"[{tag}] device={device} epochs={epochs} batch={batch_size} "
              f"lr={lr} weight_decay={weight_decay} patience={patience}")

    train_loader = DataLoader(load_dataset("train"), batch_size=batch_size, shuffle=True, num_workers=0)
    val_loader = DataLoader(load_dataset("val"), batch_size=256, shuffle=False, num_workers=0)

    model = build_model().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
    criterion = nn.CrossEntropyLoss()

    history: list[dict] = []
    best_val_loss = float("inf")
    best_state = copy.deepcopy(model.state_dict())
    best_epoch, since_improved = 0, 0

    for epoch in range(1, epochs + 1):
        model.train()
        running, seen = 0.0, 0
        for x, y in train_loader:
            x = x.to(device).float()
            y = torch.as_tensor(y).reshape(-1).long().to(device)  # labels are [B, 1]
            optimizer.zero_grad()
            loss = criterion(model(x), y)
            loss.backward()
            optimizer.step()
            running += loss.item() * x.size(0)
            seen += x.size(0)
        train_loss = running / seen
        val_loss, val_acc = _val_loss(model, val_loader, criterion, device)
        history.append({"epoch": epoch, "train_loss": train_loss, "val_loss": val_loss, "val_acc": val_acc})
        if verbose:
            print(f"  [{tag}] epoch {epoch:>2}/{epochs}  train_loss={train_loss:.4f}  "
                  f"val_loss={val_loss:.4f}  val_acc={val_acc:.4f}")

        if val_loss < best_val_loss - 1e-4:
            best_val_loss, best_epoch, since_improved = val_loss, epoch, 0
            best_state = copy.deepcopy(model.state_dict())
        else:
            since_improved += 1
            if since_improved >= patience:
                if verbose:
                    print(f"  [{tag}] early stop: no val-loss improvement for {patience} epochs "
                          f"(best epoch {best_epoch}, val_loss={best_val_loss:.4f})")
                break

    model.load_state_dict(best_state)  # restore the best checkpoint, not the last

    if write_runs:
        runs_dir = TASK_DIR / "runs"
        runs_dir.mkdir(exist_ok=True)
        (runs_dir / f"{tag}.json").write_text(
            json.dumps({"tag": tag, "epochs": epochs, "best_epoch": best_epoch, "history": history}, indent=2),
            encoding="utf-8",
        )
        if verbose:
            print(f"  [{tag}] loss history -> {runs_dir / f'{tag}.json'}")

    if out is not None:
        save_torchscript(model, out)
        if verbose:
            print(f"  [{tag}] saved TorchScript model -> {out}  (best epoch {best_epoch})")
    return history


def main() -> None:
    parser = argparse.ArgumentParser(description="Train YOUR model.py and write model.pt.")
    parser.add_argument("--epochs", type=int, default=EPOCHS)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--lr", type=float, default=LR)
    parser.add_argument("--weight-decay", type=float, default=WEIGHT_DECAY)
    parser.add_argument("--patience", type=int, default=PATIENCE,
                        help="Early-stop after this many epochs with no val-loss improvement.")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--out", default=str(TASK_DIR / MODEL_FILENAME), help="Where to write model.pt.")
    parser.add_argument("--tag", default="model",
                        help="Run name; loss history is saved to runs/<tag>.json (read by plot_curves.py).")
    args = parser.parse_args()

    train(out=args.out, tag=args.tag, epochs=args.epochs, batch_size=args.batch_size,
          lr=args.lr, weight_decay=args.weight_decay, patience=args.patience, seed=args.seed)

    # Convenience: report the val metrics right away (same as `python eval.py`).
    result = evaluate(args.out, split="val")
    print(f"\nval accuracy: {result['accuracy']:.4f}   (VAL — optimistic; the bar is on TEST)")
    print(f"val AUC:      {result['auc']:.4f}   (macro one-vs-rest)")
    print("  The shipped starter is the weak MLP baseline (~0.58 test). Replace the")
    print("  architecture with a CNN and tune the recipe until TEST clears 0.70 (submit.py).")


if __name__ == "__main__":
    main()
