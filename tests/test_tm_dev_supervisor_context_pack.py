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
    assert "## Pack Budget" in text
    assert "budget_status: ok" in text
    assert 'read_page(path="wiki/operations/project-canvas.md")' in text
    assert 'read_page(path="wiki/systems/example.md")' in text
    assert 'search_memories(query="memory_type: session-handoff supervisor", size=3)' in text
    assert f"[exists] `{target}`" in text
    assert "stay read-only" in text


def test_context_pack_marks_overbroad_inputs(monkeypatch, tmp_path):
    monkeypatch.setattr(context_pack, "REPO_ROOT", tmp_path)

    text = context_pack.build_context_pack(
        objective="review",
        stage="wide",
        files=[f"f{i}.md" for i in range(context_pack.MAX_RECOMMENDED_FILES + 1)],
        review_archives=[f"a{i}.md" for i in range(context_pack.MAX_RECOMMENDED_ARCHIVES + 1)],
        memory_queries=[f"q{i}" for i in range(context_pack.MAX_RECOMMENDED_MEMORY_QUERIES + 1)],
        read_pages=[f"wiki/systems/p{i}.md" for i in range(context_pack.MAX_RECOMMENDED_READ_PAGES + 1)],
        notes=[],
    )

    assert "budget_status: needs_shrinking" in text
    assert "local_files=" in text
    assert "review_archives=" in text
    assert "memory_queries=" in text


def test_write_context_pack_defaults_to_tmp_supervisor_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(context_pack, "OUT_DIR", tmp_path / "packs")

    out_path = context_pack.write_context_pack("hello", stage="P3.13 / Context")

    assert out_path.parent == tmp_path / "packs"
    assert out_path.name.endswith("-P3.13-Context.md")
    assert out_path.read_text(encoding="utf-8") == "hello"


def test_context_pack_includes_failed_archive_delta_and_recent_official_success(monkeypatch, tmp_path):
    monkeypatch.setattr(context_pack, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(context_pack, "LEDGER_PATH", tmp_path / "wiki" / "operations" / "development-supervisor-ledger.md")
    (tmp_path / "wiki" / "operations").mkdir(parents=True)
    context_pack.LEDGER_PATH.write_text(
        "# Ledger\n\n## 审核调用记录\n"
        "- 2026-06-14 16:20 | channel=claude-official-review | workspace=TigerMemory | "
        "role=tiger-development-reviewer | stage=ok-stage | session_ref=abc | model=sonnet | "
        "effort=high | session_mode=fresh | status=success | failure=none | prompt_hash=hash | "
        "archive=sources/review-ok.md\n",
        encoding="utf-8",
    )
    archive = tmp_path / "failed.md"
    archive.write_text(
        "---\n"
        "review_status: failed\n"
        "failure_kind: session_limit\n"
        "---\n\n"
        "## Original Task\n\n- git_head: `oldhead123456`\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(context_pack, "_git_head", lambda: "newhead123456")
    monkeypatch.setattr(context_pack, "_git_diff_names", lambda old, new: ["tools/example.py"] if old and new else [])

    text = context_pack.build_context_pack(
        objective="review",
        stage="resume",
        files=[],
        review_archives=[str(archive)],
        memory_queries=[],
        read_pages=[],
        notes=[],
    )

    assert "## Archive Continuity Checks" in text
    assert "review_status=failed" in text
    assert "archive_git_head=oldhead123456" in text
    assert "current_git_head=newhead123456" in text
    assert "changed_since_archive: `tools/example.py`" in text
    assert "## Recent Official Review Successes" in text
    assert "stage=ok-stage" in text
