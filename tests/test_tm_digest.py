from __future__ import annotations

import datetime
import json
import pathlib
import sys
from urllib.parse import parse_qs, urlparse

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_core  # type: ignore[import-not-found]
import tm_digest  # type: ignore[import-not-found]


def _ts(year: int, month: int, day: int, hour: int) -> int:
    return int(datetime.datetime(year, month, day, hour, tzinfo=tm_core.TZ_CN).timestamp())


def test_fetch_memories_for_date_uses_bearer_client_and_size_page(monkeypatch):
    calls: list[str] = []
    in_window = "99e39726-c53a-4c42-b93c-9c54877a3d16"
    iso_window = "fd65b298-05bd-493c-83ce-e37d84447362"
    outside = "11111111-1111-4111-8111-111111111111"
    outside_page = [
        {"id": f"22222222-2222-4222-8222-{i:012d}", "created_at": _ts(2026, 5, 15, 23), "content": "outside"}
        for i in range(99)
    ]

    def fake_request(url, *, timeout):
        calls.append(url)
        qs = parse_qs(urlparse(url).query)
        assert qs["user_id"] == ["tiger"]
        assert qs["size"] == ["100"]
        if qs["page"] == ["1"]:
            return json.dumps({"items": [
                {"id": in_window, "created_at": _ts(2026, 5, 16, 2), "content": "inside"},
                *outside_page,
            ]})
        if qs["page"] == ["2"]:
            return json.dumps({"items": [
                {"id": iso_window, "created_at": "2026-05-16T23:59:59+08:00", "content": "inside iso"},
                {"id": outside, "created_at": "2026-05-17T00:00:00+08:00", "content": "outside iso"},
            ]})
        return json.dumps({"items": []})

    monkeypatch.setattr(tm_digest.tm_core, "mem0_base", lambda: "http://localhost:8765")
    monkeypatch.setattr(tm_digest.tm_core, "mem0_request", fake_request)

    memories = tm_digest._fetch_memories_for_date("2026-05-16")

    assert [m["id"] for m in memories] == [in_window, iso_window]
    assert len(calls) == 2


def test_render_digest_includes_window_complete_memory_ids_and_source_id():
    ids = [
        "fd65b298-05bd-493c-83ce-e37d84447362",
        "99e39726-c53a-4c42-b93c-9c54877a3d16",
    ]
    markdown = tm_digest._render_digest_markdown(
        "2026-05-16",
        {
            "tldr": "ok",
            "facts": [{
                "fact_id": "fact-001",
                "topic": "systems",
                "text": "T-X3.5 verified",
                "source_type": "mem0",
                "source_id": ids[0],
            }],
            "audit_suggestions": [],
        },
        [{"id": mid, "content": "x", "created_at": _ts(2026, 5, 16, 3), "metadata_": {}} for mid in ids],
        [{"filename": "2026-05-16-0001-codex-systems.md", "topic": "systems", "agent": "codex"}],
    )

    assert "window_start_local: 2026-05-16T00:00:00+08:00" in markdown
    assert "window_end_local: 2026-05-16T23:59:59.999999+08:00" in markdown
    for mid in ids:
        assert mid in markdown
    assert f"(source: {ids[0]})" in markdown
    assert "..." not in markdown
