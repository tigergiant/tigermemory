<div align="center">

# TigerMemory

**Your personal AI brain — local-first, evidence-grounded, yours to own.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](#)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](#contributing)

TigerMemory turns scattered notes into a living knowledge base. Everything lives
in plain Markdown + Git on your own machine. An AI model helps organize, review,
and answer questions — always citing the exact page it pulled the answer from.

No cloud lock-in. No data uploads. No vendor silo. Your knowledge, your rules.

</div>

---

## How This Project Started

TigerMemory began as one developer's personal AI engineering system.

In April 2026, Tiger (a developer who uses AI coding tools daily) found his
knowledge scattered everywhere — conversations with Claude, Codex, ChatGPT, each
in its own silo. Every time he switched AI tools, he had to re-feed the same
context from scratch. Notes were locked in closed-source cloud apps. AI
"memory" features uploaded his private data to servers with no transparency
about what was stored or whether it was ever truly deleted.

He wanted one thing: **a local-first AI brain that lets every AI tool read the
same knowledge, while keeping all data on his own machine.**

Not another note-taking app. Not another AI chatbot. A system where AI helps
maintain your knowledge base — AI drafts, you approve, and knowledge stays under
your control.

From April to July, 859 commits turned a personal engineering system into an
open-source framework. The core idea never changed: **the LLM Wiki is your
sovereign asset. TigerMemory is just the runtime — replaceable, but your
knowledge outlives it.**

## Why TigerMemory?

| Problem | TigerMemory's answer |
|---|---|
| AI chatbots hallucinate facts about your own life | AI answers **only from your local evidence**, with citations |
| Notes vanish into closed-source cloud apps | Everything is **Markdown + Git** — open, portable, yours forever |
| Switching AI tools means starting over | Your Wiki is the **shared source of truth**; any AI tool can read it |
| AI "memory" features upload your private data | TigerMemory runs **entirely locally**; your data never leaves your machine |
| AI edits your notes without you knowing | AI drafts a proposal → **you review → you approve** → then it enters your Wiki |

## Key Features

- **Evidence-First Answers** — `tm ask` searches local memory and Wiki first, then the AI answers based on evidence, citing the source page for every claim
- **Proposal-Approval Workflow** — AI organizes your notes into Wiki page drafts, but nothing is written until you explicitly approve it
- **Pure Markdown + Git** — No proprietary format, no special app. Open your notes in VS Code, Obsidian, or any editor
- **Local SQLite Memory** — Short-term conversation memory stored in a local database with FTS5 full-text search — no vector DB required
- **Works with Any AI Tool** — Claude, Codex, ChatGPT, Cursor — anything that can run shell commands can read and write your Wiki
- **DeepSeek First** — Recommends DeepSeek as the LLM provider: low cost, OpenAI-compatible API
- **Zero Infrastructure** — Default `local` mode needs no Docker, WSL, Qdrant, or Caddy. Just Python + Git and you're running (`hybrid` mode adds these optionally)
- **Git-Safe Updates** — `tm update` only does fast-forward. No `git reset`, no `git clean`, no silent stashes — your data is never touched

## Quick Start

**What you need:** Python 3.10+, Git, and an LLM API key (recommend [DeepSeek](https://platform.deepseek.com/))

```powershell
# 1. Install
py -m pip install -e .

# 2. Initialize your local workspace
tm init

# 3. Open the setup wizard in your browser
tm dashboard
```

Your browser opens `http://127.0.0.1:9777/start`. The wizard walks you through:
choose mode → paste API key → pick reply style. Your key is stored locally under
`runtime/`, never committed to Git, never printed back.

**That's it — three steps and you have a local AI Wiki admin.**

## See It In Action (5 minutes)

```powershell
# Write something to local memory
"Remember: I prefer dark mode and concise answers" | tm write-memory --agent human --topic systems

# Search it back
tm search --query "preferences" --scope all

# Ask the AI — it reads your local evidence first, then answers with citations
tm ask --query "what are my preferences?" --scope all
```

Now try the Wiki Admin loop — the core feature:

```powershell
# 1. Jot down a rough note
echo "Project goal: build a personal recipe collection with AI-tagged ingredients" > notes.md

# 2. Ask the AI to turn it into a proper Wiki proposal
cat notes.md | tm admin propose --partition projects --title "Recipe Collection"

# 3. Review the proposal (AI suggests where it goes and how it's structured)
tm admin list
tm admin show "<proposal-id>"

# 4. Approve it — this writes the page to wiki/projects/
tm admin approve "<proposal-id>"

# 5. Ask a question — the AI cites the page it just created
tm ask --query "what is my recipe project about?" --scope wiki
```

**The key idea: AI drafts, you approve.** Nothing enters your Wiki without your
explicit say-so.

## How It Works

```
  Your notes ──→ AI proposes ──→ You review ──→ Wiki page
       │                                           │
       │         tm ask ──→ AI reads ──→ Answers with citations
       │              ↑                           │
       └──── Local SQLite memory ←────────────────┘
```

**Three layers, all local:**

| Layer | What it does | Storage |
|---|---|---|
| **Wiki** (long-term) | Durable knowledge in Markdown | Git-tracked files |
| **Memory** (short-term) | Conversation context and temporary notes | Local SQLite + FTS5 |
| **LLM** (intelligence) | Organizes, proposes, answers | DeepSeek or any OpenAI-compatible API |

**Seven Wiki partitions — no more "where do I put this?":**

| Partition | Put there... |
|---|---|
| `projects/` | Active goals, builds, experiments |
| `areas/` | Long-running responsibilities and habits |
| `resources/` | References, tutorials, reusable notes |
| `decisions/` | Durable decisions with rationale |
| `journal/` | Dated reviews and reflections |
| `systems/` | Tool setup, agent rules, workflows |
| `archive/` | Completed or outdated material |

## No API Key? No Problem

Don't have a key yet? TigerMemory still works as a local note search tool:

```powershell
# Returns local evidence only — no AI model needed
tm ask --offline --query "local memory" --scope all
tm search --scope wiki --query "agent behavior rules"
```

Write, search, and verify memory without any external API. Add an LLM key later
when you want the full AI Wiki Admin experience.

## Connecting Your AI Tools

TigerMemory works with any AI tool that can run local commands — Claude, Codex,
ChatGPT, Cursor, and more. Safe defaults: read-only access plus human approval
for Wiki changes.

```powershell
# Check current AI tool status
tm agent status

# Install project rules for your AI tool
tm agent apply --yes

# Your AI can now search and answer from local evidence
tm ask --query "what rules should my AI assistant follow?" --scope wiki
```

See [Connecting Your AI Tools](docs/connect-your-ai-tools.md) for tool-specific
setup.

## CLI Reference

| Command | What it does |
|---|---|
| `tm init` | Initialize local workspace |
| `tm dashboard` | Open web UI (port 9777) |
| `tm ask --query "..."` | Ask AI with local evidence + citations |
| `tm ask --offline` | Evidence-only, no model |
| `tm search --query "..."` | Search memory + Wiki |
| `tm write-memory` | Write to local memory |
| `tm admin propose` | AI drafts a Wiki page for your review |
| `tm admin approve` | You approve → page enters Wiki |
| `tm admin list` | See pending proposals |
| `tm update status` | Check for framework updates |
| `tm llm status` | Check if LLM is configured |
| `tm doctor` | Run local diagnostics |

Full CLI contract (every command's inputs, outputs, exit codes):
[`public-core-contract.md`](wiki/systems/public-core-contract.md)

## Configuration

| Setting | What it does | Default |
|---|---|---|
| `TIGERMEMORY_INSTANCE_ROOT` | Where your data lives | Current checkout |
| `TIGERMEMORY_PROFILE` | `local` (basic) or `hybrid` (advanced) | `local` |
| `DEEPSEEK_API_KEY` | Your LLM API key | — |
| `DEEPSEEK_MODEL` | Model for routine tasks | `deepseek-v4-flash` |
| `DEEPSEEK_ADMIN_MODEL` | Model for Wiki Admin proposals | `deepseek-v4-pro` |

Advanced: any OpenAI-compatible chat-completions endpoint works. See
[Provider Compatibility](docs/provider-compatibility.md).

## Safe Updates

```powershell
tm update status    # Check if updates are available
tm update apply     # Safe fast-forward update
```

The updater only touches source code. Your data (`wiki/`, `data/`, `runtime/`)
is never touched — no `git reset`, no `git clean`, no silent stashes.

## FAQ

**Q: Does my data get uploaded to the cloud?**
No. TigerMemory runs entirely locally. Your Wiki is local Markdown files, your
memory is a local SQLite database. The only external call is to your configured
LLM API (DeepSeek etc.), and only when you ask a question.

**Q: Can I use something other than DeepSeek?**
Yes. Any API compatible with OpenAI's chat-completions format works — OpenAI,
Qwen, Moonshot, etc. DeepSeek is recommended for its cost-effectiveness.

**Q: How is this different from Obsidian?**
Obsidian is a note editor. TigerMemory is an AI knowledge management system. You
can use Obsidian to edit TigerMemory's Wiki files — they're complementary, not
alternatives.

**Q: How is this different from Mem0?**
Mem0 is an AI memory layer focused on helping AI remember conversation context.
TigerMemory is a complete personal knowledge base + AI management system: it has
a memory layer, but also a Git-versioned Wiki for long-term knowledge, a
proposal-approval workflow, and evidence-first answer retrieval.

**Q: Is `npm install tigermemory` this project?**
No. The `tigermemory` package on npm is a different project by another
maintainer. This one installs via `pip` from source.

## Project Activity

<div align="center">

<img src="https://github-readme-activity-graph.vercel.app/graph?username=tigergiant&repo=tigermemory&theme=github-compact&hide_border=true&area=true&color=4493f8&line=4493f8&point=ffffff" width="100%" alt="GitHub Activity Graph">

</div>

<div align="center">

<img src="https://img.shields.io/github/commit-activity/m/tigergiant/tigermemory?style=for-the-badge&color=4493f8" alt="Commit Activity">
<img src="https://img.shields.io/github/languages/count/tigergiant/tigermemory?style=for-the-badge&color=blueviolet" alt="Languages">
<img src="https://img.shields.io/github/repo-size/tigergiant/tigermemory?style=for-the-badge&color=success" alt="Repo Size">
<img src="https://img.shields.io/github/last-commit/tigergiant/tigermemory?style=for-the-badge&color=orange" alt="Last Commit">

</div>

> Active since April 2026, used every day — not a toy, a daily production tool.

## Contributing

Issues and PRs are welcome!

- Found a bug → [Open an Issue](https://github.com/tigergiant/tigermemory/issues)
- Have an idea → [Send a PR](https://github.com/tigergiant/tigermemory/pulls)
- Want to discuss → Just say it in an Issue

## License

MIT License — see [LICENSE](LICENSE). Free to use, modify, and distribute,
including commercial use.

Third-party notices (FastAPI, Pydantic, Tailwind CSS, Mermaid, etc.):
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

---

<div align="center">

**TigerMemory** — your knowledge, your machine, your rules.

</div>
