from __future__ import annotations

import inspect

import pytest
import jsonschema

from tigermemory_protocols import validator


def valid_wiki_page() -> dict:
    return {
        "owner": "codex",
        "status": "active",
        "updated": "2026-05-25",
        "partition": "systems",
        "title": "Wiki Page Test",
        "subtopic": ["protocols"],
    }


def valid_memory_item() -> dict:
    return {
        "memory_id": "mem-protocol-001",
        "source": "mem0",
        "created_at": "2026-05-25T20:10:00+08:00",
        "content": "Memory content test for protocol validation.",
        "sensitivity": "internal",
        "topic": "systems",
    }


def valid_agent_policy() -> dict:
    return {
        "policy_id": "policy-protocol-001",
        "applies_to": ["codex", "cascade"],
        "rules": {
            "before_answer": ["Read the relevant wiki page first."],
            "forbidden": ["Do not invent verification results."],
            "required": ["Report any failed validation command."],
        },
        "precedence": 10,
        "version": "0.1.0",
    }


def valid_context_pack() -> dict:
    return {
        "task": "Create protocol validation tests",
        "user_intent": "Make schema validation repeatable.",
        "must_read": [
            {
                "path": "wiki/systems/tigermemory-product-vision.md",
                "reason": "Defines protocol intent.",
            }
        ],
        "risks": [{"risk": "Schema drift", "severity": "medium"}],
        "missing_context": ["No external consumers yet."],
    }


def test_wiki_page_valid_returns_no_errors() -> None:
    assert validator.validate("wiki_page", valid_wiki_page()) == []


def test_wiki_page_missing_owner_reports_required_field() -> None:
    instance = valid_wiki_page()
    instance.pop("owner")
    errors = validator.validate("wiki_page", instance)
    assert any("'owner' is a required property" in error for error in errors)


def test_wiki_page_wrong_status_enum_reports_allowed_values() -> None:
    instance = valid_wiki_page()
    instance["status"] = "published"
    errors = validator.validate("wiki_page", instance)
    assert any("'published' is not one of" in error for error in errors)


def test_memory_item_valid_returns_no_errors() -> None:
    assert validator.validate("memory_item", valid_memory_item()) == []


def test_memory_item_missing_content_reports_required_field() -> None:
    instance = valid_memory_item()
    instance.pop("content")
    errors = validator.validate("memory_item", instance)
    assert any("'content' is a required property" in error for error in errors)


def test_memory_item_wrong_sensitivity_enum_reports_allowed_values() -> None:
    instance = valid_memory_item()
    instance["sensitivity"] = "secret"
    errors = validator.validate("memory_item", instance)
    assert any("'secret' is not one of" in error for error in errors)


def test_agent_policy_valid_returns_no_errors() -> None:
    assert validator.validate("agent_policy", valid_agent_policy()) == []


def test_agent_policy_missing_rules_reports_required_field() -> None:
    instance = valid_agent_policy()
    instance.pop("rules")
    errors = validator.validate("agent_policy", instance)
    assert any("'rules' is a required property" in error for error in errors)


def test_agent_policy_unknown_applies_to_reports_oneof_failure() -> None:
    instance = valid_agent_policy()
    instance["applies_to"] = ["unknown-agent"]
    errors = validator.validate("agent_policy", instance)
    assert any("not valid under any of the given schemas" in error for error in errors)


def test_context_pack_valid_returns_no_errors() -> None:
    assert validator.validate("context_pack", valid_context_pack()) == []


def test_context_pack_missing_must_read_reports_required_field() -> None:
    instance = valid_context_pack()
    instance.pop("must_read")
    errors = validator.validate("context_pack", instance)
    assert any("'must_read' is a required property" in error for error in errors)


def test_context_pack_risks_missing_severity_reports_required_field() -> None:
    instance = valid_context_pack()
    instance["risks"] = [{"risk": "Missing severity should fail."}]
    errors = validator.validate("context_pack", instance)
    assert any("'severity' is a required property" in error for error in errors)


def test_list_protocols_returns_four_schema_names() -> None:
    assert validator.list_protocols() == [
        "agent_policy",
        "context_pack",
        "memory_item",
        "wiki_page",
    ]


def test_unknown_protocol_raises_value_error_with_available_names() -> None:
    with pytest.raises(ValueError, match="unknown protocol 'missing_protocol'"):
        validator.validate("missing_protocol", {})


def test_validate_empty_instance_returns_multiple_required_errors() -> None:
    errors = validator.validate("context_pack", {})
    assert len(errors) >= 5
    assert any("'task' is a required property" in error for error in errors)


def test_validator_uses_json_schema_2020_12_draft() -> None:
    schema = validator._load_schema("wiki_page")
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_unique_test_names_are_schema_specific() -> None:
    names = [
        name
        for name, value in globals().items()
        if name.startswith("test_") and inspect.isfunction(value)
    ]
    assert len(names) == len(set(names))
    assert all(name != "test_loads_X_works" for name in names)
