#!/usr/bin/env python3
"""Runtime smoke for tm_mcp.py HTTP transport.

On Windows, probes the deployed WSL-backed HTTP service (default
http://127.0.0.1:9766/healthz) because <repo> is the human editing
worktree and Windows Python does not own the MCP service runtime.

On POSIX/WSL, spins up tm_mcp.py on a free localhost port with a temporary API
key, probes /healthz, then shuts down.  Exit 0 on success, 1 on failure.

Requires: pip install -r deploy/mcp/requirements.txt
Usage:    python tools/tm_mcp_smoke.py [--target-url URL] [--spawn]
Inputs: MCP stdio/HTTP requests, REST JSON payloads, or local facade smoke-test arguments.
Outputs: MCP tool responses, HTTP JSON responses, health checks, or smoke-test diagnostics.
Depends-on (must-have): tm_core shared APIs, FastAPI/uvicorn or MCP runtime libraries, and local tigermemory services.
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
DEFAULT_DEPLOYED_URL = "http://127.0.0.1:9766/healthz"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _short(text: str, limit: int = 800) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def _probe_url(url: str, timeout: float) -> int:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            text = data.decode("utf-8", errors="replace")
            status = getattr(resp, "status", None) or resp.getcode()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"FAIL: target={url} phase=http status={exc.code} body={_short(body)}")
        return 1
    except (socket.timeout, TimeoutError) as exc:
        print(f"FAIL: target={url} phase=http_read timeout={timeout}s error={exc}")
        return 1
    except urllib.error.URLError as exc:
        print(f"FAIL: target={url} phase=connect timeout={timeout}s error={exc.reason}")
        return 1

    if '"ok": true' in text or '"ok":true' in text:
        print(f"PASS: target={url} status={status} body={text}")
        return 0
    print(f"FAIL: target={url} phase=response status={status} body={_short(text)}")
    return 1


def _spawn_and_probe(timeout: float) -> int:
    port = _free_port()
    api_key = "smoke-test-key-12345"
    env = os.environ.copy()
    env["TM_MCP_API_KEY"] = api_key
    # Restrict to localhost so DNS-rebind protection does not block the probe.
    env["TM_MCP_ALLOWED_HOSTS"] = "127.0.0.1,127.0.0.1:*,localhost,localhost:*"

    cmd = [
        sys.executable,
        os.path.join(TOOLS_DIR, "tm_mcp.py"),
        "--http",
        "--host", "127.0.0.1",
        "--port", str(port),
    ]

    proc = subprocess.Popen(
        cmd,
        env=env,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    url = f"http://127.0.0.1:{port}/healthz"
    start = time.monotonic()
    startup_timeout = max(timeout, 15.0)
    last_err = None

    try:
        while time.monotonic() - start < startup_timeout:
            if proc.poll() is not None:
                stdout, stderr = proc.communicate(timeout=1.0)
                print(
                    f"FAIL: spawned tm_mcp.py exited code={proc.returncode} "
                    f"target={url}\nstdout_tail={_short(stdout.decode('utf-8', errors='replace'))}"
                    f"\nstderr_tail={_short(stderr.decode('utf-8', errors='replace'))}"
                )
                return 1
            try:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=min(timeout, 2.0)) as resp:
                    data = resp.read()
                    text = data.decode("utf-8", errors="replace")
                    if '"ok": true' in text or '"ok":true' in text:
                        print(f"PASS: target={url} status={resp.getcode()} body={text}")
                        return 0
                    print(f"FAIL: target={url} phase=response status={resp.getcode()} body={_short(text)}")
                    return 1
            except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
                last_err = e
                time.sleep(0.5)
                continue
        print(f"FAIL: healthz probe timed out after {startup_timeout}s target={url} last_error={last_err}")
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-url", default=None, help="probe an existing tm-mcp health URL")
    parser.add_argument("--spawn", action="store_true", help="spawn tm_mcp.py locally instead of probing deployed service")
    parser.add_argument("--timeout", type=float, default=5.0, help="per-request timeout in seconds")
    args = parser.parse_args(argv)

    if args.spawn:
        return _spawn_and_probe(args.timeout)
    if args.target_url:
        return _probe_url(args.target_url, args.timeout)
    if os.name == "nt":
        return _probe_url(DEFAULT_DEPLOYED_URL, args.timeout)
    return _spawn_and_probe(args.timeout)


if __name__ == "__main__":
    sys.exit(main())
