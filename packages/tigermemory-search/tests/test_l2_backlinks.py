from __future__ import annotations

from pathlib import Path

import tigermemory_answer as answer


def write_page(root: Path, rel: str, title: str, body: str) -> None:
    path = root / "wiki" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                "owner: codex",
                "status: active",
                "updated: 2026-05-25",
                "partition: systems",
                f"title: {title}",
                "---",
                "",
                body,
                "",
            ]
        ),
        encoding="utf-8",
    )


def stub_wiki_hit(path: str, title: str = "Page A") -> dict:
    return {
        "path": path,
        "title": title,
        "snippet": "tiger root page",
        "score": 10.0,
        "score_breakdown": {"fixture": True},
    }


def patch_primary_wiki(monkeypatch, tmp_path: Path, hit_path: str = "wiki/systems/page-a.md") -> None:
    monkeypatch.setattr(answer.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(answer.tm_core, "primary_search_scope", lambda _query: "wiki")
    monkeypatch.setattr(answer.tm_core, "search_wiki_hybrid", lambda *_args, **_kwargs: [stub_wiki_hit(hit_path)])


def test_search_tigermemory_default_no_backlinks_field(monkeypatch, tmp_path: Path) -> None:
    write_page(tmp_path, "systems/page-a.md", "Page A", "Tiger page without expansion.")
    patch_primary_wiki(monkeypatch, tmp_path)

    result = answer.search_tigermemory("tiger", scope="wiki", dogfood_log=None)

    assert "backlink_results" not in result


def test_search_tigermemory_follow_backlinks_finds_linked_neighbor(monkeypatch, tmp_path: Path) -> None:
    write_page(tmp_path, "systems/page-a.md", "Page A", "Tiger page links to [Page B](page-b.md).")
    write_page(tmp_path, "systems/page-b.md", "Page B", "Neighbor page for Tiger context.")
    patch_primary_wiki(monkeypatch, tmp_path)

    result = answer.search_tigermemory("tiger", scope="wiki", follow_backlinks=True, dogfood_log=None)
    paths = [hit["path"] for hit in result["backlink_results"]]

    assert "wiki/systems/page-b.md" in paths


def test_search_tigermemory_follow_backlinks_stays_single_hop(monkeypatch, tmp_path: Path) -> None:
    write_page(tmp_path, "systems/page-a.md", "Page A", "Tiger page links to [Page B](page-b.md).")
    write_page(tmp_path, "systems/page-b.md", "Page B", "Page B links to [Page C](../investment/page-c.md).")
    write_page(tmp_path, "investment/page-c.md", "Page C", "Second-hop page should not be included.")
    patch_primary_wiki(monkeypatch, tmp_path)

    result = answer.search_tigermemory("tiger", scope="wiki", follow_backlinks=True, dogfood_log=None)
    paths = [hit["path"] for hit in result["backlink_results"]]

    assert "wiki/systems/page-b.md" in paths
    assert "wiki/investment/page-c.md" not in paths


def test_search_tigermemory_follow_backlinks_finds_inbound_neighbor(monkeypatch, tmp_path: Path) -> None:
    write_page(tmp_path, "systems/page-a.md", "Page A", "Tiger target page.")
    write_page(tmp_path, "systems/page-ref.md", "Page Ref", "Reference page links to [Page A](page-a.md).")
    patch_primary_wiki(monkeypatch, tmp_path)

    result = answer.search_tigermemory("tiger", scope="wiki", follow_backlinks=True, dogfood_log=None)
    paths = [hit["path"] for hit in result["backlink_results"]]

    assert "wiki/systems/page-ref.md" in paths
