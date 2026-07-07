"""Phase-2 read cutover: TM_MEMORY_READ_BACKEND=local under profile=hybrid.

Verifies: default OFF reads OpenMemory (unchanged); ON reads local hybrid;
and the fuse — a local read error falls back to OpenMemory ONCE and records
an event, so a direct cutover can never take reads down.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_core  # type: ignore[import-not-found]


@pytest.fixture()
def hybrid_local_db(monkeypatch, tmp_path):
    # profile=hybrid (production shape), but point local DB at a temp file and
    # stub the OpenMemory HTTP call so tests never touch the network.
    monkeypatch.setenv("TIGERMEMORY_PROFILE", tm_core.TIGERMEMORY_PROFILE_HYBRID)
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(tmp_path / "cut.sqlite"))
    monkeypatch.delenv("TM_MEMORY_READ_BACKEND", raising=False)
    monkeypatch.setattr(tm_core, "mem0_base", lambda: "http://stub-openmemory")
    monkeypatch.setattr(tm_core, "mem0_user_id", lambda: "tiger")

    calls = {"openmemory": 0}

    def fake_request(url, **kwargs):
        calls["openmemory"] += 1
        return json.dumps({"items": [{"id": "om-1", "content": "from openmemory"}]})

    monkeypatch.setattr(tm_core, "mem0_request", fake_request)
    return calls


def _seed_local(text: str, topic: str = "systems") -> str:
    # Write directly to the local DB via the local record helper (bypasses the
    # hybrid HTTP write path).
    return tm_core._local_write_memory_record("codex", topic, text)["id"]


def test_default_reads_openmemory(hybrid_local_db):
    assert tm_core.memory_read_backend() == "openmemory"
    payload = json.loads(tm_core.mem0_search("anything", size=5))
    assert hybrid_local_db["openmemory"] == 1
    assert payload["items"][0]["id"] == "om-1"


def test_cutover_reads_local(monkeypatch, hybrid_local_db):
    mid = _seed_local("outbox 退避重试机制")
    monkeypatch.setenv("TM_MEMORY_READ_BACKEND", "local")
    assert tm_core.memory_read_backend() == "local"
    payload = json.loads(tm_core.mem0_search("outbox 退避", size=5))
    assert payload["search_backend"] == "local-cutover"
    assert mid in [r["id"] for r in payload["results"]]
    assert hybrid_local_db["openmemory"] == 0  # OpenMemory not hit at all


def test_fuse_falls_back_to_openmemory_on_local_error(monkeypatch, hybrid_local_db):
    monkeypatch.setenv("TM_MEMORY_READ_BACKEND", "local")

    def boom(*args, **kwargs):
        raise RuntimeError("local search exploded")

    monkeypatch.setattr(tm_core, "local_search_hybrid", boom)
    events = {"recorded": 0}
    monkeypatch.setattr(
        tm_core, "_record_read_cutover_fallback", lambda q, e: events.__setitem__("recorded", 1)
    )

    payload = json.loads(tm_core.mem0_search("query", size=5))
    # Fell back to OpenMemory exactly once, and logged the fallback.
    assert hybrid_local_db["openmemory"] == 1
    assert payload["items"][0]["id"] == "om-1"
    assert events["recorded"] == 1


def test_flag_value_case_insensitive_and_strict(monkeypatch, hybrid_local_db):
    for val in ("local", "LOCAL", " local "):
        monkeypatch.setenv("TM_MEMORY_READ_BACKEND", val)
        assert tm_core.memory_read_backend() == "local"
    for val in ("openmemory", "1", "true", "hybrid", ""):
        monkeypatch.setenv("TM_MEMORY_READ_BACKEND", val)
        assert tm_core.memory_read_backend() == "openmemory"


def test_local_profile_unaffected_by_read_backend(monkeypatch, tmp_path):
    # Under profile=local, reads are always local regardless of the flag.
    monkeypatch.setenv("TIGERMEMORY_PROFILE", tm_core.TIGERMEMORY_PROFILE_LOCAL)
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(tmp_path / "loc.sqlite"))
    monkeypatch.delenv("TM_MEMORY_READ_BACKEND", raising=False)
    mid = _seed_local("精确去重 content_sha256")
    payload = json.loads(tm_core.mem0_search("精确去重", size=5))
    assert mid in [r["id"] for r in payload["results"]]
    assert payload["search_backend"] in ("local", "local+vector")
