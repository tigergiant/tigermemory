#!/usr/bin/env python3
"""tm install-ide-hooks: install TigerMemory canonical preferences into IDE rule files.

This is the P2 deliverable of spec plans/17-ide-hooks-installer.md. It loads
canonical_v0.yaml and dispatches to gate3 emitters. Writing is safe:
- replace-mode targets are tigermemory-dedicated paths (no user content clobbered)
- append-mode targets (CLAUDE.md/AGENTS.md) use marker blocks
- existing target files are backed up to .tmp/gate3-backup/ before overwrite
- C-class IDEs without a real emitter are skipped with a clear reason
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import shutil
import sys
from pathlib import Path
from typing import Callable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_PATH = REPO_ROOT / "tools" / "gate3" / "canonical_v0.yaml"
BACKUP_ROOT = REPO_ROOT / ".tmp" / "gate3-backup"
GATE3_SRC = REPO_ROOT / "tools" / "gate3"

# Make `emitters` package importable without modifying sys.path globally.
if str(GATE3_SRC) not in sys.path:
    sys.path.insert(0, str(GATE3_SRC))

from emitters import EmitResult  # noqa: E402
from emitters.claude_md import (  # noqa: E402
    BEGIN_MARKER as CLAUDE_BEGIN,
    END_MARKER as CLAUDE_END,
    emit_claude_md,
)
from emitters.agents_md import emit_agents_md  # noqa: E402
from emitters.cursor_mdc import emit_cursor_mdc  # noqa: E402
from emitters.trae import emit_trae  # noqa: E402
from emitters.antigravity import emit_antigravity  # noqa: E402
from emitters.windsurf import emit_windsurf  # noqa: E402
from emitters.kimi_code import emit_kimi_code  # noqa: E402
from emitters.opencode import emit_opencode  # noqa: E402


SIGNAL_PREFS = ("session_start_onboarding", "session_end_handoff")


# IDE registry: id -> (emitter callable or None, tier, write strategy).
# write strategy:
#   "append"  -> marker-block append/replace (CLAUDE.md style)
#   "replace" -> overwrite dedicated tigermemory rule file (safe; path is tm-only)
#   "auto"    -> trust EmitResult.write_mode; "preview" means do not write
#   "skip"    -> emitter is None; C-class pending real-world test
IDE_REGISTRY: dict[str, tuple[Callable | None, str, str]] = {
    "claude-code":  (emit_claude_md,                              "A", "append"),
    "codex":        (lambda c, w: emit_agents_md(c, w, "codex"),  "A", "auto"),
    "windsurf":     (emit_windsurf,                               "A", "replace"),
    "cursor":       (emit_cursor_mdc,                             "B", "replace"),
    "antigravity":  (emit_antigravity,                            "B", "replace"),
    "trae":         (emit_trae,                                   "B", "replace"),
    "opencode":     (emit_opencode,                               "B", "replace"),
    "kimi-code":    (emit_kimi_code,                              "C", "replace"),
    "workbuddy":    (None,                                        "C", "skip"),
    "zcode":        (None,                                        "C", "skip"),
}


def _load_canonical() -> dict:
    try:
        import yaml
    except ImportError as exc:
        print(f"PyYAML required to read canonical policy: {exc}", file=sys.stderr)
        raise SystemExit(2)
    with CANONICAL_PATH.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _now_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().replace(microsecond=0).isoformat().replace(":", "-")


def _backup_existing(target: Path, runtime: str) -> str | None:
    if not target.exists():
        return None
    stamp = _now_stamp()
    backup_dir = BACKUP_ROOT / runtime / f"snapshot-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / (target.name + ".bak")
    shutil.copy2(target, backup_path)
    return str(backup_path)


def _has_signal_prefs(content: str) -> dict[str, bool]:
    return {pref_id: pref_id in content for pref_id in SIGNAL_PREFS}


def _apply_replace(result: EmitResult, dry_run: bool) -> dict:
    target = result.target_path
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    backup = None
    if existing and existing != result.content and not dry_run:
        backup = _backup_existing(target, result.runtime)
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(result.content, encoding="utf-8")
    if dry_run:
        action = "preview"
    elif not existing:
        action = "wrote"
    elif existing == result.content:
        action = "kept"
    else:
        action = "updated"
    return {
        "runtime": result.runtime,
        "target_path": str(target),
        "write_mode": result.write_mode,
        "emitter_version": result.emitter_version,
        "action": action,
        "backup_path": backup,
        "signals": _has_signal_prefs(result.content),
    }


def _apply_append(result: EmitResult, begin_marker: str, end_marker: str, dry_run: bool) -> dict:
    target = result.target_path
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    block = result.content
    if begin_marker in existing and end_marker in existing:
        start = existing.index(begin_marker)
        end = existing.index(end_marker) + len(end_marker)
        new_content = existing[:start] + block + existing[end:]
    elif existing:
        new_content = existing.rstrip() + "\n\n" + block
    else:
        new_content = block
    backup = None
    if existing and existing != new_content and not dry_run:
        backup = _backup_existing(target, result.runtime)
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new_content, encoding="utf-8")
    if dry_run:
        action = "preview"
    elif not existing:
        action = "wrote"
    elif existing == new_content:
        action = "kept"
    else:
        action = "updated"
    return {
        "runtime": result.runtime,
        "target_path": str(target),
        "write_mode": result.write_mode,
        "emitter_version": result.emitter_version,
        "action": action,
        "backup_path": backup,
        "signals": _has_signal_prefs(block),
    }


def _apply_auto(result: EmitResult, dry_run: bool) -> dict:
    """Respect EmitResult.write_mode; 'preview' means do not touch the real file."""
    if result.write_mode == "preview":
        return {
            "runtime": result.runtime,
            "target_path": str(result.target_path),
            "write_mode": result.write_mode,
            "emitter_version": result.emitter_version,
            "action": "preview",
            "backup_path": None,
            "signals": _has_signal_prefs(result.content),
            "note": "emitter returned preview mode; real write deferred by emitter design",
        }
    if dry_run:
        # Still dry-run, but the emitter was willing to write.
        return _apply_replace(result, dry_run=True)
    return _apply_replace(result, dry_run=False)


def cmd_install(args: argparse.Namespace) -> int:
    canonical = _load_canonical()
    workspace = Path(args.workspace).resolve() if args.workspace else REPO_ROOT

    if args.all:
        ides: list[str] = list(args.ide) if args.ide else []
        for ide in IDE_REGISTRY:
            if ide not in ides:
                ides.append(ide)
    elif args.ide:
        ides = list(args.ide)
    else:
        print("error: --ide <name> or --all is required (see --help)", file=sys.stderr)
        return 2

    results: list[dict] = []
    for ide in ides:
        if ide not in IDE_REGISTRY:
            print(
                f"error: unknown ide '{ide}'; supported: {', '.join(sorted(IDE_REGISTRY))}",
                file=sys.stderr,
            )
            return 2
        emitter, tier, strategy = IDE_REGISTRY[ide]
        if emitter is None:
            results.append({
                "runtime": ide,
                "tier": tier,
                "action": "skipped",
                "reason": "C-class IDE pending real-world test (spec §7 P3)",
                "signals": {p: False for p in SIGNAL_PREFS},
            })
            continue
        try:
            result = emitter(canonical, workspace)
            if strategy == "append":
                entry = _apply_append(result, CLAUDE_BEGIN, CLAUDE_END, args.preview)
            elif strategy == "auto":
                entry = _apply_auto(result, args.preview)
            else:
                entry = _apply_replace(result, args.preview)
            entry["tier"] = tier
        except Exception as exc:  # noqa: BLE001
            results.append({
                "runtime": ide,
                "tier": tier,
                "action": "error",
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue
        results.append(entry)

    payload = {
        "schema": "tigermemory-ide-hooks-install-v1",
        "canonical_path": str(CANONICAL_PATH),
        "workspace": str(workspace),
        "dry_run": args.preview,
        "results": results,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for r in results:
            runtime = r.get("runtime", "")
            tier = r.get("tier", "")
            action = r.get("action", "")
            target = r.get("target_path", "")
            print(f"ide={runtime} tier={tier} action={action} target={target}")
            if r.get("backup_path"):
                print(f"  backup={r['backup_path']}")
            signals = r.get("signals")
            if signals:
                flags = ",".join(k for k, v in signals.items() if v)
                print(f"  signals={flags or 'none'}")
            if r.get("reason"):
                print(f"  reason={r['reason']}")
            if r.get("note"):
                print(f"  note={r['note']}")
            if r.get("error"):
                print(f"  error={r['error']}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    canonical = _load_canonical()
    workspace = Path(args.workspace).resolve() if args.workspace else REPO_ROOT

    rows: list[dict] = []
    for ide, (emitter, tier, _strategy) in IDE_REGISTRY.items():
        if emitter is None:
            rows.append({
                "ide": ide,
                "tier": tier,
                "target_path": "",
                "exists": False,
                "signals": {p: False for p in SIGNAL_PREFS},
                "status": "pending-test",
            })
            continue
        try:
            result = emitter(canonical, workspace)
            target = result.target_path
            existing = target.read_text(encoding="utf-8") if target.exists() else ""
            signals = _has_signal_prefs(existing)
            if not target.exists():
                status = "missing"
            elif all(signals.values()):
                status = "ok"
            elif any(signals.values()):
                status = "partial"
            else:
                status = "stale"
            rows.append({
                "ide": ide,
                "tier": tier,
                "target_path": str(target),
                "exists": target.exists(),
                "signals": signals,
                "emitter_version": result.emitter_version,
                "write_mode": result.write_mode,
                "status": status,
            })
        except Exception as exc:  # noqa: BLE001
            rows.append({
                "ide": ide,
                "tier": tier,
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            })

    if args.json:
        print(json.dumps({"schema": "tigermemory-ide-hooks-status-v1", "ides": rows}, ensure_ascii=False, indent=2))
    else:
        print(f"{'IDE':<14} {'TIER':<5} {'STATUS':<13} {'SIGNALS':<14} TARGET")
        for r in rows:
            ide = r["ide"]
            tier = r.get("tier", "")
            status = r.get("status", "")
            signals = r.get("signals", {})
            sig_str = ",".join(k.split("_")[0] for k, v in signals.items() if v) if signals else ""
            target = r.get("target_path", "")
            print(f"{ide:<14} {tier:<5} {status:<13} {sig_str:<14} {target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tm install-ide-hooks",
        description="Install TigerMemory canonical preferences into IDE rule files.",
    )
    parser.add_argument("--ide", action="append", choices=sorted(IDE_REGISTRY.keys()),
                        help="IDE to install; repeatable. Examples: trae, claude-code, cursor.")
    parser.add_argument("--all", action="store_true", help="Install all registered IDEs.")
    parser.add_argument("--preview", action="store_true", help="Preview only; do not write files.")
    parser.add_argument("--status", action="store_true", help="Show install status for all IDEs.")
    parser.add_argument("--workspace", default=None, help="Workspace root (default: repo root).")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.status:
        return cmd_status(args)
    return cmd_install(args)


if __name__ == "__main__":
    raise SystemExit(main())
