from __future__ import annotations

import pathlib
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_dev_supervisor_context_pack as context_pack


def test_context_pack_includes_bounded_tigermemory_queries(monkeypatch, tmp_path):
    monkeypatch.setattr(context_pack, "REPO_ROOT", tmp_path)
    (tmp_path / "wiki" / "systems").mkdir(parents=True)
    target = tmp_path / "wiki" / "systems" / "example.md"
    target.write_text("# example\n", encoding="utf-8")

    text = context_pack.build_context_pack(
        objective="review the supervisor flow",
        stage="p-test",
        files=["wiki/systems/example.md"],
        review_archives=[],
        memory_queries=["memory_type: session-handoff supervisor"],
        read_pages=["wiki/systems/example.md"],
        notes=["stay read-only"],
    )

    assert "不要默认全仓扫描" in text
    assert 'read_page(path="wiki/operations/project-canvas.md")' in text
    assert 'read_page(path="wiki/systems/example.md")' in text
    assert 'search_memories(query="memory_type: session-handoff supervisor", size=3)' in text
    assert f"[exists] `{target}`" in text
    assert "stay read-only" in text


def test_write_context_pack_defaults_to_tmp_supervisor_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(context_pack, "OUT_DIR", tmp_path / "packs")

    out_path = context_pack.write_context_pack("hello", stage="P3.13 / Context")

    assert out_path.parent == tmp_path / "packs"
    assert out_path.name.endswith("-P3.13-Context.md")
    assert out_path.read_text(encoding="utf-8") == "hello"

