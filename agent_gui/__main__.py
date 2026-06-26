import argparse
import socket
import subprocess
import threading
import time
import webbrowser
from pathlib import Path


def _listening(port: int, host: str = "127.0.0.1") -> bool:
    """True if something is already accepting connections on host:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def _ensure_frontend() -> None:
    """Build the frontend bundle on first run if it isn't built yet."""
    dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if dist.exists():
        return
    fe = dist.parent
    if not (fe / "package.json").exists():
        print("⚠  frontend/ not found — serving the API only.")
        return
    print("Building frontend (first run, this takes ~30s)…")
    try:
        subprocess.run(["npm", "install"], cwd=fe, check=True)
        subprocess.run(["npm", "run", "build"], cwd=fe, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"⚠  frontend build failed ({exc}); serving the API only.")


def _print_stack_status(port: int) -> None:
    """Report which supporting services are up so the user can start what's missing."""
    ollama = _listening(11434)
    try:
        docker_ok = subprocess.run(["docker", "info"], capture_output=True, timeout=4).returncode == 0
    except Exception:
        docker_ok = False
    print("Stack:")
    print(f"  • Backend : starting on :{port}")
    print(f"  • Ollama  : {'up (:11434)' if ollama else 'NOT detected (:11434) — start your LLM backend'}")
    print(f"  • Docker  : {'up' if docker_ok else 'NOT running — terminal tool calls will fail'}")


def _serve(args) -> None:
    import uvicorn
    from agent_gui.gui_config import load_gui_config
    from agent_gui.server import create_app

    gui_config = load_gui_config(args.hermes_home, agent_profiles_dir=args.profiles_dir)
    allowed_origins = [
        f"http://{h}:{p}"
        for h in ("localhost", "127.0.0.1")
        for p in (args.port, 5173)  # backend port + Vite dev server
    ]
    app = create_app(
        hermes_home=str(gui_config.hermes_home),
        hermes_api_url=args.hermes_api,
        workspace_root=args.workspace_root,
        allowed_origins=allowed_origins,
        gui_config=gui_config,
        experimental=args.experimental,
    )
    if not args.no_open:
        def _open() -> None:
            time.sleep(1.2)
            webbrowser.open(f"http://localhost:{args.port}")
        threading.Thread(target=_open, daemon=True).start()
    print(f"Agent GUI running at http://localhost:{args.port}  (Ctrl+C to stop)")
    if args.experimental:
        print("🧪 Experimental features ON — the Claude Code SDK agent is available in the roster.")
    if args.host not in ("127.0.0.1", "localhost"):
        print(f"⚠  Binding to {args.host}: the GUI is reachable from other machines "
              f"and has no authentication. Only do this on a trusted network.")
    # timeout_graceful_shutdown: the browser keeps activity WebSockets open, and
    # uvicorn's default is to wait for them FOREVER on Ctrl-C/SIGTERM — so the
    # lifespan shutdown (worker terminate + hermes-* container reaping) never ran
    # and a second Ctrl-C / stop.sh's SIGKILL leaked the containers. Cap the wait
    # so open connections are cancelled and cleanup always runs.
    #
    # Hitting that cap is the NORMAL Ctrl-C path here, but uvicorn announces it
    # with "ERROR: Cancel N running task(s)" where N is the count of open
    # WebSockets (≈3 per desk panel per tab) — alarming and uninformative. Drop
    # it; _shutdown_cleanup prints a per-stream breakdown instead.
    import logging

    class _QuietWsCancelFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return "timeout graceful shutdown exceeded" not in record.getMessage()

    logging.getLogger("uvicorn.error").addFilter(_QuietWsCancelFilter())
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning",
                timeout_graceful_shutdown=5,
                h11_max_incomplete_event_size=50 * 1024 * 1024)  # 50 MB — allows large image uploads


def main():
    parser = argparse.ArgumentParser(description="Agent GUI — agent workbench visualizer")
    parser.add_argument("--port", type=int, default=8765, help="Port to run on (default: 8765)")
    parser.add_argument("--host", type=str, default="127.0.0.1",
                        help="Interface to bind (default: 127.0.0.1, localhost-only). "
                             "Use 0.0.0.0 to expose on the network — no auth, trusted networks only.")
    parser.add_argument("--hermes-home", type=str, default=None, help="Path to Hermes home dir (default: ~/.hermes)")
    parser.add_argument("--profiles-dir", type=str, default=None,
                        help="Agent profiles directory (default: <hermes-home>/profiles)")
    parser.add_argument("--hermes-api", type=str, default="http://localhost:9119", help="Hermes API base URL")
    parser.add_argument("--workspace-root", type=str, default=None, help="Root dir for task workspaces (default: ~/workspace)")
    parser.add_argument("--no-open", action="store_true", help="Don't open browser automatically")
    parser.add_argument("--experimental", action="store_true",
                        help="Enable experimental/developmental features (currently the Claude "
                             "Code SDK agent). Off by default; the released app exposes only the "
                             "stable Hermes agents.")
    args = parser.parse_args()

    # If a server is already listening on the port, don't start a second one —
    # just surface it (and open the browser) so the command is idempotent.
    if _listening(args.port):
        print(f"Agent GUI is already running at http://localhost:{args.port} — opening browser.")
        if not args.no_open:
            webbrowser.open(f"http://localhost:{args.port}")
        return

    _ensure_frontend()
    _print_stack_status(args.port)
    _serve(args)


if __name__ == "__main__":
    main()
