# Advanced Agent Setup

This page is for users who already completed the basic local + LLM setup.
Beginner TigerMemory usage does not require MCP, hooks, Docker, WSL, Qdrant, or
OpenMemory.

## Goal

The advanced setup lets an IDE agent or CLI agent use TigerMemory as a local
evidence source while keeping durable writes under human control.

## Recommended Stages

1. **Read-only search**
   - Allow `tm ask` and `tm search`.
   - Keep MCP role as `reader`.
   - Verify answers cite local evidence.
2. **Proposal drafting**
   - Allow `tm admin propose`.
   - Keep `tm admin approve` human-only.
   - Review route, partition, source refs, sensitivity, and evidence quality.
3. **Optional hooks**
   - Use hooks only to remind agents about safe behavior.
   - Do not put secrets or provider tokens in hook files.
   - Keep hooks easy to disable and easy to inspect.
4. **Reviewed writer workflow**
   - Add writer tools only after you have tests and rollback steps.
   - Keep private data and public Wiki export boundaries separate.

## MCP Defaults

Use reader mode first:

```json
{
  "mcpServers": {
    "tigermemory": {
      "command": "tm-mcp",
      "args": ["--role=reader", "--tool-profile=memory"]
    }
  }
}
```

The `tm-mcp` command is an adapter example. If your installation uses a
different MCP server command, keep the same permission shape: reader role,
memory-focused tool profile, no automatic approval.

## Hook Defaults

Hook examples live in `docs/examples/hooks/`. They are intentionally passive:
they remind the agent to use proposals and evidence, but they do not write Wiki
pages or approve proposals.

## Approval Boundary

`tm admin approve` is the human boundary. An AI can suggest, summarize, and
draft. The user decides what becomes durable knowledge.
