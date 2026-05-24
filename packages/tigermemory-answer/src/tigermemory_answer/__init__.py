"""tigermemory_answer — evidence-first memory answer and grouped search.

This package combines TigerMemory's grouped search helpers with the
evidence-first memory answer orchestration used by MCP, HTTP, CLI, trace, and
eval tools.

It is responsible for:

- grouping Wiki, lessons, onboarding, and Mem0 search results without flattening
  their authority model;
- building answer evidence lists, applying authority scores, weak evidence
  gates, and deterministic conflict scans;
- generating evidence-backed answers through the configured DeepSeek JSON helper;
- redacting secrets and writing answer traces under the repository root.

The package is repository-aware through ``tigermemory_core.REPO_ROOT`` and does
not own storage, MCP transport, HTTP routing, or eval harnesses.
"""
from __future__ import annotations

__all__: list[str] = []
