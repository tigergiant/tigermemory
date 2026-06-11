from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.tm_memory_recommend as tm_rec


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "tools" / "tm_memory_recommend.py"


def _write_page(path: Path, title: str, body: str = "", frontmatter: dict[str, object] | None = None) -> None:
    frontmatter = frontmatter or {}
    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, (list, tuple)):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
        elif value is None or value == "":
            lines.append(f"{key}:")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    if body:
        lines.append(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_build(tmp_root: Path, *, output_dir: Path) -> tuple[list[dict[str, object]], dict[str, object]]:
    return tm_rec.build_related_map(
        repo_root=tmp_root,
        output_dir=output_dir,
        wiki_map_path=tmp_root / "runtime" / "llm_wiki" / "wiki_map.jsonl",
    )


def test_tm_memory_recommend_build_excludes_person_and_forbidden_and_explains_reasons(tmp_path: Path) -> None:
    root = tmp_path
    _write_page(
        root / "wiki/systems/source-a.md",
        "Source A",
        frontmatter={
            "title": "Source A",
            "subtopic": ["retrieval"],
            "aliases": ["cache", "index"],
            "summary": "Memory retrieval index surface",
            "keywords": ["memory", "cache", "index"],
            "answer_facets": ["现状", "计划"],
        },
        body="[Related](target-a.md)",
    )
    _write_page(
        root / "wiki/systems/target-a.md",
        "Target A",
        frontmatter={
            "title": "Target A",
            "subtopic": ["retrieval"],
            "aliases": ["cache"],
            "summary": "Cache plan for retrieval index",
            "keywords": ["memory", "cache", "index"],
        },
    )
    _write_page(
        root / "wiki/systems/target-b.md",
        "Target B",
        frontmatter={
            "title": "Target B",
            "subtopic": ["retrieval"],
            "summary": "Another retrieval page",
            "keywords": ["memory", "evidence"],
        },
    )
    _write_page(
        root / "wiki/person/tiger.md",
        "Tiger",
        frontmatter={"title": "Tiger", "subtopic": ["person"]},
    )
    _write_page(
        root / "sources/runtime/skip-me.md",
        "Skip",
        frontmatter={"title": "Runtime Skip", "subtopic": ["runtime"]},
    )

    output_dir = tmp_path / "runtime" / "memory_recommendation"
    edges, meta = _run_build(root, output_dir=output_dir)

    assert output_dir.joinpath("related_map.jsonl").exists()
    assert output_dir.joinpath("related_map.meta.json").exists()
    assert meta["page_count"] > 0
    assert meta["skipped_count"] >= 2  # person + runtime source
    assert "markdown_link" in meta["reason_distribution"]
    assert "shared_alias" in meta["reason_distribution"]
    assert all("person/" not in edge["source_path"] and "person/" not in edge["target_path"] for edge in edges)
    assert all("runtime/" not in edge["source_path"] and "runtime/" not in edge["target_path"] for edge in edges)
    assert all(edge["reasons"] for edge in edges)
    assert all(edge["built_from"] for edge in edges)


def test_tm_memory_recommend_parses_inline_frontmatter_lists(tmp_path: Path) -> None:
    root = tmp_path
    page = root / "wiki/systems/inline.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        """---
title: Inline Lists
aliases: ["Memory Answer", "推荐层"]
subtopic: ["memory-engine", "retrieval"]
keywords: [memory, recommendation]
answer_facets: ["现状", "计划"]
---

# Inline Lists

## 摘要

用于验证 inline YAML-like list 不带括号进入推荐字段。
""",
        encoding="utf-8",
    )

    records, skipped = tm_rec.load_page_records(root, wiki_map_path=root / "runtime" / "llm_wiki" / "wiki_map.jsonl")
    record = records["wiki/systems/inline.md"]

    assert skipped == 0
    assert "memory-engine" in record.subtopics
    assert "retrieval" in record.subtopics
    assert "[\"memory-engine\"" not in record.subtopics
    assert "memory answer" in record.aliases
    assert "推荐层" in record.aliases
    assert "recommendation" in record.keywords
    assert "现状" in record.answer_facets


def test_tm_memory_recommend_wiki_map_missing_fields_and_forbidden_paths(tmp_path: Path) -> None:
    root = tmp_path
    _write_page(
        root / "wiki/systems/ok.md",
        "OK",
        frontmatter={"title": "OK", "summary": "Stable page"},
    )
    wiki_map = root / "runtime" / "llm_wiki" / "wiki_map.jsonl"
    wiki_map.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"path": "wiki/systems/ok.md", "title": "OK Enriched"},
        {"path": "tests/fixtures/leak.md", "title": "Test Leak"},
        {"path": ".tmp/leak.md", "title": "Tmp Leak"},
        {"path": "runtime/leak.md", "title": "Runtime Leak"},
        {"path": "review-artifacts/leak.md", "title": "Review Leak"},
        {"path": "wiki/person/tiger.md", "title": "Person Leak"},
        {"path": "sources/person/tiger.md", "title": "Source Person Leak"},
    ]
    wiki_map.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    records, skipped = tm_rec.load_page_records(root, wiki_map_path=wiki_map)

    assert records["wiki/systems/ok.md"].title == "OK Enriched"
    assert skipped >= 6
    assert all("person/" not in path for path in records)
    assert "tests/fixtures/leak.md" not in records
    assert ".tmp/leak.md" not in records
    assert "runtime/leak.md" not in records
    assert "review-artifacts/leak.md" not in records


def test_tm_memory_recommend_top12_and_deterministic(tmp_path: Path) -> None:
    root = tmp_path
    _write_page(
        root / "wiki/systems/source-core.md",
        "Source Core",
        frontmatter={
            "title": "Source Core",
            "subtopic": ["retrieval"],
            "summary": "Anchor page for deterministic graph",
            "keywords": ["memory", "index"],
        },
    )
    for index in range(14):
        _write_page(
            root / f"wiki/systems/related-{index:02d}.md",
            f"Related {index}",
            frontmatter={
                "title": f"Related {index}",
                "subtopic": ["retrieval"],
                "summary": "Memory retrieval evidence map",
                "keywords": ["memory", "index", "shared"],
            },
        )

    output_dir = tmp_path / "runtime" / "memory_recommendation"
    edges1, meta1 = _run_build(root, output_dir=output_dir)
    payload1 = output_dir.joinpath("related_map.jsonl").read_text(encoding="utf-8")
    meta_payload1 = output_dir.joinpath("related_map.meta.json").read_text(encoding="utf-8")
    source_edges_1 = [edge for edge in edges1 if edge["source_path"] == "wiki/systems/source-core.md"]
    assert len(source_edges_1) <= 12

    edges2, meta2 = _run_build(root, output_dir=output_dir)
    payload2 = output_dir.joinpath("related_map.jsonl").read_text(encoding="utf-8")
    meta_payload2 = output_dir.joinpath("related_map.meta.json").read_text(encoding="utf-8")
    assert payload1 == payload2
    assert meta_payload1 == meta_payload2
    assert meta1["sha256"] == meta2["sha256"]
    assert "generated_at" not in meta1
    assert meta1["edge_count"] == meta2["edge_count"]


def test_tm_memory_recommend_same_partition_and_directory_alone_stays_low_confidence(tmp_path: Path) -> None:
    root = tmp_path
    _write_page(
        root / "wiki/systems/alpha.md",
        "Alpha",
        frontmatter={
            "title": "Alpha",
            "subtopic": ["alpha"],
            "summary": "qwerty asdf zeta.",
        },
    )
    _write_page(
        root / "wiki/systems/beta.md",
        "Beta",
        frontmatter={
            "title": "Beta",
            "subtopic": ["beta"],
            "summary": "uiop hjkl omega.",
        },
    )

    output_dir = tmp_path / "runtime" / "memory_recommendation"
    edges, _ = _run_build(root, output_dir=output_dir)
    pair = [
        edge
        for edge in edges
        if edge["source_path"] == "wiki/systems/alpha.md" and edge["target_path"] == "wiki/systems/beta.md"
    ]

    assert pair
    edge = pair[0]
    assert edge["reasons"] == ["same_directory:wiki/systems", "same_partition:systems"]
    assert edge["built_from"] == ["directory", "subpartition"]
    assert edge["score"] <= 3.0


def test_tm_memory_recommend_stats_and_inspect_output(tmp_path: Path) -> None:
    root = tmp_path
    _write_page(
        root / "wiki/systems/base.md",
        "Base",
        frontmatter={
            "title": "Base",
            "subtopic": ["plan"],
            "summary": "Base evidence map",
            "answer_facets": ["现状"],
        },
    )
    _write_page(
        root / "wiki/systems/next.md",
        "Next",
        frontmatter={
            "title": "Next",
            "subtopic": ["plan"],
            "summary": "Next evidence map",
            "answer_facets": ["现状"],
        },
        body="[Base](base.md)",
    )

    output_dir = tmp_path / "runtime" / "memory_recommendation"
    edges, meta = _run_build(root, output_dir=output_dir)

    assert meta["page_count"] == 2
    assert meta["edge_count"] > 0
    assert isinstance(meta["reason_distribution"], dict)
    assert isinstance(meta["top_isolated_pages"], list)

    inspect_result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--repo-root",
            str(root),
            "--output-dir",
            str(output_dir),
            "inspect",
            "--path",
            "wiki/systems/base.md",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(inspect_result.stdout.strip())
    assert "\\u73b0\\u72b6" in inspect_result.stdout
    assert isinstance(payload, list)
    assert payload and payload[0]["source_path"] == "wiki/systems/base.md"

    stats_result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--repo-root",
            str(root),
            "--output-dir",
            str(output_dir),
            "stats",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    stats_payload = json.loads(stats_result.stdout.strip())
    assert stats_payload["page_count"] == 2
    assert stats_payload["edge_count"] == meta["edge_count"]
    assert stats_payload["version"] == tm_rec.SCHEMA_VERSION
    assert stats_payload["sha256"] == meta["sha256"]
    assert "reason_distribution" in stats_payload
    assert "top_isolated_pages" in stats_payload
