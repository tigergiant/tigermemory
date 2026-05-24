# tigermemory-persona

`tigermemory-persona` compiles deterministic onboarding snapshots for TigerMemory agents. It reads stable Markdown sources from the repository and renders the same 30-second, 5-minute, and full snapshots used by the MCP `get_agent_onboarding` tool.

It does not call LLMs, Mem0, vector search, or external services.

The package is extracted from `tools/tm_persona.py`. The legacy script remains as a compatibility shim:

```powershell
py tools\tm_persona.py compile --depth 30s
```

New package usage:

```powershell
pip install -e ./packages/tigermemory-persona
tigermemory-persona compile --depth 30s
```

Python callers can import the public helper directly:

```python
from tigermemory_persona import compile_snapshot
```

## Repository Root

The package detects the TigerMemory repository root in this order:

1. `TIGERMEMORY_ROOT` environment variable.
2. Walk upward from the installed module until a directory contains both `.git` and `wiki/`.
3. Fallback to the monorepo editable-install layout.
