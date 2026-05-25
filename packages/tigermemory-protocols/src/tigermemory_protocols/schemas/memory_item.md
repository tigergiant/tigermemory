# Memory Item Protocol v0.1

`memory_item` describes an atomic memory record before it is promoted, archived,
or used as evidence by a higher-level answer system.

This protocol is an internal draft. It does not migrate Mem0, OpenMemory, or
inbox records in v0.1.

## Required Fields

| Field | Why It Is Required |
|---|---|
| `memory_id` | Lets audits and review UI refer to the exact stored item. |
| `source` | Records which agent or data source created the memory. |
| `created_at` | Keeps retention, recency scoring, and daily reports time-aware. |
| `content` | Holds the actual memory text; metadata without content is not useful. |

`source` intentionally uses `tigermemory_core.AGENTS`, not only regular commit
agents. Memory writes can come from `mem0`, `tigermemory-ce`, or `dsa-cron`,
which are data-source identities rather than wiki page owners.

`content` is required and has `minLength: 1` because empty memories create
false evidence without information value.

## Retention Fields

`decay` and `ttl_days` are specific to memory lifecycle management. Wiki pages
do not use them because long-lived pages are archived through Git and explicit
page status, not automatic short-term retention.

`decay` is a normalized number from 0 to 1. A retention audit can use it as one
factor together with creation time, access frequency, and route score.

`ttl_days` is optional. A missing value means the retention layer has no
explicit TTL instruction from this protocol.

## Privacy Fields

`sensitivity` separates public, internal, and sensitive memory. This differs
from the wiki page protocol, where `public` is a boolean export opt-in.

Sensitive memory should normally stay out of long-term public exports unless a
human explicitly rewrites it in redacted form.

## Topic And Partition

`topic` describes where the memory is stored or searched today. `partition`
describes where it may be promoted later.

Both use the data-write topic spelling from `tigermemory_core.TOPICS`, including
`selfevolution`. This is different from the wiki directory name
`self-evolution`.

## Valid Example

```yaml
memory_id: "2c416bff-b49f-48f1-a923-33a3cd1ca34b"
source: codex
created_at: "2026-05-25T13:45:49+08:00"
content: "P3-H completed tigermemory-digest package extraction."
topic: systems
sensitivity: internal
decay: 0.1
ttl_days: 90
```

## Invalid Example

This instance is invalid because `content` is missing:

```yaml
memory_id: "missing-content"
source: codex
created_at: "2026-05-25T13:45:49+08:00"
topic: systems
```

## v0.1 Notes

`additionalProperties` is `true` so existing memory backends can keep fields
such as route score, direct readback status, or audit markers while this draft
evolves.
