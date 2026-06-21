# Connect Your AI Tools

TigerMemory works best when your AI tool can read local evidence before it
answers. The safe default is read-only access plus human approval for durable
Wiki changes.

## Recommended Flow

1. Install TigerMemory and configure your LLM provider.
2. Run `tm agent status` to see which project-level AI rules are already in place.
3. Run `tm agent apply --yes` to install AGENTS / Claude / Cursor project rules.
4. Ask the AI to read `README.md`, `AGENTS.md`, and this file.
5. Let the AI use `tm ask` and `tm search` for evidence.
6. Let the AI use `tm admin propose` for drafts.
7. Approve with `tm admin approve` yourself after checking the proposal.

## Tool Permissions

Use this default split:

| Tool action | Default |
|---|---|
| Search local memory and Wiki | allowed |
| Ask with citations from local evidence | allowed |
| Create a Wiki proposal | allowed |
| Approve a proposal into Wiki | human only |
| Delete or rewrite user data | human only |
| Read secrets or env files | blocked |

## Codex

Start with project rules first:

```powershell
tm agent status
tm agent apply --target codex --yes
tm ask --query "what rules should my AI assistant follow?" --scope wiki
tm admin guide
```

If you later install an MCP adapter, use the read-only example in
`.codex/config.toml.example` or `docs/examples/mcp/tigermemory-reader.mcp.json`.
Keep `--role=reader` and `--tool-profile=memory` until you have a reviewed
writer workflow.

## Claude

Give Claude this starting instruction:

```text
Read README.md, AGENTS.md, CLAUDE.md, and docs/connect-your-ai-tools.md.
Use tm ask and tm search for evidence. Use tm admin propose for Wiki drafts.
Do not approve proposals or write secrets.
```

## ChatGPT or Other Assistants

If the tool can run local commands, use the same `tm ask`, `tm search`, and
`tm admin propose` pattern. If it cannot run local commands, give it exported
pages or proposal files, then approve changes locally yourself.

## Safety Rules

- Do not connect an AI tool with write access on the first day.
- Do not paste MCP examples until `tm agent print-config --client codex` reports
  that a local MCP command is available.
- Do not expose API keys, cookies, passwords, private keys, identity numbers, or
  personal records.
- Do not treat a model answer as durable truth. It needs local evidence and
  human approval.
- Do not make MCP or hooks part of the beginner quickstart. They are optional
  advanced integrations.
