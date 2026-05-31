# tigermemory-publish

`tigermemory-publish` builds a commit-safe public distribution snapshot from a TigerMemory repository. It copies only explicit allowlist content:

- top-level public project files such as `README.md`, `index.md`, and `pyproject.toml`
- a sanitized public `AGENTS.md` generated from `packages/tigermemory-publish/src/tigermemory_publish/templates/AGENTS.md`
- `schemas/`
- an explicit allowlist of basic CLI/dashboard tool files plus `tools/static/`
  and `tools/memory_answer/`
- wiki pages whose frontmatter has `public: true`
- runtime config templates ending in `.example`

It does not call Git, Mem0, LLMs, or network services.
It deliberately does not copy the private source repository's root `AGENTS.md`;
that file contains workspace-specific operating rules. The public snapshot gets
a sanitized root `AGENTS.md` from the publish package template instead.
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
builder. Private TigerMemory worktrees are expected to fail this stricter audit
until local paths, private notes, and non-public research pages are removed or
moved to a private repository.

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
