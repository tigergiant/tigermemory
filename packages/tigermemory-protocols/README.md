# tigermemory-protocols

`tigermemory-protocols` contains TigerMemory's internal protocol schemas for
the LLM Wiki and memory context boundary.

It ships JSON Schema 2020-12 YAML files plus a small Python validator and a
dry-run CLI.

The v0.1 schemas are internal drafts. Fields may break before v1.0.

## Protocols

- `wiki_page`: long-lived Markdown wiki page metadata.
- `memory_item`: short or medium-lived memory item metadata and content.
- `agent_policy`: agent behavior policy payloads.
- `context_pack`: task context returned to agents before multi-step work.

## Install

From the TigerMemory repository root:

```powershell
pip install -e .\packages\tigermemory-protocols
```

## Usage

```powershell
tigermemory-protocols list-schemas
tigermemory-protocols validate wiki_page .\sample.yaml
tigermemory-protocols scan-wiki --json
```

`scan-wiki` is dry-run only. It prints conformance reports and never rewrites
existing wiki frontmatter.
