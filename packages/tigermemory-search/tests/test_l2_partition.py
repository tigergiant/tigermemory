from __future__ import annotations

from pathlib import Path

import tigermemory_answer as answer
import tigermemory_core as tm_core


def write_page(root: Path, rel: str, partition: str, title: str, body: str) -> None:
    path = root / "wiki" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                "owner: codex",
                "status: active",
                "updated: 2026-05-25",
                f"partition: {partition}",
                f"title: {title}",
                "---",
                "",
                body,
                "",
            ]
        ),
        encoding="utf-8",
    )


def stub_primary(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(answer.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(answer.tm_core, "primary_search_scope", lambda _query: "wiki")
    monkeypatch.setattr(
        answer.tm_core,
        "search_wiki_hybrid",
        lambda *_args, **_kwargs: [
            {
                "path": "wiki/systems/page-a.md",
                "title": "Page A",
                "snippet": "tiger memory anchor",
                "score": 20.0,
                "score_breakdown": {"fixture": True},
            }
        ],
    )


def test_search_tigermemory_default_no_partition_field(monkeypatch, tmp_path: Path) -> None:
    write_page(tmp_path, "systems/page-a.md", "systems", "Page A", "Tiger anchor page.")
    stub_primary(monkeypatch, tmp_path)

    result = answer.search_tigermemory("tiger memory", scope="wiki", dogfood_log=None)

    assert "partition_results" not in result


def test_search_tigermemory_expand_partition_same_partition_only(monkeypatch, tmp_path: Path) -> None:
    write_page(tmp_path, "systems/page-a.md", "systems", "Page A", "Tiger anchor page.")
    write_page(tmp_path, "systems/page-d.md", "systems", "Page D", "Tiger memory neighboring systems page.")
    write_page(tmp_path, "investment/page-c.md", "investment", "Page C", "Tiger memory investment page.")
    stub_primary(monkeypatch, tmp_path)

    result = answer.search_tigermemory("tiger memory", scope="wiki", expand_partition=True, dogfood_log=None)
    paths = [hit["path"] for hit in result["partition_results"]]

    assert "wiki/systems/page-d.md" in paths
    assert "wiki/investment/page-c.md" not in paths


def test_search_tigermemory_expand_partition_token_relevance_ranking(monkeypatch, tmp_path: Path) -> None:
    write_page(tmp_path, "systems/page-a.md", "systems", "Page A", "Tiger anchor page.")
    write_page(tmp_path, "systems/page-low.md", "systems", "Page Low", "Tiger appears once.")
    write_page(tmp_path, "systems/page-high.md", "systems", "Page High", "Tiger memory memory context.")
    stub_primary(monkeypatch, tmp_path)

    result = answer.search_tigermemory("tiger memory", scope="wiki", expand_partition=True, dogfood_log=None)
    paths = [hit["path"] for hit in result["partition_results"]]

    assert paths[:2] == ["wiki/systems/page-high.md", "wiki/systems/page-low.md"]


def test_signal_tokens_shared_from_core_filters_generic_terms() -> None:
    assert tm_core.signal_tokens("what tiger memory retrieval") == ["tiger", "retrieval"]
