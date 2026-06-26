"""The frozen BASELINE — DO NOT EDIT.

This one file is the *complete* baseline: it defines the baseline **architecture**
(a plain fully-connected MLP) **and** the baseline **training recipe** (optimizer,
learning rate, epoch budget, early stopping). Running it end to end is exactly how
the shipped reference ``model.pt`` was produced — that ~0.58-on-test artifact is
the output of this file, nothing more.

An MLP on flattened pixels is the wrong tool for images (it discards all spatial
structure), which is why it tops out around **~0.58 on the held-out test split**.
That number is the floor you have to beat. This file exists for two reasons:

  1. it is the reference curve in the required base-vs-advanced comparison
     (``compare.py``), and
  2. it is the file ``model.py`` is *copied from* — your starting point.

Your work happens in ``model.py`` (a copy of this file), where you are free to
rewrite **both** halves — the architecture *and* the training recipe — because a
real CNN generally wants a different recipe than this little MLP.

What stays frozen (so two runs stay comparable and the bar is fair): the data +
preprocessing + the scorer (``eval.py`` / ``submit.py``) and the ``model.pt``
save/load contract (``eval.save_torchscript`` / ``eval.load_model``). Everything
about *how you model and train* is yours.

What ``model.py`` must keep (everything else — including all of ``train()`` — is
free to rewrite):
  * ``build_model() -> nn.Module`` mapping ``[N, 1, 28, 28]`` -> ``[N, 11]`` logits.
  * Running it writes the TorchScript ``model.pt`` (via ``eval.save_torchscript``)
    and the per-epoch loss history ``runs/<tag>.json`` (``epoch`` / ``train_loss``
    / ``val_loss`` / ``val_acc``), which ``compare.py`` overlays and
    ``plot_curves.py`` plots.
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
    N_CHANNELS,
    N_CLASSES,
    TASK_DIR,
    evaluate,
    load_dataset,
    pick_device,
    save_torchscript,
)
from prepare_data import ensure_data


# --- Baseline architecture ----------------------------------------------------
def build_model() -> nn.Module:
    """Plain MLP baseline (the weak reference model)."""
    in_dim = N_CHANNELS * IMG_SIZE * IMG_SIZE
    return nn.Sequential(
        nn.Flatten(),
        nn.Linear(in_dim, 256), nn.ReLU(inplace=True),
        nn.Linear(256, 128), nn.ReLU(inplace=True),
        nn.Linear(128, N_CLASSES),
    )


# --- Baseline training recipe -------------------------------------------------
# The baseline's hyperparameters. In ``model.py`` these are YOURS to tune — a
# deeper CNN usually wants a different lr / batch size / epoch budget / weight
# decay (and often augmentation or a scheduler) than this little MLP.
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
    tag: str = "base",
    epochs: int = EPOCHS,
    batch_size: int = BATCH_SIZE,
    lr: float = LR,
    weight_decay: float = WEIGHT_DECAY,
    patience: int = PATIENCE,
    seed: int = SEED,
    write_runs: bool = True,
    verbose: bool = True,
) -> list[dict]:
    """Train ``build_model()`` with this file's recipe; return the per-epoch history.

    Seeds *before* building (so weight init + training reproduce), records
    ``train_loss`` / ``val_loss`` / ``val_acc`` each epoch, keeps the
    lowest-val-loss checkpoint, and early-stops after ``patience`` epochs without
    improvement. With ``out`` set, saves the best checkpoint as the TorchScript
    ``model.pt`` (via the frozen ``eval.save_torchscript``); with ``write_runs``
    set, writes the history to ``runs/<tag>.json`` (read by ``plot_curves.py``).
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
    parser = argparse.ArgumentParser(
        description="Train the baseline MLP and write its loss curve (runs/base.json).")
    parser.add_argument("--epochs", type=int, default=EPOCHS)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--lr", type=float, default=LR)
    parser.add_argument("--weight-decay", type=float, default=WEIGHT_DECAY)
    parser.add_argument("--patience", type=int, default=PATIENCE,
                        help="Early-stop after this many epochs with no val-loss improvement "
                             "(pass a big value, e.g. 99, for a full-length curve to compare against).")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--out", default=None,
                        help="Optional: also save the trained baseline as a TorchScript .pt "
                             "(e.g. `--out model.pt` regenerates the baseline artifact). Default: "
                             "only the loss history is written, so this can never clobber your model.pt.")
    parser.add_argument("--tag", default="base",
                        help="Run name; loss history is saved to runs/<tag>.json (read by compare.py / plot_curves.py).")
    args = parser.parse_args()

    train(out=args.out, tag=args.tag, epochs=args.epochs, batch_size=args.batch_size,
          lr=args.lr, weight_decay=args.weight_decay, patience=args.patience, seed=args.seed)

    if args.out:  # only when explicitly asked to save the baseline artifact
        result = evaluate(args.out, split="val")
        print(f"\nval accuracy: {result['accuracy']:.4f}   (VAL — optimistic; the bar is on TEST)")
        print(f"val AUC:      {result['auc']:.4f}   (macro one-vs-rest)")
    print("\n  Baseline floor (~0.58 test). Your work is in model.py — a copy of this file")
    print("  you may rewrite end to end (architecture AND training recipe).")
    print("  Comparison plot: this wrote runs/base.json — now run `python model.py`, then `python compare.py`.")


if __name__ == "__main__":
    main()
