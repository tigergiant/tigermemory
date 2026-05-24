# tigermemory-index

`tigermemory-index` is the standalone deterministic index compiler for TigerMemory's Markdown LLM Wiki. It reads `wiki/<partition>/*.md`, preserves the curated preamble in each partition `index.md`, and regenerates the `## 页面` listing plus the experimental `index-by-subtopic.md` preview.

The package is extracted from `tools/tm_compile_index.py`. The legacy script remains as a compatibility shim, so existing commands keep working:

```powershell
py tools\tm_compile_index.py check --partition systems
```

New package usage:

```powershell
pip install -e ./packages/tigermemory-index
tigermemory-index check --partition systems
```

Python callers can import the public helpers directly:

```python
from tigermemory_index import compile_partition_index, render_preview
```

## Repository Root

The package detects the TigerMemory repository root in this order:

1. `TIGERMEMORY_ROOT` environment variable.
2. Walk upward from the installed module until a directory contains both `.git` and `wiki/`.
3. Fallback to the monorepo editable-install layout.

The compiler is stdlib-only and does not call Mem0, LLMs, Git, or network services.
