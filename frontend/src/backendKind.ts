/** True when the desk backend is a local Ollama instance. */
export function isOllamaBackend(baseUrl: string): boolean {
  const u = baseUrl.trim().toLowerCase();
  if (!u) return false;
  return u.includes(":11434") || u.includes("ollama");
}

/** vLLM and other OpenAI-compat servers — reasoning effort is not wired through Hermes yet. */
export function isVllmBackend(baseUrl: string): boolean {
  const u = baseUrl.trim().toLowerCase();
  if (!u || isOllamaBackend(u)) return false;
  return u.includes("vllm") || /:\d{4,5}\/v1/.test(u);
}
