# TigerMemory

**Your personal AI brain — local-first, evidence-grounded, yours to own.**

TigerMemory turns scattered notes into a living knowledge base. It keeps
everything in plain Markdown + Git on your own machine, uses local SQLite for
private memory, and brings in an AI model to help organize, review, and answer
questions — always citing the exact page it pulled the answer from.

No cloud lock-in. No vendor data silo. Just your knowledge, your rules, and an
AI that reads before it speaks.

---

## Why TigerMemory?

| Problem | TigerMemory's answer |
|---|---|
| AI chatbots hallucinate facts about your own life | AI answers **only from your local evidence**, with citations |
| Notes vanish into closed-source cloud apps | Everything is **Markdown + Git** — open, portable, yours forever |
| Switching AI tools means starting over | Your Wiki is the **shared truth**; any AI tool can read it |
| AI "memory" features upload your private data | TigerMemory runs **locally**; your data never leaves your machine |

## Quick Start

**What you need:** Python 3.10+, Git, and an LLM API key (DeepSeek recommended
for its low cost and OpenAI-compatible API).

```powershell
# 1. Install
py -m pip install -e .

# 2. Initialize — creates your local workspace
tm init

# 3. Open the setup wizard in your browser
tm dashboard
```

The wizard at `http://127.0.0.1:9777/start` walks you through everything: choose
local mode, paste your API key, pick a reply style. Your key is stored locally
under `runtime/`, never committed to Git, never printed back.

**That's it.** You now have a local AI Wiki admin.

## See It In Action (5 minutes)

```powershell
# Write something to local memory
"Remember: I prefer dark mode and concise answers" | tm write-memory --agent human --topic systems

# Search it back
tm search --query "preferences" --scope all

# Ask the AI — it reads your local evidence first, then answers with citations
tm ask --query "what are my preferences?" --scope all
```

Now try the Wiki Admin loop — this is the core feature:

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

**The key idea:** the AI drafts, you approve. Nothing enters your Wiki without
your explicit say-so. This keeps your knowledge base clean and trustworthy.

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
| **Wiki** (Layer 1) | Long-term knowledge in Markdown | Git-tracked files |
| **Memory** (Layer 2) | Short-term context and conversation notes | Local SQLite + FTS5 |
| **LLM** | Organizes, proposes, answers — using your evidence | DeepSeek or any OpenAI-compatible API |

**Seven Wiki partitions** keep things organized:

| Partition | Put there... |
|---|---|
| `projects/` | Active goals, builds, experiments |
| `areas/` | Long-running responsibilities and habits |
| `resources/` | References, tutorials, reusable notes |
| `decisions/` | Durable decisions with rationale |
| `journal/` | Dated reviews and reflections |
| `systems/` | Tool setup, agent rules, workflows |
| `archive/` | Completed or outdated material |

## Connecting Your AI Tools

TigerMemory works with any AI tool that can run local commands — Claude, Codex,
ChatGPT, and others. The safe default is read-only access plus human approval
for Wiki changes.

```powershell
# See what AI rules are already in place
tm agent status

# Install project rules for your AI tool
tm agent apply --yes

# Your AI can now search and ask from local evidence
tm ask --query "what rules should my AI assistant follow?" --scope wiki
```

See [`docs/connect-your-ai-tools.md`](docs/connect-your-ai-tools.md) for
tool-specific setup (Claude, Codex, ChatGPT).

## No-Model Fallback

Don't have an API key yet? TigerMemory still works as a local note search tool:

```powershell
# Returns local evidence only — no AI model needed
tm ask --offline --query "local memory" --scope all
tm search --scope wiki --query "agent behavior"
```

You can write, search, and verify memory without any external API. Add an LLM
key later when you want the full AI Wiki Admin experience.

## Keep It Updated

```powershell
tm update status          # Check if updates are available
tm update apply           # Safe fast-forward update
```

The updater only touches source code. Your data (`wiki/`, `data/`, `runtime/`)
is never touched — no `git reset`, no `git clean`, no silent stashes.

## Configuration

| Setting | What it does | Default |
|---|---|---|
| `TIGERMEMORY_INSTANCE_ROOT` | Where your data lives | Current checkout |
| `TIGERMEMORY_PROFILE` | `local` (basic) or `hybrid` (advanced) | `local` |
| `DEEPSEEK_API_KEY` | Your LLM API key | — |
| `DEEPSEEK_MODEL` | Model for routine tasks | `deepseek-v4-flash` |
| `DEEPSEEK_ADMIN_MODEL` | Model for Wiki Admin proposals | `deepseek-v4-pro` |

Advanced: any OpenAI-compatible chat-completions endpoint works. See
[`docs/provider-compatibility.md`](docs/provider-compatibility.md).

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
[`wiki/systems/public-core-contract.md`](wiki/systems/public-core-contract.md)

## License

AGPL-3.0-or-later. See [`LICENSE`](LICENSE).

Third-party notices (FastAPI, Pydantic, Tailwind CSS, Mermaid, etc.):
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

> **Note:** Do not `npm install tigermemory` — that's a different project by
> another maintainer. Install this one with `pip` from source.

---

**TigerMemory** — your knowledge, your machine, your rules.
