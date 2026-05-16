from __future__ import annotations

import io
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_io  # type: ignore[import-not-found]


def test_cmd_mem0_update_content_reads_stdin(monkeypatch, capsys):
    captured = {}

    class Args:
        id = "fd65b298-05bd-493c-83ce-e37d84447362"

    def fake_update(memory_id, memory_content):
        captured["memory_id"] = memory_id
        captured["memory_content"] = memory_content
        return '{"ok": true}'

    monkeypatch.setattr(tm_io.sys, "stdin", io.StringIO("replacement content"))
    monkeypatch.setattr(tm_io.tm_core, "mem0_update_content", fake_update)

    tm_io.cmd_mem0_update_content(Args())

    assert captured == {
        "memory_id": "fd65b298-05bd-493c-83ce-e37d84447362",
        "memory_content": "replacement content",
    }
    assert '{"ok": true}' in capsys.readouterr().out


def test_mem0_update_content_cli_has_no_metadata_option(monkeypatch):
    monkeypatch.setattr(tm_io.sys, "argv", [
        "tm_io.py",
        "mem0-update-content",
        "--id",
        "fd65b298-05bd-493c-83ce-e37d84447362",
        "--metadata",
        '{"topic": "systems"}',
    ])

    with pytest.raises(SystemExit) as exc:
        tm_io.main()

    assert exc.value.code == 2
