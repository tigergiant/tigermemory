from __future__ import annotations

import io
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_lessons  # type: ignore[import-not-found]


def test_configure_stdio_backslashreplaces_unencodable_stdout(monkeypatch):
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp936", errors="strict")
    monkeypatch.setattr(tm_lessons.sys, "stdout", stream)

    tm_lessons._configure_stdio()
    print("git \u2194 WSL", file=tm_lessons.sys.stdout)
    tm_lessons.sys.stdout.flush()

    assert raw.getvalue().decode("cp936") == r"git \u2194 WSL" + "\n"
