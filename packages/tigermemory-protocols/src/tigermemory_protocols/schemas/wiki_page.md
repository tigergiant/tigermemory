# Wiki Page Protocol v0.1

`wiki_page` describes the minimum frontmatter contract for a long-lived
TigerMemory LLM Wiki page.

This protocol is an internal draft. It does not replace
`schemas/PAGE_FORMATS.md` in v0.1.

## Required Fields

| Field | Why It Is Required |
|---|---|
| `owner` | Identifies the regular agent or human responsible for the page. |
| `status` | Separates active facts from drafts and archived material. |
| `updated` | Gives reviewers and retrieval tools a stable freshness date. |
| `partition` | Maps the page to the top-level wiki directory and product domain. |
| `title` | Gives humans and tools a concise display label. |

`owner` intentionally uses the regular commit-agent set, not special data
source identities such as `mem0` or `tigermemory-ce`.

`partition` uses wiki directory semantics. The self-evolution partition is
written as `self-evolution`, even though inbox topic filenames use
`selfevolution` to avoid hyphens.

## Optional Fields

`subtopic` is the Memory Tree second layer inside a partition. It stays
optional so existing pages do not need a migration before this protocol is
useful.

`aliases` are retrieval hints and user-facing synonyms. They are not tags:
aliases should help a query find the page, while tags group pages loosely.

`public` defaults to `false`. This matches the current publish design where
wiki pages are private unless explicitly opted into export.

`source_links` holds supporting URLs when a page is backed by external sources.
It is optional because many internal operational pages are backed by local
commit evidence rather than web URLs.

## Relationship To PAGE_FORMATS.md

`schemas/PAGE_FORMATS.md` remains the human-facing page format rule in v0.1.
This YAML schema is a machine-readable companion for validation experiments.

The two are intentionally allowed to overlap until v1.0 decides whether YAML
schemas become the stronger source of truth.

## Valid Example

```yaml
owner: codex
status: active
updated: 2026-05-25
partition: systems
title: "Memory Answer Evidence Policy"
subtopic: ["memory-engine", "retrieval"]
aliases: ["evidence-first answer", "memory_answer policy"]
public: false
```

## Invalid Example

This instance is invalid because `owner` is missing:

```yaml
status: active
updated: 2026-05-25
partition: systems
title: "Missing Owner Example"
```

## v0.1 Notes

`additionalProperties` is `true` for v0.1 because existing wiki pages contain
specialized frontmatter fields. A future v1.0 may tighten this after real scan
results show which fields are durable.
