"""Plot the train-vs-val loss curve (and val accuracy) from a training run.

Each training run (``python model.py``, or ``python base_model.py``) writes
``runs/<tag>.json`` with a per-epoch history of ``train_loss`` / ``val_loss`` /
``val_acc``. This frozen helper turns that into ``loss_curves.png``:

  * left panel  — **train loss vs. val loss** per epoch. The canonical
    overfitting view: when val loss flattens or rises while train loss keeps
    falling, the model is overfitting (the gap is the tell).
  * right panel — **val accuracy** per epoch.

    python model.py                          # -> runs/<tag>.json
    python plot_curves.py                    # -> loss_curves.png

With several runs in ``runs/`` it overlays them (one colour per tag), so you can
compare recipes. Frozen — it only reads whatever ``runs/*.json`` exist.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

TASK_DIR = Path(__file__).resolve().parent
RUNS_DIR = TASK_DIR / "runs"
OUT = TASK_DIR / "loss_curves.png"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Plot only this run (default: all runs in runs/).")
    args = parser.parse_args()

    runs = sorted(RUNS_DIR.glob("*.json")) if RUNS_DIR.is_dir() else []
    if args.tag:
        runs = [p for p in runs if p.stem == args.tag]
    if not runs:
        raise SystemExit(f"no runs found in {RUNS_DIR}. Train first, e.g. `python model.py`.")

    fig, (ax_loss, ax_acc) = plt.subplots(1, 2, figsize=(13, 5))
    multi = len(runs) > 1

    for path in runs:
        data = json.loads(path.read_text(encoding="utf-8"))
        hist = data.get("history", [])
        if not hist:
            continue
        tag = data.get("tag", path.stem)
        epochs = [h["epoch"] for h in hist]
        train_loss = [h["train_loss"] for h in hist]
        val_loss = [h.get("val_loss") for h in hist]
        val_acc = [h.get("val_acc") for h in hist]
        suffix = f" — {tag}" if multi else ""

        ax_loss.plot(epochs, train_loss, linewidth=1.8, label=f"train loss{suffix}")
        ax_loss.plot(epochs, val_loss, linewidth=1.8, linestyle="--", label=f"val loss{suffix}")
        ax_acc.plot(epochs, val_acc, linewidth=1.8, label=f"val acc{suffix}")
        best = max((a for a in val_acc if a is not None), default=None)
        print(f"  {tag:<16} epochs={len(epochs):<4} final_train_loss={train_loss[-1]:.4f} "
              f"final_val_loss={val_loss[-1]:.4f} best_val_acc={best:.4f}")

    ax_loss.set_xlabel("epoch"); ax_loss.set_ylabel("loss")
    ax_loss.set_title("Training vs. validation loss"); ax_loss.grid(True, alpha=0.3); ax_loss.legend()

    ax_acc.set_xlabel("epoch"); ax_acc.set_ylabel("val accuracy")
    ax_acc.set_title("Validation accuracy"); ax_acc.grid(True, alpha=0.3); ax_acc.legend()

    fig.tight_layout()
    fig.savefig(OUT, dpi=120)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
