# TigerMemory

TigerMemory is an LLM-first local Wiki and memory admin starter. It keeps your
durable knowledge in Markdown + Git, uses local SQLite for a private memory
store, and is designed for an AI model to help organize, review, and answer from
your own Wiki with sources.

The recommended first LLM provider is DeepSeek through an OpenAI-compatible
endpoint. Docker, WSL, OpenMemory, Qdrant, Caddy, npm, and multi-IDE
integrations are optional advanced features, not first-run requirements.

## Requirements

- Python 3.10 or newer.
- Git.
- An LLM API key for the full Wiki Admin experience. DeepSeek is recommended.
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
py -m pip install -e .
tm init
tm profile show
tm dashboard
tm llm guide
tm llm status
tm admin guide
```

Editable install is recommended for the public source-first workflow: code stays
in the Git checkout, while `tm` is available as a normal command. The installed
command is the `tm` console script. Do not use `python -m tm`; there is no
module entry point with that name.

Expected profile after `tm init`:

```text
effective=local
```

`tm dashboard` starts the local web console and opens the beginner setup
wizard. It walks through local vs hybrid mode, reply style, LLM provider setup,
and the main dashboard pages. If your browser does not open automatically, open
`http://127.0.0.1:9777/start` manually.

The setup wizard can save the recommended DeepSeek settings directly into the
local TigerMemory runtime config. The API key is stored under `runtime/`, is not
committed to Git, and is not printed back by the dashboard. `tm llm status` does
not call the model and does not print secrets; it only checks whether provider
settings exist. Command-line environment variables such as `DEEPSEEK_API_KEY`,
`DEEPSEEK_BASE_URL`, and `DEEPSEEK_MODEL` are still supported as an advanced
fallback. Routine JSON/routing work defaults to `deepseek-v4-flash`. The
reviewable Wiki Admin proposal flow defaults to `deepseek-v4-pro` through
`DEEPSEEK_ADMIN_MODEL`, because those drafts become long-term knowledge after
human approval.

## 15-Minute First Run

Use this first-run path when you want to see the full AI Wiki Admin loop without
any advanced services:

1. Install from this checkout with `py -m pip install -e .`.
2. Run `tm init`, then confirm `tm profile show` prints `effective=local`.
3. Run `tm dashboard`; the browser should open the guided setup wizard.
4. On the LLM setup step, paste your DeepSeek API key and click
   `Save and connect TigerMemory`.
5. Run `tm llm status`; it should show that provider settings exist without
   printing your key.
6. Run `tm admin guide` and read the partition guide before writing anything.
7. Put a short note in `notes.md`, then run `tm admin propose`.
8. Inspect the proposal with `tm admin show "<proposal-id>"`.
9. Approve only after checking the suggested partition and target path.
10. Ask `tm ask --query "what did I just add?" --scope wiki` to verify the page
   can be found with citations.

Draft a reviewable Wiki proposal with the LLM Admin flow:

```powershell
Get-Content .\notes.md | tm admin propose --partition projects --title "My Notes"
tm admin list
tm admin show "<proposal-id>"
tm admin approve "<proposal-id>"
```

`tm admin propose` writes a proposal under `runtime/tigermemory/admin-proposals/`.
It does not modify `wiki/`. `tm admin approve` is the explicit user action that
writes the generated Markdown page to the proposed `wiki/<partition>/...` path.

Write, search, and verify local memory:

```powershell
"hello local memory" | tm write-memory --agent codex --topic systems
tm search --query "hello local memory" --size 5
tm verify --id "<id printed by write-memory>" --terms "hello local"
```

Search the Markdown Wiki as part of the same basic mode:

```powershell
tm search --scope wiki --query "AI brain starter"
tm search --scope all --query "local memory"
tm search --scope wiki --query "agent behavior rules"
```

Ask a source-grounded natural-language question with the configured LLM:

```powershell
tm ask --query "what do we know about the AI brain starter?" --scope all
tm ask --query "what rules should my AI assistant follow?" --scope wiki
```

`tm ask` retrieves local memory and Wiki evidence first, then asks the configured
LLM to answer only from that evidence with citations. The Wiki Admin answer role
uses the durable admin model setting (`DEEPSEEK_ADMIN_MODEL`, default
`deepseek-v4-pro`).

Use the offline fallback when you want to inspect evidence without calling a
model:

```powershell
tm ask --offline --query "local memory" --scope all
tm ask --offline --query "agent behavior rules" --scope wiki
```

Offline ask only returns local evidence from SQLite and Markdown. It does not
call an AI model and does not generate a final natural-language answer.

The public starter Wiki uses seven beginner-friendly partitions:
`projects`, `areas`, `resources`, `decisions`, `journal`, `systems`, and
`archive`. Keep private people, health, finance, and secrets out of the public
Wiki; use private local files or a separate secured workspace for those topics.

Start the local dashboard:

```powershell
tm dashboard
```

This opens `http://127.0.0.1:9777/start` for the beginner setup wizard. Use
`tm dashboard --no-open` when you want to start the server without opening a
browser. `http://127.0.0.1:9777/health` remains the system-check page.

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

## Public Core Contract

The public snapshot promises an LLM-first starter with a local evidence
fallback before any advanced integrations. The stable contract for CLI
commands, JSON fields, profile behavior, optional services, and release gates is documented in
`wiki/systems/public-core-contract.md`.

Provider behavior and overclaim boundaries are documented in
`docs/provider-compatibility.md`. The starter recommends DeepSeek first; other
providers should be treated as supported only after their request/response shape
has been tested.

## Public Core vs Your Data

TigerMemory separates the installed framework from your personal memory
workspace:

- The **public core** is the Python package, `tm` CLI, dashboard package, schemas,
  public starter docs, and LLM configuration checks.
- Your **instance root** is your own data workspace: `wiki/`, `data/`, and
  `runtime/`.

Set `TIGERMEMORY_INSTANCE_ROOT` when you want `tm` to operate on a workspace
outside the installed source checkout:

```powershell
$env:TIGERMEMORY_INSTANCE_ROOT="C:\path\to\my-tigermemory"
tm init
tm write-memory --agent codex --topic systems
```

`TIGERMEMORY_ROOT` is still accepted as an older compatibility variable when
`TIGERMEMORY_INSTANCE_ROOT` is not set.

`tm publish` is a maintainer-only export and audit command. It uses the
TigerMemory source/export root (`TIGERMEMORY_APP_ROOT` when set), not your
personal instance root. Normal public usage does not require `tm publish`.

## Updating TigerMemory

When you use TigerMemory from a Git source checkout, update the framework with
the built-in Git-aware updater:

```powershell
tm update status
tm update check
tm update apply --dry-run
tm update apply --strategy ff-only
```

The updater only changes the source checkout. It does not touch your
`TIGERMEMORY_INSTANCE_ROOT` data.

Local source edits are protected:

- uncommitted tracked edits block automatic update;
- untracked files block automatic update until you review them;
- local commits are not overwritten;
- the updater never runs `git reset --hard`, `git clean`, or an automatic stash.

If you changed TigerMemory locally, commit your work to a branch first. A clean
checkout can fast-forward from upstream. Real merge or rebase conflicts are
reported for you to resolve manually.

The dashboard health page and `tm doctor` show the same read-only update status.
The dashboard does not apply code updates from the browser.

## What Ships In This Snapshot

The public snapshot is assembled from declared public modules:

- `public-cli`: the `tm` command and root install files.
- `public-core`: local memory, config, route, search, index, lessons, persona,
  doctor, digest, protocols, source updater, and schemas.
- `public-answer-offline`: evidence-only fallback before or beside LLM calls.
- `public-dashboard`: local dashboard package and static assets; private
  review/promote tools are not shipped in public core.
- `public-publish`: snapshot builder, audit, and release templates.
- `public-wiki-seed`: seven beginner starter Wiki pages and the public
  personal-knowledge taxonomy.

Private dogfood modules and optional hybrid integrations are intentionally not
part of the basic public snapshot.

Generated snapshots also include:

- `MODULES.md`: human-readable public module summary.
- `tigermemory-public-modules.json`: machine-readable module manifest.

`tm publish --module <id>` is an inspection view for maintainers. It is not a
complete release gate and does not replace the full snapshot audit.

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
It blocks on high-confidence non-public data or local-only paths. Warning-only
test fixtures remain visible as review notes: `repo_public_ready=true` means no
blocking findings remain, while `repo_warning_free=false` means the repository is
not completely warning-free.

To inspect the declared release checks for each public module:

```powershell
tm publish --print-checks --json
```

To inspect the full snapshot release evidence before publishing:

```powershell
tm publish --dry-run --json --audit-pii --evidence-report --validate-checks
```

This release evidence command validates the public module manifest, checks that
declared module check paths exist, includes per-module release evidence, and
includes the full snapshot audit result. It is still a dry run and does not
publish files.

Maintainers can also verify the true split smoke:

```powershell
tm publish --dry-run --json --audit-pii --target public-core --split-report --verify-split-smoke --verify-source-update-smoke
```

That check installs the exported public core into a temporary environment and
runs it against a separate empty instance root. It also creates a temporary
public-core Git remote and proves `tm update apply --strategy ff-only` can bring
a cloned checkout forward without touching instance data. It is the proof that
public core code and private user data are no longer the same thing.
