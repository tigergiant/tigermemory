# tigermemory-answer

`tigermemory-answer` contains TigerMemory's evidence-first memory answerer and grouped search helpers.

It extracts the production logic that historically lived in `tools/tm_answer.py` and `tools/tm_search.py`.

The package is still repository-oriented: it reads Wiki, lessons, onboarding, Mem0, answer trace, and eval fixtures through `tigermemory_core.REPO_ROOT`.

It does not create a new database.

It does not replace MCP or HTTP.

It does not change ranking policy.

It exposes the same answer/search behavior through importable Python functions.

The old `tools/tm_answer.py` and `tools/tm_search.py` entrypoints remain compatibility shims.

## Public API

The answer API includes:

- `memory_answer_core`
- `decide_injection_eligibility`
- `trim_evidence_for_prompt`
- `redact_secrets`
- `query_hash`
- `normalize_run_id`
- `classify_query`
- `expand_queries`
- `expand_evidence`
- `scan_conflicts`
- `build_arg_parser`
- `cmd_answer`
- `main`

The search API includes:

- `format_search_hit`
- `search_tigermemory`

Private helpers are kept in the same module for compatibility during extraction.

They are implementation details unless explicitly documented later.

## Usage

Install editable packages from the repository root:

```powershell
pip install -e .\packages\tigermemory-core
pip install -e .\packages\tigermemory-lessons
pip install -e .\packages\tigermemory-persona
pip install -e .\packages\tigermemory-answer
```

Run the answer CLI:

```powershell
tigermemory-answer answer "memory_answer 现在有哪些证据门禁？"
```

Legacy calls remain valid:

```powershell
py tools\tm_answer.py answer "memory_answer 现在有哪些证据门禁？"
```

Import from Python:

```python
import tigermemory_answer as tm_answer

result = tm_answer.memory_answer_core("P3-C 计划是什么？", include_trace=False)
```

## L3 Context Pack

`memory_answer` is the L3 layer when the caller passes a `task_context` dictionary such as `{"task": "...", "intent": "review"}`.

In that mode the answer payload keeps the existing `evidence`, `claims`, `trace`, and conflict fields, and also exposes Context Pack style fields:

- `must_read`: high-authority evidence mapped to `{path, reason}`
- `risks`: deterministic conflict signals mapped to `{risk, severity}`
- `missing_context`: evidence trimming or gate warnings that should be visible to the agent
- `applied_policies`: v0.1 placeholder list, currently `[]` until agent policy instances are wired

Without `task_context`, these four fields still exist but return empty arrays. That keeps normal answer calls compatible while giving task-aware agents a stable schema when they opt in.

The field names and shapes intentionally match `packages/tigermemory-protocols/src/tigermemory_protocols/schemas/context_pack.yaml`.

## Compatibility

MCP, HTTP, trace, and eval callers can continue using `import tm_answer` and `import tm_search`.

The shims use `_bootstrap_paths`, which auto-discovers `packages/*/src` and avoids per-package path edits.

Package extraction must not alter evidence scoring, weak evidence gates, conflict detection, trace writing, or retrieval ranking.

Any behavior change belongs in a separate, reviewed task.
