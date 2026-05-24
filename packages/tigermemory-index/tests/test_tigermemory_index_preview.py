from __future__ import annotations

import pathlib

import pytest

import tigermemory_index


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
    monkeypatch.setattr(tigermemory_index, "WIKI_ROOT", root)
    return root


def test_preview_subcommand_writes_partition_file(wiki_root, capsys):
    _write(wiki_root / "brand" / "alpha.md", _page("Alpha", 'subtopic: ["copywriting"]'))

    with pytest.raises(SystemExit) as exc:
        tigermemory_index.main(["preview", "--partition", "brand"])

    assert exc.value.code == 0
    out_path = wiki_root / "brand" / tigermemory_index.PREVIEW_FILENAME
    assert out_path.exists()
    assert "WROTE: wiki/brand/index-by-subtopic.md" in capsys.readouterr().out


def test_subtopic_groups_sort_by_size_then_name(wiki_root):
    _write(wiki_root / "brand" / "a.md", _page("A", 'subtopic: ["beta"]'))
    _write(wiki_root / "brand" / "b.md", _page("B", 'subtopic: ["alpha", "beta"]'))
    _write(wiki_root / "brand" / "c.md", _page("C", 'subtopic: ["alpha"]'))
    _write(wiki_root / "brand" / "d.md", _page("D", 'subtopic: ["zeta"]'))

    text = tigermemory_index.render_preview("brand", date="2026-05-24")

    assert text.index("## alpha (2)") < text.index("## beta (2)")
    assert text.index("## beta (2)") < text.index("## zeta (1)")


def test_multi_subtopic_page_is_rendered_in_each_group(wiki_root):
    _write(wiki_root / "brand" / "shared.md", _page("Shared", 'subtopic: ["alpha", "beta"]'))

    text = tigermemory_index.render_preview("brand", date="2026-05-24")

    assert text.count("[Shared](shared.md)") == 2


def test_untagged_group_is_last_and_omitted_when_empty(wiki_root):
    _write(wiki_root / "brand" / "tagged.md", _page("Tagged", 'subtopic: ["alpha"]'))
    _write(wiki_root / "brand" / "untagged.md", _page("Untagged"))

    text = tigermemory_index.render_preview("brand", date="2026-05-24")

    assert text.index("## alpha (1)") < text.index("## 未打 subtopic (1)")

    (wiki_root / "brand" / "untagged.md").unlink()
    text_without_untagged = tigermemory_index.render_preview("brand", date="2026-05-24")
    assert "## 未打 subtopic" not in text_without_untagged


def test_preview_file_is_not_included_in_partition_index(wiki_root):
    _write(wiki_root / "brand" / "alpha.md", _page("Alpha", 'subtopic: ["copywriting"]'))
    _write(wiki_root / "brand" / "index-by-subtopic.md", tigermemory_index.render_preview("brand", date="2026-05-24"))

    new_index, _old_index = tigermemory_index.compile_partition_index("brand")

    assert "alpha.md" in new_index
    assert "- [brand Index" not in new_index
    assert tigermemory_index.PREVIEW_LINK_LINE in new_index


def test_partition_index_omits_preview_footer_when_preview_missing(wiki_root):
    _write(wiki_root / "brand" / "alpha.md", _page("Alpha", 'subtopic: ["copywriting"]'))

    new_index, _old_index = tigermemory_index.compile_partition_index("brand")

    assert tigermemory_index.PREVIEW_LINK_LINE not in new_index


def test_detect_repo_root_honors_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TIGERMEMORY_ROOT", str(tmp_path))

    assert tigermemory_index._detect_repo_root() == tmp_path.resolve()
