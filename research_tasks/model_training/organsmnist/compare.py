"""Frozen base-vs-advanced comparison PLOT — DO NOT EDIT.

This does **not** train anything. It just overlays two *already-trained*
validation-loss curves on one graph — the baseline and your model — so you can see
at a glance whether your architecture + recipe actually beat the MLP.

Produce the two curves first (each training run writes its own ``runs/<tag>.json``),
then plot:

    python base_model.py --epochs 20    # baseline curve  -> runs/base.json
    python model.py      --epochs 20    # your model      -> runs/model.json (+ model.pt)
    python compare.py                   # overlay both val-loss curves -> comparison.png

Order doesn't matter: ``base_model.py`` never writes ``model.pt``, so it can't
clobber your model. Both honor early stopping; add ``--patience 99`` to each
training command if you want both curves to span the full 20 epochs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

TASK_DIR = Path(__file__).resolve().parent
RUNS_DIR = TASK_DIR / "runs"
OUT = TASK_DIR / "comparison.png"


def _load(tag: str) -> list[dict]:
    """Read one training run's per-epoch history from ``runs/<tag>.json``."""
    path = RUNS_DIR / f"{tag}.json"
    if not path.is_file():
        raise SystemExit(
            f"missing {path} — train it first, e.g.:\n"
            f"  python base_model.py --epochs 20    # -> runs/base.json\n"
            f"  python model.py      --epochs 20    # -> runs/model.json"
        )
    return json.loads(path.read_text(encoding="utf-8")).get("history", [])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="base", help="run tag for the baseline curve (runs/<tag>.json)")
    parser.add_argument("--model", default="model", help="run tag for your model's curve (runs/<tag>.json)")
    args = parser.parse_args()

    base = _load(args.base)
    adv = _load(args.model)

    fig, ax = plt.subplots(figsize=(7, 5))
    ax.plot([h["epoch"] for h in base], [h["val_loss"] for h in base], "-o", ms=3,
            label=f"baseline MLP — final val_acc {base[-1]['val_acc']:.3f}")
    ax.plot([h["epoch"] for h in adv], [h["val_loss"] for h in adv], "-o", ms=3,
            label=f"advanced (model.py) — final val_acc {adv[-1]['val_acc']:.3f}")
    ax.set_xlabel("epoch")
    ax.set_ylabel("validation loss")
    ax.set_title("Val loss: baseline vs advanced")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT, dpi=120)

    print(f"baseline ({args.base}):  {len(base):>2} epochs   "
          f"final val_loss={base[-1]['val_loss']:.4f}  val_acc={base[-1]['val_acc']:.3f}")
    print(f"advanced ({args.model}): {len(adv):>2} epochs   "
          f"final val_loss={adv[-1]['val_loss']:.4f}  val_acc={adv[-1]['val_acc']:.3f}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
