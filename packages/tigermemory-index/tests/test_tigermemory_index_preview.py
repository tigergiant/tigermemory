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


def test_generated_partition_index_has_navigation_preamble(wiki_root):
    _write(wiki_root / "brand" / "alpha.md", _page("Alpha", 'subtopic: ["copywriting"]'))

    text, _old = tigermemory_index.compile_partition_index("brand")

    assert 'title: "品牌分区入口"' in text
    assert 'description: "品牌分区的目录和导航页' in text
    assert "品牌有哪些页面" in text
    assert 'subtopic: ["navigation", "index"]' in text
    assert "# 品牌分区入口" in text
    assert "本页是 `brand` 分区的目录和导航页" in text
    assert "## 来源" in text
    assert text.index("## 来源") < text.index("## 页面")
    assert "tools/tm_compile_index.py" in text


def test_partition_index_standard_preserves_existing_intro_and_refreshes_date(wiki_root):
    _write(wiki_root / "brand" / "alpha.md", _page("Alpha", 'subtopic: ["copywriting"]'))
    _write(
        wiki_root / "brand" / "index.md",
        "\n".join(
            [
                "---",
                'aliases: ["品牌"]',
                "owner: human",
                "status: active",
                "updated: 2026-05-01",
                'title: "品牌"',
                "---",
                "",
                "# Brand",
                "",
                "原有人工说明。",
                "",
                "## 重点入口",
                "",
                "- [人工入口](alpha.md)",
                "",
                "## 页面",
                "",
            ]
        ),
    )

    text, _old = tigermemory_index.compile_partition_index("brand")

    assert "owner: human" in text
    assert f"updated: {tigermemory_index._today_cn()}" in text
    assert "# 品牌分区入口" in text
    assert "原有人工说明。" in text
    assert text.index("## 重点入口") < text.index("## 页面")
    assert "- [人工入口](alpha.md)" in text


def test_person_partition_preamble_is_not_normalized(wiki_root):
    _write(wiki_root / "person" / "tiger.md", _page("Tiger"))
    _write(
        wiki_root / "person" / "index.md",
        "\n".join(
            [
                "---",
                'aliases: ["人物"]',
                'title: "人物"',
                "---",
                "",
                "# Person",
                "",
                "系统主人档案与偏好设置见 [虎哥](tiger.md)。",
                "",
                "## 页面",
                "",
            ]
        ),
    )

    text, _old = tigermemory_index.compile_partition_index("person")

    assert 'aliases: ["人物"]' in text
    assert 'title: "人物"' in text
    assert "owner: codex" not in text
    assert "# Person" in text
    assert "人物分区入口" not in text


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


def test_partition_index_omits_draft_pages(wiki_root):
    _write(wiki_root / "brand" / "active.md", _page("Active", 'subtopic: ["copywriting"]'))
    _write(
        wiki_root / "brand" / "draft.md",
        _page("Draft", 'subtopic: ["copywriting"]').replace("status: active", "status: draft"),
    )

    new_index, _old_index = tigermemory_index.compile_partition_index("brand")
    preview = tigermemory_index.render_preview("brand", date="2026-05-24")

    assert "active.md" in new_index
    assert "draft.md" not in new_index
    assert "active.md" in preview
    assert "draft.md" not in preview


def test_detect_repo_root_honors_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TIGERMEMORY_ROOT", str(tmp_path))

    assert tigermemory_index._detect_repo_root() == tmp_path.resolve()
