# Third-Party Notices

This public TigerMemory snapshot includes source code and vendored browser
assets from third-party projects. This notice is a release checklist aid; the
upstream license files remain authoritative.

## Python Runtime Dependencies

- FastAPI — MIT license — https://github.com/fastapi/fastapi
- Pydantic — MIT license — https://github.com/pydantic/pydantic
- Uvicorn — BSD 3-Clause license — https://github.com/encode/uvicorn

## Optional Development Dependencies

- HTTPX — BSD 3-Clause license — https://github.com/encode/httpx
- pytest — MIT license — https://github.com/pytest-dev/pytest

## Vendored Dashboard Assets

The dashboard ships local browser assets so the basic mode works without CDN
access:

- Tailwind CSS 3.4.17 — MIT license — `tools/static/assets/tailwindcss.min.js`
- Lucide 0.468.0 — ISC license — `tools/static/assets/lucide.min.js`
- Mermaid 10.9.1 — MIT license — `tools/static/assets/mermaid.min.js`

## Notes

- The TigerMemory framework files in this public snapshot are distributed under
  AGPL-3.0-or-later; see `LICENSE`.
- User wiki content, local memories, runtime databases, `.env` files, and
  private worktree notes are not part of the public snapshot license boundary.
