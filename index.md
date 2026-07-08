# TigerMemory Public Starter

TigerMemory is a local AI brain starter: Markdown + Git hold the durable Wiki,
SQLite keeps local memory, and an LLM helps propose, organize, and answer from
your own evidence. Use DeepSeek or another OpenAI-compatible model for the full
Wiki Admin experience.

## Start Here

- Read `README.md` first.
- Run `py -m pip install .`.
- Run `tm init` and `tm profile show`.
- Run `tm llm guide` and `tm llm status`.
- Run `tm admin guide` before asking an AI to maintain the Wiki.
- Use `tm profile guide local` before changing runtime mode.
- Use `tm admin propose` to draft reviewable Wiki pages; approve manually.
- Use `tm ask --query "what rules should my AI assistant follow?" --scope wiki`
  after setup to verify source-grounded answers.
- Use `tm ask --offline` when you want local evidence without connecting an AI
  model; it is a fallback, not the full LLM answer path.
- Open `http://127.0.0.1:9777/start` after `tm dashboard` for the beginner
  start page.

## Where Notes Go

| If the note is about... | Put it in... |
|---|---|
| A current goal, build, client project, or experiment | `wiki/projects/` |
| A long-running responsibility, habit, domain, or personal operating area | `wiki/areas/` |
| A reusable reference, tutorial, external article, or checklist | `wiki/resources/` |
| A decision, tradeoff, rule, or approved direction | `wiki/decisions/` |
| A dated review, progress note, or weekly reflection | `wiki/journal/` |
| Tool setup, agent behavior rules, prompts, workflows, or automation | `wiki/systems/` |
| Completed, replaced, or no-longer-active material kept for context | `wiki/archive/` |

Raw clips, long excerpts, and first-hand source material should keep provenance
and become reviewable proposals first. Do not put secrets, private people data,
health data, finance records, passwords, tokens, or local machine secrets in the
public Wiki.

## Try These Questions

- `tm ask --query "what is this AI brain for?" --scope wiki`
- `tm ask --query "where should a reusable note go?" --scope wiki`
- `tm ask --query "what rules should my AI assistant follow?" --scope wiki`
- `tm ask --offline --query "agent behavior rules" --scope wiki`

## Included Areas

- `wiki/projects/` — active projects with goals and next steps.
- `wiki/areas/` — long-running responsibilities and habits.
- `wiki/resources/` — references, tutorials, and reusable notes.
- `wiki/decisions/` — durable decisions with rationale.
- `wiki/journal/` — dated reviews and recent progress.
- `wiki/systems/` — tool setup, agent behavior rules, and workflows.
- `wiki/archive/` — completed or outdated material kept for context.
- `tools/` — allowlisted local tools needed by the basic workflow.
- `packages/` — Python packages used by the `tm` CLI.
- `runtime/**/*.example` — safe configuration templates only.

## Not Included

Private notes, personal runtime configs, API keys, machine-local paths,
investment records, and non-public research are intentionally excluded from
this public snapshot.
