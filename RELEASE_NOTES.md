# Release Notes

## 1.1.0 (2026-06-30): Research features and support for Claude models & agents.

### Hermes agent integration
- **Claude API integration** — run Hermes desks on Claude models via an `ANTHROPIC_API_KEY` (Docker-sandboxed, full Hermes toolset).
- **Subagent desks** — subagents spanwed by Hermes main agent gets their own mini desks, and are visualized.

### Claude agent integration
- **Claude Agent SDK** — drive a desk with your Claude code subscription (login token, no API key needed). Experimental (`--experimental`); runs on the host using Claude Agent harness, no Hermes docker sandbox.

### General
- **Research task templates** — self-contained tasks with frozen scoring. Two examples: prompt-tuning (medical reasoning) and iteratively improving models (model training and evaluation).
- **GPU server support** — `start.sh` detects a GPU host and lets you pick which GPU(s) to use at startup. Useful for hosting GPU-accelerated models or tool execution.
- **UI improvements** - smoother experience for streaming, scrolling, and agent desk inspection.

## 1.0.0 (2026-06-11): Initial release.