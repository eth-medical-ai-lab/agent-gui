import { describe, expect, it } from "vitest";
import { isOllamaBackend, isVllmBackend } from "./backendKind";

describe("backendKind", () => {
  it("detects Ollama URLs", () => {
    expect(isOllamaBackend("http://127.0.0.1:11434/v1")).toBe(true);
    expect(isOllamaBackend("http://ollama.local:11434/v1")).toBe(true);
  });

  it("detects vLLM URLs", () => {
    expect(isVllmBackend("http://127.0.0.1:8010/v1")).toBe(true);
    expect(isVllmBackend("http://gpu/vllm/v1")).toBe(true);
    expect(isVllmBackend("http://127.0.0.1:11434/v1")).toBe(false);
  });
});
