# TigerMemory

TigerMemory is an LLM-first local Wiki and memory admin starter. It keeps
durable knowledge in Markdown + Git, uses a local SQLite memory store by
default, and is designed for an AI model to help organize, review, and answer
from your own Wiki with sources.

DeepSeek through an OpenAI-compatible endpoint is the recommended first LLM
provider. OpenMemory/Mem0 remains an advanced optional layer for cross-device
memory.

## What You Need

- Python 3.10 or newer.
- Git.
- An LLM API key for the full Wiki Admin experience. DeepSeek is recommended.
- No Docker, WSL, OpenMemory, Qdrant, Caddy, or npm for the first-run local path.

Node/npm is only used by optional subprojects such as the OpenClaw context
engine plugin under `deploy/openclaw-ce/` and ingestion experiments under
`tools/ingest/`. It is not the TigerMemory installer. The public npm package
name `tigermemory` is already used by a different project, so do not use
`npm install tigermemory` for this repository.

## Do Not Install From npm

Do not run `npm install -g tigermemory` for this project. That npm package is a
different Node/TypeScript Claude Code memory server published by another
maintainer. TigerMemory's public basic mode is installed from this Git checkout
with Python:

```powershell
py -m pip install .
```

If TigerMemory later offers an npm installer, it must use a scoped or different
package name and this README will say so explicitly.

## Public Snapshot License Boundary

The public framework snapshot is prepared with AGPL-3.0-or-later metadata,
`LICENSE`, and `THIRD_PARTY_NOTICES.md`. This private development worktree does
not place a root `LICENSE` over all local data because private notes, runtime
state, and non-public research are outside the public release boundary.

## Quick Start From GitHub

```powershell
git clone https://github.com/tigergiant/tigermemory.git
cd tigermemory
py -m pip install .
tm init
tm profile show
tm llm guide
tm llm status
```

The installed command is the `tm` console script. Do not use `python -m tm`;
there is no module entry point with that name.

Expected profile after `tm init`:

```text
effective=local
```

`tm llm status` only checks whether provider environment variables are present.
It does not call the model and does not print secrets. For the recommended
DeepSeek path, set `DEEPSEEK_API_KEY`; optionally set `DEEPSEEK_BASE_URL` and
`DEEPSEEK_MODEL`.

Write, search, and verify local memory without Docker:

```powershell
$env:TIGERMEMORY_PROFILE='local'
"hello local memory" | tm write-memory --agent codex --topic systems
tm search --query "hello local memory" --size 5
tm verify --id "<id printed by write-memory>" --terms "hello local"
```

Search the Markdown Wiki as part of the same basic mode:

```powershell
tm search --scope wiki --query "project canvas"
tm search --scope all --query "local memory"
```

Start the dashboard:

```powershell
tm dashboard
```

Then open `http://127.0.0.1:9777/start` for the beginner start page, or
`http://127.0.0.1:9777/health` for system checks.

The `tm dashboard` quick-start command uses port `9777`. The long-running
private `tm-dashboard` service used by this development repository still uses
port `1998`, so public first-run testing does not collide with that service.

## Which Mode Should I Use?

Start with **local + LLM** unless you already know you need a shared memory
service.

| Need | Use | Requires |
|---|---|---|
| Try TigerMemory as an AI Wiki Admin with local data | `local + LLM` | Python + Git + DeepSeek/OpenAI-compatible key |
| Inspect evidence without model calls | offline fallback | Python + Git |
| Connect multiple machines or IDE agents to the same live memory layer | `hybrid` | OpenMemory/Mem0 service and extra deployment setup |
| Develop the optional OpenClaw Context Engine plugin | optional subproject | Node/npm for that subproject only |

Do not install WSL, Docker, Qdrant, Caddy, or OpenMemory just to try the basic
mode. Those pieces are advanced integrations and can be added later.

## Runtime Profiles

- `local`: default basic mode. Uses Markdown + Git + local SQLite + FTS5
  lexical search. Pair it with an LLM provider for the intended Wiki Admin
  experience.
- `hybrid`: advanced mode. Requires OpenMemory/Mem0 and can use Qdrant/Caddy
  and multi-IDE integrations.

Useful commands:

```powershell
tm profile guide local
tm profile guide hybrid
tm llm guide
tm llm status --json
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
published files. The private source repository's root `README.md`, `AGENTS.md`,
and Project Canvas are not copied into public snapshots; the snapshot receives
sanitized public starter files instead.

This private development repository is not the public artifact. Before making a
whole Git repository public, run the stricter tracked-repo audit:

```powershell
tm publish --dry-run --json --audit-pii --audit-scope repo
```

That command blocks when tracked source still contains high-confidence private
data or local-only paths. Warning-only test fixtures are reported as warnings:
`repo_public_ready=true` means no blocking findings remain, while
`repo_warning_free=false` means review notes still exist. Use the default
snapshot audit for public-package preparation; use `--audit-scope repo` to prove
that the entire tracked repository is safe to expose.

## Development Checks

```powershell
py -m pytest tests\test_tm_cli.py tests\test_tm_io.py tests\test_tm_local_memory.py tests\test_tm_publish.py -q
py tools\tm_io.py lint-repo --json
tm publish --dry-run --json --audit-pii
```

## Current Boundary

The basic local mode is designed for public GitHub use. Advanced integrations
remain optional and should not be required for first-run success.
