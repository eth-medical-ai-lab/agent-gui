"""LLM model listing for desk/profile pickers (Ollama, OpenAI-compat, Gemini)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml

from agent_gui.llm_backend import is_ollama_backend

_GEMINI_HOST = "generativelanguage.googleapis.com"
_ENV_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def is_gemini_backend(base_url: str, provider: str = "") -> bool:
    if (provider or "").strip().lower() == "gemini":
        return True
    return _GEMINI_HOST in (base_url or "")


def read_profile_provider(profile_dir: Path) -> str:
    cfg_path = profile_dir / "config.yaml"
    if not cfg_path.is_file():
        return ""
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        m = cfg.get("model") or {}
        return str(m.get("provider") or "")
    except Exception:
        return ""


def read_profile_providers(profile_dir: Path) -> list[dict]:
    """Backends from a profile's ``providers:`` block, active provider first.

    Each entry: ``{id, name, base_url, default_model, models}`` where ``base_url``
    is the provider's ``api`` endpoint. Providers without an ``api`` are skipped.
    """
    cfg_path = profile_dir / "config.yaml"
    if not cfg_path.is_file():
        return []
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    if not isinstance(cfg, dict):
        return []

    block = cfg.get("providers")
    if not isinstance(block, dict):
        return []
    active = str((cfg.get("model") or {}).get("provider") or "")

    out: list[dict] = []
    for pid, raw in block.items():
        if not isinstance(raw, dict):
            continue
        api = str(raw.get("api") or "").strip()
        if not api:
            continue
        models = [str(m) for m in (raw.get("models") or []) if m]
        out.append({
            "id": str(pid),
            "name": str(raw.get("name") or pid),
            "base_url": api,
            "default_model": str(raw.get("default_model") or ""),
            "models": models,
        })
    out.sort(key=lambda p: p["id"] != active)
    return out


def read_profile_env(profile_dir: Path) -> dict[str, str]:
    path = profile_dir / ".env"
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = _ENV_KEY_RE.match(line)
            if not m:
                continue
            key, val = m.group(1), m.group(2).strip()
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            out[key] = val
    except OSError:
        pass
    return out


def _ollama_root_from_base(base_url: str) -> str:
    parsed = urlparse(base_url.strip())
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return "http://127.0.0.1:11434"


def _openai_models_url(base_url: str) -> str:
    v1 = (base_url or "").strip().rstrip("/")
    if not v1.endswith("/v1"):
        v1 = f"{v1}/v1"
    return f"{v1}/models"


def _gemini_cache_provider_keys(provider: str) -> list[str]:
    prov = (provider or "").strip().lower()
    if prov == "gemini":
        return ["google"]
    if prov:
        return [prov, prov.replace("_", "-")]
    return ["google"]


def models_from_hermes_cache(profile_dir: Path, provider: str = "") -> list[str]:
    """Model ids from Hermes ``models_dev_cache.json`` (populated at profile setup)."""
    cache_path = profile_dir / "models_dev_cache.json"
    if not cache_path.is_file():
        return []
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []

    found: list[str] = []
    for key in _gemini_cache_provider_keys(provider):
        block = data.get(key)
        if not isinstance(block, dict):
            continue
        models = block.get("models")
        if not isinstance(models, dict):
            continue
        for mid in models:
            if mid:
                found.append(str(mid))
    return sorted(set(found))


def _gemini_api_key(profile_dir: Path | None) -> str:
    if not profile_dir:
        return ""
    env = read_profile_env(profile_dir)
    for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY"):
        val = (env.get(name) or "").strip()
        if val:
            return val
    return ""


async def fetch_gemini_models_live(base_url: str, api_key: str) -> list[str]:
    if not api_key:
        return []
    root = (base_url or "").strip().rstrip("/") or f"https://{_GEMINI_HOST}/v1beta"
    url = f"{root}/models"
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get(url, params={"key": api_key, "pageSize": 200})
            if r.status_code != 200:
                return []
            payload = r.json()
    except Exception:
        return []

    out: list[str] = []
    for item in payload.get("models") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if name.startswith("models/"):
            name = name.split("/", 1)[1]
        methods = item.get("supportedGenerationMethods") or []
        if methods and "generateContent" not in methods:
            continue
        if name:
            out.append(name)
    return sorted(set(out))


async def fetch_llm_models(
    base_url: str,
    *,
    profile_dir: Path | None = None,
    provider: str = "",
) -> list[str]:
    """List models for Ollama, OpenAI-compat, or Gemini/Google API profiles."""
    base = (base_url or "").strip()
    prov = provider or (read_profile_provider(profile_dir) if profile_dir else "")

    if is_gemini_backend(base, prov):
        if profile_dir:
            cached = models_from_hermes_cache(profile_dir, prov)
            if cached:
                return cached
        live = await fetch_gemini_models_live(base, _gemini_api_key(profile_dir))
        if live:
            return live
        return []

    models: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            if is_ollama_backend(base, prov):
                root = _ollama_root_from_base(base)
                r = await client.get(f"{root}/api/tags")
                if r.status_code == 200:
                    models = [m["name"] for m in r.json().get("models", []) if m.get("name")]
            if not models and base:
                r = await client.get(_openai_models_url(base))
                if r.status_code == 200:
                    payload = r.json()
                    for item in payload.get("data") or []:
                        if isinstance(item, dict) and item.get("id"):
                            models.append(str(item["id"]))
    except Exception:
        pass
    return models
