#!/usr/bin/env python3
"""Compile a deterministic tigermemory agent onboarding snapshot.

v0.2.0 deliberately avoids LLMs, Mem0, vector search, or external services.
It reads the stable repo sources that define agent conduct and renders a
compact snapshot for new agents:

    py tools/tm_persona.py compile --depth 30s
    py tools/tm_persona.py compile --depth 5min
    py tools/tm_persona.py compile --depth full

The output is meant for humans, Codex/Claude sessions, and the read-only MCP
tool `get_agent_onboarding`.
Inputs: Local repo state, service health endpoints, lessons/wiki pages, Mem0 records, or dashboard preference files.
Outputs: Doctor/audit/onboarding/metrics reports, local UI helper effects, or JSON diagnostics.
Depends-on (must-have): tm_core helpers, local filesystem/git state, and configured local services when the command asks for live checks.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from zoneinfo import ZoneInfo

def _detect_repo_root() -> pathlib.Path:
    explicit = os.environ.get("TIGERMEMORY_INSTANCE_ROOT")
    if explicit:
        return pathlib.Path(explicit).resolve()
    explicit = os.environ.get("TIGERMEMORY_ROOT")
    if explicit:
        return pathlib.Path(explicit).resolve()
    here = pathlib.Path(__file__).resolve()
    for ancestor in [here.parent, *here.parents]:
        if (ancestor / "wiki").is_dir() and (
            (ancestor / ".git").is_dir()
            or (ancestor / ".git").is_file()
            or (ancestor / "tools").is_dir()
            or (ancestor / "pyproject.toml").is_file()
        ):
            return ancestor
    return here.parent.parent.parent.parent


REPO_ROOT = _detect_repo_root()

OPTIONAL_SOURCE_PATHS = [
    "AGENTS.md",
]
REQUIRED_SOURCE_PATHS = [
    "wiki/systems/tigermemory-agent-access.md",
    "wiki/systems/agent-write-toolkit.md",
    "wiki/self-evolution/lessons/index.md",
    "wiki/systems/services-inventory.md",
]
SOURCE_PATHS = OPTIONAL_SOURCE_PATHS + REQUIRED_SOURCE_PATHS
SNAPSHOT_PAGE = "wiki/systems/agent-onboarding.md"
AGENT_CONTEXT_PACK = "runtime/agent-context/latest.json"
SNAPSHOT_PAGE_REQUIRED_PHRASES = [
    "v0.2.3 接入状态",
    "暂时停止大功能开发",
    "继续开发条件",
    "get_agent_onboarding",
]

LESSONS_DIR = REPO_ROOT / "wiki" / "self-evolution" / "lessons"
VALID_DEPTHS = {"30s", "5min", "full"}


@dataclass(frozen=True)
class Lesson:
    slug: str
    title: str
    summary: str


def _read_source(rel: str) -> str:
    path = REPO_ROOT / rel
    if not path.exists():
        raise FileNotFoundError(f"required source missing: {rel}")
    return path.read_text(encoding="utf-8")


def _frontmatter_title(text: str) -> str:
    m = re.search(r'^title:\s*"?([^"\n]+?)"?\s*$', text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    h1 = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    return h1.group(1).strip() if h1 else ""


def _section(text: str, heading: str) -> str:
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$\n(?P<body>.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(text)
    if not m:
        return ""
    body = m.group("body").strip()
    return re.sub(r"\n{3,}", "\n\n", body)


def _first_paragraph(markdown: str) -> str:
    for block in re.split(r"\n\s*\n", markdown.strip()):
        clean = " ".join(line.strip() for line in block.splitlines() if line.strip())
        if clean and not clean.startswith("- "):
            return clean
    return ""


def load_lessons(limit: int = 7) -> list[Lesson]:
    lessons: list[Lesson] = []
    if not LESSONS_DIR.exists():
        return lessons
    for path in sorted(LESSONS_DIR.glob("*.md")):
        if path.name == "index.md":
            continue
        text = path.read_text(encoding="utf-8")
        title = _frontmatter_title(text) or path.stem
        summary = _first_paragraph(_section(text, "摘要"))
        if not summary:
            summary = _first_paragraph(_section(text, "预防性教训"))
        lessons.append(Lesson(path.stem, title, summary))
    return lessons[:limit]


def _bullet_lines(items: Iterable[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _load_services_inventory() -> str:
    """Return the 生产服务清单 + 端口快查 sections of services-inventory.md.

    These two sections are what an onboarding agent needs to know on day 1:
    which services are running and on which ports. The full inventory page
    (timer detail, compile rules, etc.) is one link away.

    The page is auto-compiled by tools/tm_compile_systemd_inventory.py from
    deploy/mcp/*.service so it cannot drift from systemd ground truth.
    Failure to read raises FileNotFoundError, which compile_snapshot()
    surfaces early via the SOURCE_PATHS guard.
    """
    text = _read_source("wiki/systems/services-inventory.md")
    services = _section(text, "生产服务清单")
    port_lookup = _section(text, "端口快查")
    parts: list[str] = []
    if services:
        parts.append(services)
    if port_lookup:
        parts.append("**端口快查**\n\n" + port_lookup)
    return "\n\n".join(parts).strip() or "（services-inventory.md 解析失败，请人工核查）"


def _load_agent_context_status() -> str:
    path = REPO_ROOT / AGENT_CONTEXT_PACK
    if not path.exists():
        return (
            "- Agent Context Pack：missing。运行 "
            "`py tools\\tm_agent_context.py build --profile codex --task \"<task>\"` 生成。"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"- Agent Context Pack：unreadable（{type(exc).__name__}）。运行 `py tools\\tm_agent_context.py validate --json` 检查。"
    generated_raw = str(data.get("generated_at") or "")
    stale_after = int((data.get("freshness") or {}).get("stale_after_minutes") or 240)
    status = "unknown"
    age_text = "age unknown"
    try:
        generated = datetime.fromisoformat(generated_raw)
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        age_minutes = max(
            0,
            int((datetime.now(ZoneInfo("Asia/Shanghai")) - generated.astimezone(ZoneInfo("Asia/Shanghai"))).total_seconds() // 60),
        )
        status = "fresh" if age_minutes <= stale_after else "stale"
        age_text = f"age={age_minutes}min"
    except Exception:
        status = "invalid_time"
    pack_hash = str(data.get("pack_hash") or "")[:16]
    return (
        f"- Agent Context Pack：{status}，{age_text}，hash={pack_hash or 'none'}，"
        f"path=`{path}`。重建：`py tools\\tm_agent_context.py build --profile codex --task \"<task>\"`。"
    )


def render_30s() -> str:
    return """# tigermemory Agent Onboarding Snapshot (30s)

- 开工先做：`git pull --ff-only origin master`，再用 `git status --short | Measure-Object -Line` 判断完整 dirty 行数，并跑 `py tools/tm_io.py preflight`。
- 开工还必须跑 lessons 检索：`$env:TM_AGENT="<agent>"; py tools/tm_lessons.py search "<任务关键词>"`，读 top-3，避免重复事故。
- 首次/陌生 agent 再读本快照：`py tools/tm_persona.py compile --depth 30s` 或 MCP `get_agent_onboarding("30s")`。
- 写入边界：只写自己拥有的 wiki 分区；不确定、跨分区、self-evolution 提案走 inbox；`topic` 用 `selfevolution`，不是 `self-evolution`。
- 写入入口：inbox 用 `tm_io.py write-inbox` 或 MCP `write_inbox`；稳定 wiki 用 owner 路径；对话级事实用 `write_memory` 路由，不直接造 inbox 文件。报告 Mem0 id 时区分 `write_memory returned id`、`direct_readback_ok`、`search_self_hit`、`not checked`。
- 结论纪律：计划、推断、已验证现状必须分开；依赖本机/服务状态的结论先查 live state；不要把“准备做”写成“已落地”。
- Git 纪律：只 stage 本次文件；`git add` 后同回合 commit；commit 后同回合 push；hook reject 先修根因，agent 不用 `--no-verify`。
"""


def render_5min(lessons: list[Lesson]) -> str:
    lesson_lines = [
        f"`{lesson.slug}`：{lesson.title}。{lesson.summary}" for lesson in lessons[:5]
    ]
    start_steps = _bullet_lines(
        [
            "`git pull --ff-only origin master`，确认本侧 worktree 不读陈旧文件。",
            "`git status --short | Measure-Object -Line`，用完整行数判断 dirty，不看终端尾部。",
            "`py tools/tm_io.py preflight`，blocker 非空就停下来报告。",
            '`py tools/tm_lessons.py search "<任务关键词>"`，读 top-3 lesson 后再动手。',
            '首次/陌生 agent 跑 `py tools/tm_persona.py compile --depth 30s` 或 MCP `get_agent_onboarding("30s")`。',
        ]
    )
    write_boundaries = _bullet_lines(
        [
            "除 `wiki/person/` 外所有 wiki 分区，所有常规 agent（claude-code / cascade / codex / chatgpt / openclaw / hermes / deerflow / kimi / gemini / linter / trae）均可直写，由 L2 评审分数 ≥ 30 把关；< 30 降级写 inbox 提案。详见 AGENTS.md §4。",
            "`AGENTS.md`、`schemas/`、根 `index.md` 属元规则，只有 `claude-code` / `cascade` / `human` 可改。",
            "`log.md` 不手写，由 `claude-code compile` 从 git log 汇总。",
            "跨分区、不确定、需人工二审内容写 inbox；self-evolution 的 inbox topic key 是 `selfevolution`。",
        ]
    )
    tool_entries = _bullet_lines(
        [
            "CLI preflight：`py tools/tm_io.py preflight`。",
            "CLI inbox：`py tools/tm_io.py write-inbox --agent <agent> --topic <topic> --title <title>`，正文走 stdin；强制投递用 `--force-inbox`。",
            "CLI Mem0 审计：`py tools/tm_io.py mem0-verify --id <uuid> --terms \"<key terms>\"`；不要用 grep/digest/search 0 命中直接判定 id 幻觉。",
            "MCP 只读：`check_worktree`、`close_session`、`read_page`、`list_partition`、`lint_page`、`lint_repo`、`get_agent_onboarding`。",
            "MCP 写入：`write_inbox`、`propose_wiki_page`、`write_memory`；MCP 审计：`verify_memory_id`；reader role 调用写工具会被拒绝。",
        ]
    )
    live_state_rules = _bullet_lines(
        [
            "涉及服务、配置、API、进程、hook、Git 状态时，先查本机真实输出再下结论。",
            "Windows PowerShell 下不要内联复杂 ssh/curl/Bearer/URL `&` 命令；用远端 bash 脚本或 stdin 稳定传递。",
            "结论依赖请求头时，用 `curl -v` 看实际发出的头。",
            "不要把单点失败说成已解决；必须有实际验证。",
        ]
    )
    # Agent ecosystem one-liners. Goal: when a fresh agent first hits this
    # snapshot it knows what each system does without doing N extra searches.
    # Each line points at the canonical wiki page so the agent can deep-dive
    # on demand instead of getting context bloat up front.
    agent_ecosystem = _bullet_lines(
        [
            "**OpenClaw**：本地 agent runtime / MCP 客户端容器，笔记本端跑 agent 进程；tigermemory 通过 `tigermemory-ce` 插件接入。详见 `wiki/systems/openclaw-runtime.md`、`wiki/systems/openclaw-capabilities.md`。",
            "**Hermes**：持续跟踪 / 分阶段推进 agent，适合多轮研究、长期跟踪、阶段性总结；不擅长一次性深度专题。详见 `wiki/systems/hermes-docs-index.md`。",
            "**DeerFlow**：深度专题研究 agent，吃结构化任务（目标 / 范围 / 时间窗 / 输出格式），不要把模糊问题整包丢进去。详见 `wiki/systems/deerflow-research-engine.md`。",
            "**Mem0 / OpenMemory**：对话级实时记忆层（atomic event-style，例如“X 部署了”“Y 工具不适合 Z”），HTTP `:8765` `user_id=tiger`；长文 / 规则 / 历史文案在 Wiki，不在 Mem0。`write_memory` 返回的 id 用 `verify_memory_id` direct readback 审计，不靠文件 grep 0 命中判幻觉。详见 `wiki/systems/multi-endpoint-mem0.md`、`wiki/systems/openmemory-ce-limits.md`。",
            "**Cascade / Codex / Claude Code / Kimi**：常驻 agent。写入主入口：本机仓库 `<repo>`（人工 + Cascade / Codex / Obsidian）↔ 运行时 worktree（Claude Code / MCP / Hermes / DeerFlow），通过 GitHub origin 同步；开工先 `git pull --ff-only`。元规则修改权限只在 `claude-code` + `human`。完整 agent 枚举：`AGENTS.md §3`。",
            "**OpenSpace**：登录态网页 / 浏览器自动化 / 桌面采集；目前主要通过技能 / 上游 agent 间接接入。",
            "**memory_answer**（MCP）：普通自然语言记忆问答的主要入口，自动检索、展开证据并返回 answer / claims / evidence / trace_id；`search_tigermemory`、`search_memories`、`search_wiki` 是 raw 候选浏览、二次核对和召回调试备用。详见 `wiki/systems/agent-write-toolkit.md`。",
        ]
    )
    lesson_entries = _bullet_lines(lesson_lines)
    services_inventory = _load_services_inventory()
    return f"""# tigermemory Agent Onboarding Snapshot (5min)

## 1. 开工顺序

{start_steps}

## 2. 写入权限边界

{write_boundaries}

## 3. 工具入口

{tool_entries}

## 4. Agent 生态地图（一句话定位）

{agent_ecosystem}

## 5. 当前上下文卡

{_load_agent_context_status()}

## 6. 生产服务清单（live runtime services）

下表自动从 `deploy/mcp/*.service` 编译（`tools/tm_compile_systemd_inventory.py`），是 tigermemory 当前实际跑在 WSL2 / VPS 上的长驻服务与端口的事实源。完整页含 timer / oneshot / 编译规则：`wiki/systems/services-inventory.md`。

{services_inventory}

## 7. Live-state 优先原则

{live_state_rules}

## 8. 必须避免的 lesson

{lesson_entries}
"""


def render_full(lessons: list[Lesson]) -> str:
    all_lesson_lines = [
        f"- `{lesson.slug}`：{lesson.title}\n  {lesson.summary}" for lesson in lessons
    ]
    access_boundaries = _bullet_lines(
        [
            "OpenClaw、Hermes、DeerFlow 已有直接 MCP 接入；OpenSpace 当前主要通过技能/上游 agent 间接接入。",
            "`writer` role 可调用写工具；`reader` role 只读，适合 DeerFlow、OpenSpace、未受信 agent。",
            "新增 agent 要先登记接入方式、权限边界和验证证据，再允许写核心 Wiki。",
        ]
    )
    lesson_catalog = "\n".join(all_lesson_lines)
    v02_scope = _bullet_lines(
        [
            "本 snapshot 是 agent onboarding，不是完整 persona engine。",
            "v0.2.0 不引入 LLM、Mem0、向量库、Telegram、Obsidian Plugin 或自动评分器。",
            "后续如做 Persona Engine，应从这个确定性快照升级，而不是另起系统。",
        ]
    )
    source_lines = _bullet_lines(SOURCE_PATHS)
    return f"""{render_5min(lessons).rstrip()}

## 9. Agent 接入边界

{access_boundaries}

## 10. 完整 lesson 清单

{lesson_catalog}

## 11. v0.2 范围

{v02_scope}

## 来源

{source_lines}
"""


def compile_snapshot(depth: str = "5min") -> str:
    """Return the requested onboarding snapshot as Markdown."""
    if depth not in VALID_DEPTHS:
        raise ValueError(f"invalid depth {depth!r}; expected one of {sorted(VALID_DEPTHS)}")

    # Fail early if required sources are missing. The current renderer is
    # deterministic, but these reads keep the output grounded in repo truth.
    for rel in REQUIRED_SOURCE_PATHS:
        _read_source(rel)

    lessons = load_lessons()
    if depth == "30s":
        return render_30s().rstrip() + "\n"
    if depth == "5min":
        return render_5min(lessons).rstrip() + "\n"
    return render_full(lessons).rstrip() + "\n"


def _safe_stdout_write(text: str) -> None:
    """Write human-facing CLI output without crashing on legacy consoles."""
    try:
        sys.stdout.write(text)
    except BrokenPipeError:
        return
    except UnicodeEncodeError:
        if not sys.stdout.isatty() and hasattr(sys.stdout, "buffer"):
            try:
                sys.stdout.buffer.write(text.encode("utf-8"))
            except BrokenPipeError:
                return
            return
        encoding = sys.stdout.encoding or "utf-8"
        fallback = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        try:
            sys.stdout.write(fallback)
        except BrokenPipeError:
            return


def cmd_compile(args: argparse.Namespace) -> int:
    try:
        _safe_stdout_write(compile_snapshot(args.depth))
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_check(_args: argparse.Namespace) -> int:
    """Verify all source files exist and are tracked by git."""
    import subprocess

    all_ok = True
    tracked_paths = REQUIRED_SOURCE_PATHS + [SNAPSHOT_PAGE]
    for rel in tracked_paths:
        path = REPO_ROOT / rel
        if not path.exists():
            print(f"MISSING {rel}", file=sys.stderr)
            all_ok = False
            continue
        try:
            subprocess.check_output(
                ["git", "ls-files", "--error-unmatch", rel],
                cwd=REPO_ROOT,
                stderr=subprocess.STDOUT,
            )
            print(f"OK      {rel}")
        except subprocess.CalledProcessError:
            print(f"UNTRACKED {rel}", file=sys.stderr)
            all_ok = False
    for rel in OPTIONAL_SOURCE_PATHS:
        path = REPO_ROOT / rel
        if not path.exists():
            print(f"OPTIONAL_MISSING {rel}")
            continue
        try:
            subprocess.check_output(
                ["git", "ls-files", "--error-unmatch", rel],
                cwd=REPO_ROOT,
                stderr=subprocess.STDOUT,
            )
            print(f"OK      {rel}")
        except subprocess.CalledProcessError:
            print(f"OPTIONAL_UNTRACKED {rel}")
    snapshot_text = _read_source(SNAPSHOT_PAGE) if (REPO_ROOT / SNAPSHOT_PAGE).exists() else ""
    for phrase in SNAPSHOT_PAGE_REQUIRED_PHRASES:
        if phrase not in snapshot_text:
            print(f"SNAPSHOT_MISSING_PHRASE {phrase}", file=sys.stderr)
            all_ok = False
    return 0 if all_ok else 1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tm_persona.py", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    compile_p = sub.add_parser("compile", help="compile onboarding snapshot")
    compile_p.add_argument("--depth", default="5min")
    compile_p.set_defaults(func=cmd_compile)

    check_p = sub.add_parser("check", help="verify source files are present and tracked")
    check_p.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
