# TigerMemory Public Snapshot

This snapshot contains the TigerMemory LLM Wiki Admin starter: public framework,
starter Wiki content, CLI, dashboard assets, publish guard, and safe runtime
templates. Use DeepSeek or another OpenAI-compatible model for the full Wiki
Admin experience.

## Start Here

- Read `README.md` first.
- Run `py -m pip install .`.
- Run `tm init` and `tm profile show`.
- Run `tm llm guide` and `tm llm status`.
- Run `tm admin guide` before asking an AI to maintain the Wiki.
- Use `tm profile guide local` before changing runtime mode.
- Use `tm ask --offline` when you want local evidence without connecting an AI
  model; it is a fallback, not the full LLM answer path.
- Open `http://127.0.0.1:9777/start` after `tm dashboard` for the beginner
  start page.

## Included Areas

- `wiki/operations/project-canvas.md` — public starter project canvas.
- `tools/` — allowlisted local tools needed by the basic workflow.
- `packages/` — Python packages used by the `tm` CLI.
- `runtime/**/*.example` — safe configuration templates only.

## Not Included

Private notes, personal runtime configs, API keys, machine-local paths,
investment records, and non-public research are intentionally excluded from
this public snapshot.
