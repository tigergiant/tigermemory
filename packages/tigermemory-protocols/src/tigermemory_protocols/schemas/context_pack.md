# Context Pack Protocol v0.1

`context_pack` describes the structured context an agent should receive before
starting a multi-step task.

This protocol is an internal draft. It does not change `memory_answer` output
in v0.1, but it defines the field names that the later retrieval uplift work
should fill.

## Required Fields

| Field | Why It Is Required |
|---|---|
| `task` | Names the concrete work item. |
| `user_intent` | States the agent's interpretation of the request. |
| `must_read` | Lists context that must be read before acting. |
| `risks` | Surfaces known failure modes instead of hiding them in prose. |
| `missing_context` | Records what is still unknown or needs confirmation. |

`must_read` is an array of objects, not strings. Each entry needs both `path`
and `reason` so the agent understands why a page or source is mandatory.

`risks` is also an array of objects. Every risk must include `severity` so UI
and agents can distinguish low-friction caveats from high-risk blockers.

`missing_context` remains an array of strings because these gaps are often
plain-language questions rather than stable structured facts.

## Relationship To memory_answer

Current `memory_answer` already returns evidence, claims, traces, conflicts,
and warnings.

`context_pack` is a higher-level task context shape. It can reuse existing
evidence, but it adds task-specific fields such as `must_read`,
`missing_context`, and `applied_policies`.

In a later retrieval uplift, `memory_answer` can map high-authority evidence
into `must_read` when the evidence is required before an agent acts.

## Evidence Authority Mapping

If an evidence object has an `authority_score`, a future context builder can
prefer high-score canonical wiki pages for `must_read`.

Lower-authority memories may still appear in `evidence`, but should not become
`must_read` unless the task is specifically about recent conversation state.

This separation keeps Context Pack from treating every search hit as mandatory
reading.

## Optional Fields

`wiki_pages` and `memories` preserve selected context references.

`applied_policies` connects this pack to `agent_policy` protocol IDs.

`recommendations` stores suggested next actions; it is advisory and should not
silently execute changes.

## Valid Example

```yaml
task: "Fix MCP startup failure"
user_intent: "The user wants Codex sessions to initialize tigermemory MCP reliably."
must_read:
  - path: "wiki/systems/services-inventory.md"
    reason: "Contains the current MCP service and port inventory."
risks:
  - risk: "WSL worktree may be stale or dirty."
    severity: high
missing_context:
  - "Need the live stderr from tm_mcp_auto_update.sh."
applied_policies:
  - "live-state-before-claim"
recommendations:
  - "Run the startup wrapper directly before editing config."
```

## Invalid Example

This instance is invalid because a risk object is missing `severity`:

```yaml
task: "Answer memory question"
user_intent: "Find evidence-backed facts."
must_read:
  - path: "wiki/systems/memory-answer-evidence-policy.md"
    reason: "Defines authority scoring."
risks:
  - risk: "Evidence may be weak."
missing_context: []
```

## v0.1 Notes

This schema is intentionally independent from any specific LLM model. It
defines the context payload shape, not the ranking model or prompt that fills
the fields.
