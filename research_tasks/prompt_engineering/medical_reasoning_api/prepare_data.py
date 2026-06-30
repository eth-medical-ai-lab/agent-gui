"""Regenerate `train.jsonl` / `test.jsonl` — the fixed subsamples for this task.

Both files are produced by this script and checked in, so the task runs offline.
Re-run this only to reproduce or change the subsamples.

Train/test discipline
---------------------
You iterate on your prompt against `train.jsonl` and report your final number on
`test.jsonl`. The two are **disjoint by construction**: a single seeded draw of
`N_TRAIN + N_TEST` distinct indices is partitioned into the two splits, so no item
can appear in both. Never tune against `test.jsonl`.

Provenance
----------
- Dataset : TsinghuaC3I/MedXpertQA, config "Text" (text-only reasoning MCQs).
- Split   : "test" (2450 items). NOTE: MedXpertQA's released "dev" split has only
            5 items (few-shot exemplars), so our subsamples can't come from it. We
            carve fixed, seeded held-out subsamples from the benchmark set: one for
            prompt iteration (train) and one for reporting (test). Keep the full
            "test" set untouched for any number you intend to publish.
- Sample  : `N_TRAIN + N_TEST` distinct indices chosen by
            `random.Random(SEED).sample(range(SPLIT_SIZE), N_TRAIN + N_TEST)`, then
            split into train/test and sorted. With SEED frozen, both subsamples are
            deterministic ("fixed but random").

Uses only the Python standard library via the public HF datasets-server REST API
(no `datasets` package required). Needs network access.
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
N_TRAIN = 20
N_TEST = 20
SEED = 42

TASK_DIR = Path(__file__).resolve().parent
TRAIN_PATH = TASK_DIR / "train.jsonl"
TEST_PATH = TASK_DIR / "test.jsonl"
CACHE_DIR = TASK_DIR / ".row_cache"  # per-row cache so reruns resume after 429s
ROWS_URL = "https://datasets-server.huggingface.co/rows"


def _fetch_row(index: int) -> dict:
    """Fetch one row, caching it on disk so reruns don't refetch (429-resilient)."""
    cache_file = CACHE_DIR / f"{index}.json"
    if cache_file.is_file():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    url = (
        f"{ROWS_URL}?dataset={DATASET}&config={CONFIG}&split={SPLIT}"
        f"&offset={index}&length=1"
    )
    last_err: Exception | None = None
    for attempt in range(6):
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                row = json.load(resp)["rows"][0]["row"]
            CACHE_DIR.mkdir(exist_ok=True)
            cache_file.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
            return row
        except Exception as exc:  # noqa: BLE001 - retry transient errors / 429s
            last_err = exc
            time.sleep(2.0 * (attempt + 1))  # exponential-ish backoff (up to ~12s)
    raise RuntimeError(f"failed to fetch row {index}: {last_err}")


def _write_split(name: str, indices: list[int], out_path: Path) -> None:
    """Fetch the rows at ``indices`` and write them as one JSONL split."""
    written = 0
    with out_path.open("w", encoding="utf-8") as out:
        for i in indices:
            row = _fetch_row(i)
            options = {k: v for k, v in (row.get("options") or {}).items() if v}
            assert row["label"] in options, f"bad label for {row['id']}"
            record = {
                "id": row["id"],
                "question": row["question"],
                "options": options,
                "label": row["label"],
                "medical_task": row.get("medical_task"),
                "body_system": row.get("body_system"),
                "question_type": row.get("question_type"),
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1
            print(f"  [{name}] [{written}/{len(indices)}] {row['id']}")
            time.sleep(0.4)  # be polite to the datasets-server, avoid 429s
    print(f"wrote {written} samples to {out_path}")


def main() -> None:
    # One seeded draw of distinct indices, then partition -> train and test are
    # disjoint by construction (no item can leak across the split).
    drawn = random.Random(SEED).sample(range(SPLIT_SIZE), N_TRAIN + N_TEST)
    train_idx = sorted(drawn[:N_TRAIN])
    test_idx = sorted(drawn[N_TRAIN:])
    assert not set(train_idx) & set(test_idx), "train/test overlap"
    print(f"seed={SEED}")
    print(f"  train indices {train_idx}")
    print(f"  test  indices {test_idx}")

    _write_split("train", train_idx, TRAIN_PATH)
    _write_split("test", test_idx, TEST_PATH)


if __name__ == "__main__":
    main()
