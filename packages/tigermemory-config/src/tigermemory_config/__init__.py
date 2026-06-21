"""IDE/agent configuration explainer and Runtime Config Manager for TigerMemory.

Inputs: local repository path plus known project-level agent config surfaces
        such as AGENTS.md, CLAUDE.md, .cursor/rules, and .githooks.
Outputs: Chinese explanation records describing what each config surface is
         likely controlling, plus optional Gate 3 manager plan/apply/verify
         commands for approved runtime policy entrypoints.
Depends-on: Python stdlib only. The explainer remains read-only. Manager writes
            only when invoked through explicit subcommands that require --yes.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
from typing import Iterable


SUPPORT_FULL = "full"
SUPPORT_PARTIAL = "partial"
SUPPORT_SOFT_ONLY = "soft_only"
SUPPORT_UNSUPPORTED_BUT_EXPLAINED = "unsupported_but_explained"
SUPPORT_REQUIRES_EXTERNAL_GUARD = "requires_external_guard"

SUPPORT_LABELS_CN = {
    SUPPORT_FULL: "full：配置可被当前工具完整表达和执行。",
    SUPPORT_PARTIAL: "partial：配置能表达一部分约束，但仍依赖 hook、权限或人工确认。",
    SUPPORT_SOFT_ONLY: "soft_only：配置主要是软提示，不能当作硬权限。",
    SUPPORT_UNSUPPORTED_BUT_EXPLAINED: "unsupported_but_explained：当前不能直接落地，但可以解释边界和替代路径。",
    SUPPORT_REQUIRES_EXTERNAL_GUARD: "requires_external_guard：需要 Git hook、权限系统或外部守卫才有强约束。",
}

KNOWN_FILES: dict[str, dict[str, str]] = {
    "AGENTS.md": {
        "target": "generic-agent",
        "kind": "project_policy",
        "support": SUPPORT_PARTIAL,
        "summary_cn": "项目级 AI 行为准则入口，通常约束所有 agent 在这个仓库里的开工、写入、提交和收尾方式。",
    },
    "CLAUDE.md": {
        "target": "claude-code",
        "kind": "project_policy",
        "support": SUPPORT_SOFT_ONLY,
        "summary_cn": "Claude Code 项目提示词入口，主要是软提示规则，不能单独当作硬权限保护。",
    },
    "GEMINI.md": {
        "target": "gemini",
        "kind": "project_policy",
        "support": SUPPORT_SOFT_ONLY,
        "summary_cn": "Gemini 项目提示词入口，主要帮助模型理解项目约束。",
    },
    "CODEX.md": {
        "target": "codex",
        "kind": "project_policy",
        "support": SUPPORT_SOFT_ONLY,
        "summary_cn": "Codex 项目提示词入口，主要是项目内行为说明。",
    },
    ".cursorrules": {
        "target": "cursor",
        "kind": "project_policy",
        "support": SUPPORT_SOFT_ONLY,
        "summary_cn": "Cursor 旧式项目规则入口，通常是软提示，不等于硬权限。",
    },
    ".windsurfrules": {
        "target": "windsurf",
        "kind": "project_policy",
        "support": SUPPORT_SOFT_ONLY,
        "summary_cn": "Windsurf/Cascade 规则入口，主要影响 agent 行为风格和流程。",
    },
}

RULE_DIRS: tuple[tuple[str, str, str], ...] = (
    (".cursor/rules", "cursor", "Cursor 分文件规则目录，适合按主题组织软提示规则。"),
    (".clinerules", "cline", "Cline 项目规则入口，通常配合审批和工具权限使用。"),
    (".roo/rules", "roocode", "RooCode 规则目录，通常配合 mode 和工具组使用。"),
    (".codex", "codex", "Codex 项目配置目录，可能包含项目级 hooks 或本地策略。"),
)

CONTROL_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("git pull", "开工前同步代码"),
    ("git status", "检查工作区状态"),
    ("--no-verify", "禁止绕过提交检查"),
    ("write_memory", "收尾写入记忆"),
    ("get_agent_onboarding", "开工读取 onboarding"),
    ("hook", "使用 hook 或自动化守卫"),
    ("permission", "涉及工具权限"),
    ("delete", "涉及删除/危险操作"),
    ("secret", "涉及密钥或隐私保护"),
    (".env", "涉及环境变量/密钥文件"),
    ("mcp", "涉及 MCP 工具接入"),
)


def _detect_repo_root() -> pathlib.Path:
    explicit = os.environ.get("TIGERMEMORY_ROOT")
    if explicit:
        return pathlib.Path(explicit).resolve()
    here = pathlib.Path.cwd().resolve()
    for ancestor in [here, *here.parents]:
        if (ancestor / ".git").is_dir():
            return ancestor
    return here


def configure_stdio() -> None:
    if sys.version_info >= (3, 7):
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                try:
                    stream.reconfigure(errors="backslashreplace")
                except Exception:
                    pass


def _read_text(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _line_count(text: str) -> int:
    return len(text.splitlines()) if text else 0


def _detect_controls(text: str) -> list[str]:
    lowered = text.lower()
    controls = [label for keyword, label in CONTROL_KEYWORDS if keyword.lower() in lowered]
    return sorted(set(controls))


def _risk_notes(kind: str, support: str, controls: list[str]) -> list[str]:
    risks: list[str] = []
    if support == SUPPORT_SOFT_ONLY:
        risks.append("这是软提示规则，模型可能遵守，但不能当作硬权限。")
    if support == SUPPORT_PARTIAL:
        risks.append("部分规则需要配合 hooks、权限或人工确认才有强约束。")
    if kind == "git_hook":
        risks.append("hook 只有在本仓库 core.hooksPath 指向 .githooks 时才会生效。")
    if "涉及密钥或隐私保护" in controls or "涉及环境变量/密钥文件" in controls:
        risks.append("涉及隐私或密钥时，应再配合文件权限、发布脱敏和人工确认。")
    return risks


def _make_item(
    *,
    root: pathlib.Path,
    path: pathlib.Path,
    target: str,
    kind: str,
    support: str,
    summary_cn: str,
) -> dict[str, object]:
    rel = path.relative_to(root).as_posix()
    text = _read_text(path)
    controls = _detect_controls(text)
    return {
        "path": rel,
        "target": target,
        "kind": kind,
        "support": support,
        "support_label_cn": SUPPORT_LABELS_CN.get(support, support),
        "summary_cn": summary_cn,
        "line_count": _line_count(text),
        "controls_cn": controls,
        "risks_cn": _risk_notes(kind, support, controls),
    }


def _iter_rule_dir_files(root: pathlib.Path, rel_dir: str) -> Iterable[pathlib.Path]:
    base = root / rel_dir
    if not base.exists():
        return []
    if base.is_file():
        return [base]
    return sorted(p for p in base.rglob("*") if p.is_file() and p.suffix.lower() in {".md", ".txt", ".json", ".toml"})


def scan_config_surfaces(root: pathlib.Path) -> list[dict[str, object]]:
    """Return read-only explanations for known IDE/agent config surfaces."""
    root = root.resolve()
    items: list[dict[str, object]] = []

    for rel, meta in KNOWN_FILES.items():
        path = root / rel
        if path.is_file():
            items.append(
                _make_item(
                    root=root,
                    path=path,
                    target=meta["target"],
                    kind=meta["kind"],
                    support=meta["support"],
                    summary_cn=meta["summary_cn"],
                )
            )

    hooks_dir = root / ".githooks"
    if hooks_dir.is_dir():
        for path in sorted(p for p in hooks_dir.iterdir() if p.is_file()):
            items.append(
                _make_item(
                    root=root,
                    path=path,
                    target="git",
                    kind="git_hook",
                    support=SUPPORT_REQUIRES_EXTERNAL_GUARD,
                    summary_cn="Git hook 脚本，能在提交或其他 Git 动作时提供硬守卫，但依赖本地 Git hooksPath 配置。",
                )
            )

    for rel_dir, target, summary in RULE_DIRS:
        for path in _iter_rule_dir_files(root, rel_dir):
            items.append(
                _make_item(
                    root=root,
                    path=path,
                    target=target,
                    kind="project_policy",
                    support=SUPPORT_SOFT_ONLY,
                    summary_cn=summary,
                )
            )

    return sorted(items, key=lambda item: str(item["path"]))


def summarize(items: list[dict[str, object]]) -> dict[str, object]:
    by_target: dict[str, int] = {}
    by_support: dict[str, int] = {}
    for item in items:
        by_target[str(item["target"])] = by_target.get(str(item["target"]), 0) + 1
        by_support[str(item["support"])] = by_support.get(str(item["support"]), 0) + 1
    return {
        "total": len(items),
        "by_target": dict(sorted(by_target.items())),
        "by_support": dict(sorted(by_support.items())),
    }


def _cmd_explain(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(prog="tigermemory-config", description=__doc__)
    parser.add_argument("--root", default=None, help="repository root to scan")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of Chinese text")
    args = parser.parse_args(argv)

    root = pathlib.Path(args.root).resolve() if args.root else _detect_repo_root()
    items = scan_config_surfaces(root)
    result = {
        "ok": True,
        "root": str(root),
        "summary": summarize(items),
        "items": items,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"扫描目录：{root}")
    print(f"发现配置入口：{result['summary']['total']} 个")
    for item in items:
        print(f"- {item['path']} [{item['target']} / {item['support']}]")
        print(f"  说明：{item['summary_cn']}")
        if item["controls_cn"]:
            print("  已识别控制点：" + "、".join(str(x) for x in item["controls_cn"]))
        if item["risks_cn"]:
            print("  风险提示：" + "；".join(str(x) for x in item["risks_cn"]))
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "manager":
        from . import manager

        return manager.main(argv[1:])
    if argv and argv[0] == "agent":
        from . import agent_connect

        return agent_connect.main(argv[1:])
    if argv and argv[0] == "explain":
        return _cmd_explain(argv[1:])
    return _cmd_explain(argv)


if __name__ == "__main__":
    sys.exit(main())
