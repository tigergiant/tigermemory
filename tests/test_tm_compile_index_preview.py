from __future__ import annotations

import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_compile_index  # type: ignore[import-not-found]
import tm_core  # type: ignore[import-not-found]


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _page(title: str, subtopic: str | None = None) -> str:
    lines = [
        "---",
        "owner: codex",
        "status: active",
        "updated: 2026-05-24",
        f'aliases: ["{title}"]',
    ]
    if subtopic:
        lines.append(subtopic)
    lines.extend(
        [
            "---",
            "",
            f"# {title}",
            "",
            "## 摘要",
            "",
            f"{title} summary.",
            "",
            "## 来源",
            "",
            "- test fixture",
        ]
    )
    return "\n".join(lines) + "\n"


@pytest.fixture()
def wiki_root(tmp_path, monkeypatch):
    root = tmp_path / "wiki"
    monkeypatch.setattr(tm_compile_index, "WIKI_ROOT", root)
    return root


def test_preview_subcommand_writes_partition_file(wiki_root, capsys):
    _write(wiki_root / "brand" / "alpha.md", _page("Alpha", 'subtopic: ["copywriting"]'))

    with pytest.raises(SystemExit) as exc:
        tm_compile_index.main(["preview", "--partition", "brand"])

    assert exc.value.code == 0
    out_path = wiki_root / "brand" / tm_compile_index.PREVIEW_FILENAME
    assert out_path.exists()
    assert "WROTE: wiki/brand/index-by-subtopic.md" in capsys.readouterr().out


def test_subtopic_groups_sort_by_size_then_name(wiki_root):
    _write(wiki_root / "brand" / "a.md", _page("A", 'subtopic: ["beta"]'))
    _write(wiki_root / "brand" / "b.md", _page("B", 'subtopic: ["alpha", "beta"]'))
    _write(wiki_root / "brand" / "c.md", _page("C", 'subtopic: ["alpha"]'))
    _write(wiki_root / "brand" / "d.md", _page("D", 'subtopic: ["zeta"]'))

    text = tm_compile_index.render_preview("brand", date="2026-05-24")

    assert text.index("## alpha (2)") < text.index("## beta (2)")
    assert text.index("## beta (2)") < text.index("## zeta (1)")


def test_multi_subtopic_page_is_rendered_in_each_group(wiki_root):
    _write(wiki_root / "brand" / "shared.md", _page("Shared", 'subtopic: ["alpha", "beta"]'))

    text = tm_compile_index.render_preview("brand", date="2026-05-24")

    assert text.count("[Shared](shared.md)") == 2


def test_untagged_group_is_last_and_omitted_when_empty(wiki_root):
    _write(wiki_root / "brand" / "tagged.md", _page("Tagged", 'subtopic: ["alpha"]'))
    _write(wiki_root / "brand" / "untagged.md", _page("Untagged"))

    text = tm_compile_index.render_preview("brand", date="2026-05-24")

    assert text.index("## alpha (1)") < text.index("## 未打 subtopic (1)")

    (wiki_root / "brand" / "untagged.md").unlink()
    text_without_untagged = tm_compile_index.render_preview("brand", date="2026-05-24")
    assert "## 未打 subtopic" not in text_without_untagged


def test_generated_preview_passes_page_lint(wiki_root):
    _write(wiki_root / "brand" / "alpha.md", _page("Alpha", 'subtopic: ["copywriting"]'))

    text = tm_compile_index.render_preview("brand", date="2026-05-24")

    assert "owner: linter" in text
    assert tm_core.lint_page_errors(text) == []


def test_preview_file_is_not_included_in_partition_index(wiki_root):
    _write(wiki_root / "brand" / "alpha.md", _page("Alpha", 'subtopic: ["copywriting"]'))
    _write(wiki_root / "brand" / "index-by-subtopic.md", tm_compile_index.render_preview("brand", date="2026-05-24"))

    new_index, _old_index = tm_compile_index.compile_partition_index("brand")

    assert "alpha.md" in new_index
    assert "index-by-subtopic.md" not in new_index


def test_linter_dashboard_preview_paths_are_exempt_from_repo_lint(tmp_path, monkeypatch):
    repo_root = tmp_path
    wiki_root = repo_root / "wiki"
    monkeypatch.setattr(tm_core, "REPO_ROOT", repo_root)

    _write(wiki_root / "brand" / "index.md", "# Brand\n\n## 页面\n")
    _write(
        wiki_root / "brand" / "index-by-subtopic.md",
        "\n".join(
            [
                "---",
                "owner: linter",
                "status: draft",
                "updated: 2026-05-24",
                "---",
                "",
                "# brand Index — by subtopic (PoC preview)",
                "",
                "## 摘要",
                "",
                "preview",
                "",
                "## 来源",
                "",
                "- test fixture",
                "",
            ]
        ),
    )

    result = tm_core.lint_repo_scan()

    assert result["orphan_pages"] == []
    assert result["missing_sources"] == []
    assert result["partition_mismatches"] == []
