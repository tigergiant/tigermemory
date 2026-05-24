# tigermemory-route

`tigermemory-route` is the memory routing decision engine for TigerMemory. It takes a single memory (`text` + `topic` + `agent`), calls DeepSeek for a quality + sensitivity + transience evaluation, and returns a `RouteDecision` dataclass with one of three targets: `mem0` (high quality, durable), `inbox` (medium quality, sensitive, or needs human review), or `discard` (low quality or transient).

**Purity**: this package has zero file I/O. It does not write to mem0, does not touch git, and does not read or write any local files. The only side effect is one HTTPS call to DeepSeek through `tigermemory_core._call_deepseek_json`. DeepSeek being unreachable produces a fail-warn `unreviewed=true` inbox route, not a crash.

## Public API

```python
from tigermemory_route import RouteDecision, route_memory

decision = route_memory(
    text="...",
    topic="systems",
    agent="cascade",
    source_hint="optional",
)
# decision.route ∈ {"mem0", "inbox", "discard"}
# decision.score ∈ [0, 100]
# decision.as_metadata() → dict suitable for mem0 metadata or inbox frontmatter
```

## Routing rules (first match wins)

1. `is_sensitive == True` → `inbox`
2. `topic == "person"` → `inbox`
3. daily-health index pointer + score ≥ 70 → `mem0`
4. daily-health index pointer + score ≥ 30 → `inbox`
5. curated workflow-radar summary + score ≥ 70 → `mem0`
6. curated workflow-radar summary + score ≥ 30 → `inbox`
7. `is_transient == True` → `discard`
8. score < 30 → `discard`
9. 30 ≤ score < 70 OR `needs_human_review` → `inbox`
10. score ≥ 70 → `mem0`
11. DeepSeek unreachable → `inbox` with `unreviewed=true`

## Compatibility entry

The legacy `tools/tm_route.py` import path is preserved as a shim that re-exports this package. All existing call sites (`tools/tm_route_audit.py` / `tools/tm_memory_ops.py` / `tools/tm_io.py` / `tests/test_tm_route*.py`) keep working without changes.

```powershell
pip install -e ./packages/tigermemory-route
```

## History

Extracted from `tools/tm_route.py` on 2026-05-25 (cascade P2 stage3 抽包 audit recommendation A) as part of TigerMemory's move toward leaf-package modularity.
