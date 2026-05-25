# tigermemory-doctor

`tigermemory-doctor` contains TigerMemory's local health, metrics, and
retention-audit helpers.

It extracts the production logic that historically lived in:

- `tools/tm_metrics.py`
- `tools/tm_agent_doctor.py`
- `tools/tm_retention_audit.py`

The package is repository-oriented. It reads the local worktree, wiki files,
Mem0 exports, health endpoints, lessons logs, and retention signals through
existing TigerMemory helpers.

It does not mutate Mem0 during retention audit.

It does not replace MCP, HTTP, or the review UI.

It keeps the legacy `tools/` entrypoints as compatibility shims.

## Modules

- `tigermemory_doctor.metrics`
  Compiles local project metrics rows and updates the metrics markdown block.
- `tigermemory_doctor.diagnose`
  Runs the agent doctor checks for worktree, tm-http, Mem0, L2 review, lessons,
  and retention warnings.
- `tigermemory_doctor.retention`
  Scores Mem0 records for retention review without deleting or updating them.

## Usage

Install editable packages from the repository root:

```powershell
pip install -e .\packages\tigermemory-core
pip install -e .\packages\tigermemory-doctor
```

Legacy CLI entrypoints remain valid:

```powershell
py tools\tm_retention_audit.py --help
py tools\tm_metrics.py --help
py tools\tm_agent_doctor.py --help
```

Import from Python:

```python
from tigermemory_doctor.retention import run_retention_audit

report = run_retention_audit(source="sample")
```

## Compatibility

Existing callers can continue using `import tm_retention_audit`,
`import tm_metrics`, and `import tm_agent_doctor` when the `tools/` directory is
on `sys.path`, as it is for TigerMemory's existing CLIs and tests.

The compatibility shims replace themselves with the package modules via
`sys.modules[__name__] = _impl`, so monkeypatching and identity checks continue
to point at the real implementation module.
