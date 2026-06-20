# TigerMemory Agent Guide

This repository is the public TigerMemory LLM Wiki Admin starter. It is designed
to run with Python, Markdown, Git, a local SQLite memory store, and a user-owned
LLM provider. DeepSeek via an OpenAI-compatible endpoint is the recommended
first provider.

## Ground Rules

- Treat Markdown and Git history as the durable source of truth.
- Use the configured LLM to help organize, summarize, propose Wiki updates, and
  answer with sources. Do not treat model output as durable truth without
  evidence.
- Do not commit secrets, API keys, access tokens, personal data, or machine-local
  paths.
- Prefer the `tm` CLI over ad hoc scripts for normal operations.
- Keep optional integrations separate from the public LLM-first local path.
- Keep the installed framework separate from user data. Runtime commands should
  use `TIGERMEMORY_INSTANCE_ROOT` for the user's workspace. Maintainer export
  commands such as `tm publish` use the TigerMemory source/export root
  (`TIGERMEMORY_APP_ROOT` when set).
- Source updates must go through `tm update status/check/apply` or normal Git
  commands. Do not overwrite user source edits, run `git reset --hard`, run
  `git clean`, or silently stash local work during an update.
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

## Basic LLM-First Workflow

```powershell
tm init
tm profile show
tm llm guide
tm llm status
"hello local memory" | tm write-memory --agent codex --topic systems
tm search --query "hello local memory"
tm ask --offline --query "hello local memory"
```

`tm llm status` only checks whether provider environment variables exist; it
must not print secrets. `tm ask --offline` returns local evidence only. It must
not call online Mem0 or an AI model and should be treated as fallback evidence,
not the full Wiki Admin answer path.

## Wiki Admin Role

When acting as the TigerMemory Wiki Admin:

- Prefer durable Markdown pages for stable knowledge.
- Prefer local memory for recent, short-lived conversation context.
- Generate reviewable proposals before changing long-term Wiki facts.
- Include source paths or evidence snippets when answering.
- Keep private data, person notes, investment data, runtime env files, and
  review archives out of public snapshots.

`tm publish` is not part of the normal public runtime workflow. It is a
maintainer-only snapshot/export command and must not read a user's personal
instance root as if it were the source checkout.

## Runtime Profiles

- `local`: default starter mode. No Docker, WSL, OpenMemory, Qdrant, Caddy, or
  npm is required; pair it with an LLM provider for the intended product
  experience.
- `hybrid`: advanced mode for OpenMemory/Mem0 and multi-runtime integrations.

Use `tm profile guide local` or `tm profile guide hybrid` before changing modes.
