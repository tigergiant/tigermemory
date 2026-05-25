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

## L1/L2/L3 Retrieval Layers

L1 is the current default retrieval behavior exposed through TigerMemory's grouped search entrypoint: `search_tigermemory(query, scope, top_k)`.

It keeps the existing grouped intent model, uses wiki hybrid RRF where available, and does not flatten wiki, lessons, onboarding, and Mem0 into one opaque ranking.

L2 adds optional neighborhood expansion on top of that default search result. `follow_backlinks=True` asks the search layer to return single-hop wiki pages that link to the selected hits. `expand_partition=True` asks it to return the most relevant nearby pages from the same wiki partition.

Both L2 switches default to `False`, so existing callers continue to see the exact L1 payload unless they opt in.

L3 belongs to `tigermemory-answer`: `memory_answer(..., task_context={...})` turns selected evidence into Context Pack style fields for agents that need task-aware reading guidance.

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
