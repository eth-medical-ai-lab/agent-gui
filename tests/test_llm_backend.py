"""Tests for LLM backend detection and api_mode normalization."""
from agent_gui.llm_backend import (
    is_local_openai_compat,
    is_ollama_backend,
    normalize_api_mode,
    should_apply_reasoning_effort,
    supports_ollama_think_param,
    wants_native_ollama_proxy,
)


def test_is_ollama_by_port():
    assert is_ollama_backend("http://127.0.0.1:11434/v1")
    assert not is_ollama_backend("http://localhost:8010/v1")


def test_is_ollama_by_provider():
    assert is_ollama_backend("http://gpu:8080/v1", provider="ollama")
    assert not is_ollama_backend("http://gpu:8080/v1", provider="custom")


def test_normalize_openai_to_chat_completions():
    assert normalize_api_mode("openai", "http://127.0.0.1:11434/v1") == "chat_completions"


def test_normalize_codex_responses_local_vllm():
    assert normalize_api_mode(
        "codex_responses", "http://localhost:8010/v1", provider="custom",
    ) == "chat_completions"


def test_normalize_codex_responses_remote_unchanged():
    assert normalize_api_mode(
        "codex_responses", "https://api.openai.com/v1", provider="openai",
    ) == "codex_responses"


def test_wants_native_only_when_explicit():
    assert wants_native_ollama_proxy("ollama", "")
    assert not wants_native_ollama_proxy("chat_completions", "")
    assert not wants_native_ollama_proxy("", "openai")
    assert not wants_native_ollama_proxy("chat_completions", "ollama")


def test_supports_ollama_think_param():
    assert supports_ollama_think_param("qwen3.5:4b")
    assert not supports_ollama_think_param("llama3.2:3b")


def test_should_apply_reasoning_effort():
    assert should_apply_reasoning_effort("qwen3.5:4b", "http://127.0.0.1:11434/v1")
    assert not should_apply_reasoning_effort("llama3.2:3b", "http://127.0.0.1:11434/v1")
    assert should_apply_reasoning_effort("gpt-4", "https://api.openai.com/v1")


def test_is_local_openai_compat():
    assert is_local_openai_compat("http://127.0.0.1:11434/v1")
    assert is_local_openai_compat("http://localhost:8010/v1", provider="custom")
    assert not is_local_openai_compat("https://api.openai.com/v1")
