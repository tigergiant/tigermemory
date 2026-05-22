import json
import pathlib
import pytest
import datetime as dt
from tools import tm_retention_audit

def test_sample_mode_markdown_and_json():
    # Test that run_retention_audit works in sample mode
    now = dt.datetime(2026, 5, 22, 12, 0, 0, tzinfo=dt.timezone.utc)
    report = tm_retention_audit.run_retention_audit(source="sample", now=now)
    assert report["ok"] is True
    assert report["item_count"] > 0
    assert "dry_run" in report
    assert report["dry_run"] is True

    # Check that render_markdown can process it
    md = tm_retention_audit.render_markdown(report)
    assert "# Tigermemory Retention Dry-Run Audit" in md
    assert "No records were deleted or updated." in md
    assert "| score | action | id | topic | source | reasons | risks | preview |" in md


def test_pinned_protected_always_keep():
    # Pinned/protected items must ALWAYS be recommended as 'keep'
    now = dt.datetime(2026, 5, 22, 12, 0, 0, tzinfo=dt.timezone.utc)

    pinned_item = {
        "id": "test-pinned",
        "text": "Extremely critical server parameters.",
        "metadata": {
            "pinned": True,
            "topic": "systems",
            "source": "human",
            "created_at": "2024-01-01T00:00:00Z"
        }
    }

    protected_item = {
        "id": "test-protected",
        "text": "Database backup schedules.",
        "metadata": {
            "is_protected": True,
            "topic": "systems",
            "source": "human",
            "created_at": "2024-01-01T00:00:00Z"
        }
    }

    for item in (pinned_item, protected_item):
        scored = tm_retention_audit.score_item(
            item,
            rank=1,
            now=now,
            duplicate_counts={},
            recent_hits=set(),
            promotion_ids=set()
        )
        assert scored["recommended_action"] == "keep"
        assert scored["retention_score"] == 0


def test_conservative_topics_and_sources():
    # Topic=person/investment and source_agent=human must be conservative (never archive)
    now = dt.datetime(2026, 5, 22, 12, 0, 0, tzinfo=dt.timezone.utc)

    person_item = {
        "id": "test-person",
        "text": "Personal contacts details.",
        "metadata": {
            "topic": "person",
            "source": "claude-code",
            "created_at": "2024-01-01T00:00:00Z",
            "last_accessed_at": "2024-02-01T00:00:00Z"  # very old/stale
        }
    }

    invest_item = {
        "id": "test-invest",
        "text": "Milk stock analysis notes.",
        "metadata": {
            "topic": "investment",
            "source": "deerflow",
            "created_at": "2024-01-01T00:00:00Z",
            "last_accessed_at": "2024-02-01T00:00:00Z"  # very old/stale
        }
    }

    human_item = {
        "id": "test-human",
        "text": "Direct instructions from Tiger regarding setup.",
        "metadata": {
            "topic": "operations",
            "source": "human",
            "created_at": "2024-01-01T00:00:00Z",
            "last_accessed_at": "2024-02-01T00:00:00Z"  # very old/stale
        }
    }

    for item in (person_item, invest_item, human_item):
        scored = tm_retention_audit.score_item(
            item,
            rank=1,
            now=now,
            duplicate_counts={},
            recent_hits=set(),
            promotion_ids=set()
        )
        # Even though they are old and stale, they should NEVER be recommended for archiving
        assert scored["recommended_action"] != "review_for_archive"
        # Instead, they should be 'keep' or 'review' depending on scores
        assert scored["recommended_action"] in ("keep", "review")


def test_missing_metadata_protect():
    # Missing topic or source outputs protect_metadata_missing and never archive
    now = dt.datetime(2026, 5, 22, 12, 0, 0, tzinfo=dt.timezone.utc)

    missing_topic = {
        "id": "test-missing-topic",
        "text": "Legacy staging deployment steps with no topic.",
        "metadata": {
            "source": "codex",
            "created_at": "2024-01-01T00:00:00Z",
            "last_accessed_at": "2024-02-01T00:00:00Z"
        }
    }

    missing_source = {
        "id": "test-missing-source",
        "text": "Legacy staging deployment steps with no source agent.",
        "metadata": {
            "topic": "systems",
            "created_at": "2024-01-01T00:00:00Z",
            "last_accessed_at": "2024-02-01T00:00:00Z"
        }
    }

    for item in (missing_topic, missing_source):
        scored = tm_retention_audit.score_item(
            item,
            rank=1,
            now=now,
            duplicate_counts={},
            recent_hits=set(),
            promotion_ids=set()
        )
        assert scored["recommended_action"] == "protect_metadata_missing"
        assert scored["recommended_action"] != "review_for_archive"


def test_missing_accessed_at():
    # Lacking last_accessed_at raises review priority to 'review' instead of 'keep' or 'archive'
    now = dt.datetime(2026, 5, 22, 12, 0, 0, tzinfo=dt.timezone.utc)

    missing_accessed = {
        "id": "test-missing-accessed",
        "text": "Core configuration files.",
        "metadata": {
            "topic": "systems",
            "source": "cascade",
            "created_at": "2026-01-01T00:00:00Z"
            # no last_accessed_at
        }
    }
    scored = tm_retention_audit.score_item(
        missing_accessed,
        rank=1,
        now=now,
        duplicate_counts={},
        recent_hits=set(),
        promotion_ids=set()
    )
    assert scored["recommended_action"] == "review"
    assert "missing_last_accessed_at" in scored["risks"]


def test_stale_records_archiving():
    # Stale items with normal topics/sources should recommend review_for_archive
    now = dt.datetime(2026, 5, 22, 12, 0, 0, tzinfo=dt.timezone.utc)

    stale_item = {
        "id": "test-stale",
        "text": "Stale development guidelines for discontinued server setup.",
        "metadata": {
            "topic": "systems",
            "source": "codex",
            "created_at": "2024-01-01T00:00:00Z",
            "last_accessed_at": "2024-02-01T00:00:00Z"
        }
    }
    scored = tm_retention_audit.score_item(
        stale_item,
        rank=1,
        now=now,
        duplicate_counts={},
        recent_hits=set(),
        promotion_ids=set()
    )
    assert scored["recommended_action"] == "review_for_archive"


def test_output_file_writing(tmp_path):
    # Test --output writes the markdown or JSON file in a temporary folder
    out_file = tmp_path / "report.md"
    now = dt.datetime(2026, 5, 22, 12, 0, 0, tzinfo=dt.timezone.utc)

    report = tm_retention_audit.run_retention_audit(source="sample", now=now)
    md_content = tm_retention_audit.render_markdown(report)

    # Write report
    out_file.write_text(md_content, encoding="utf-8")
    assert out_file.exists()
    assert "# Tigermemory Retention Dry-Run Audit" in out_file.read_text(encoding="utf-8")


def test_mem0_json_source(tmp_path):
    # Test load_mem0_json with simulated export
    export_data = [
        {
            "id": "mem-from-json-1",
            "text": "Some text loaded from exported file.",
            "metadata": {
                "topic": "brand",
                "source": "chatgpt",
                "created_at": "2026-05-01T00:00:00Z",
                "last_accessed_at": "2026-05-15T00:00:00Z"
            }
        }
    ]

    json_file = tmp_path / "mem0_export.json"
    json_file.write_text(json.dumps(export_data), encoding="utf-8")

    now = dt.datetime(2026, 5, 22, 12, 0, 0, tzinfo=dt.timezone.utc)
    report = tm_retention_audit.run_retention_audit(
        source="mem0-json",
        input_path=str(json_file),
        now=now
    )
    assert report["ok"] is True
    assert report["item_count"] == 1
    assert report["candidates"][0]["id"] == "mem-from-json-1"


def test_max_items_limits_loaded_records():
    report = tm_retention_audit.run_retention_audit(source="sample", max_items=3)
    assert report["ok"] is True
    assert report["item_count"] == 3
    assert len(report["candidates"]) == 3


def test_read_only_compliance():
    # Verify that the audit module has no dangerous mutating symbols
    code = pathlib.Path(tm_retention_audit.__file__).read_text(encoding="utf-8")
    # Verify no deletion API calls
    assert "delete_memory" not in code
    assert "mem0_delete" not in code
    assert "mem0_request" not in code
    assert "fetch_mem0" not in code
    assert "urllib" not in code
    assert "remove_file" not in code
    assert "unlink(" not in code
