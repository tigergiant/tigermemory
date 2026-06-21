"""Project-level AI agent connection manager for TigerMemory public starters.

The manager borrows the safe part of AgentMemory's connect flow: plan first,
backup before write, merge managed blocks idempotently, verify by reading the
file back, and keep MCP/global configuration behind explicit availability
checks. It intentionally defaults to project-local files.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import sys
import time
from dataclasses import dataclass
from typing import Sequence


BEGIN_RE = re.compile(
    r"<!-- tigermemory-agent-connect:start target=(?P<target>[\w.-]+) [^>]*-->\r?\n.*?\r?\n<!-- tigermemory-agent-connect:end -->",
    re.DOTALL,
)
BEGIN_TEMPLATE = "<!-- tigermemory-agent-connect:start target={target} snapshot_id={snapshot_id} -->"
END_MARKER = "<!-- tigermemory-agent-connect:end -->"
DEFAULT_BACKUP_DIR = pathlib.Path(".tmp") / "agent-connect-backups"
DEFAULT_TARGETS = ("codex", "claude-code", "cursor", "hooks")
KNOWN_TARGETS = (*DEFAULT_TARGETS, "mcp")


@dataclass(frozen=True)
class AgentConnectTarget:
    target: str
    target_id: str
    rel_path: str
    write_policy: str
    label_cn: str
    summary_cn: str


TARGETS: dict[str, list[AgentConnectTarget]] = {
    "codex": [
        AgentConnectTarget(
            "codex",
            "root-agents",
            "AGENTS.md",
            "managed_block",
            "Codex / 通用 Agent 规则",
            "让 Codex 和其他会读 AGENTS.md 的工具知道先查 TigerMemory、用提案而不是直接写长期 Wiki。",
        ),
    ],
    "claude-code": [
        AgentConnectTarget(
            "claude-code",
            "root-claude",
            "CLAUDE.md",
            "managed_block",
            "Claude Code 项目规则",
            "给 Claude Code 一份项目内规则入口，强调 tm ask/search/admin propose 的安全顺序。",
        ),
    ],
    "cursor": [
        AgentConnectTarget(
            "cursor",
            "cursor-rule",
            ".cursor/rules/tigermemory.md",
            "managed_block",
            "Cursor 项目规则",
            "给 Cursor/IDE agent 一份项目内规则文件，不改全局 IDE 设置。",
        ),
    ],
    "hooks": [
        AgentConnectTarget(
            "hooks",
            "hooks-readme",
            ".tigermemory/agent-hooks/README.md",
            "template",
            "Hooks 模板说明",
            "准备可检查的 hooks 说明，但不自动启用任何拦截脚本。",
        ),
        AgentConnectTarget(
            "hooks",
            "pre-tool-use-example",
            ".tigermemory/agent-hooks/pre_tool_use.example.ps1",
            "template",
            "PreToolUse 示例",
            "准备一个只读提醒示例，供高级用户手动接入 Codex/Claude hook 系统。",
        ),
    ],
    "mcp": [
        AgentConnectTarget(
            "mcp",
            "mcp-command",
            "",
            "availability_check",
            "MCP 服务端",
            "只有检测到可执行的 TigerMemory MCP 命令后，才应该写入真实 MCP 客户端配置。",
        ),
    ],
}


def _configure_stdio() -> None:
    if sys.version_info >= (3, 7):
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _now() -> str:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).replace(microsecond=0).isoformat()


def _detect_repo_root() -> pathlib.Path:
    explicit = os.environ.get("TIGERMEMORY_INSTANCE_ROOT") or os.environ.get("TIGERMEMORY_ROOT")
    if explicit:
        return pathlib.Path(explicit).resolve()
    cwd = pathlib.Path.cwd().resolve()
    for ancestor in [cwd, *cwd.parents]:
        if (ancestor / "tigermemory_cli.py").is_file() and (ancestor / "wiki").is_dir():
            return ancestor
    return cwd


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: pathlib.Path) -> str | None:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError:
        return None


def _snapshot_id() -> str:
    seed = f"{_now()}:{os.getpid()}:{time.monotonic_ns()}".encode("utf-8")
    return f"agent-connect-{_sha256_bytes(seed)[:12]}"


def _safe_backup_name(path: pathlib.Path) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "__", str(path)).strip("_") or "target"


def _selected_targets(targets: Sequence[str] | None) -> list[str]:
    raw = list(targets or DEFAULT_TARGETS)
    if "all" in raw:
        raw = list(DEFAULT_TARGETS)
    selected: list[str] = []
    for target in raw:
        if target not in KNOWN_TARGETS:
            raise ValueError(f"unknown target: {target}")
        if target not in selected:
            selected.append(target)
    return selected


def _target_path(root: pathlib.Path, target: AgentConnectTarget) -> pathlib.Path | None:
    if not target.rel_path:
        return None
    return root / pathlib.Path(target.rel_path)


def mcp_command_status() -> dict[str, object]:
    command = shutil.which("tm-mcp")
    return {
        "available": bool(command),
        "command": command or "tm-mcp",
        "reason": "found_on_path" if command else "missing_tm_mcp_command",
        "safe_to_write_client_config": bool(command),
    }


def _is_private_source_agents(path: pathlib.Path) -> bool:
    try:
        head = "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[:40])
    except OSError:
        return False
    return "TigerMemory Agent 入口规则" in head or "--- project-doc ---" in head


def _is_private_source_checkout(root: pathlib.Path) -> bool:
    return _is_private_source_agents(root / "AGENTS.md")


def _is_protected_private_target(root: pathlib.Path, target: AgentConnectTarget) -> bool:
    return target.write_policy in {"managed_block", "template"} and _is_private_source_checkout(root)


def _managed_block(target: str, snapshot_id: str) -> str:
    lines = [
        BEGIN_TEMPLATE.format(target=target, snapshot_id=snapshot_id),
        "# TigerMemory Agent Connection",
        "",
        "TigerMemory is this project's local AI brain. Before answering project questions:",
        "",
        "1. Prefer `tm ask --query \"...\" --scope all` for natural-language answers with local evidence.",
        "2. Use `tm search --query \"...\" --scope wiki` when you need to inspect raw local pages.",
        "3. Use `tm admin propose` to draft durable Wiki updates; `tm admin approve` is human-only.",
        "4. Do not read or print API keys, cookies, passwords, private keys, identity numbers, or personal records.",
        "5. Keep user data in this checkout. Do not move, delete, or rewrite knowledge files unless the user clearly asks.",
        "6. If TigerMemory is not configured yet, guide the user to `/start`, `tm llm status`, and `tm agent status`.",
        "",
        "For beginners: local Wiki + LLM is the default path. MCP and hooks are optional advanced integrations.",
        END_MARKER,
    ]
    return "\n".join(lines) + "\n"


def _hook_readme() -> str:
    return (
        "# TigerMemory Agent Hooks\n\n"
        "These files are templates only. They are not active until you wire them into your AI tool's hook system.\n\n"
        "Recommended order:\n\n"
        "1. Use project rules first: `tm agent apply --target codex --target claude-code --yes`.\n"
        "2. Verify local evidence works: `tm ask --offline --query \"agent behavior rules\" --scope wiki`.\n"
        "3. Only then adapt hook examples to your own tool.\n\n"
        "Hooks must never store API keys or silently approve Wiki writes.\n"
    )


def _hook_example() -> str:
    return (
        "# TigerMemory passive PreToolUse example.\n"
        "# This is intentionally not enabled automatically.\n"
        "$inputJson = [Console]::In.ReadToEnd()\n"
        "if ($inputJson -match 'tm admin approve') {\n"
        "  Write-Error 'TigerMemory: tm admin approve is human-only. Ask the user to approve in the dashboard.'\n"
        "  exit 2\n"
        "}\n"
        "exit 0\n"
    )


def _template_content(target: AgentConnectTarget, snapshot_id: str) -> str:
    if target.write_policy == "managed_block":
        return _managed_block(target.target, snapshot_id)
    if target.target_id == "hooks-readme":
        return _hook_readme()
    if target.target_id == "pre-tool-use-example":
        return _hook_example()
    raise ValueError(f"no template for target: {target.target_id}")


def _upsert_managed_block(text: str, block: str, target: str) -> str:
    text = text.rstrip()
    target_re = re.compile(
        rf"<!-- tigermemory-agent-connect:start target={re.escape(target)} [^>]*-->\r?\n.*?\r?\n<!-- tigermemory-agent-connect:end -->",
        re.DOTALL,
    )
    if target_re.search(text):
        return target_re.sub(block.rstrip(), text).rstrip() + "\n"
    return (text + "\n\n" + block).lstrip()


def _path_status(root: pathlib.Path, target: AgentConnectTarget) -> dict[str, object]:
    if target.write_policy == "availability_check":
        status = mcp_command_status()
        return {
            "target": target.target,
            "target_id": target.target_id,
            "label_cn": target.label_cn,
            "write_policy": target.write_policy,
            "status": "available" if status["available"] else "blocked",
            "exists": bool(status["available"]),
            "path": None,
            "reason": status["reason"],
            "mcp": status,
        }
    path = _target_path(root, target)
    assert path is not None
    exists = path.is_file()
    text = path.read_text(encoding="utf-8", errors="replace") if exists else ""
    protected = _is_protected_private_target(root, target)
    has_block = False
    if target.write_policy == "managed_block":
        has_block = any(match.group("target") == target.target for match in BEGIN_RE.finditer(text))
        status = "protected" if protected else ("ok" if has_block else "missing_block")
    else:
        status = "protected" if protected else ("ok" if exists else "missing")
    return {
        "target": target.target,
        "target_id": target.target_id,
        "label_cn": target.label_cn,
        "summary_cn": target.summary_cn,
        "write_policy": target.write_policy,
        "path": str(path),
        "rel_path": target.rel_path,
        "exists": exists,
        "has_managed_block": has_block,
        "protected": protected,
        "status": status,
        "reason": "private_source_checkout_is_read_only_for_agent_connect" if protected else None,
        "sha256": _sha256_file(path),
    }


def plan_agent_connect(targets: Sequence[str] | None = None, *, repo_root: pathlib.Path | None = None) -> dict[str, object]:
    root = (repo_root or _detect_repo_root()).resolve()
    selected = _selected_targets(targets)
    rows: list[dict[str, object]] = []
    for target in selected:
        for item in TARGETS[target]:
            status = _path_status(root, item)
            status["will_write"] = item.write_policy in {"managed_block", "template"} and not status.get("protected")
            rows.append(status)
    return {
        "ok": True,
        "action": "plan",
        "repo_root": str(root),
        "targets": rows,
        "mcp": mcp_command_status(),
        "notes_cn": [
            "默认只写项目内规则和模板，不修改全局 Codex / Claude / Cursor 配置。",
            "MCP 客户端配置只有在检测到 tm-mcp 命令后才建议自动写入。",
        ],
    }


def status_agent_connect(targets: Sequence[str] | None = None, *, repo_root: pathlib.Path | None = None) -> dict[str, object]:
    result = plan_agent_connect(targets, repo_root=repo_root)
    statuses = [str(item.get("status")) for item in result["targets"]]  # type: ignore[index]
    result["action"] = "status"
    result["ok"] = all(status in {"ok", "available"} for status in statuses if status != "blocked")
    result["configured_count"] = sum(1 for status in statuses if status in {"ok", "available"})
    result["missing_count"] = sum(1 for status in statuses if status in {"missing", "missing_block"})
    result["blocked_count"] = sum(1 for status in statuses if status == "blocked")
    return result


def apply_agent_connect(
    targets: Sequence[str] | None = None,
    *,
    yes: bool = False,
    dry_run: bool = False,
    repo_root: pathlib.Path | None = None,
    backup_root: pathlib.Path | None = None,
) -> dict[str, object]:
    root = (repo_root or _detect_repo_root()).resolve()
    if not yes and not dry_run:
        return {"ok": False, "action": "apply", "repo_root": str(root), "errors": ["apply requires --yes unless --dry-run is set"]}
    selected = _selected_targets(targets)
    snapshot_id = _snapshot_id()
    backup_root = backup_root or (root / DEFAULT_BACKUP_DIR)
    snapshot_root = backup_root / snapshot_id
    rows: list[dict[str, object]] = []
    errors: list[str] = []
    for target in selected:
        for item in TARGETS[target]:
            if item.write_policy == "availability_check":
                status = _path_status(root, item)
                rows.append({**status, "changed": False, "dry_run": dry_run})
                if not status["exists"]:
                    errors.append(f"{item.target}: {status['reason']}")
                continue
            if _is_protected_private_target(root, item):
                status = _path_status(root, item)
                rows.append({**status, "changed": False, "dry_run": dry_run, "will_write": False})
                continue
            path = _target_path(root, item)
            assert path is not None
            existed_before = path.exists()
            before = path.read_bytes() if existed_before else b""
            before_sha = _sha256_bytes(before)
            backup_path = snapshot_root / item.target / (_safe_backup_name(path) + ".bak")
            content = _template_content(item, snapshot_id)
            if item.write_policy == "managed_block":
                before_text = before.decode("utf-8", errors="replace") if before else ""
                after_text = _upsert_managed_block(before_text, content, item.target)
                after_bytes = after_text.encode("utf-8")
            else:
                after_bytes = content.encode("utf-8")
            changed = before != after_bytes
            if not dry_run:
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                backup_path.write_bytes(before)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(after_bytes)
            rows.append(
                {
                    "target": item.target,
                    "target_id": item.target_id,
                    "label_cn": item.label_cn,
                    "write_policy": item.write_policy,
                    "path": str(path),
                    "rel_path": item.rel_path,
                    "backup_path": str(backup_path),
                    "before_sha256": before_sha,
                    "after_sha256": _sha256_bytes(after_bytes),
                    "backup_sha256": _sha256_bytes(before),
                    "existed_before": existed_before,
                    "changed": changed,
                    "dry_run": dry_run,
                }
            )
    manifest = {
        "snapshot_id": snapshot_id,
        "created_at": _now(),
        "repo_root": str(root),
        "targets": rows,
        "errors": errors,
    }
    manifest_path = snapshot_root / "manifest.json"
    if not dry_run:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": not errors,
        "action": "apply",
        "repo_root": str(root),
        "snapshot_id": snapshot_id,
        "manifest_path": str(manifest_path),
        "targets": rows,
        "errors": errors,
        "dry_run": dry_run,
    }


def rollback_agent_connect(
    snapshot_id: str,
    *,
    targets: Sequence[str] | None = None,
    yes: bool = False,
    dry_run: bool = False,
    repo_root: pathlib.Path | None = None,
    backup_root: pathlib.Path | None = None,
) -> dict[str, object]:
    root = (repo_root or _detect_repo_root()).resolve()
    if not yes and not dry_run:
        return {"ok": False, "action": "rollback", "repo_root": str(root), "errors": ["rollback requires --yes unless --dry-run is set"]}
    backup_root = backup_root or (root / DEFAULT_BACKUP_DIR)
    manifest_path = backup_root / snapshot_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    selected = set(targets or [])
    rows: list[dict[str, object]] = []
    errors: list[str] = []
    for item in manifest.get("targets", []):
        row = dict(item)
        if selected and row.get("target") not in selected:
            continue
        if not row.get("backup_path") or not row.get("path"):
            continue
        backup = pathlib.Path(str(row["backup_path"]))
        path = pathlib.Path(str(row["path"]))
        backup_bytes = backup.read_bytes()
        if _sha256_bytes(backup_bytes) != row.get("backup_sha256"):
            errors.append(f"{row.get('target')}:{row.get('target_id')}: backup sha256 mismatch")
            continue
        if not dry_run:
            if row.get("existed_before") is False:
                if path.exists():
                    path.unlink()
                _remove_empty_parents(path.parent, root)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(backup_bytes)
        rows.append({"target": row.get("target"), "target_id": row.get("target_id"), "path": str(path), "restored": not dry_run})
    return {"ok": not errors, "action": "rollback", "repo_root": str(root), "snapshot_id": snapshot_id, "targets": rows, "errors": errors, "dry_run": dry_run}


def _remove_empty_parents(start: pathlib.Path, root: pathlib.Path) -> None:
    current = start.resolve()
    root_resolved = root.resolve()
    while current != root_resolved and root_resolved in current.parents:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def mcp_config_payload(client: str = "codex") -> dict[str, object]:
    status = mcp_command_status()
    if client not in {"codex", "claude-code", "claude-desktop", "cursor", "json"}:
        raise ValueError(f"unsupported MCP client: {client}")
    config = {
        "mcpServers": {
            "tigermemory": {
                "command": "tm-mcp",
                "args": ["--role=reader", "--tool-profile=memory"],
            }
        }
    }
    if client == "codex":
        text = "[mcp_servers.tigermemory]\ncommand = \"tm-mcp\"\nargs = [\"--role=reader\", \"--tool-profile=memory\"]\n"
    else:
        text = json.dumps(config, ensure_ascii=False, indent=2)
    return {"ok": bool(status["available"]), "client": client, "mcp": status, "config": config, "text": text}


def _print_result(result: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"action={result.get('action')} ok={str(bool(result.get('ok'))).lower()}")
    if result.get("repo_root"):
        print(f"repo_root={result.get('repo_root')}")
    if result.get("snapshot_id"):
        print(f"snapshot_id={result.get('snapshot_id')}")
    for item in result.get("targets", []):  # type: ignore[union-attr]
        row = dict(item)  # type: ignore[arg-type]
        label = row.get("label_cn") or row.get("target_id")
        status = row.get("status") or ("changed" if row.get("changed") else "unchanged")
        print(f"- {row.get('target')}:{row.get('target_id')} {status} {label}")
        if row.get("path"):
            print(f"  path={row.get('path')}")
        if row.get("reason"):
            print(f"  reason={row.get('reason')}")
    for error in result.get("errors", []) or []:  # type: ignore[union-attr]
        print(f"error={error}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(prog="tm agent", description="Connect AI tools to a TigerMemory project safely")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "status", "apply"):
        p = sub.add_parser(name)
        p.add_argument("--target", action="append", choices=[*KNOWN_TARGETS, "all"], default=None)
        p.add_argument("--repo-root", type=pathlib.Path, default=None)
        p.add_argument("--json", action="store_true")
        if name == "apply":
            p.add_argument("--yes", action="store_true")
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("--backup-root", type=pathlib.Path, default=None)
    rb = sub.add_parser("rollback")
    rb.add_argument("--snapshot-id", required=True)
    rb.add_argument("--target", action="append", choices=[*KNOWN_TARGETS, "all"], default=None)
    rb.add_argument("--repo-root", type=pathlib.Path, default=None)
    rb.add_argument("--backup-root", type=pathlib.Path, default=None)
    rb.add_argument("--yes", action="store_true")
    rb.add_argument("--dry-run", action="store_true")
    rb.add_argument("--json", action="store_true")
    pc = sub.add_parser("print-config")
    pc.add_argument("--client", choices=["codex", "claude-code", "claude-desktop", "cursor", "json"], default="codex")
    pc.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv or []))
    try:
        if args.command == "plan":
            result = plan_agent_connect(args.target, repo_root=args.repo_root)
        elif args.command == "status":
            result = status_agent_connect(args.target, repo_root=args.repo_root)
        elif args.command == "apply":
            result = apply_agent_connect(args.target, yes=args.yes, dry_run=args.dry_run, repo_root=args.repo_root, backup_root=args.backup_root)
        elif args.command == "rollback":
            result = rollback_agent_connect(args.snapshot_id, targets=args.target, yes=args.yes, dry_run=args.dry_run, repo_root=args.repo_root, backup_root=args.backup_root)
        else:
            result = mcp_config_payload(args.client)
    except Exception as exc:
        result = {"ok": False, "action": getattr(args, "command", "unknown"), "errors": [str(exc)]}
    _print_result(result, as_json=bool(args.json))
    if args.command == "print-config" and not args.json and result.get("text"):
        print(str(result["text"]).rstrip())
    return 0 if result.get("ok") or args.command == "print-config" else 1


if __name__ == "__main__":
    raise SystemExit(main())
