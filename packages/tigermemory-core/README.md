# tigermemory-core

`tigermemory-core` is the standalone Python package for TigerMemory's LLM Wiki core primitives: repository paths, Git/Wiki rules, Mem0/OpenMemory helpers, search utilities, lint checks, and shared constants.

This package is extracted from `tools/tm_core.py` as the first P3-A module boundary step. The implementation intentionally remains a single module in v0.1 so behavior stays compatible while callers move from `import tm_core` to:

```python
import tigermemory_core as tm_core
```

## Install

From the TigerMemory repository root:

```powershell
pip install -e ./packages/tigermemory-core
```

## Repository Root

The package detects the TigerMemory repository root in this order:

1. `TIGERMEMORY_ROOT` environment variable.
2. Walk upward from the installed module until a directory contains both `.git` and `wiki/`.
3. Fallback to the monorepo editable-install layout.

## Source Spec

Extraction contract: `sources/internal-analysis/2026-05-24-codex-overnight-goal-p3-a-tm-core-package.md`.
