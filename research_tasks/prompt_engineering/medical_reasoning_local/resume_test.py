"""Resume building data_test.jsonl: fetch only the still-missing test indices,
with throttling + backoff to respect the HF datasets-server rate limit."""
from __future__ import annotations
import json, random, time, urllib.request
from pathlib import Path

DATASET="TsinghuaC3I/MedXpertQA"; CONFIG="Text"; SPLIT="test"; SPLIT_SIZE=2450
DEV_SEED=42; DEV_N=20; TEST_SEED=2024; TEST_N=100
HERE=Path(__file__).resolve().parent; OUT=HERE/"data_test.jsonl"
ROWS_URL="https://datasets-server.huggingface.co/rows"

def fetch(index:int)->dict:
    url=f"{ROWS_URL}?dataset={DATASET}&config={CONFIG}&split={SPLIT}&offset={index}&length=1"
    last=None
    for a in range(8):
        try:
            with urllib.request.urlopen(url,timeout=30) as r:
                return json.load(r)["rows"][0]["row"]
        except urllib.error.HTTPError as e:
            last=e; time.sleep(12 if e.code==429 else 2*(a+1))
        except Exception as e:
            last=e; time.sleep(2*(a+1))
    raise RuntimeError(f"fail {index}: {last}")

def main():
    dev=set(random.Random(DEV_SEED).sample(range(SPLIT_SIZE),DEV_N))
    pool=[i for i in range(SPLIT_SIZE) if i not in dev]
    test_idx=sorted(random.Random(TEST_SEED).sample(pool,TEST_N))
    have_ids=set()
    if OUT.is_file():
        for ln in OUT.read_text().splitlines():
            ln=ln.strip()
            if ln: have_ids.add(json.loads(ln)["id"])
    have=len(have_ids)
    remaining=test_idx[have:]  # rows were written in sorted-index order -> positional resume
    print(f"already have {have} rows; fetching {len(remaining)} more; target {TEST_N}")
    written=0
    with OUT.open("a",encoding="utf-8") as out:
        for i in remaining:
            row=fetch(i)
            if row["id"] in have_ids:
                continue
            opts={k:v for k,v in (row.get("options") or {}).items() if v}
            assert row["label"] in opts, f"bad label {row['id']}"
            out.write(json.dumps({"id":row["id"],"question":row["question"],"options":opts,
                "label":row["label"],"medical_task":row.get("medical_task"),
                "body_system":row.get("body_system"),"question_type":row.get("question_type")},
                ensure_ascii=False)+"\n")
            out.flush(); have_ids.add(row["id"]); written+=1
            print(f"  +{row['id']} (total {len(have_ids)})")
            time.sleep(0.5)
    print(f"done. wrote {written} new; file now has {len(have_ids)} rows")

if __name__=="__main__":
    main()
