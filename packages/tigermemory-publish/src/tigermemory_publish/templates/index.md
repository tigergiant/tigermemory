# TigerMemory Public Snapshot

This snapshot contains the local-first TigerMemory framework, starter Wiki
content, CLI, dashboard assets, publish guard, and basic runtime templates.

## Start Here

- Read `README.md` first.
- Run `py -m pip install -e .`.
- Run `tm init` and `tm profile show`.
- Use `tm profile guide local` before changing runtime mode.
- Use `tm ask --offline` when you want local evidence without connecting an AI
  model.
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
