---
owner: human
status: active
updated: 2026-07-08
public: true
title: "TigerMemory Public Core Contract"
aliases: ["public core contract", "tm public CLI contract"]
---

# TigerMemory Public Core Contract

## Summary

This document defines the public core boundary that the first public TigerMemory
snapshot can promise. The public product is LLM-first: the full experience
defaults to requiring a DeepSeek / OpenAI-compatible model so the AI can help
organize, review, and answer from the local Wiki; while keeping a local fallback
built from Python, Git, Markdown Wiki, and SQLite/FTS5 for initialization,
self-checks, evidence retrieval, and no-model degradation.

This is not a physical split design. Capabilities not listed as public core may
continue to exist, but must not become implicit prerequisites for installing,
running, or publishing the basic version.

## Public First Release

Runtime dependencies for the full Wiki Admin experience:

| Dependency | Purpose | Commitment |
|---|---|---|
| Python 3.10+ | Run `tm` CLI, SQLite adapter, indexing, and dashboard process | Required |
| Git | Versioned Markdown Wiki and release snapshot source | Required |
| Markdown files | Long-term Wiki fact layer | Required |
| SQLite + FTS5 | Local profile memory storage and lexical search | Required |
| LLM API key | Wiki Admin, natural-language organization, proposal generation, and sourced answers | Required for full experience; DeepSeek is the recommended path |

The first release does not require Docker, WSL, OpenMemory, Qdrant, Caddy, or
any external service. The dashboard may depend on the Python-package-bundled web
server library, but must not require external services to be online for the local
fallback core read-write-search to work.

Without an LLM, TigerMemory is not the full product experience: it can only
perform local writes, searches, verify, dashboard self-checks, and `tm ask
--offline` evidence viewing. After configuring an LLM, `tm ask` defaults to
retrieving local evidence first, then generating a sourced natural-language
answer. Public documentation must not present the no-LLM path as equivalent to
the LLM-first path.

## Public CLI

The stable entry point for the public CLI is `tm`. Public runtime commands use
the instance root (user data root); maintainer export commands use the app root
(TigerMemory source/export root). `TIGERMEMORY_INSTANCE_ROOT` takes precedence
over the legacy compatibility variable `TIGERMEMORY_ROOT`;
`TIGERMEMORY_PROFILE=local` forces the local profile; `TIGERMEMORY_LOCAL_DB` can
override the local SQLite DB path.

Root directory conventions:

| Variable | Purpose | Priority |
|---|---|---|
| `TIGERMEMORY_INSTANCE_ROOT` | Public runtime data instance root, containing the user's own `wiki/`, `data/`, `runtime/` | Highest |
| `TIGERMEMORY_ROOT` | Legacy compatibility; used as instance root when `TIGERMEMORY_INSTANCE_ROOT` is not set | Compatibility |
| `TIGERMEMORY_APP_ROOT` | TigerMemory source/application root; used by `tm update` and maintainer `tm publish` source/export commands | Source |

Public runtime commands must not require the user instance root to also be the
TigerMemory source checkout. Source update commands only affect the app root and
must not touch instance root user data. Maintainer commands must not treat a
user instance root as a source checkout.

Private runtime data convention: user-specific data such as `inbox/`, `log.md`,
local `AGENTS.md` / `CLAUDE.md` / `GEMINI.md`, `memory_exports/`, `sources/`
subdirectories, and any user-created private Wiki partitions belong to the
instance root data, not the public source checkout. Maintainers may keep these
directories and files locally, but they must not enter the public source as
tracked files. Public entry rules are generated from
`packages/tigermemory-publish` templates.

General exit code conventions:

| Exit code | Meaning |
|---|---|
| `0` | Success |
| `2` | Argument, input, or validation error |
| `3` | Publish audit found a blocking finding, e.g. secret/PII/path leak |
| `4` | Backend runtime error, e.g. required service unreachable or local DB operation failed |

Subcommands forwarding to underlying tools may return the underlying tool's exit
code. Public scripts may only rely on the exit code categories listed here, not
undocumented stderr wording.

## CLI Commands Overview

The public CLI `tm` provides these stable subcommands:

- `tm init` - Create local runtime directories and write the profile file.
- `tm profile show/set/guide` - View, set, or explain the runtime profile.
- `tm llm status/guide` - Check or explain LLM provider configuration. Does not
  connect to the network, does not save keys, does not print key plaintext.
- `tm admin guide/propose/list/show/approve/reject` - Minimal LLM Wiki Admin
  loop. Proposals are human-review only; `approve` is the only command that
  writes to `wiki/`.
- `tm write-memory` - Write a memory item from stdin to local SQLite.
- `tm search` - Search local memory, Markdown Wiki, or combined results.
- `tm verify` - Verify by UUID whether a memory record can be read back.
- `tm ask` - Retrieve local evidence first, then generate a sourced
  natural-language answer. `--offline` returns evidence only.
- `tm dashboard` - Start the local dashboard (default port 9777).
- `tm update status/check/apply` - Check and safely update the TigerMemory source
  checkout. Only affects app root, never touches instance root data.
- `tm publish` - Maintainer-only snapshot/export command. Not part of public
  runtime path.

## Public Package / Import Boundary

The `tm` CLI is the most stable public entry point. Python import API stability
is lower than CLI; external users should prioritize depending on CLI and JSON
fields.

Public core packages:

| Package | Public positioning |
|---|---|
| `tigermemory-core` | Local memory, Wiki/search base functions, validation, and shared constants |
| `tigermemory-config` | Profile and configuration parsing |
| `tigermemory-publish` | Snapshot planning, copying, and auditing |
| `tigermemory-dashboard` | Package-backed local dashboard server and static assets |
| `tigermemory-protocols` | schema/protocol validation |
| `tigermemory-index` | Markdown Wiki index compilation |
| `tigermemory-lessons` | Lessons retrieval entry |
| `tigermemory-route` | Memory routing pure functions and review result structures |
| `tigermemory-search` | Lexical/hybrid search helpers; vector capability must be degradable |
| `tigermemory-doctor` | Local diagnostics and retention/health helpers |
| `tigermemory-persona` | Onboarding snapshot compilation |
| `tigermemory-digest` | Digest-related public helpers |
| `tigermemory-answer` | Evidence-first answer/search orchestration; basic offline path must not require private data |

## Two Gates

The Snapshot Release Gate is the publish gate for the first public snapshot. It
only requires the public snapshot to be safe, installable, and auditable:
snapshot audit passes, public boundary test passes, README quickstart smoke
passes, `tm profile show`, `tm llm status`, `tm ask --offline`, online `tm ask`
mocked/smoke, and local workspace dashboard smoke pass.

The True Split Gate is the split gate for future physical repository separation.
It requires repo audit blockers to be migrated, excluded, or accepted as private
instances, at least one round of external user installation feedback, public CLI
with no breaking changes across two consecutive iterations, and private instances
can depend on public core rather than copying source code.

The two gates must not be mixed: a repo audit failure can block the True Split
Gate, but should not automatically block the Snapshot Release Gate; a snapshot
audit failure must block the first public snapshot.

## Non-Commitments

- No commitment that local fallback provides semantic vector recall; local
  profile only promises SQLite/FTS5 lexical search.
- No commitment that the first release connects to OpenMemory, Qdrant, Caddy, or
  any external service.
- `tm ask` online mode is already part of the public LLM-first loop, but is
  still an evidence-grounded starter: no commitment for a full planner, hybrid
  dashboard approval console, OpenMemory/Mem0 semantic layer, or production-grade
  multi-agent autonomous governance.
- No commitment that the public snapshot includes private governance, user
  private data, runtime env, or internal review archives.
- No commitment that Python internal functions are permanently stable; imports,
  fields, and stderr wording not listed in this document are internal
  implementation.

## Sources

- Public TigerMemory starter template.
- Public core CLI contract requirements.
