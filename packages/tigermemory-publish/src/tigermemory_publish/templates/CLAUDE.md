# TigerMemory Claude Guide

You are working inside a public TigerMemory starter checkout.

## First Read

1. `README.md`
2. `AGENTS.md`
3. `docs/connect-your-ai-tools.md`
4. `wiki/systems/agent-behavior-rules.md`

## Default Behavior

- Use `tm ask --query "<question>" --scope wiki` for source-grounded answers.
- Use `tm search --scope all --query "<query>"` when you need raw evidence.
- Use `tm admin propose` to draft a reviewable Wiki page.
- Do not run `tm admin approve` automatically.
- Do not write secrets, identity numbers, private personal records, or local env
  files into the Wiki.

## Routing

- Current goals and experiments go to `wiki/projects/`.
- Long-running responsibilities go to `wiki/areas/`.
- References and reusable notes go to `wiki/resources/`.
- Durable decisions go to `wiki/decisions/`.
- Dated reviews go to `wiki/journal/`.
- Tool setup and agent rules go to `wiki/systems/`.
- Completed or replaced material goes to `wiki/archive/`.

Raw source material should become a proposal with evidence references first.
Human approval is required before it becomes durable Wiki knowledge.
