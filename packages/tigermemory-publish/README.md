# tigermemory-publish

`tigermemory-publish` builds a commit-safe public distribution snapshot from a TigerMemory repository. It copies only explicit allowlist content:

- top-level public project files such as `index.md` and `pyproject.toml`
- a sanitized public `README.md` generated from `packages/tigermemory-publish/src/tigermemory_publish/templates/README.md`
- a sanitized public `AGENTS.md` generated from `packages/tigermemory-publish/src/tigermemory_publish/templates/AGENTS.md`
- a sanitized starter Project Canvas generated from `packages/tigermemory-publish/src/tigermemory_publish/templates/wiki/operations/project-canvas.md`
- `schemas/`
- an explicit allowlist of basic CLI/dashboard tool files plus `tools/static/`
  and `tools/memory_answer/`
- wiki pages whose frontmatter has `public: true`
- runtime config templates ending in `.example`

Normal snapshot planning does not call Git, Mem0, LLMs, or network services.
The explicit `--verify-source-update-smoke` release check creates temporary
local Git repositories only to prove source-first update behavior.
It deliberately does not copy the private source repository's root `README.md`
or `AGENTS.md`; those files can contain workspace-specific operating rules or
private-repository instructions. The public snapshot gets sanitized root
`README.md`, `AGENTS.md`, and starter Project Canvas files from publish package
templates instead.
It also deliberately avoids private or optional tooling directories such as
expense importers, ingestion experiments, and IDE adapters unless they are
separately promoted into the public allowlist.

Before copying files, the CLI audits the publish plan for high-confidence
sensitive material such as private key blocks, long bearer/API tokens, Chinese
ID numbers, and phone numbers in public wiki pages. Dry-run output includes
`sensitive_findings`; non-dry-run publishing exits non-zero and copies nothing
when findings are present.

By default the audit scans only the publish snapshot. To prove that an entire
tracked repository is safe to make public, run:

```powershell
tigermemory-publish --dry-run --json --audit-pii --audit-scope repo
```

Use the repo scope as a release-readiness check, not as the normal snapshot
builder. It blocks on high-confidence private data or local-only paths. Warning-
only test fixtures remain visible for review but no longer make
`repo_public_ready` false; use `repo_warning_free` when you need a completely
quiet audit.

For the stronger public-core split and source-update smoke:

```powershell
tigermemory-publish --dry-run --json --audit-pii --target public-core --split-report --verify-split-smoke --verify-source-update-smoke
```

That command installs the exported public core with a separate instance root,
then proves a cloned public-core source checkout can fast-forward through
`tm update apply --strategy ff-only` without overwriting instance data.

The package is extracted from `tools/tm_publish.py`. The legacy script remains as a compatibility shim:

```powershell
py tools\tm_publish.py --dry-run --json
```

New package usage:

```powershell
pip install -e ./packages/tigermemory-publish
tigermemory-publish --dry-run --json
```

Python callers can import the public helpers directly:

```python
from tigermemory_publish import collect_publish_plan, execute_plan
```

## Repository Root

The package detects the TigerMemory repository root in this order:

1. `TIGERMEMORY_ROOT` environment variable.
2. Walk upward from the installed module until a directory contains both `.git` and `wiki/`.
3. Fallback to the monorepo editable-install layout.
