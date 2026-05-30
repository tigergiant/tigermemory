# TigerMemory

TigerMemory is a local-first LLM wiki and memory runtime. It keeps durable
knowledge in Markdown + Git, uses a local SQLite memory store by default, and
can optionally connect to OpenMemory/Mem0 for advanced cross-device memory.

## What You Need

- Python 3.10 or newer.
- Git.
- No Docker, WSL, OpenMemory, Qdrant, Caddy, or npm for the basic mode.

Node/npm is only used by optional subprojects such as the OpenClaw context
engine plugin under `deploy/openclaw-ce/` and ingestion experiments under
`tools/ingest/`. It is not the TigerMemory installer. The public npm package
name `tigermemory` is already used by a different project, so do not use
`npm install tigermemory` for this repository.

## Quick Start From GitHub

```powershell
git clone https://github.com/tigergiant/tigermemory.git
cd tigermemory
py -m pip install -e .
tm init
tm profile show
```

Expected profile after `tm init`:

```text
effective=local
```

Write, search, and verify local memory without Docker:

```powershell
$env:TIGERMEMORY_PROFILE='local'
"hello local memory" | tm write-memory --agent codex --topic systems
tm search --query "hello local memory" --size 5
tm verify --id "<id printed by write-memory>" --terms "hello local"
```

Start the dashboard:

```powershell
tm dashboard --host 127.0.0.1 --port 9777
```

Then open `http://127.0.0.1:9777/health`.

## Runtime Profiles

- `local`: default basic mode. Uses Markdown + Git + local SQLite + FTS5
  lexical search. It is intended for new users and does not require external
  services.
- `hybrid`: advanced mode. Requires OpenMemory/Mem0 and can use Qdrant/Caddy
  and multi-IDE integrations.

Useful commands:

```powershell
tm profile guide local
tm profile guide hybrid
tm profile set hybrid
tm profile set local
```

Before switching a real deployment to `hybrid`, read `deploy/openmemory/README.md`
and back up existing OpenMemory data. You can always roll back to local mode with
`tm profile set local`.

## Publish Guard

Public snapshots are produced by `tigermemory-publish` through:

```powershell
tm publish --dry-run --json --audit-pii
```

The guard blocks high-confidence secrets, PII, and personal path leaks in
published files. The private source repository's root `AGENTS.md` is not copied
into public snapshots because it contains workspace-specific operating rules.

This private development repository is not the public artifact. Before making a
whole Git repository public, run the stricter tracked-repo audit:

```powershell
tm publish --dry-run --json --audit-pii --audit-scope repo
```

That command is expected to fail while private notes, local paths, or non-public
research pages remain in the repository. Use the default snapshot audit for
public-package preparation; use `--audit-scope repo` only to prove that the
entire tracked repository is safe to expose.

## Development Checks

```powershell
py -m pytest tests\test_tm_cli.py tests\test_tm_io.py tests\test_tm_local_memory.py tests\test_tm_publish.py -q
py tools\tm_io.py lint-repo --json
tm publish --dry-run --json --audit-pii
```

## Current Boundary

The basic local mode is designed for public GitHub use. Advanced integrations
remain optional and should not be required for first-run success.
