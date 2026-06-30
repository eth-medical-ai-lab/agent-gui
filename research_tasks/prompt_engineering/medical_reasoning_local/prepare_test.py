"""Build a held-out TEST set for the medical_reasoning task.

Experiment design
-----------------
- DEV set  = ``data.jsonl`` (seed=42, n=20). We *prompt-engineer* on this.
- TEST set = ``data_test.jsonl`` (this script). A larger, DISJOINT sample from
  the same MedXpertQA "test" split, used ONLY to report final performance of a
  prompt that was tuned on dev. Disjointness from dev is enforced explicitly.

Uses only the standard library via the public HF datasets-server REST API.
"""

from __future__ import annotations

import json
import random
import time
import urllib.request
from pathlib import Path

DATASET = "TsinghuaC3I/MedXpertQA"
CONFIG = "Text"
SPLIT = "test"
SPLIT_SIZE = 2450

DEV_SEED = 42          # must match prepare_data.py so we can exclude dev items
DEV_N = 20
TEST_SEED = 2024
TEST_N = 100

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "data_test.jsonl"
ROWS_URL = "https://datasets-server.huggingface.co/rows"


def _fetch_row(index: int) -> dict:
    url = (f"{ROWS_URL}?dataset={DATASET}&config={CONFIG}&split={SPLIT}"
           f"&offset={index}&length=1")
    last_err = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=25) as resp:
                return json.load(resp)["rows"][0]["row"]
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"failed to fetch row {index}: {last_err}")


def main() -> None:
    dev_idx = set(random.Random(DEV_SEED).sample(range(SPLIT_SIZE), DEV_N))
    pool = [i for i in range(SPLIT_SIZE) if i not in dev_idx]
    test_idx = sorted(random.Random(TEST_SEED).sample(pool, TEST_N))
    assert not (set(test_idx) & dev_idx), "test/dev overlap!"
    print(f"dev_n={len(dev_idx)} test_n={len(test_idx)} (disjoint) seed_test={TEST_SEED}")

    written = 0
    with OUT_PATH.open("w", encoding="utf-8") as out:
        for i in test_idx:
            row = _fetch_row(i)
            options = {k: v for k, v in (row.get("options") or {}).items() if v}
            assert row["label"] in options, f"bad label for {row['id']}"
            out.write(json.dumps({
                "id": row["id"],
                "question": row["question"],
                "options": options,
                "label": row["label"],
                "medical_task": row.get("medical_task"),
                "body_system": row.get("body_system"),
                "question_type": row.get("question_type"),
            }, ensure_ascii=False) + "\n")
            written += 1
            if written % 10 == 0:
                print(f"  [{written}/{TEST_N}]")
    print(f"wrote {written} samples to {OUT_PATH}")


if __name__ == "__main__":
    main()
