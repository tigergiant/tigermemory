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
tm admin guide
"hello local memory" | tm write-memory --agent codex --topic systems
tm search --query "hello local memory"
tm ask --query "hello local memory"
tm ask --offline --query "hello local memory"
```

`tm llm status` only checks whether provider environment variables exist; it
must not print secrets. `tm ask` retrieves local evidence first, then asks the
configured LLM to answer with citations. `tm ask --offline` returns local
evidence only and must not call online Mem0 or an AI model.

Recommended DeepSeek defaults are role-based: routine JSON/routing uses
`DEEPSEEK_MODEL=deepseek-v4-flash`; `tm admin propose` uses
`DEEPSEEK_ADMIN_MODEL=deepseek-v4-pro` for durable Wiki Admin drafts.

## Wiki Admin Role

When acting as the TigerMemory Wiki Admin:

- Prefer durable Markdown pages for stable knowledge.
- Prefer local memory for recent, short-lived conversation context.
- Generate reviewable proposals before changing long-term Wiki facts.
- Use `tm admin propose` to draft into `runtime/tigermemory/admin-proposals/`;
  only `tm admin approve` may write the generated page to `wiki/`.
- Treat proposals as human-review only. Model-generated proposals must carry a
  route, source references, sensitivity, stability, and evidence quality; they
  are not approval.
- Use the public starter taxonomy:
  - `projects`: active projects with goals and next steps.
  - `areas`: long-running responsibilities and habits.
  - `resources`: references, tutorials, and reusable notes.
  - `decisions`: durable decisions with rationale.
  - `journal`: dated reviews and recent progress.
  - `systems`: tool setup, agent behavior rules, and workflows.
  - `archive`: completed or outdated material kept for context.
- Route raw clips, long excerpts, and source captures to a proposal/source lane
  first; do not turn raw source text into Wiki truth without review.
- Route private people data, secrets, health data, financial records, passwords,
  tokens, and identity numbers away from the public Wiki.
- Reject low-value duplicates, empty placeholders, and unsafe private material
  instead of storing them.
- Include source paths or evidence snippets when answering.
- Keep private data, person notes, investment data, runtime env files, and
  review archives out of public snapshots.
- Reject secrets, passwords, API tokens, private keys, identity numbers, and
  raw private personal records instead of storing them in the Wiki.

## Connecting AI Tools

AI tools should start in read-only mode:

- Read `README.md`, this `AGENTS.md`, and `docs/connect-your-ai-tools.md`.
- Prefer `tm ask --query ...` for source-grounded answers.
- Use `tm search` when you need raw evidence and can inspect it yourself.
- Use MCP only with `--role=reader` and `--tool-profile=memory` unless the user
  explicitly opts into an advanced workflow.
- Do not run `tm admin approve` automatically. Approval is a user action.

Optional hooks and MCP examples live under `docs/examples/`; they are templates,
not active policy until the user copies and enables them.

`tm publish` is not part of the normal public runtime workflow. It is a
maintainer-only snapshot/export command and must not read a user's personal
instance root as if it were the source checkout.

## Runtime Profiles

- `local`: default starter mode. No Docker, WSL, OpenMemory, Qdrant, Caddy, or
  npm is required; pair it with an LLM provider for the intended product
  experience.
- `hybrid`: advanced mode for OpenMemory/Mem0 and multi-runtime integrations.

Use `tm profile guide local` or `tm profile guide hybrid` before changing modes.
