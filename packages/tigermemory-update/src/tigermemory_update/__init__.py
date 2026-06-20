"""Git-aware update checks for source-first TigerMemory installs."""

from __future__ import annotations

import json
import pathlib
import subprocess
import urllib.parse
import urllib.request
from importlib import metadata as importlib_metadata
from typing import Any

try:
    from tigermemory_core.roots import resolve_instance_root
except Exception:  # pragma: no cover - source tree bootstrap fallback
    resolve_instance_root = None  # type: ignore[assignment]


SCHEMA = "tigermemory-update-status-v1"


def run_git(
    app_root: pathlib.Path,
    args: list[str],
    timeout: int = 20,
) -> subprocess.CompletedProcess[str]:
    """Run git in app_root without raising on git exit status."""

    return subprocess.run(
        ["git", "-C", str(app_root), *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def _short(text: str, limit: int = 500) -> str:
    value = (text or "").strip()
    return value[:limit]


def _base_status(app_root: pathlib.Path) -> dict[str, Any]:
    instance_root = ""
    if resolve_instance_root is not None:
        try:
            instance_root = str(resolve_instance_root())
        except Exception:
            instance_root = ""
    return {
        "ok": True,
        "schema": SCHEMA,
        "source_mode": "unknown",
        "app_root": str(app_root),
        "instance_root": instance_root,
        "branch": "",
        "head": "",
        "upstream": "",
        "remote_head": "",
        "ahead": 0,
        "behind": 0,
        "dirty": False,
        "tracked_dirty_count": 0,
        "untracked_count": 0,
        "has_local_commits": False,
        "update_available": False,
        "safe_to_apply": False,
        "apply_strategy": "ff_only",
        "requires_user_action": True,
        "recommended_action": "",
        "warnings": [],
        "reason": "",
    }


def _git_stdout(app_root: pathlib.Path, args: list[str], default: str = "") -> str:
    result = run_git(app_root, args)
    if result.returncode != 0:
        return default
    return result.stdout.strip()


def _is_git_worktree(app_root: pathlib.Path) -> bool:
    result = run_git(app_root, ["rev-parse", "--is-inside-work-tree"])
    return result.returncode == 0 and result.stdout.strip().lower() == "true"


def _editable_install_matches_app_root(app_root: pathlib.Path) -> bool:
    root = app_root.resolve()
    for dist in importlib_metadata.distributions():
        try:
            text = dist.read_text("direct_url.json")
        except Exception:
            continue
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not (data.get("dir_info") or {}).get("editable"):
            continue
        url = str(data.get("url") or "")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme and parsed.scheme != "file":
            continue
        path_text = urllib.request.url2pathname(parsed.path if parsed.scheme else url)
        if parsed.netloc and parsed.scheme == "file":
            path_text = f"//{parsed.netloc}{path_text}"
        try:
            if pathlib.Path(path_text).resolve() == root:
                return True
        except (OSError, ValueError):
            continue
    return False


def _remote_unavailable_status(app_root: pathlib.Path, detail: str) -> dict[str, Any]:
    status = _base_status(app_root)
    status.update(
        {
            "ok": False,
            "source_mode": "git_source" if _is_git_worktree(app_root) else "installed_package",
            "safe_to_apply": False,
            "requires_user_action": True,
            "reason": "remote_unavailable",
            "recommended_action": "远端暂时不可用，稍后重试 tm update check --refresh。",
            "warnings": [detail] if detail else ["git fetch failed"],
        }
    )
    return status


def get_update_status(
    app_root: pathlib.Path | str,
    *,
    remote: str = "origin",
    branch: str | None = None,
    refresh_remote: bool = False,
) -> dict[str, Any]:
    """Return source checkout update state without modifying user data."""

    root = pathlib.Path(app_root).expanduser().resolve()
    status = _base_status(root)

    if not _is_git_worktree(root):
        status.update(
            {
                "source_mode": "installed_package",
                "reason": "not_git_source",
                "recommended_action": "当前不是 Git 源码安装；如需热更新，请从 GitHub clone 后使用 editable install。",
            }
        )
        return status

    if refresh_remote:
        try:
            fetch = run_git(root, ["fetch", "--prune", remote], timeout=30)
        except subprocess.TimeoutExpired:
            return _remote_unavailable_status(root, "git fetch timed out")
        if fetch.returncode != 0:
            return _remote_unavailable_status(root, _short(fetch.stderr or fetch.stdout))

    source_mode = "editable_install" if _editable_install_matches_app_root(root) else "git_source"
    current_branch = branch or _git_stdout(root, ["branch", "--show-current"])
    upstream = _git_stdout(root, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    head = _git_stdout(root, ["rev-parse", "--short=12", "HEAD"])
    remote_head = _git_stdout(root, ["rev-parse", "--short=12", "@{u}"]) if upstream else ""
    porcelain = _git_stdout(root, ["status", "--porcelain"])
    status_lines = [line for line in porcelain.splitlines() if line.strip()]
    untracked_count = sum(1 for line in status_lines if line.startswith("??"))
    tracked_dirty_count = len(status_lines) - untracked_count

    ahead = 0
    behind = 0
    if upstream:
        counts = _git_stdout(root, ["rev-list", "--left-right", "--count", "HEAD...@{u}"])
        parts = counts.split()
        if len(parts) == 2 and all(part.isdigit() for part in parts):
            ahead, behind = int(parts[0]), int(parts[1])

    warnings: list[str] = []
    reason = ""
    if not upstream:
        reason = "missing_upstream"
        warnings.append("No upstream branch is configured.")
    elif tracked_dirty_count:
        reason = "dirty_worktree"
        warnings.append("Tracked source files have local edits.")
    elif untracked_count:
        reason = "untracked_files"
        warnings.append("Untracked files exist; commit or move them before automatic update.")
    elif ahead:
        reason = "local_commits"
        warnings.append("Local commits exist; use an explicit strategy after review.")

    update_available = bool(upstream and behind > 0)
    safe_to_apply = bool(update_available and not reason)
    requires_user_action = not safe_to_apply
    if safe_to_apply:
        recommended_action = "Run tm update apply --strategy ff-only."
    elif not update_available and upstream and not reason:
        recommended_action = "Already up to date."
        requires_user_action = False
    elif reason == "dirty_worktree":
        recommended_action = "Commit, stash, or move local source edits before updating."
    elif reason == "untracked_files":
        recommended_action = "Review untracked files before updating."
    elif reason == "local_commits":
        recommended_action = "Push, rebase, or branch local commits before updating."
    elif reason == "missing_upstream":
        recommended_action = "Set a Git upstream remote before using tm update apply."
    else:
        recommended_action = "No safe automatic update action is available."

    status.update(
        {
            "source_mode": source_mode,
            "branch": current_branch,
            "head": head,
            "upstream": upstream,
            "remote_head": remote_head,
            "ahead": ahead,
            "behind": behind,
            "dirty": bool(tracked_dirty_count),
            "tracked_dirty_count": tracked_dirty_count,
            "untracked_count": untracked_count,
            "has_local_commits": bool(ahead),
            "update_available": update_available,
            "safe_to_apply": safe_to_apply,
            "requires_user_action": requires_user_action,
            "recommended_action": recommended_action,
            "warnings": warnings,
            "reason": reason,
        }
    )
    return status


def apply_update(
    app_root: pathlib.Path | str,
    *,
    strategy: str = "ff-only",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply a Git update only when it can be done without discarding edits."""

    root = pathlib.Path(app_root).expanduser().resolve()
    status = get_update_status(root, refresh_remote=True)
    if not status.get("ok", False):
        return {
            "ok": False,
            "applied": False,
            "reason": status.get("reason") or "status_unavailable",
            "status": status,
        }
    if status.get("dirty"):
        return {"ok": False, "applied": False, "reason": "dirty_worktree", "status": status}
    if status.get("untracked_count"):
        return {"ok": False, "applied": False, "reason": "untracked_files", "status": status}
    if not status.get("upstream"):
        return {"ok": False, "applied": False, "reason": "missing_upstream", "status": status}
    if not status.get("update_available"):
        return {"ok": True, "applied": False, "reason": "up_to_date", "status": status}
    if status.get("ahead") and strategy != "rebase":
        return {"ok": False, "applied": False, "reason": "local_commits", "status": status}
    if strategy not in {"ff-only", "rebase"}:
        return {"ok": False, "applied": False, "reason": "unsupported_strategy", "status": status}
    if dry_run:
        return {
            "ok": True,
            "applied": False,
            "reason": "dry_run",
            "planned_strategy": strategy,
            "status": status,
        }

    if strategy == "ff-only":
        result = run_git(root, ["merge", "--ff-only", "@{u}"], timeout=60)
        if result.returncode == 0:
            return {"ok": True, "applied": True, "reason": "updated", "status": status}
        return {
            "ok": False,
            "applied": False,
            "reason": "conflict",
            "stderr": _short(result.stderr or result.stdout),
            "status": status,
        }

    result = run_git(root, ["rebase", "@{u}"], timeout=120)
    if result.returncode == 0:
        return {"ok": True, "applied": True, "reason": "updated", "status": status}
    run_git(root, ["rebase", "--abort"], timeout=30)
    return {
        "ok": False,
        "applied": False,
        "reason": "conflict",
        "stderr": _short(result.stderr or result.stdout),
        "status": status,
    }


__all__ = ["SCHEMA", "apply_update", "get_update_status", "run_git"]
