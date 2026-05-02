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
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
from dataclasses import dataclass
from typing import Iterable

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

SOURCE_PATHS = [
    "AGENTS.md",
    "wiki/systems/tigermemory-agent-access.md",
    "wiki/systems/agent-write-toolkit.md",
    "wiki/self-evolution/lessons/index.md",
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


def render_30s() -> str:
    return """# tigermemory Agent Onboarding Snapshot (30s)

- 开工先做：`git pull --ff-only origin master`，再用 `git status --short | Measure-Object -Line` 判断完整 dirty 行数，并跑 `py tools/tm_io.py preflight`。
- 开工还必须跑 lessons 检索：`$env:TM_AGENT="<agent>"; py tools/tm_lessons.py search "<任务关键词>"`，读 top-3，避免重复事故。
- 写入边界：只写自己拥有的 wiki 分区；不确定、跨分区、self-evolution 提案走 inbox；`topic` 用 `selfevolution`，不是 `self-evolution`。
- 写入入口：inbox 用 `tm_io.py write-inbox` 或 MCP `write_inbox`；稳定 wiki 用 owner 路径；对话级事实用 `write_memory` 路由，不直接造 inbox 文件。
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
        ]
    )
    write_boundaries = _bullet_lines(
        [
            "`wiki/systems/` 可由 `codex` 或 `claude-code` 写；`wiki/self-evolution/` 只由 `claude-code` 直接写，其他 agent 走 inbox 提案。",
            "`AGENTS.md`、`schemas/`、根 `index.md` 属元规则，只有 `claude-code` 和 `human` 可改。",
            "`log.md` 不手写，由 `claude-code compile` 从 git log 汇总。",
            "跨分区、不确定、需人工二审内容写 inbox；self-evolution 的 inbox topic key 是 `selfevolution`。",
        ]
    )
    tool_entries = _bullet_lines(
        [
            "CLI preflight：`py tools/tm_io.py preflight`。",
            "CLI inbox：`py tools/tm_io.py write-inbox --agent <agent> --topic <topic> --title <title>`，正文走 stdin；强制投递用 `--force-inbox`。",
            "MCP 只读：`check_worktree`、`close_session`、`read_page`、`list_partition`、`lint_page`、`lint_repo`、`get_agent_onboarding`。",
            "MCP 写入：`write_inbox`、`propose_wiki_page`、`write_memory`；reader role 调用写工具会被拒绝。",
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
    lesson_entries = _bullet_lines(lesson_lines)
    return f"""# tigermemory Agent Onboarding Snapshot (5min)

## 1. 开工顺序

{start_steps}

## 2. 写入权限边界

{write_boundaries}

## 3. 工具入口

{tool_entries}

## 4. Live-state 优先原则

{live_state_rules}

## 5. 必须避免的 lesson

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

## 6. Agent 接入边界

{access_boundaries}

## 7. 完整 lesson 清单

{lesson_catalog}

## 8. v0.2 范围

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
    for rel in SOURCE_PATHS:
        _read_source(rel)

    lessons = load_lessons()
    if depth == "30s":
        return render_30s().rstrip() + "\n"
    if depth == "5min":
        return render_5min(lessons).rstrip() + "\n"
    return render_full(lessons).rstrip() + "\n"


def cmd_compile(args: argparse.Namespace) -> int:
    try:
        sys.stdout.write(compile_snapshot(args.depth))
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="tm_persona.py", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    compile_p = sub.add_parser("compile", help="compile onboarding snapshot")
    compile_p.add_argument("--depth", default="5min")
    compile_p.set_defaults(func=cmd_compile)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
