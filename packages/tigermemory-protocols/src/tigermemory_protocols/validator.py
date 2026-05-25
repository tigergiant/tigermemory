"""JSON Schema validator entrypoints for TigerMemory protocol drafts."""

from __future__ import annotations

from importlib import resources
from importlib.resources.abc import Traversable
from typing import Any

import jsonschema
import yaml

SCHEMA_PACKAGE = "tigermemory_protocols.schemas"
_SCHEMA_CACHE: dict[str, dict[str, Any]] = {}


def _schema_root() -> Traversable:
    return resources.files(SCHEMA_PACKAGE)


def list_protocols() -> list[str]:
    """Return available protocol names."""
    return sorted(
        path.name.removesuffix(".yaml")
        for path in _schema_root().iterdir()
        if path.name.endswith(".yaml")
    )


def _load_schema(protocol_name: str) -> dict[str, Any]:
    if protocol_name in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[protocol_name]

    schema_path = _schema_root().joinpath(f"{protocol_name}.yaml")
    if not schema_path.is_file():
        available = ", ".join(list_protocols())
        raise ValueError(f"unknown protocol '{protocol_name}'; available: {available}")

    loaded = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"schema '{protocol_name}' did not load as an object")

    jsonschema.Draft202012Validator.check_schema(loaded)
    _SCHEMA_CACHE[protocol_name] = loaded
    return loaded


def validate(protocol_name: str, instance: dict) -> list[str]:
    """Validate an instance against a protocol schema."""
    schema = _load_schema(protocol_name)
    validator = jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
    )
    errors = sorted(validator.iter_errors(instance), key=lambda item: list(item.path))
    return [error.message for error in errors]
