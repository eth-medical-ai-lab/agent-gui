"""Local Qwen-solver evaluation harness for the `medical_reasoning_local` task.

Same contract as the API sibling task (same fixed samples, same answer
extraction, same scoring, same result schema) — but the *solver model* is
Qwen3.5-27B served on a remote GPU server (OpenAI-compatible vLLM), reached over
an SSH tunnel opened **on the host** (by default on local port 8111):

    ssh -L 8111:127.0.0.1:8111 <user>@<gpu-host>   # host :8111 -> remote vLLM

This harness runs inside the per-desk Docker sandbox, which cannot see the host's
loopback. Reach the host's tunnel through Docker's host gateway instead — point
QWEN_BASE_URL at ``host.docker.internal`` (the default below). If you run the
harness directly on the host (not in a container), override it with the loopback:

    QWEN_BASE_URL=http://127.0.0.1:8111/v1

The tunnel's port sometimes changes between sessions. If the default 8111 is down,
run ``python check_tunnel.py`` — it pokes around the likely host:port combos
(8010, …), prints a live base URL, and you ``export QWEN_BASE_URL`` to that.

The server is assumed to expose an OpenAI-compatible REST API (vLLM). We talk to
it with the standard library only (urllib) so nothing has to be pip-installed.

Usage
-----
    python eval_qwen.py --prompt-file baseline_prompt.txt
    python eval_qwen.py --prompt "You are a careful diagnostician..."

Config via environment (all optional):
    QWEN_BASE_URL   default http://host.docker.internal:8111/v1 — if that's not
                    live, run check_tunnel.py to discover the right port.
    QWEN_MODEL      default: auto-detected from GET /v1/models
    QWEN_THINK      "1" to enable Qwen native <think> reasoning, "0" (default) to
                    rely on the JSON "reasoning" field (more robust w/ schema)
    QWEN_API_KEY    default "EMPTY" (vLLM ignores it)

Only the *system prompt* is meant to change between runs — everything else here
is held fixed so two prompt revisions stay comparable.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --- Frozen-ish evaluation settings -------------------------------------------
BASE_URL = os.environ.get("QWEN_BASE_URL", "http://host.docker.internal:8111/v1").rstrip("/")
API_KEY = os.environ.get("QWEN_API_KEY", "EMPTY")
ENV_MODEL = os.environ.get("QWEN_MODEL")  # if unset, auto-detect from /v1/models
ENABLE_THINKING = os.environ.get("QWEN_THINK", "0") == "1"
MAX_TOKENS = 4000   # concise medical reasoning fits easily; caps runaway verbose prompts
TEMPERATURE = 0.0          # greedy -> reproducible scoring
DEFAULT_WORKERS = 4
HTTP_TIMEOUT = 600

TASK_DIR = Path(__file__).resolve().parent
DATA_PATH = TASK_DIR / "data.jsonl"

# Output-format strategy, adapted lazily on the first request if the server
# rejects the preferred one. One of: "json_schema", "guided_json", "plain".
_OUTPUT_MODE = "json_schema"


# --- Data ---------------------------------------------------------------------
def load_samples(data_path: Path = DATA_PATH) -> list[dict[str, Any]]:
    if not data_path.is_file():
        raise FileNotFoundError(
            f"{data_path} not found. Run `python prepare_data.py` / `prepare_test.py`."
        )
    samples = []
    with data_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    return samples


def _format_question(sample: dict[str, Any]) -> str:
    question = sample["question"].strip()
    if "Answer Choices" in question:
        return question
    lines = [question, "", "Answer Choices:"]
    for letter, text in sorted(sample["options"].items()):
        lines.append(f"({letter}) {text}")
    return "\n".join(lines)


# --- Minimal OpenAI-compatible HTTP client ------------------------------------
def _http_json(method: str, path: str, payload: dict | None = None) -> dict:
    url = f"{BASE_URL}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {API_KEY}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def detect_model() -> str:
    if ENV_MODEL:
        return ENV_MODEL
    info = _http_json("GET", "/models")
    ids = [m["id"] for m in info.get("data", [])]
    if not ids:
        raise RuntimeError(f"No models reported by {BASE_URL}/models")
    return ids[0]


def _build_payload(model: str, system_prompt: str, user_msg: str,
                   letters: list[str], mode: str) -> dict:
    schema = {
        "type": "object",
        "properties": {
            "reasoning": {"type": "string",
                          "description": "Concise clinical reasoning toward the answer."},
            "answer": {"type": "string", "enum": letters,
                       "description": "The single best option letter."},
        },
        "required": ["reasoning", "answer"],
        "additionalProperties": False,
    }
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
    }
    # Qwen native thinking toggle (vLLM chat-template kwarg).
    payload["chat_template_kwargs"] = {"enable_thinking": ENABLE_THINKING}

    if mode == "json_schema":
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "medical_answer", "strict": True, "schema": schema},
        }
    elif mode == "guided_json":
        payload["guided_json"] = schema
    elif mode == "plain":
        payload["response_format"] = {"type": "json_object"}
    return payload


_ANSWER_RE = re.compile(r'"answer"\s*:\s*"([A-Z])"')


def _parse_output(content: str, reasoning_field: str | None,
                  letters: list[str]) -> tuple[str | None, str | None]:
    """Return (predicted_letter, reasoning)."""
    # 1) clean JSON
    try:
        obj = json.loads(content)
        ans = str(obj.get("answer", "")).strip().upper()
        if ans in letters:
            return ans, obj.get("reasoning") or reasoning_field
    except Exception:
        pass
    # 2) embedded JSON answer field
    m = _ANSWER_RE.search(content or "")
    if m and m.group(1) in letters:
        return m.group(1), reasoning_field or content
    # 3) last-ditch: a lone option letter near the end
    tail = (content or "")[-200:]
    for tok in re.findall(r"\b([A-Z])\b", tail):
        if tok in letters:
            return tok, reasoning_field or content
    return None, reasoning_field or content


def _chat_once(model: str, system_prompt: str, user_msg: str,
               letters: list[str]) -> dict[str, Any]:
    global _OUTPUT_MODE
    modes_to_try = [_OUTPUT_MODE] + [m for m in ("json_schema", "guided_json", "plain")
                                     if m != _OUTPUT_MODE]
    last_err: Exception | None = None
    for mode in modes_to_try:
        payload = _build_payload(model, system_prompt, user_msg, letters, mode)
        try:
            resp = _http_json("POST", "/chat/completions", payload)
            if mode != _OUTPUT_MODE:
                _OUTPUT_MODE = mode  # remember what worked
            choice = resp["choices"][0]
            msg = choice.get("message", {})
            return {
                "content": msg.get("content") or "",
                "reasoning_field": msg.get("reasoning_content"),
                "usage": resp.get("usage", {}),
                "finish_reason": choice.get("finish_reason"),
            }
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8")[:300]
            except Exception:
                pass
            last_err = RuntimeError(f"HTTP {exc.code} ({mode}): {body}")
            # 400 likely = unsupported param -> try next mode; else abort
            if exc.code not in (400, 422, 404, 501):
                raise last_err
        except Exception as exc:  # network etc.
            raise
    raise last_err or RuntimeError("all output modes failed")


# --- Single-sample scoring ----------------------------------------------------
def _grade_one(model: str, system_prompt: str, sample: dict[str, Any]) -> dict[str, Any]:
    letters = sorted(sample["options"].keys())
    record: dict[str, Any] = {
        "id": sample["id"],
        "medical_task": sample.get("medical_task"),
        "body_system": sample.get("body_system"),
        "question_type": sample.get("question_type"),
        "correct": sample["label"],
        "predicted": None,
        "is_correct": False,
        "reasoning": None,
        "latency_s": None,
        "usage": None,
        "error": None,
    }
    start = time.monotonic()
    try:
        out = _chat_once(model, system_prompt, _format_question(sample), letters)
        record["latency_s"] = round(time.monotonic() - start, 2)
        predicted, reasoning = _parse_output(
            out["content"], out["reasoning_field"], letters)
        record["predicted"] = predicted
        record["reasoning"] = reasoning
        record["is_correct"] = predicted == sample["label"]
        u = out.get("usage") or {}
        record["usage"] = {
            "input_tokens": u.get("prompt_tokens"),
            "output_tokens": u.get("completion_tokens"),
        }
        if predicted is None:
            record["error"] = "unparseable_answer"
    except Exception as exc:  # noqa: BLE001
        record["latency_s"] = round(time.monotonic() - start, 2)
        record["error"] = f"{type(exc).__name__}: {exc}"
    return record


# --- Public entry point -------------------------------------------------------
def evaluate(prompt: str, *, workers: int = DEFAULT_WORKERS,
             model: str | None = None, data_path: Path = DATA_PATH,
             split: str | None = None) -> dict[str, Any]:
    if not prompt or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    model = model or detect_model()
    samples = load_samples(data_path)

    if workers <= 1:
        results = [_grade_one(model, prompt, s) for s in samples]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(lambda s: _grade_one(model, prompt, s), samples))

    n = len(results)
    n_correct = sum(1 for r in results if r["is_correct"])
    n_errors = sum(1 for r in results if r["error"])

    by_task: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = by_task.setdefault(r["medical_task"] or "Unknown", {"correct": 0, "total": 0})
        bucket["total"] += 1
        bucket["correct"] += int(r["is_correct"])

    return {
        "task": "medical_reasoning",
        "dataset": "MedXpertQA (Text)",
        "split": split or data_path.stem,
        "data_file": data_path.name,
        "model": model,
        "solver": "qwen (remote vLLM via host SSH tunnel, reached at host.docker.internal)",
        "base_url": BASE_URL,
        "output_mode": _OUTPUT_MODE,
        "enable_thinking": ENABLE_THINKING,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "n": n,
        "n_correct": n_correct,
        "n_errors": n_errors,
        "accuracy": round(n_correct / n, 4) if n else 0.0,
        "accuracy_by_medical_task": {
            k: round(v["correct"] / v["total"], 4) for k, v in sorted(by_task.items())
        },
        "prompt": prompt,
        "samples": results,
    }


def _read_prompt(args: argparse.Namespace) -> str:
    if args.prompt_file:
        return Path(args.prompt_file).read_text(encoding="utf-8")
    if args.prompt:
        return args.prompt
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("Provide a prompt via --prompt, --prompt-file, or stdin.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", help="System prompt string to evaluate.")
    parser.add_argument("--prompt-file", help="File containing the system prompt.")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--model", help="Override model id (else auto-detect).")
    parser.add_argument("--data", help="Dataset jsonl (default data.jsonl = dev).")
    parser.add_argument("--split", help="Split label for logging (dev/test).")
    parser.add_argument("--out", help="Write the full result JSON to this path.")
    args = parser.parse_args()

    prompt = _read_prompt(args)
    data_path = Path(args.data) if args.data else DATA_PATH
    try:
        model = args.model or detect_model()
    except Exception as exc:
        raise SystemExit(
            f"Could not reach the Qwen server at {BASE_URL}: {exc}\n"
            "Is the SSH tunnel up on the host (default local port 8111)? "
            "ssh -L 8111:127.0.0.1:8111 <user>@<gpu-host>\n"
            "If the port moved, run `python check_tunnel.py` to discover the live "
            "endpoint, then `export QWEN_BASE_URL=<it>`. From inside the Docker "
            "sandbox the host is host.docker.internal, not 127.0.0.1."
        )

    print(f"server:   {BASE_URL}")
    print(f"model:    {model}")
    print(f"data:     {data_path.name}")
    result = evaluate(prompt, workers=args.workers, model=model,
                      data_path=data_path, split=args.split)

    print(f"accuracy: {result['accuracy']:.3f}  ({result['n_correct']}/{result['n']})")
    if result["n_errors"]:
        print(f"errors:   {result['n_errors']}")
    print(f"output_mode: {result['output_mode']}  thinking: {result['enable_thinking']}")
    print("by medical_task:")
    for task, acc in result["accuracy_by_medical_task"].items():
        print(f"  {task:<24} {acc:.3f}")
    print("\nper-sample:")
    for r in result["samples"]:
        mark = "✓" if r["is_correct"] else ("!" if r["error"] else "✗")
        detail = r["error"] or f"pred={r['predicted']} gold={r['correct']}"
        print(f"  {mark} {r['id']:<12} {detail}")

    out_path = Path(args.out) if args.out else (TASK_DIR / "last_result_qwen.json")
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nfull log written to {out_path}")


if __name__ == "__main__":
    main()
