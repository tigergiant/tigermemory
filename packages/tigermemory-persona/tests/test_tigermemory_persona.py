"""Smoke tests for tm_persona.py compile_snapshot and helpers.

Run:  cd <repo> && py -m pytest tests/test_tm_persona.py -v

These are fast deterministic tests (no LLM, no network, no external services).
They guard against accidental breakage of the onboarding snapshot contract.
"""
from __future__ import annotations

import sys
import importlib

import tigermemory_persona


# ------------------------------------------------------------------
# compile_snapshot — contract tests per depth
# ------------------------------------------------------------------

def test_compile_snapshot_30s():
    out = tigermemory_persona.compile_snapshot("30s")
    assert isinstance(out, str)
    assert out.startswith("# tigermemory Agent Onboarding Snapshot (30s)")
    # Must contain the six canonical bullets (contract from agent-onboarding.md)
    assert "git pull --ff-only origin master" in out
    assert "tm_lessons.py search" in out
    assert 'get_agent_onboarding("30s")' in out
    assert "selfevolution" in out
    assert "write_memory" in out or "write_inbox" in out
    assert "commit + push" in out or "同回合 push" in out
    assert "--no-verify" in out
    # Length guard: 30s is a tight summary, should be compact but not empty
    assert 200 < len(out) < 3000, f"30s length {len(out)} outside expected band"


def test_compile_snapshot_5min():
    out = tigermemory_persona.compile_snapshot("5min")
    assert isinstance(out, str)
    assert out.startswith("# tigermemory Agent Onboarding Snapshot (5min)")
    # Structural sections must be present
    assert "## 1. 开工顺序" in out
    assert "## 2. 写入权限边界" in out
    assert "## 3. 工具入口" in out
    assert "## 4. Agent 生态地图（一句话定位）" in out
    assert "## 5. 生产服务清单（live runtime services）" in out
    assert "## 6. Live-state 优先原则" in out
    assert "## 7. 必须避免的 lesson" in out
    # Agent ecosystem must mention the major systems by name
    for token in ("OpenClaw", "Hermes", "DeerFlow", "Mem0", "OpenSpace", "search_tigermemory"):
        assert token in out, f"agent ecosystem section missing {token!r}"
    # Live runtime services section must surface the systemd-tracked services
    # so a fresh agent reading the 5min snapshot sees dashboard / tm-http /
    # tm-mcp / OpenAI MCP / OpenMemory ports without further searching.
    for token in ("tm-dashboard", "1998", "tm-http", "8790", "tm-mcp", "9766", "8765", "OpenMemory"):
        assert token in out, f"services-inventory section missing {token!r}"
    # Must still mention core keywords
    assert "git pull --ff-only origin master" in out
    assert "tm_lessons.py search" in out
    assert 'get_agent_onboarding("30s")' in out
    assert "selfevolution" in out
    # 5min should be longer than 30s; embedded inventory adds ~2KB so the
    # ceiling is now 10KB (was 8KB). Keep the guard as drift detector.
    assert len(out) > 1500, f"5min length {len(out)} suspiciously short"
    assert len(out) < 10000, f"5min length {len(out)} suspiciously long"


def test_compile_snapshot_full():
    out = tigermemory_persona.compile_snapshot("full")
    assert isinstance(out, str)
    assert out.startswith("# tigermemory Agent Onboarding Snapshot (5min)")
    # full is 5min superset (sections 8/9/10 + 来源)
    assert "## 8. Agent 接入边界" in out
    assert "## 9. 完整 lesson 清单" in out
    assert "## 10. v0.2 范围" in out
    assert "## 来源" in out
    # Must contain source path list (the contract footer)
    assert "AGENTS.md" in out
    assert "wiki/systems/tigermemory-agent-access.md" in out
    # full should be longest
    assert len(out) > len(tigermemory_persona.compile_snapshot("5min"))


import pytest


def test_compile_snapshot_invalid_depth_raises():
    with pytest.raises(ValueError):
        tigermemory_persona.compile_snapshot("1s")


# ------------------------------------------------------------------
# Internal helpers — frontmatter / section / lessons
# ------------------------------------------------------------------

def test_frontmatter_title_extracts_yaml():
    text = '---\ntitle: "Foo Bar"\n---\n\n# Baz\n'
    assert tigermemory_persona._frontmatter_title(text) == "Foo Bar"


def test_frontmatter_title_fallback_h1():
    text = "# Hello World\n"
    assert tigermemory_persona._frontmatter_title(text) == "Hello World"


def test_frontmatter_title_empty():
    assert tigermemory_persona._frontmatter_title("") == ""


def test_section_extraction_hits():
    text = "## 摘要\n\nThis is the abstract.\n\n## 现状\n\nCurrent state.\n"
    assert "This is the abstract." in tigermemory_persona._section(text, "摘要")
    assert "Current state." in tigermemory_persona._section(text, "现状")


def test_section_extraction_miss_returns_empty():
    assert tigermemory_persona._section("# Title\n\nbody", "来源") == ""


def test_first_paragraph_skips_lists():
    text = "## 摘要\n\n- bullet\n\nActual paragraph.\n"
    # The first non-list block after heading-split would be "Actual paragraph."
    # But _first_paragraph operates on raw markdown, so let's feed it the section body
    body = "- bullet\n\nActual paragraph.\n"
    assert tigermemory_persona._first_paragraph(body) == "Actual paragraph."


def test_first_paragraph_empty():
    assert tigermemory_persona._first_paragraph("") == ""


def test_load_lessons_returns_list_of_lessons():
    lessons = tigermemory_persona.load_lessons(limit=7)
    assert isinstance(lessons, list)
    # If lessons exist, each item must be a Lesson dataclass with the three fields
    for lesson in lessons:
        assert isinstance(lesson, tigermemory_persona.Lesson)
        assert lesson.slug
        assert lesson.title
        # summary may be empty if a lesson lacks 摘要 / 预防性教训


def test_load_lessons_respects_limit():
    lessons = tigermemory_persona.load_lessons(limit=2)
    assert len(lessons) <= 2


# ------------------------------------------------------------------
# README-style docstring sanity (catches accidental API drift)
# ------------------------------------------------------------------

def test_module_docstring_mentions_all_depths():
    doc = tigermemory_persona.__doc__ or ""
    assert "30s" in doc
    assert "5min" in doc
    assert "full" in doc
    assert "get_agent_onboarding" in doc


def test_valid_depths_set():
    assert tigermemory_persona.VALID_DEPTHS == {"30s", "5min", "full"}


# Guard against accidentally breaking the deterministic promise
def test_no_llm_imports():
    """Ensure tm_persona does not import openai, requests, mem0, etc."""
    forbidden = {"openai", "requests", "mem0", "httpx", "aiohttp"}
    before = set(sys.modules.keys())
    importlib.reload(tigermemory_persona)
    new = set(sys.modules.keys()) - before
    overlap = forbidden & new
    assert not overlap, f"tm_persona leaked external deps: {overlap}"


# ------------------------------------------------------------------
# CLI subcommands
# ------------------------------------------------------------------

def test_cmd_check_exits_zero_and_lists_sources():
    import argparse

    args = argparse.Namespace()
    rc = tigermemory_persona.cmd_check(args)
    # In a clean git checkout all SOURCE_PATHS should be tracked
    assert rc == 0, f"cmd_check returned {rc}; expected 0 in tracked repo"


def test_snapshot_page_required_phrases_are_declared():
    assert tigermemory_persona.SNAPSHOT_PAGE == "wiki/systems/agent-onboarding.md"
    assert "继续开发条件" in tigermemory_persona.SNAPSHOT_PAGE_REQUIRED_PHRASES


def test_detect_repo_root_honors_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TIGERMEMORY_ROOT", str(tmp_path))

    assert tigermemory_persona._detect_repo_root() == tmp_path.resolve()
