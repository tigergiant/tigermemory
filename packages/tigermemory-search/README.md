# tigermemory-search

`tigermemory-search` contains TigerMemory's local embedding and hybrid retrieval index helpers.

It extracts the production search backend logic that historically lived in:

- `tools/tm_embed_index.py`
- `tools/tm_hier_index.py`
- `tools/tm_doubao_hybrid_index.py`
- `tools/tm_qwen_v4_index.py`

The package stays repository-oriented: it reads Markdown knowledge, sources, and runtime embedding index files through `tigermemory_core.REPO_ROOT`.

It does not create a new database.

It does not change ranking policy.

It does not replace the legacy CLI entrypoints.

The old `tools/tm_*.py` files remain compatibility shims.

## Modules

- `tigermemory_search.embed`
- `tigermemory_search.hier`
- `tigermemory_search.doubao`
- `tigermemory_search.qwen`

## Usage

Install editable packages from the repository root:

```powershell
pip install -e .\packages\tigermemory-core
pip install -e .\packages\tigermemory-search
```

Legacy calls remain valid:

```powershell
py tools\tm_embed_index.py stats --scope wiki
```

Import from Python:

```python
from tigermemory_search import embed

hits = embed.search("memory answer evidence policy", k=3)
```

## Compatibility

Package extraction must not alter embedding index paths, cached vector formats, hybrid retrieval ranking, or evaluation results.
