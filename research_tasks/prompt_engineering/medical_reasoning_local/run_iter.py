"""Run one DEV prompt-engineering iteration (the held-out test lives in submit.py).

    python run_iter.py <prompt_file> [--name v3] [--workers 20]

Policy:
  - DEV  (data.jsonl, n=20): evaluated EVERY call -> iteration counter += 1.
  - TEST (data_test.jsonl, n=100): NEVER scored here. It is scored exactly
    twice, both sealed one-shots via submit.py — `submit.py --baseline` before
    iterating, and `submit.py --prompt-file <best>` once at the very end.
    Iterating against test would defeat the point of a held-out set.

Every run is appended to results/leaderboard.jsonl and the live dashboard
(results/index.html) is rebuilt.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import eval_qwen as E
import results_log as RL

TASK_DIR = Path(__file__).resolve().parent
DEV_DATA = TASK_DIR / "data.jsonl"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("prompt_file")
    ap.add_argument("--name", help="Prompt label (default = file stem).")
    ap.add_argument("--workers", type=int, default=20)
    args = ap.parse_args()

    prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    name = args.name or Path(args.prompt_file).stem.replace("candidate_prompt_", "").replace("_prompt", "")

    model = E.detect_model()
    state = RL.load_state()
    state["iteration"] += 1
    it = state["iteration"]

    # --- DEV (always) ---
    dev = E.evaluate(prompt, workers=args.workers, model=model,
                     data_path=DEV_DATA, split="dev")
    is_best = dev["accuracy"] > state["best_dev_acc"]
    RL.append_run(iteration=it, prompt_name=name, split="dev", result=dev,
                  trigger="iter", is_dev_best=is_best)
    print(f"[iter {it}] {name}  DEV acc={dev['accuracy']:.3f} "
          f"({dev['n_correct']}/{dev['n']})  best_so_far={max(state['best_dev_acc'],0):.3f}"
          f"{'  <-- NEW DEV BEST' if is_best else ''}")
    if is_best:
        state["best_dev_acc"] = dev["accuracy"]
        state["best_dev_prompt"] = name

    # Persist the full per-question dev log and surface the misses, so the next
    # hand-edit can target real failures — this is the fuel for the loop.
    dev_result = TASK_DIR / "results" / "last_dev_result.json"
    dev_result.parent.mkdir(exist_ok=True)
    dev_result.write_text(json.dumps(dev, indent=2, ensure_ascii=False), encoding="utf-8")
    misses = [r for r in dev["samples"] if not r["is_correct"]]
    if misses:
        print(f"  {len(misses)} miss(es) to fix next (full log: results/{dev_result.name}):")
        for r in misses:
            why = (r.get("reasoning") or r.get("error") or "").strip().replace("\n", " ")
            if len(why) > 200:
                why = why[:200] + "…"
            print(f"    ✗ {r['id']:<12} gold={r['correct']} pred={r['predicted']}  {why}")

    # --- TEST: never here. Two sealed one-shots live in submit.py. ---
    print(f"[iter {it}] dev only — the held-out test is scored exactly twice via "
          f"submit.py (--baseline before iterating; --prompt-file <best> at the end)")

    RL.save_state(state)
    print(f"dashboard: {RL.INDEX}")


if __name__ == "__main__":
    main()
