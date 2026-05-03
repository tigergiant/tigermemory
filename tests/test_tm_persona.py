"""Smoke tests for tm_persona.py compile_snapshot and helpers.

Run:  cd d:\tigermemory && py -m pytest tests/test_tm_persona.py -v

These are fast deterministic tests (no LLM, no network, no external services).
They guard against accidental breakage of the onboarding snapshot contract.
"""
from __future__ import annotations

import pathlib
import sys

# Add tools/ to path for direct import (works cross-platform: Windows native or WSL)
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_persona  # type: ignore[import-not-found]


# ------------------------------------------------------------------
# compile_snapshot — contract tests per depth
# ------------------------------------------------------------------

def test_compile_snapshot_30s():
    out = tm_persona.compile_snapshot("30s")
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
    out = tm_persona.compile_snapshot("5min")
    assert isinstance(out, str)
    assert out.startswith("# tigermemory Agent Onboarding Snapshot (5min)")
    # Structural sections must be present
    assert "## 1. 开工顺序" in out
    assert "## 2. 写入权限边界" in out
    assert "## 3. 工具入口" in out
    assert "## 4. Live-state 优先原则" in out
    assert "## 5. 必须避免的 lesson" in out
    # Must still mention core keywords
    assert "git pull --ff-only origin master" in out
    assert "tm_lessons.py search" in out
    assert 'get_agent_onboarding("30s")' in out
    assert "selfevolution" in out
    # 5min should be longer than 30s
    assert len(out) > 1500, f"5min length {len(out)} suspiciously short"
    assert len(out) < 8000, f"5min length {len(out)} suspiciously long"


def test_compile_snapshot_full():
    out = tm_persona.compile_snapshot("full")
    assert isinstance(out, str)
    assert out.startswith("# tigermemory Agent Onboarding Snapshot (5min)")
    # full is 5min superset
    assert "## 6. Agent 接入边界" in out
    assert "## 7. 完整 lesson 清单" in out
    assert "## 8. v0.2 范围" in out
    assert "## 来源" in out
    # Must contain source path list (the contract footer)
    assert "AGENTS.md" in out
    assert "wiki/systems/tigermemory-agent-access.md" in out
    # full should be longest
    assert len(out) > len(tm_persona.compile_snapshot("5min"))


import pytest


def test_compile_snapshot_invalid_depth_raises():
    with pytest.raises(ValueError):
        tm_persona.compile_snapshot("1s")


# ------------------------------------------------------------------
# Internal helpers — frontmatter / section / lessons
# ------------------------------------------------------------------

def test_frontmatter_title_extracts_yaml():
    text = '---\ntitle: "Foo Bar"\n---\n\n# Baz\n'
    assert tm_persona._frontmatter_title(text) == "Foo Bar"


def test_frontmatter_title_fallback_h1():
    text = "# Hello World\n"
    assert tm_persona._frontmatter_title(text) == "Hello World"


def test_frontmatter_title_empty():
    assert tm_persona._frontmatter_title("") == ""


def test_section_extraction_hits():
    text = "## 摘要\n\nThis is the abstract.\n\n## 现状\n\nCurrent state.\n"
    assert "This is the abstract." in tm_persona._section(text, "摘要")
    assert "Current state." in tm_persona._section(text, "现状")


def test_section_extraction_miss_returns_empty():
    assert tm_persona._section("# Title\n\nbody", "来源") == ""


def test_first_paragraph_skips_lists():
    text = "## 摘要\n\n- bullet\n\nActual paragraph.\n"
    # The first non-list block after heading-split would be "Actual paragraph."
    # But _first_paragraph operates on raw markdown, so let's feed it the section body
    body = "- bullet\n\nActual paragraph.\n"
    assert tm_persona._first_paragraph(body) == "Actual paragraph."


def test_first_paragraph_empty():
    assert tm_persona._first_paragraph("") == ""


def test_load_lessons_returns_list_of_lessons():
    lessons = tm_persona.load_lessons(limit=7)
    assert isinstance(lessons, list)
    # If lessons exist, each item must be a Lesson dataclass with the three fields
    for lesson in lessons:
        assert isinstance(lesson, tm_persona.Lesson)
        assert lesson.slug
        assert lesson.title
        # summary may be empty if a lesson lacks 摘要 / 预防性教训


def test_load_lessons_respects_limit():
    lessons = tm_persona.load_lessons(limit=2)
    assert len(lessons) <= 2


# ------------------------------------------------------------------
# README-style docstring sanity (catches accidental API drift)
# ------------------------------------------------------------------

def test_module_docstring_mentions_all_depths():
    doc = tm_persona.__doc__ or ""
    assert "30s" in doc
    assert "5min" in doc
    assert "full" in doc
    assert "get_agent_onboarding" in doc


def test_valid_depths_set():
    assert tm_persona.VALID_DEPTHS == {"30s", "5min", "full"}


# Guard against accidentally breaking the deterministic promise
def test_no_llm_imports():
    """Ensure tm_persona does not import openai, requests, mem0, etc."""
    forbidden = {"openai", "requests", "mem0", "httpx", "aiohttp"}
    # ASSUMPTION: pytest was launched from REPO_ROOT so '' is in sys.path,
    # making 'tools' resolvable as a namespace package.
    before = set(sys.modules.keys())
    import tools.tm_persona as _tp  # noqa: F401  # fresh key → executes top-level
    new = set(sys.modules.keys()) - before
    overlap = forbidden & new
    assert not overlap, f"tm_persona leaked external deps: {overlap}"


# ------------------------------------------------------------------
# CLI subcommands
# ------------------------------------------------------------------

def test_cmd_check_exits_zero_and_lists_sources():
    import argparse

    args = argparse.Namespace()
    rc = tm_persona.cmd_check(args)
    # In a clean git checkout all SOURCE_PATHS should be tracked
    assert rc == 0, f"cmd_check returned {rc}; expected 0 in tracked repo"


def test_snapshot_page_required_phrases_are_declared():
    assert tm_persona.SNAPSHOT_PAGE == "wiki/systems/agent-onboarding.md"
    assert "继续开发条件" in tm_persona.SNAPSHOT_PAGE_REQUIRED_PHRASES
