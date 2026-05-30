# TigerMemory

TigerMemory is a local-first LLM wiki and memory runtime. It keeps durable
knowledge in Markdown + Git, and can optionally connect to OpenMemory/Mem0 for
cross-device or vector-enhanced memory.

## Runtime profiles

- `local`: default open-source path. Uses the local SQLite memory backend and
  does not require Docker, OpenMemory, Qdrant, or Caddy.
- `hybrid`: advanced integration path. Keeps OpenMemory/Mem0 as a required
  runtime dependency.

## Quick start from a clone

```powershell
py -m pip install -e .
tm init --profile local
tm profile show
tm lessons search "dashboard mermaid"
tm persona compile --depth 5min
```

Write and search local memory without Docker:

```powershell
$env:TIGERMEMORY_PROFILE='local'
"hello local memory" | tm write-memory --agent codex --topic systems
tm search --query "hello local memory" --size 5
```

Start the dashboard:

```powershell
tm dashboard --host 127.0.0.1 --port 9777
```

## Publish guard

Open-source snapshots are produced by `tigermemory-publish` through:

```powershell
tm publish --dry-run --json --audit-pii
```

The guard blocks high-confidence secrets, PII, and personal path leaks in
published files. Governance files such as `AGENTS.md` can report path leaks as
warnings, but ordinary public wiki pages and shipped tooling are blocked.

## Optional OpenMemory integration

OpenMemory, Qdrant, and Caddy are optional advanced integrations for the
`hybrid` profile. See `deploy/openmemory/README.md` for setup and version pinning
guidance. Do not delete existing Docker volumes during migration; export and
backup first.
