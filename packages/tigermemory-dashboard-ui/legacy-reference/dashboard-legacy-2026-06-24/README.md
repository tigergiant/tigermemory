# Dashboard Legacy Reference Snapshot

Snapshot date: 2026-06-24

Source commit: `1691b4dc2f20bab3c550474d8017a6d4deedaae3`

Purpose: keep the pre-full-React dashboard implementation available for visual,
copy, data-flow, and interaction comparison while the dashboard is migrated page
by page. These files are reference-only; production routes must not serve files
from this directory.

## Page Sources

| Route | Reference file | Legacy controller |
|---|---|---|
| `/start` | `start.html` | React migration already exists; keep this for visual comparison |
| `/digest`, `/review` | `review.html` | `dashboard-pages.js`, digest / daily review handlers |
| `/health` | `health.html` | `dashboard-pages.js`, `window.tmPages.health` |
| `/quality` | `quality.html` | `dashboard-pages.js`, `window.tmPages.quality` |
| `/canvas` | `canvas.html` | `dashboard-pages.js`, `window.tmPages.canvas` |
| `/agent-tools` | `agent-tools.html` | `dashboard-pages.js`, `window.tmPages.agentTools` |
| `/settings` | `settings.html` | `dashboard-pages.js`, `window.tmPages.settings` |
| `/self-evolution` | `self-evolution.html` | `dashboard-pages.js`, `window.tmPages.selfEvolution` |
| `/ledger` | `ledger.html` | server-rendered static shell plus related ledger APIs |

## Shared Legacy Assets

| File | Why it is kept |
|---|---|
| `_components/header.html` | Navigation structure, tab order, active state reference |
| `_components/style.css` | Legacy warm card/background token reference |
| `dashboard-common.js` | PJAX, shared bootstrapping, navigation behavior |
| `dashboard-pages.js` | Per-page data fetch and interaction logic |
| `i18n.json` | Legacy copy source |
| `i18n.js` | Legacy language switching behavior |

## File Hashes

| File | SHA256 prefix |
|---|---|
| `agent-tools.html` | `b42d9f8fbd7a` |
| `canvas.html` | `81337201d1bf` |
| `dashboard-common.js` | `fd56d6382d94` |
| `dashboard-pages.js` | `999300c2602a` |
| `health.html` | `c9d99e28fd58` |
| `i18n.js` | `557de0b44344` |
| `i18n.json` | `1a1c5be31ff1` |
| `ledger.html` | `bbf03121feab` |
| `quality.html` | `f093950b4e22` |
| `review.html` | `437e57b29530` |
| `self-evolution.html` | `1b98ccccf468` |
| `settings.html` | `31990ab212ad` |
| `start.html` | `bc0467431b5c` |
| `_components/header.html` | `3e4f687d6c86` |
| `_components/style.css` | `18a1a38509f8` |

## Migration Rule

Before migrating a page, compare the new React page against this snapshot for:

- navigation position and active tab behavior,
- card background, border, density, and typography,
- loading, empty, error, and toast states,
- old data fetch endpoints and write actions,
- transition feel and reduced-motion behavior.
