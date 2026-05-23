from __future__ import annotations

import json
import pathlib
import sys
from typing import Optional

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_subtopic_audit  # type: ignore[import-not-found]


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _page(subtopic: Optional[str] = None, title: str = "Page") -> str:
    lines = [
        "---",
        "owner: codex",
        "status: active",
        "updated: 2026-05-24",
        f'title: "{title}"',
    ]
    if subtopic is not None:
        lines.append(subtopic)
    lines.extend(["---", "", f"# {title}", "", "## 摘要", "", "test"])
    return "\n".join(lines)


def test_collects_top_level_pages_and_ignores_index_and_nested_by_default(tmp_path):
    _write(tmp_path / "wiki" / "systems" / "a.md", _page('subtopic: ["memory-engine"]'))
    _write(tmp_path / "wiki" / "systems" / "index.md", _page('subtopic: ["ignored"]'))
    _write(tmp_path / "wiki" / "systems" / "nested" / "b.md", _page('subtopic: ["nested"]'))

    pages = tm_subtopic_audit.collect_pages(tmp_path)

    assert [page.path for page in pages] == ["wiki/systems/a.md"]
    assert pages[0].subtopics == ["memory-engine"]


def test_include_nested_adds_nested_pages(tmp_path):
    _write(tmp_path / "wiki" / "systems" / "a.md", _page('subtopic: ["memory-engine"]'))
    _write(tmp_path / "wiki" / "systems" / "nested" / "b.md", _page('subtopic: ["retrieval"]'))

    report = tm_subtopic_audit.build_report(tm_subtopic_audit.collect_pages(tmp_path, include_nested=True))

    assert report["total_pages"] == 2
    assert report["partition_subtopics"]["systems"]["memory-engine"] == 1
    assert report["partition_subtopics"]["systems"]["retrieval"] == 1


def test_reports_untagged_too_many_and_low_frequency(tmp_path):
    _write(tmp_path / "wiki" / "systems" / "a.md", _page('subtopic: ["memory-engine", "retrieval", "governance", "agent-system"]'))
    _write(tmp_path / "wiki" / "operations" / "b.md", _page())
    _write(tmp_path / "wiki" / "investment" / "c.md", _page('subtopic: ["governance"]'))

    report = tm_subtopic_audit.build_report(tm_subtopic_audit.collect_pages(tmp_path))

    assert report["untagged_count"] == 1
    assert report["untagged_pages"][0]["path"] == "wiki/operations/b.md"
    assert report["too_many_count"] == 1
    assert report["too_many_pages"][0]["path"] == "wiki/systems/a.md"
    assert report["low_frequency_subtopics"]["retrieval"] == 1


def test_parses_block_and_scalar_subtopic_forms(tmp_path):
    _write(tmp_path / "wiki" / "systems" / "block.md", _page("subtopic:\n  - memory-engine\n  - retrieval"))
    _write(tmp_path / "wiki" / "systems" / "scalar.md", _page("subtopic: governance"))

    report = tm_subtopic_audit.build_report(tm_subtopic_audit.collect_pages(tmp_path))

    assert report["subtopic_counts"]["memory-engine"] == 1
    assert report["subtopic_counts"]["retrieval"] == 1
    assert report["subtopic_counts"]["governance"] == 1


def test_cli_json_output(tmp_path, capsys):
    _write(tmp_path / "wiki" / "systems" / "a.md", _page('subtopic: ["memory-engine"]'))

    code = tm_subtopic_audit.main(["--root", str(tmp_path), "--json"])

    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["subtopic_counts"]["memory-engine"] == 1


def test_markdown_output_contains_core_sections(tmp_path, capsys):
    _write(tmp_path / "wiki" / "systems" / "a.md", _page('subtopic: ["memory-engine"]'))

    code = tm_subtopic_audit.main(["--root", str(tmp_path)])

    assert code == 0
    out = capsys.readouterr().out
    assert "## Partition x Subtopic" in out
    assert "| `systems` | `memory-engine` | 1 |" in out
