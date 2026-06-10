from __future__ import annotations

import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "tigermemory-core" / "src"))

import tm_llm_wiki_map as wiki_map  # type: ignore[import-not-found]


def _write(path: pathlib.Path, text: str) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_build_record_extracts_schema_fields(tmp_path):
    page = _write(
        tmp_path / "wiki" / "systems" / "session-handoff-protocol.md",
        """---
title: Session Handoff Protocol
aliases:
  - 会话交接协议
  - session handoff
subtopic: [memory-engine, handoff]
status: active
updated: 2026-06-10
---

# Session Handoff Protocol

## 摘要

会话交接协议用于避免 Codex 收工漏写记忆，并通过 pending-handoff.json 做兜底提示。

## 验收

运行 tools/tm_answer_eval.py 和 memory_answer plan_query 检查 P3.7。
""",
    )

    record = wiki_map.build_record_for_file(page, repo_root=tmp_path).to_dict()

    assert wiki_map.validate_map_record(record) == []
    assert record["path"] == "wiki/systems/session-handoff-protocol.md"
    assert record["source_surface"] == "wiki"
    assert record["partition"] == "systems"
    assert record["title"] == "Session Handoff Protocol"
    assert "会话交接协议" in record["aliases"]
    assert "memory-engine" in record["subtopic"]
    assert "会话交接协议用于避免" in record["summary"]
    assert "摘要" in record["headings"]
    assert "验收" in record["answer_facets"]
    assert "tools/tm_answer_eval.py" in record["typed_entities"]["paths"]
    assert "plan_query" in record["typed_entities"]["tools"]
    assert "P3.7" in record["typed_entities"]["phases"]
    assert record["cjk_bridge_terms"]
    assert record["extraction_sources"]


def test_build_records_skips_person_and_forbidden_paths(tmp_path):
    _write(tmp_path / "wiki" / "systems" / "ok.md", "# OK\n\n## 摘要\n\n可索引。")
    _write(tmp_path / "wiki" / "person" / "secret.md", "# Person\n\n个人资料。")
    _write(tmp_path / "sources" / "person" / "secret.md", "# Source Person\n\n个人资料。")
    _write(tmp_path / ".tmp" / "leak.md", "# Leak\n\n不应进入地图。")
    _write(tmp_path / "runtime" / "llm_wiki" / "old-map.md", "# Runtime\n\n不应进入地图。")
    _write(tmp_path / "tests" / "fixtures" / "eval.md", "# Test Fixture\n\n不应进入地图。")

    records, skipped = wiki_map.build_records(tmp_path)

    assert [record.path for record in records] == ["wiki/systems/ok.md"]
    assert {item["path"] for item in skipped} == {
        "wiki/person/secret.md",
        "sources/person/secret.md",
    }
    assert wiki_map.is_forbidden_input_path("tests/fixtures/memory_answer_diagnosis_100.jsonl")
    assert wiki_map.is_forbidden_input_path(".tmp/llm-wiki-map-quality-report.md")
    assert wiki_map.is_forbidden_input_path("runtime/llm_wiki/wiki_map.jsonl")


def test_build_records_includes_only_root_agents_allowlist(tmp_path):
    _write(
        tmp_path / "AGENTS.md",
        """---
title: Agent Rules
aliases: [变基出现冲突怎么办, rebase 冲突]
---

# AGENTS.md

## 摘要

rebase 过程中出现 conflict 时立即 abort，不要 continue。
""",
    )
    _write(tmp_path / "README.md", "# README\n\n不是地图输入。")
    _write(tmp_path / "wiki" / "systems" / "ok.md", "# OK\n\n## 摘要\n\n可索引。")

    records, skipped = wiki_map.build_records(tmp_path)
    by_path = {record.path: record for record in records}

    assert "AGENTS.md" in by_path
    assert "README.md" not in by_path
    assert by_path["AGENTS.md"].source_surface == "wiki"
    assert by_path["AGENTS.md"].partition == "systems"
    assert "变基出现冲突怎么办" in by_path["AGENTS.md"].aliases
    assert "基出" not in by_path["AGENTS.md"].cjk_bridge_terms
    assert skipped == []


def test_root_agents_scoring_prefers_rule_queries_without_date_pollution(tmp_path):
    agents = _write(
        tmp_path / "AGENTS.md",
        """---
title: Agent Rules
aliases: [变基出现冲突怎么办, rebase 冲突]
---

# AGENTS.md

## 摘要

rebase 过程中出现 conflict 时立即 abort，不要 continue。
dashboard 1998 是正式服务端口，tm-http 8790 是 HTTP router。
updated: 2026-06-10
""",
    )
    generic = _write(
        tmp_path / "wiki" / "systems" / "annual-review.md",
        """---
title: 2026 年度回顾
---

# 2026 年度回顾

## 摘要

这个页面记录 2026 年项目回顾，与 agent 开工规则无关。
""",
    )
    records = [
        wiki_map.build_record_for_file(agents, repo_root=tmp_path).to_dict(),
        wiki_map.build_record_for_file(generic, repo_root=tmp_path).to_dict(),
    ]

    rule_hits = wiki_map.map_recall("变基出现冲突怎么办", records=records, limit=2)
    date_hits = wiki_map.map_recall("2026 年度回顾", records=records, limit=2)

    assert rule_hits[0]["path"] == "AGENTS.md"
    assert date_hits[0]["path"] == "wiki/systems/annual-review.md"


def test_root_agents_does_not_outrank_specific_pages_on_broad_terms(tmp_path):
    agents = _write(
        tmp_path / "AGENTS.md",
        """---
title: Agent Rules
---

# AGENTS.md

## 摘要

agent 开工、提交、推送和 hook 规则。
MCP、dashboard 1998 是正式服务端口，tm-http 8790 是 HTTP router。
""",
    )
    dashboard = _write(
        tmp_path / "wiki" / "systems" / "dashboard-service.md",
        """---
title: Dashboard Service
aliases: [dashboard service, dashboard 1998]
---

# Dashboard Service

## 摘要

dashboard service 页面说明 MCP dashboard、1998 服务端口、页面加载和运行检查。
""",
    )
    records = [
        wiki_map.build_record_for_file(agents, repo_root=tmp_path).to_dict(),
        wiki_map.build_record_for_file(dashboard, repo_root=tmp_path).to_dict(),
    ]

    broad_hits = wiki_map.map_recall("dashboard service", records=records, limit=2)
    service_hits = wiki_map.map_recall("MCP dashboard service 端口", records=records, limit=2)
    rule_hits = wiki_map.map_recall("agent 提交推送 hook 规则", records=records, limit=2)

    assert broad_hits[0]["path"] == "wiki/systems/dashboard-service.md"
    assert service_hits[0]["path"] == "wiki/systems/dashboard-service.md"
    assert rule_hits[0]["path"] == "AGENTS.md"


def test_write_map_is_stable_and_recall_ranks_candidate(tmp_path):
    _write(
        tmp_path / "wiki" / "systems" / "memory-answer-development-plan.md",
        """---
title: Memory Answer 开发计划
aliases: [记忆问答开发计划, natural recall]
---

# Memory Answer 开发计划

## 摘要

这个页面记录 memory_answer 自然语言召回、检索评测和证据门控计划。
""",
    )
    _write(
        tmp_path / "wiki" / "operations" / "project-canvas.md",
        "# Project Canvas\n\n项目星图和 dashboard 状态。",
    )
    records, skipped = wiki_map.build_records(tmp_path)
    map_path = tmp_path / "runtime" / "llm_wiki" / "wiki_map.jsonl"
    meta_path = tmp_path / "runtime" / "llm_wiki" / "wiki_map.meta.json"

    first_meta = wiki_map.write_map(records, skipped, map_path=map_path, meta_path=meta_path)
    first_payload = map_path.read_text(encoding="utf-8")
    second_meta = wiki_map.write_map(records, skipped, map_path=map_path, meta_path=meta_path)

    assert first_payload == map_path.read_text(encoding="utf-8")
    assert first_meta["sha256"] == second_meta["sha256"]
    loaded = wiki_map.load_map(map_path)
    hits = wiki_map.map_recall("为什么自然语言召回找不到记忆问答开发计划", records=loaded, limit=5)
    assert hits[0]["path"] == "wiki/systems/memory-answer-development-plan.md"
    assert hits[0]["score"] > 0


def test_quality_report_omits_raw_eval_query_canary(tmp_path):
    _write(tmp_path / "wiki" / "systems" / "plain.md", "# Plain Page\n\n没有摘要的页面。")
    records, skipped = wiki_map.build_records(tmp_path)
    map_path = tmp_path / "runtime" / "llm_wiki" / "wiki_map.jsonl"
    meta_path = tmp_path / "runtime" / "llm_wiki" / "wiki_map.meta.json"
    wiki_map.write_map(records, skipped, map_path=map_path, meta_path=meta_path)
    report_path = tmp_path / ".tmp" / "llm-wiki-map-quality-report.md"

    wiki_map.write_quality_report([record.to_dict() for record in records], output=report_path)
    payload = report_path.read_text(encoding="utf-8")

    assert "canaryrawquerytoken_summary_only_20260609" not in payload
    assert "wiki/systems/plain.md" in payload
    stats = wiki_map.summarize_records([record.to_dict() for record in records], skipped)
    assert stats["missing_summary_count"] == 1
    assert json.loads(meta_path.read_text(encoding="utf-8"))["page_count"] == 1
