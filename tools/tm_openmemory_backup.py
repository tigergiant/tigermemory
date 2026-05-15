#!/usr/bin/env python3
"""
Rolling local backups for tigermemory OpenMemory / Mem0 runtime data.

Backs up the two runtime stores that are not fully covered by Git:
- OpenMemory SQLite metadata DB inside the openmemory container.
- Qdrant collection snapshots for vector payloads.

Default output is runtime/openmemory/backups/<timestamp>/, which is ignored by
Git because runtime/ is local machine state.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKUP_ROOT = REPO_ROOT / "runtime" / "openmemory" / "backups"
DEFAULT_OPENMEMORY_CONTAINER = "openmemory-openmemory-mcp-1"
DEFAULT_OPENMEMORY_DB = "/usr/src/openmemory/openmemory.db"
DEFAULT_QDRANT_URL = "http://localhost:6333"


def _now_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")


def _http_json(method: str, url: str, data: bytes | None = None, timeout: int = 30) -> dict[str, Any]:
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: HTTP {exc.code}: {body[:300]}") from exc
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _download(url: str, dest: Path, timeout: int = 120) -> None:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        with dest.open("wb") as f:
            shutil.copyfileobj(resp, f)
    if not dest.exists() or dest.stat().st_size == 0:
        raise RuntimeError(f"download produced empty file: {dest}")


def qdrant_collections(qdrant_url: str) -> list[str]:
    data = _http_json("GET", f"{qdrant_url.rstrip('/')}/collections")
    collections = data.get("result", {}).get("collections", [])
    return sorted(str(item["name"]) for item in collections if item.get("name"))


def backup_qdrant(qdrant_url: str, backup_dir: Path, collections: list[str]) -> list[dict[str, Any]]:
    out_dir = backup_dir / "qdrant"
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    base = qdrant_url.rstrip("/")

    for collection in collections:
        created = _http_json("POST", f"{base}/collections/{collection}/snapshots", data=b"{}")
        snapshot_name = created.get("result", {}).get("name")
        if not snapshot_name:
            raise RuntimeError(f"Qdrant did not return a snapshot name for collection {collection}: {created}")
        dest = out_dir / f"{collection}--{snapshot_name}"
        _download(f"{base}/collections/{collection}/snapshots/{snapshot_name}", dest)
        # Keep the local backup, then remove the extra runtime snapshot so the
        # Qdrant volume does not grow unbounded.
        try:
            _http_json("DELETE", f"{base}/collections/{collection}/snapshots/{snapshot_name}")
            deleted_runtime_snapshot = True
        except Exception:
            deleted_runtime_snapshot = False
        results.append({
            "collection": collection,
            "snapshot": snapshot_name,
            "path": str(dest),
            "bytes": dest.stat().st_size,
            "deleted_runtime_snapshot": deleted_runtime_snapshot,
        })
    return results


def backup_openmemory_db(container: str, db_path: str, backup_dir: Path) -> dict[str, Any]:
    out_dir = backup_dir / "sqlite"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / "openmemory.db"
    remote_tmp = f"/tmp/tigermemory-openmemory-backup-{os.getpid()}.db"
    code = (
        "import sqlite3, pathlib; "
        f"src={db_path!r}; dst={remote_tmp!r}; "
        "pathlib.Path(src).exists() or (_ for _ in ()).throw(FileNotFoundError(src)); "
        "s=sqlite3.connect(src); d=sqlite3.connect(dst); "
        "s.backup(d); d.close(); s.close()"
    )
    subprocess.run(["docker", "exec", container, "python", "-c", code], check=True)
    try:
        subprocess.run(["docker", "cp", f"{container}:{remote_tmp}", str(dest)], check=True)
    finally:
        subprocess.run(["docker", "exec", container, "rm", "-f", remote_tmp], check=False)
    if not dest.exists() or dest.stat().st_size == 0:
        raise RuntimeError(f"SQLite backup produced empty file: {dest}")
    return {"path": str(dest), "bytes": dest.stat().st_size, "container": container, "source": db_path}


def prune_backups(root: Path, keep: int) -> list[str]:
    backups = sorted([p for p in root.iterdir() if p.is_dir() and p.name.startswith("openmemory-")], reverse=True)
    deleted: list[str] = []
    for old in backups[keep:]:
        shutil.rmtree(old)
        deleted.append(str(old))
    return deleted


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Back up OpenMemory SQLite DB and Qdrant snapshots.")
    ap.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT))
    ap.add_argument("--keep", type=int, default=7, help="Number of timestamped backups to retain.")
    ap.add_argument("--qdrant-url", default=DEFAULT_QDRANT_URL)
    ap.add_argument("--openmemory-container", default=DEFAULT_OPENMEMORY_CONTAINER)
    ap.add_argument("--openmemory-db", default=DEFAULT_OPENMEMORY_DB)
    ap.add_argument("--skip-qdrant", action="store_true")
    ap.add_argument("--skip-sqlite", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.backup_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    backup_dir = root / f"openmemory-{_now_stamp()}"
    backup_dir.mkdir()

    manifest: dict[str, Any] = {
        "created_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(),
        "backup_dir": str(backup_dir),
        "qdrant_url": args.qdrant_url,
        "openmemory_container": args.openmemory_container,
        "openmemory_db": args.openmemory_db,
        "results": {},
    }

    try:
        if not args.skip_sqlite:
            manifest["results"]["sqlite"] = backup_openmemory_db(
                args.openmemory_container,
                args.openmemory_db,
                backup_dir,
            )
        if not args.skip_qdrant:
            collections = qdrant_collections(args.qdrant_url)
            manifest["results"]["qdrant"] = backup_qdrant(args.qdrant_url, backup_dir, collections)
        deleted = prune_backups(root, args.keep)
        manifest["retention"] = {"keep": args.keep, "deleted": deleted}
        (backup_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "backup_dir": str(backup_dir), "deleted": deleted}, ensure_ascii=False))
        return 0
    except Exception as exc:
        manifest["ok"] = False
        manifest["error"] = str(exc)
        (backup_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": False, "backup_dir": str(backup_dir), "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
