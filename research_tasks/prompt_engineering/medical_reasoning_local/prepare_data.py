"""Regenerate `data.jsonl` — the fixed validation subsample for this task.

The committed `data.jsonl` was produced by this script and is checked in, so the
task runs offline. Re-run this only to reproduce or change the subsample.

Provenance
----------
- Dataset : TsinghuaC3I/MedXpertQA, config "Text" (text-only reasoning MCQs).
- Split   : "test" (2450 items). NOTE: MedXpertQA's released "dev" split has only
            5 items (few-shot exemplars), so a 20-item *validation* subsample can't
            come from it. We therefore carve a fixed, seeded held-out subsample
            from the benchmark set and use it strictly for prompt iteration. Keep
            the full "test" set untouched for any final reported number.
- Sample  : 20 items chosen by `random.Random(SEED).sample(range(N), 20)`, sorted.
            With SEED frozen, the subsample is deterministic ("fixed but random").

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
N = 20
SEED = 42

OUT_PATH = Path(__file__).resolve().parent / "data.jsonl"
ROWS_URL = "https://datasets-server.huggingface.co/rows"


def _fetch_row(index: int) -> dict:
    url = (
        f"{ROWS_URL}?dataset={DATASET}&config={CONFIG}&split={SPLIT}"
        f"&offset={index}&length=1"
    )
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                return json.load(resp)["rows"][0]["row"]
        except Exception as exc:  # noqa: BLE001 - retry transient network errors
            last_err = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"failed to fetch row {index}: {last_err}")


def main() -> None:
    indices = sorted(random.Random(SEED).sample(range(SPLIT_SIZE), N))
    print(f"seed={SEED} -> indices {indices}")

    written = 0
    with OUT_PATH.open("w", encoding="utf-8") as out:
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
            print(f"  [{written}/{N}] {row['id']}")

    print(f"wrote {written} samples to {OUT_PATH}")


if __name__ == "__main__":
    main()
