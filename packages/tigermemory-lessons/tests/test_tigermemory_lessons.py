from __future__ import annotations

import io
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
for _pkg_src in (
    REPO_ROOT / "packages" / "tigermemory-core" / "src",
    REPO_ROOT / "packages" / "tigermemory-lessons" / "src",
):
    if str(_pkg_src) not in sys.path:
        sys.path.insert(0, str(_pkg_src))

import tigermemory_lessons as tm_lessons


def test_configure_stdio_backslashreplaces_unencodable_stdout(monkeypatch):
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp936", errors="strict")
    monkeypatch.setattr(tm_lessons.sys, "stdout", stream)

    tm_lessons._configure_stdio()
    print("git \u2194 WSL", file=tm_lessons.sys.stdout)
    tm_lessons.sys.stdout.flush()

    assert raw.getvalue().decode("utf-8").replace("\r\n", "\n") == "git ↔ WSL\n"


def test_score_lesson_returns_four_tuple_without_breakdown_by_default():
    text = """---
title: Commit Push Discipline
aliases: ["commit push"]
---

# Commit Push Discipline

Body mentions hooks once.
"""

    score, title, aliases, breakdown = tm_lessons._score_lesson(text, ["commit", "hooks"])

    assert score > 0
    assert title == "Commit Push Discipline"
    assert aliases == ["commit push"]
    assert breakdown is None


def test_score_lesson_explain_counts_title_alias_and_body_hits():
    text = """---
title: Commit Push Discipline
aliases: ["hook safety"]
---

hook body body
"""

    score, _title, _aliases, breakdown = tm_lessons._score_lesson(
        text,
        ["commit", "hook", "body"],
        explain=True,
    )

    assert breakdown == {
        "title_hits": 1,
        "alias_hits": 1,
        "body_hits": 3,
        "matched_terms": ["commit", "hook", "body"],
        "final_score": score,
    }
