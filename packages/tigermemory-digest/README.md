# tigermemory-digest

`tigermemory-digest` contains TigerMemory's local digest and review report helpers.

It extracts production logic that historically lived in:

- `tools/tm_memory_reflection.py`
- `tools/tm_digest.py`
- `tools/tm_summary_proposals.py`
- `tools/tm_open_digest.py`

The package stays repository-oriented: it reads and writes daily digest files, proposal material, inbox review metadata, and generated report artifacts through `tigermemory_core.REPO_ROOT`.

It does not change memory routing policy.

It does not change review UI behavior.

It does not replace the legacy CLI entrypoints.

The old `tools/tm_*.py` files remain compatibility shims.

## Modules

- `tigermemory_digest.reflection`
- `tigermemory_digest.digest`
- `tigermemory_digest.summary`
- `tigermemory_digest.open_digest`

## Usage

Install editable packages from the repository root:

```powershell
pip install -e .\packages\tigermemory-core
pip install -e .\packages\tigermemory-search
pip install -e .\packages\tigermemory-digest
```

Legacy calls remain valid:

```powershell
py tools\tm_memory_reflection.py daily --date 2026-05-25
```

Import from Python:

```python
from tigermemory_digest import reflection

report_path = reflection.render_daily_report("2026-05-25")
```

## Compatibility

Package extraction must not alter digest rendering, proposal parsing, inbox review grouping, or browser-opening safety behavior.
