# TigerMemory Agent Guide

This repository is the public, local-first TigerMemory snapshot. It is designed
to run with Python, Markdown, Git, and a local SQLite memory store.

## Ground Rules

- Treat Markdown and Git history as the durable source of truth.
- Do not commit secrets, API keys, access tokens, personal data, or machine-local
  paths.
- Prefer the `tm` CLI over ad hoc scripts for normal operations.
- Keep optional integrations separate from the basic local path.
- Keep the installed framework separate from user data. Runtime commands should
  use `TIGERMEMORY_INSTANCE_ROOT` for the user's workspace. Maintainer export
  commands such as `tm publish` use the TigerMemory source/export root
  (`TIGERMEMORY_APP_ROOT` when set).
- Before publishing or sharing a snapshot, run:

```powershell
tm publish --dry-run --json --audit-pii
```

For a whole repository public-readiness check, run:

```powershell
tm publish --dry-run --json --audit-pii --audit-scope repo
```

That stricter repo audit may fail in private development worktrees; it must pass
before making an entire repository public.

## Basic Local Workflow

```powershell
tm init
tm profile show
"hello local memory" | tm write-memory --agent codex --topic systems
tm search --query "hello local memory"
tm ask --offline --query "hello local memory"
```

`tm ask --offline` returns local evidence only. It must not call online Mem0 or
an AI model in the public basic path.

`tm publish` is not part of the normal public runtime workflow. It is a
maintainer-only snapshot/export command and must not read a user's personal
instance root as if it were the source checkout.

## Runtime Profiles

- `local`: default basic mode. No Docker, WSL, OpenMemory, Qdrant, Caddy, or npm
  is required.
- `hybrid`: advanced mode for OpenMemory/Mem0 and multi-runtime integrations.

Use `tm profile guide local` or `tm profile guide hybrid` before changing modes.
