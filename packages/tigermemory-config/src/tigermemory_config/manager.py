"""Runtime Config Manager v0 for TigerMemory Gate 3 policy entries.

The manager owns the file-level safety loop for runtime policy entrypoints:
plan, backup, apply a managed block, verify, and rollback. v0 applies only to
OpenClaw and Hermes; other runtimes are intentionally preview-only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sys
import time
from dataclasses import dataclass
from typing import Iterable, Sequence


BEGIN_RE = re.compile(r"<!-- tigermemory-policy:start [^>]*-->\n.*?\n<!-- tigermemory-policy:end -->", re.DOTALL)
BEGIN_TEMPLATE = "<!-- tigermemory-policy:start canonical_sha={canonical_sha} snapshot_id={snapshot_id} -->"
END_MARKER = "<!-- tigermemory-policy:end -->"
DEFAULT_BACKUP_DIR = pathlib.Path(".tmp") / "gate3-manager-backups"
CANONICAL_REL = pathlib.Path("tools") / "gate3" / "canonical_v0.yaml"
APPLY_RUNTIMES = {"openclaw", "hermes"}
KNOWN_RUNTIMES = {
    "cursor",
    "claude-code",
    "trae-solo",
    "antigravity",
    "opencode",
    "deepseek-tui",
    "reasonix",
    "openclaw",
    "hermes",
    "codex",
    "windsurf",
}
SUPPORT_PARTIAL = "partial"
SUPPORT_UNSUPPORTED_BUT_EXPLAINED = "unsupported_but_explained"
CAPABILITY_LABELS_CN = {
    SUPPORT_PARTIAL: "partial：P2 只保证文件级写入、读回、验证和回滚，不保证运行时热加载。",
    SUPPORT_UNSUPPORTED_BUT_EXPLAINED: "unsupported_but_explained：P2 只解释或预览，不写入该 runtime 的真实配置。",
}


@dataclass(frozen=True)
class RuntimeTarget:
    runtime: str
    target_id: str
    rel_path: str
    write_policy: str
    support: str
    summary_cn: str


def _now() -> str:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).replace(microsecond=0).isoformat()


def _record_manager_event(
    event_type: str,
    result: dict[str, object],
    *,
    start: float,
    runtimes: Sequence[str] | None = None,
    dry_run: bool | None = None,
) -> None:
    try:
        from tigermemory_core import runtime_events as tm_runtime_events

        ok = bool(result.get("ok"))
        errors = result.get("errors") or []
        targets = result.get("targets") or []
        tm_runtime_events.record_event(
            event_type=event_type,
            service="tm-config-manager",
            component="runtime_config",
            ok=ok,
            severity=None if ok else "error",
            duration_ms=(time.monotonic() - start) * 1000,
            route="managed_runtime_config",
            outcome=str(result.get("action") or event_type),
            target_ref={
                "snapshot_id": result.get("snapshot_id"),
                "manifest_path": result.get("manifest_path"),
            },
            source_log="config-manager",
            error="; ".join(str(item) for item in errors[:3]) if errors else None,
            extra={
                "runtimes": list(runtimes or []),
                "target_count": len(targets) if isinstance(targets, list) else 0,
                "error_count": len(errors) if isinstance(errors, list) else 0,
                "dry_run": dry_run,
            },
        )
    except Exception:
        pass


def _detect_repo_root() -> pathlib.Path:
    explicit = os.environ.get("TIGERMEMORY_ROOT")
    if explicit:
        return pathlib.Path(explicit).resolve()
    here = pathlib.Path(__file__).resolve()
    for ancestor in [here, *here.parents]:
        if (ancestor / CANONICAL_REL).is_file() and (ancestor / "wiki").is_dir():
            return ancestor
    return pathlib.Path.cwd().resolve()


def _default_wsl_home() -> pathlib.Path:
    explicit = os.environ.get("TIGERMEMORY_MANAGER_WSL_HOME")
    if explicit:
        return pathlib.Path(explicit)
    home = pathlib.Path.home()
    if os.name == "nt":
        detected = _windows_wsl_home(home)
        if detected is not None:
            return detected
    return home


def _wsl_unc_candidates(home: pathlib.Path) -> list[pathlib.Path]:
    distro = os.environ.get("TIGERMEMORY_MANAGER_WSL_DISTRO") or os.environ.get("TM_WSL_DISTRO") or "Ubuntu"
    user = os.environ.get("TIGERMEMORY_MANAGER_WSL_USER") or os.environ.get("TM_WSL_USER") or home.name.lower()
    return [
        pathlib.Path("\\\\" + "wsl.localhost") / distro / "home" / user,
        pathlib.Path("\\\\" + "wsl$") / distro / "home" / user,
    ]


def _windows_wsl_home(home: pathlib.Path) -> pathlib.Path | None:
    for candidate in _wsl_unc_candidates(home):
        if candidate.is_dir():
            return candidate
    return None


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: pathlib.Path) -> str | None:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError:
        return None


def _safe_backup_name(path: pathlib.Path) -> str:
    raw = str(path)
    return re.sub(r"[^A-Za-z0-9._-]+", "__", raw).strip("_") or "target"


def _read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def runtime_targets(wsl_home: pathlib.Path | None = None) -> dict[str, list[RuntimeTarget]]:
    """Return v0 target registry keyed by runtime."""
    _ = wsl_home or _default_wsl_home()
    return {
        "openclaw": [
            RuntimeTarget("openclaw", "workspace-agents", "workspaces/openclaw/AGENTS.md", "managed_block", "partial", "OpenClaw 主工作区 AGENTS.md 行为规则入口。"),
            RuntimeTarget("openclaw", "workspace-soul", "workspaces/openclaw/SOUL.md", "managed_block", "partial", "OpenClaw 主工作区 SOUL.md 行为规则入口。"),
            RuntimeTarget("openclaw", "runtime-agents", ".openclaw/workspace/AGENTS.md", "managed_block", "partial", "OpenClaw 运行时工作区 AGENTS.md。"),
            RuntimeTarget("openclaw", "runtime-tools", ".openclaw/workspace/TOOLS.md", "managed_block", "partial", "OpenClaw 运行时工具说明入口，可承载 TigerMemory policy 提醒。"),
        ],
        "hermes": [
            RuntimeTarget("hermes", "profile-soul", ".hermes/profiles/tigermemory/SOUL.md", "managed_block", "partial", "Hermes tigermemory profile 的 SOUL.md 行为规则入口。"),
            RuntimeTarget("hermes", "profile-config", ".hermes/profiles/tigermemory/config.yaml", "backup_only", "partial", "Hermes tigermemory profile 配置文件；v0 只备份和验证可读，不写 policy。"),
        ],
    }


def _target_path(target: RuntimeTarget, wsl_home: pathlib.Path) -> pathlib.Path:
    return wsl_home / pathlib.Path(target.rel_path)


def _parse_scalar(line: str) -> str:
    return line.split(":", 1)[1].strip().strip('"').strip("'")


def load_canonical(path: pathlib.Path) -> dict[str, object]:
    """Load the small Gate 3 canonical YAML without adding a runtime dependency."""
    text = path.read_text(encoding="utf-8")
    preferences: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    block_field: str | None = None
    block_lines: list[str] = []

    def flush_block() -> None:
        nonlocal block_field, block_lines
        if current is not None and block_field:
            current[block_field] = "\n".join(line.rstrip() for line in block_lines).strip()
        block_field = None
        block_lines = []

    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("- id:"):
            flush_block()
            current = {"id": _parse_scalar(stripped)}
            preferences.append(current)
            continue
        if current is None:
            continue
        if block_field and (raw.startswith("      ") or not stripped):
            block_lines.append(raw[6:] if raw.startswith("      ") else "")
            continue
        if stripped.startswith("title:"):
            flush_block()
            current["title"] = _parse_scalar(stripped)
        elif stripped.startswith("severity:"):
            flush_block()
            current["severity"] = _parse_scalar(stripped)
        elif stripped.startswith("description: |"):
            flush_block()
            block_field = "description"
        elif stripped.startswith("natural_language: |"):
            flush_block()
            block_field = "natural_language"
    flush_block()
    return {
        "canonical_sha256": _sha256_bytes(text.encode("utf-8")),
        "preference_ids": [item["id"] for item in preferences],
        "preferences": preferences,
    }


def render_managed_block(canonical: dict[str, object], snapshot_id: str) -> str:
    lines = [
        BEGIN_TEMPLATE.format(canonical_sha=str(canonical["canonical_sha256"])[:12], snapshot_id=snapshot_id),
        "# TigerMemory Preferences",
        "",
        "These rules are managed by TigerMemory Runtime Config Manager v0.",
        "Do not edit inside this managed block; edit tools/gate3/canonical_v0.yaml instead.",
        "",
    ]
    for item in canonical["preferences"]:  # type: ignore[index]
        pref = dict(item)  # type: ignore[arg-type]
        lines.extend(
            [
                f"## {pref['id']} - {pref.get('title', '')}",
                f"- Severity: {pref.get('severity', 'should')}",
                f"- Rule: {pref.get('natural_language', '').strip()}",
                f"- Detail: {pref.get('description', '').strip()}",
                "",
            ]
        )
    lines.append(END_MARKER)
    return "\n".join(lines).strip() + "\n"


def upsert_managed_block(text: str, block: str) -> str:
    text = text.rstrip()
    if BEGIN_RE.search(text):
        return BEGIN_RE.sub(block.rstrip(), text).rstrip() + "\n"
    return (text + "\n\n" + block).lstrip()


def _current_managed_block(text: str) -> str:
    match = BEGIN_RE.search(text)
    return match.group(0) if match else ""


def _managed_block_canonical_sha(block: str) -> str | None:
    match = re.search(r"<!-- tigermemory-policy:start\s+([^>]*)-->", block)
    if not match:
        return None
    for token in match.group(1).split():
        if token.startswith("canonical_sha="):
            return token.split("=", 1)[1]
    return None


def _selected_runtimes(runtimes: Sequence[str] | None) -> list[str]:
    selected = list(runtimes or sorted(KNOWN_RUNTIMES))
    unknown = [runtime for runtime in selected if runtime not in KNOWN_RUNTIMES]
    if unknown:
        raise ValueError("unknown runtime(s): " + ", ".join(sorted(unknown)))
    return selected


def build_plan(
    runtimes: Sequence[str] | None = None,
    *,
    wsl_home: pathlib.Path | None = None,
    repo_root: pathlib.Path | None = None,
) -> dict[str, object]:
    repo_root = repo_root or _detect_repo_root()
    wsl_home = wsl_home or _default_wsl_home()
    target_map = runtime_targets(wsl_home)
    rows: list[dict[str, object]] = []
    for runtime in _selected_runtimes(runtimes):
        targets = []
        for target in target_map.get(runtime, []):
            path = _target_path(target, wsl_home)
            targets.append(
                {
                    "target_id": target.target_id,
                    "path": str(path),
                    "exists": path.exists(),
                    "write_policy": target.write_policy,
                    "support": target.support,
                    "summary_cn": target.summary_cn,
                }
            )
        rows.append(
            {
                "runtime": runtime,
                "apply_supported": runtime in APPLY_RUNTIMES,
                "mode": "apply" if runtime in APPLY_RUNTIMES else "preview_only",
                "targets": targets,
            }
        )
    return {
        "ok": True,
        "action": "plan",
        "repo_root": str(repo_root),
        "wsl_home": str(wsl_home),
        "runtimes": rows,
    }


def runtime_capabilities(runtimes: Sequence[str] | None = None) -> dict[str, object]:
    """Return read-only P2 capability labels for every known runtime."""
    rows: list[dict[str, object]] = []
    for runtime in _selected_runtimes(runtimes):
        apply_supported = runtime in APPLY_RUNTIMES
        support = SUPPORT_PARTIAL if apply_supported else SUPPORT_UNSUPPORTED_BUT_EXPLAINED
        rows.append(
            {
                "runtime": runtime,
                "apply_supported": apply_supported,
                "mode": "apply" if apply_supported else "preview_only",
                "support": support,
                "capability_label_cn": CAPABILITY_LABELS_CN[support],
                "summary_cn": (
                    "Runtime Config Manager v0 可对已确认入口执行 managed block 写入和回滚。"
                    if apply_supported
                    else "Runtime Config Manager v0 尚未确认可回滚写入入口；P2 只保留 explain / preview。"
                ),
            }
        )
    return {"ok": True, "action": "capabilities", "runtimes": rows}


def _snapshot_id(canonical_sha: str) -> str:
    stamp = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{canonical_sha[:12]}"


def _manifest_path(backup_root: pathlib.Path, snapshot_id: str) -> pathlib.Path:
    return backup_root / snapshot_id / "manifest.json"


def _precheck_apply(runtimes: Sequence[str], wsl_home: pathlib.Path) -> list[str]:
    errors: list[str] = []
    target_map = runtime_targets(wsl_home)
    for runtime in runtimes:
        if runtime not in APPLY_RUNTIMES:
            errors.append(f"{runtime}: preview only / unsupported in v0")
            continue
        for target in target_map[runtime]:
            path = _target_path(target, wsl_home)
            if not path.is_file():
                errors.append(f"{runtime}:{target.target_id}: missing target file {path}")
    return errors


def apply_manager(
    runtimes: Sequence[str],
    *,
    yes: bool = False,
    repo_root: pathlib.Path | None = None,
    wsl_home: pathlib.Path | None = None,
    backup_root: pathlib.Path | None = None,
) -> dict[str, object]:
    start = time.monotonic()

    def finish(result: dict[str, object]) -> dict[str, object]:
        try:
            event_runtimes: Sequence[str] | None = selected
        except NameError:
            event_runtimes = runtimes
        _record_manager_event("runtime_config_apply", result, start=start, runtimes=event_runtimes)
        return result

    if not yes:
        return finish({"ok": False, "action": "apply", "errors": ["apply requires --yes"]})
    repo_root = repo_root or _detect_repo_root()
    wsl_home = wsl_home or _default_wsl_home()
    backup_root = backup_root or (repo_root / DEFAULT_BACKUP_DIR)
    selected = _selected_runtimes(runtimes)
    errors = _precheck_apply(selected, wsl_home)
    if errors:
        return finish({"ok": False, "action": "apply", "errors": errors})

    canonical_path = repo_root / CANONICAL_REL
    canonical = load_canonical(canonical_path)
    snapshot_id = _snapshot_id(str(canonical["canonical_sha256"]))
    block = render_managed_block(canonical, snapshot_id)
    snapshot_root = backup_root / snapshot_id
    targets_out: list[dict[str, object]] = []

    for runtime in selected:
        for target in runtime_targets(wsl_home)[runtime]:
            path = _target_path(target, wsl_home)
            before = path.read_bytes()
            before_sha = _sha256_bytes(before)
            backup_path = snapshot_root / runtime / (_safe_backup_name(path) + ".bak")
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_bytes(before)
            changed = False
            if target.write_policy == "managed_block":
                # Use text-mode reads here so CRLF files normalize before the
                # managed-block regex runs; otherwise repeated apply can miss
                # an existing block written with Windows newlines.
                before_text = path.read_text(encoding="utf-8")
                new_text = upsert_managed_block(before_text, block)
                _write_text(path, new_text)
                changed = _sha256_file(path) != before_sha
            after_sha = _sha256_file(path)
            targets_out.append(
                {
                    "runtime": runtime,
                    "target_id": target.target_id,
                    "path": str(path),
                    "backup_path": str(backup_path),
                    "write_policy": target.write_policy,
                    "before_sha256": before_sha,
                    "backup_sha256": _sha256_file(backup_path),
                    "after_sha256": after_sha,
                    "changed": changed,
                }
            )

    manifest = {
        "snapshot_id": snapshot_id,
        "created_at": _now(),
        "repo_root": str(repo_root),
        "canonical_path": str(canonical_path),
        "canonical_sha256": canonical["canonical_sha256"],
        "preference_ids": canonical["preference_ids"],
        "targets": targets_out,
    }
    manifest_path = _manifest_path(backup_root, snapshot_id)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return finish({
        "ok": True,
        "action": "apply",
        "snapshot_id": snapshot_id,
        "manifest_path": str(manifest_path),
        "targets": targets_out,
    })


def _load_manifest(snapshot_id: str, backup_root: pathlib.Path) -> dict[str, object]:
    path = _manifest_path(backup_root, snapshot_id)
    if not path.is_file():
        raise FileNotFoundError(f"manifest not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def verify_manager(
    snapshot_id: str,
    *,
    runtimes: Sequence[str] | None = None,
    repo_root: pathlib.Path | None = None,
    backup_root: pathlib.Path | None = None,
) -> dict[str, object]:
    start = time.monotonic()
    repo_root = repo_root or _detect_repo_root()
    backup_root = backup_root or (repo_root / DEFAULT_BACKUP_DIR)
    manifest = _load_manifest(snapshot_id, backup_root)
    selected = set(runtimes or [])
    errors: list[str] = []
    rows: list[dict[str, object]] = []
    preference_ids = list(manifest.get("preference_ids", []))
    for item in manifest.get("targets", []):
        target = dict(item)
        runtime = str(target["runtime"])
        if selected and runtime not in selected:
            continue
        path = pathlib.Path(str(target["path"]))
        readable = path.is_file()
        text = _read_text(path) if readable else ""
        missing_ids = [pref_id for pref_id in preference_ids if pref_id not in text] if target["write_policy"] == "managed_block" else []
        has_block = bool(BEGIN_RE.search(text)) if target["write_policy"] == "managed_block" else False
        current_sha = _sha256_file(path)
        row = {
            "runtime": runtime,
            "target_id": target["target_id"],
            "path": str(path),
            "write_policy": target["write_policy"],
            "readable": readable,
            "has_managed_block": has_block,
            "missing_preference_ids": missing_ids,
            "current_sha256": current_sha,
            "changed_since_apply": current_sha != target.get("after_sha256"),
        }
        if not readable:
            errors.append(f"{runtime}:{target['target_id']}: not readable")
        elif target["write_policy"] == "managed_block" and (not has_block or missing_ids):
            errors.append(f"{runtime}:{target['target_id']}: managed block verify failed")
        rows.append(row)
    result = {"ok": not errors, "action": "verify", "snapshot_id": snapshot_id, "targets": rows, "errors": errors}
    _record_manager_event("runtime_config_verify", result, start=start, runtimes=runtimes)
    return result


def _status_for_target(
    target: RuntimeTarget,
    *,
    path: pathlib.Path,
    canonical: dict[str, object],
) -> dict[str, object]:
    readable = path.is_file()
    text = _read_text(path) if readable else ""
    block = _current_managed_block(text) if readable else ""
    expected_sha = str(canonical["canonical_sha256"])[:12]
    current_block_sha = _managed_block_canonical_sha(block) if block else None
    preference_ids = [str(item) for item in canonical["preference_ids"]]  # type: ignore[index]
    missing_ids = [pref_id for pref_id in preference_ids if pref_id not in block] if target.write_policy == "managed_block" else []

    if target.write_policy == "backup_only":
        status = "backup_only_readable" if readable else "missing"
    elif not readable:
        status = "missing"
    elif not block:
        status = "missing_block"
    elif current_block_sha != expected_sha:
        status = "stale"
    elif missing_ids:
        status = "incomplete"
    else:
        status = "ok"

    return {
        "runtime": target.runtime,
        "target_id": target.target_id,
        "path": str(path),
        "write_policy": target.write_policy,
        "support": target.support,
        "summary_cn": target.summary_cn,
        "readable": readable,
        "current_sha256": _sha256_file(path),
        "has_managed_block": bool(block),
        "current_canonical_sha": current_block_sha,
        "expected_canonical_sha": expected_sha,
        "canonical_match": current_block_sha == expected_sha if target.write_policy == "managed_block" else None,
        "missing_preference_ids": missing_ids,
        "status": status,
    }


def status_manager(
    runtimes: Sequence[str] | None = None,
    *,
    repo_root: pathlib.Path | None = None,
    wsl_home: pathlib.Path | None = None,
) -> dict[str, object]:
    repo_root = repo_root or _detect_repo_root()
    wsl_home = wsl_home or _default_wsl_home()
    canonical = load_canonical(repo_root / CANONICAL_REL)
    target_map = runtime_targets(wsl_home)
    runtime_rows: list[dict[str, object]] = []
    errors: list[str] = []
    for runtime in _selected_runtimes(runtimes):
        targets = [
            _status_for_target(target, path=_target_path(target, wsl_home), canonical=canonical)
            for target in target_map.get(runtime, [])
        ]
        for target in targets:
            status = str(target["status"])
            if status not in {"ok", "backup_only_readable"}:
                errors.append(f"{runtime}:{target['target_id']}: {status}")
        runtime_rows.append(
            {
                "runtime": runtime,
                "apply_supported": runtime in APPLY_RUNTIMES,
                "mode": "apply" if runtime in APPLY_RUNTIMES else "preview_only",
                "targets": targets,
            }
        )
    return {
        "ok": not errors,
        "action": "status",
        "repo_root": str(repo_root),
        "wsl_home": str(wsl_home),
        "canonical_sha256": canonical["canonical_sha256"],
        "preference_ids": canonical["preference_ids"],
        "runtimes": runtime_rows,
        "errors": errors,
    }


def rollback_manager(
    snapshot_id: str,
    *,
    runtimes: Sequence[str] | None = None,
    yes: bool = False,
    dry_run: bool = False,
    repo_root: pathlib.Path | None = None,
    backup_root: pathlib.Path | None = None,
) -> dict[str, object]:
    start = time.monotonic()

    def finish(result: dict[str, object]) -> dict[str, object]:
        _record_manager_event("runtime_config_rollback", result, start=start, runtimes=runtimes, dry_run=dry_run)
        return result

    if not dry_run and not yes:
        return finish({"ok": False, "action": "rollback", "errors": ["rollback requires --yes unless --dry-run is set"]})
    repo_root = repo_root or _detect_repo_root()
    backup_root = backup_root or (repo_root / DEFAULT_BACKUP_DIR)
    manifest = _load_manifest(snapshot_id, backup_root)
    selected = set(runtimes or [])
    errors: list[str] = []
    rows: list[dict[str, object]] = []
    for item in manifest.get("targets", []):
        target = dict(item)
        runtime = str(target["runtime"])
        if selected and runtime not in selected:
            continue
        backup = pathlib.Path(str(target["backup_path"]))
        original = pathlib.Path(str(target["path"]))
        backup_sha = _sha256_file(backup)
        if backup_sha != target.get("backup_sha256"):
            errors.append(f"{runtime}:{target['target_id']}: backup sha256 mismatch")
            continue
        if not dry_run:
            original.parent.mkdir(parents=True, exist_ok=True)
            original.write_bytes(backup.read_bytes())
        rows.append(
            {
                "runtime": runtime,
                "target_id": target["target_id"],
                "path": str(original),
                "backup_path": str(backup),
                "dry_run": dry_run,
                "restored": not dry_run,
            }
        )
    return finish({"ok": not errors, "action": "rollback", "snapshot_id": snapshot_id, "targets": rows, "errors": errors})


def _print_result(result: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"{result.get('action')}: ok={result.get('ok')}")
    if result.get("snapshot_id"):
        print(f"snapshot_id: {result['snapshot_id']}")
    for error in result.get("errors", []) or []:
        print(f"ERROR: {error}", file=sys.stderr)


def _add_common_runtime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--runtime", action="append", default=None, help="runtime to target; repeatable")
    parser.add_argument("--repo-root", type=pathlib.Path, default=None)
    parser.add_argument("--wsl-home", type=pathlib.Path, default=None)
    parser.add_argument("--backup-root", type=pathlib.Path, default=None)
    parser.add_argument("--json", action="store_true")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tigermemory-config manager", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    plan_p = sub.add_parser("plan")
    _add_common_runtime_args(plan_p)

    status_p = sub.add_parser("status")
    _add_common_runtime_args(status_p)

    capabilities_p = sub.add_parser("capabilities")
    capabilities_p.add_argument("--runtime", action="append", default=None, help="runtime to include; repeatable")
    capabilities_p.add_argument("--json", action="store_true")

    apply_p = sub.add_parser("apply")
    _add_common_runtime_args(apply_p)
    apply_p.add_argument("--yes", action="store_true")

    verify_p = sub.add_parser("verify")
    verify_p.add_argument("--snapshot-id", required=True)
    verify_p.add_argument("--runtime", action="append", default=None)
    verify_p.add_argument("--repo-root", type=pathlib.Path, default=None)
    verify_p.add_argument("--backup-root", type=pathlib.Path, default=None)
    verify_p.add_argument("--json", action="store_true")

    rollback_p = sub.add_parser("rollback")
    rollback_p.add_argument("--snapshot-id", required=True)
    rollback_p.add_argument("--runtime", action="append", default=None)
    rollback_p.add_argument("--repo-root", type=pathlib.Path, default=None)
    rollback_p.add_argument("--backup-root", type=pathlib.Path, default=None)
    rollback_p.add_argument("--dry-run", action="store_true")
    rollback_p.add_argument("--yes", action="store_true")
    rollback_p.add_argument("--json", action="store_true")

    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "plan":
            result = build_plan(args.runtime, wsl_home=args.wsl_home, repo_root=args.repo_root)
        elif args.command == "status":
            result = status_manager(args.runtime, repo_root=args.repo_root, wsl_home=args.wsl_home)
        elif args.command == "capabilities":
            result = runtime_capabilities(args.runtime)
        elif args.command == "apply":
            result = apply_manager(args.runtime, yes=args.yes, repo_root=args.repo_root, wsl_home=args.wsl_home, backup_root=args.backup_root)
        elif args.command == "verify":
            result = verify_manager(args.snapshot_id, runtimes=args.runtime, repo_root=args.repo_root, backup_root=args.backup_root)
        else:
            result = rollback_manager(args.snapshot_id, runtimes=args.runtime, yes=args.yes, dry_run=args.dry_run, repo_root=args.repo_root, backup_root=args.backup_root)
    except (OSError, ValueError) as exc:
        result = {"ok": False, "action": args.command, "errors": [str(exc)]}
    _print_result(result, bool(args.json))
    return 0 if result.get("ok") else 1
