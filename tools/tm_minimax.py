#!/usr/bin/env python3
"""
tools/tm_minimax.py — MiniMax CLI (mmx-cli) wrapper for tigermemory MCP.

Wraps `mmx` CLI commands as Python functions callable from tm_mcp.py.
Each function shells out to `mmx` with --output json --non-interactive
and parses the result.

Requires: npm install -g mmx-cli && mmx auth login --api-key <key>

Supported capabilities (Token Plan):
  - vision describe: image understanding (coding-plan-vlm, 150/day)
  - video generate:  Hailuo 2.3 video gen
  - speech synthesize: Speech 2.8 TTS
  - music generate:  Music 2.6
  - image generate:  image-01
  - search query:    web search (coding-plan-search, 150/day)
  - quota show:      usage dashboard
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


# ---------- Helpers ----------

def _find_mmx() -> str:
    """Locate the mmx executable. Raises RuntimeError if not found."""
    mmx = shutil.which("mmx")
    if mmx:
        return mmx
    # Windows npm global fallback
    npm_global = Path(os.environ.get("APPDATA", "")) / "npm" / "mmx.cmd"
    if npm_global.exists():
        return str(npm_global)
    raise RuntimeError(
        "mmx-cli not found. Install with: npm install -g mmx-cli && mmx auth login --api-key <key>"
    )


def _run_mmx(args: list[str], timeout: int = 300) -> dict[str, Any]:
    """Run mmx with --output json --non-interactive and return parsed JSON.

    Falls back to raw text output if JSON parsing fails (some mmx commands
    don't support --output json).
    """
    mmx = _find_mmx()
    cmd = [mmx] + args + ["--output", "json", "--non-interactive"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"mmx command timed out after {timeout}s: {' '.join(args)}")
    except FileNotFoundError:
        raise RuntimeError(f"mmx executable not found at: {mmx}")

    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"mmx exited {result.returncode}: {stderr}")

    stdout = result.stdout.strip()
    if not stdout:
        return {"ok": True, "raw": ""}

    # Try JSON first
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        # Some commands output plain text even with --output json
        return {"ok": True, "raw": stdout}


def _run_mmx_text(args: list[str], timeout: int = 300) -> str:
    """Run mmx and return raw stdout text (no --output json)."""
    mmx = _find_mmx()
    cmd = [mmx] + args + ["--non-interactive", "--no-color"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"mmx command timed out after {timeout}s: {' '.join(args)}")

    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"mmx exited {result.returncode}: {stderr}")

    return result.stdout.strip()


# ---------- Vision ----------

def vision_describe(
    image: str,
    prompt: str = "Describe the image in detail.",
    timeout: int = 120,
) -> dict[str, Any]:
    """Describe an image using MiniMax VLM.

    Args:
        image: Local file path or URL to the image.
        prompt: Question or instruction about the image.
        timeout: Request timeout in seconds.

    Returns:
        {"ok": True, "description": "...", "image": "...", "prompt": "..."}
    """
    args = ["vision", "describe", "--image", image, "--prompt", prompt,
            "--timeout", str(timeout)]
    result = _run_mmx(args, timeout=timeout + 10)
    # mmx vision JSON has {"content": "...", "base_resp": {...}}
    description = result.get("content") or result.get("raw", "")
    return {
        "ok": True,
        "description": description,
        "image": image,
        "prompt": prompt,
    }


# ---------- Video ----------

def video_generate(
    prompt: str,
    image: str | None = None,
    timeout: int = 600,
) -> dict[str, Any]:
    """Generate a video with Hailuo 2.3.

    Args:
        prompt: Text description for the video.
        image: Optional reference image (path or URL).
        timeout: Request timeout in seconds (video gen can be slow).

    Returns:
        mmx JSON response with task_id or download info.
    """
    args = ["video", "generate", "--prompt", prompt, "--timeout", str(timeout)]
    if image:
        args.extend(["--image", image])
    return _run_mmx(args, timeout=timeout + 30)


# ---------- Speech ----------

def speech_synthesize(
    text: str,
    voice: str | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    """Synthesize speech from text using Speech 2.8.

    Args:
        text: Text to convert to speech.
        voice: Optional voice ID.
        timeout: Request timeout in seconds.

    Returns:
        mmx JSON response with output file path or audio data.
    """
    args = ["speech", "synthesize", "--text", text, "--timeout", str(timeout)]
    if voice:
        args.extend(["--voice", voice])
    return _run_mmx(args, timeout=timeout + 10)


# ---------- Music ----------

def music_generate(
    prompt: str,
    timeout: int = 300,
) -> dict[str, Any]:
    """Generate music using Music 2.6.

    Args:
        prompt: Description of the music to generate.
        timeout: Request timeout in seconds.

    Returns:
        mmx JSON response with output info.
    """
    args = ["music", "generate", "--prompt", prompt, "--timeout", str(timeout)]
    return _run_mmx(args, timeout=timeout + 30)


# ---------- Image Generation ----------

def image_generate(
    prompt: str,
    timeout: int = 120,
) -> dict[str, Any]:
    """Generate an image using image-01.

    Args:
        prompt: Description of the image to generate.
        timeout: Request timeout in seconds.

    Returns:
        mmx JSON response with output info.
    """
    args = ["image", "generate", "--prompt", prompt, "--timeout", str(timeout)]
    return _run_mmx(args, timeout=timeout + 10)


# ---------- Search ----------

def search_query(
    query: str,
    timeout: int = 30,
) -> dict[str, Any]:
    """Web search via MiniMax search API.

    Args:
        query: Search query text.
        timeout: Request timeout in seconds.

    Returns:
        mmx JSON response with search results.
    """
    args = ["search", "query", "--query", query, "--timeout", str(timeout)]
    return _run_mmx(args, timeout=timeout + 10)


# ---------- Quota ----------

def quota_show(timeout: int = 15) -> dict[str, Any]:
    """Show current MiniMax Token Plan quota usage.

    Returns:
        {"ok": True, "raw": "<quota table text>"}
    """
    raw = _run_mmx_text(["quota", "show"], timeout=timeout)
    return {"ok": True, "raw": raw}


# ---------- CLI self-test ----------

if __name__ == "__main__":
    print(f"mmx path: {_find_mmx()}")
    print("--- quota ---")
    q = quota_show()
    print(q.get("raw", q))
