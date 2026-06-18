# TigerMemory

TigerMemory is a local-first LLM wiki and memory runtime. The public snapshot is
designed to start with Python, Markdown, Git, and a local SQLite memory store.
Docker, WSL, OpenMemory, Qdrant, Caddy, npm, and multi-IDE integrations are
optional advanced features, not first-run requirements.

## Requirements

- Python 3.10 or newer.
- Git.
- No Docker, WSL, OpenMemory, Qdrant, Caddy, or npm for the basic mode.

Node/npm is only used by optional subprojects such as the OpenClaw context
engine plugin and ingestion experiments. It is not the TigerMemory installer.
The public npm package name `tigermemory` is already used by a different
project, so do not use `npm install tigermemory` for this repository.

## Do Not Install From npm

Do not run `npm install -g tigermemory` for this project. That npm package is a
different Node/TypeScript Claude Code memory server published by another
maintainer. TigerMemory's public basic mode is installed from this snapshot
checkout with Python:

```powershell
py -m pip install .
```

If TigerMemory later offers an npm installer, it must use a scoped or different
package name and this README will say so explicitly.

## License

The public TigerMemory framework snapshot is distributed under
AGPL-3.0-or-later. See `LICENSE`.

Third-party dependency and vendored dashboard asset notices are listed in
`THIRD_PARTY_NOTICES.md`.

## Quick Start

Run these commands from this repository checkout:

```powershell
py -m pip install .
tm init
tm profile show
```

The installed command is the `tm` console script. Do not use `python -m tm`;
there is no module entry point with that name.

Expected profile after `tm init`:

```text
effective=local
```

Write, search, and verify local memory:

```powershell
"hello local memory" | tm write-memory --agent codex --topic systems
tm search --query "hello local memory" --size 5
tm verify --id "<id printed by write-memory>" --terms "hello local"
```

Search the Markdown Wiki as part of the same basic mode:

```powershell
tm search --scope wiki --query "project canvas"
tm search --scope all --query "local memory"
tm search --scope wiki --query "项目画布"
```

Ask without an online model:

```powershell
tm ask --offline --query "local memory" --scope all
tm ask --offline --query "项目画布" --scope wiki
```

Offline ask only returns local evidence from SQLite and Markdown. It does not
call an AI model and does not generate a final natural-language answer.

Start the local dashboard:

```powershell
tm dashboard
```

Then open `http://127.0.0.1:9777/start` for the beginner start page, or
`http://127.0.0.1:9777/health` for system checks.

## Which Mode Should I Use?

Start with **local** unless you already know you need a shared memory service.

| Need | Use | Requires |
|---|---|---|
| Try TigerMemory, keep personal notes local, search Markdown Wiki and local memory | `local` | Python + Git |
| Connect multiple machines or IDE agents to the same live memory layer | `hybrid` | OpenMemory/Mem0 service and extra deployment setup |
| Develop the optional OpenClaw Context Engine plugin | optional subproject | Node/npm for that subproject only |

Do not install WSL, Docker, Qdrant, Caddy, or OpenMemory just to try the basic
mode. Those pieces are advanced integrations and can be added later.

## Public Core Contract

The public snapshot promises a small local-first core before any advanced
integrations. The stable contract for CLI commands, JSON fields, profile
behavior, optional services, and release gates is documented in
`wiki/systems/public-core-contract.md`.

## Runtime Profiles

- `local`: default basic mode. Uses Markdown + Git + local SQLite + FTS5
  lexical search.
- `hybrid`: advanced mode. Requires OpenMemory/Mem0 and can use Qdrant/Caddy
  and multi-IDE integrations.

Useful commands:

```powershell
tm profile guide local
tm profile guide hybrid
tm profile set hybrid
tm profile set local
```

Before switching a real deployment to `hybrid`, read the OpenMemory deployment
docs in `deploy/openmemory/` and back up existing data. You can always roll
back to local mode with `tm profile set local`.

## Publish Safety

Public snapshots are created by the publish guard. Before sharing a snapshot,
run:

```powershell
tm publish --dry-run --json --audit-pii
```

That default audit scans only the publish snapshot. It blocks high-confidence
secrets, PII, and personal path leaks in published files.

For maintainers, the stricter whole-repository audit is:

```powershell
tm publish --dry-run --json --audit-pii --audit-scope repo
```

Use the repo-scope audit before making an entire development repository public.
It may fail in private worktrees that still contain non-public notes, local
paths, or research pages.
