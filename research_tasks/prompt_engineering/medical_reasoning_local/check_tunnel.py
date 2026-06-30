"""Find the local Qwen tunnel and print a working QWEN_BASE_URL.

The harness expects the SSH tunnel on local port 8010 (reached from the Docker
sandbox at host.docker.internal:8010). But the tunnel's port sometimes changes
between sessions, so if 8010 is down this pokes around the other likely host:port
combinations and reports whichever one answers the OpenAI-style GET /v1/models.

    python check_tunnel.py

Then export what it prints, e.g.:
    export QWEN_BASE_URL=http://host.docker.internal:8111/v1
"""
import json
import os
import urllib.request

# From inside the Docker sandbox the host's tunnel is reached via the host
# gateway; on the host itself it's loopback. Try both.
HOSTS = ["host.docker.internal", "127.0.0.1", "localhost"]
# 8010 is the documented default; the rest are fallbacks the tunnel has used.
PORTS = [8010, 8111, 8000, 8001, 8080, 5000]
PROBE_TIMEOUT = 3  # seconds; a refused connection returns immediately anyway


def candidates() -> list[str]:
    """QWEN_BASE_URL first (if set), then every host:port combo, de-duplicated."""
    urls: list[str] = []
    env = os.environ.get("QWEN_BASE_URL")
    if env:
        urls.append(env.rstrip("/"))
    for host in HOSTS:
        for port in PORTS:
            url = f"http://{host}:{port}/v1"
            if url not in urls:
                urls.append(url)
    return urls


def probe(base: str) -> list[str]:
    req = urllib.request.Request(base + "/models")
    req.add_header("Authorization", "Bearer EMPTY")
    data = json.loads(urllib.request.urlopen(req, timeout=PROBE_TIMEOUT).read())
    return [m["id"] for m in data.get("data", [])]


def main() -> None:
    found = []
    for base in candidates():
        try:
            models = probe(base)
            print(f"TUNNEL OK  {base}   models={models}")
            found.append(base)
        except Exception as e:
            print(f"  no answer at {base}  ({type(e).__name__})")
    print()
    if found:
        print(f"Use it:  export QWEN_BASE_URL={found[0]}")
    else:
        print(
            "TUNNEL NOT FOUND. Open the SSH tunnel on the host (default local port "
            "8010 -> remote vLLM), then re-run. If it's on a port not listed above, "
            "widen PORTS in this file."
        )


if __name__ == "__main__":
    main()
