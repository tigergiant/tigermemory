"""JSON Schema validator entrypoints for TigerMemory protocol drafts."""

from __future__ import annotations


def list_protocols() -> list[str]:
    """Return available protocol names.

    Implemented in the validator task after schema files are added.
    """
    return []


def validate(protocol_name: str, instance: dict) -> list[str]:
    """Validate an instance against a protocol schema.

    Implemented in the validator task after schema files are added.
    """
    raise NotImplementedError("validator is implemented after protocol schemas land")
