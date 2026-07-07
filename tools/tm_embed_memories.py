#!/usr/bin/env python3
"""Backfill / run local memory embeddings — WSL-side one-shot for direction-1.

Inputs: the live local SQLite (data/tigermemory/memory.sqlite) + the configured
        embedding backend (Qwen :19190). Outputs: memory_embeddings rows.
Depends-on: tigermemory_core (embed backend must be reachable — WSL only).

The VM cannot reach :19190, so this is meant to run on WSL where the embedding
service lives. It embeds active memories that have no vector yet, in batches,
and reports progress. Safe to re-run (idempotent per memory_id).

Usage (WSL):
  python tools/tm_embed_memories.py backfill            # embed all pending
  python tools/tm_embed_memories.py backfill --batch 100 --max 500
  python tools/tm_embed_memories.py status              # how many still pending
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "tigermemory-core" / "src"))

import tigermemory_core as tm_core  # noqa: E402


def cmd_status(_args: argparse.Namespace) -> int:
    stats = tm_core.local_memory_stats()
    pending = len(tm_core.memories_without_embedding(limit=10_000_000))
    out = {
        "total": stats["total"],
        "by_vector_status": stats["by_vector_status"],
        "vectored_rows": stats["vectored_rows"],
        "pending_embedding": pending,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    total_embedded = 0
    total_failed = 0
    rounds = 0
    while True:
        res = tm_core.backfill_embeddings(limit=args.batch)
        total_embedded += res["embedded"]
        total_failed += res["failed"]
        rounds += 1
        print(
            f"round {rounds}: attempted={res['attempted']} "
            f"embedded={res['embedded']} failed={res['failed']}",
            file=sys.stderr,
        )
        if res["attempted"] == 0:
            break  # nothing left to embed
        if res["embedded"] == 0:
            # A full batch failed (backend likely down) — stop rather than spin.
            print("no progress this round; stopping (embedding backend down?)", file=sys.stderr)
            break
        if args.max and total_embedded >= args.max:
            break
    print(json.dumps({"embedded": total_embedded, "failed": total_failed, "rounds": rounds}, ensure_ascii=False))
    return 0 if total_failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    st = sub.add_parser("status", help="show pending-embedding count")
    st.set_defaults(func=cmd_status)
    bf = sub.add_parser("backfill", help="embed pending memories in batches")
    bf.add_argument("--batch", type=int, default=100, help="rows per round")
    bf.add_argument("--max", type=int, default=0, help="stop after N embedded (0 = all)")
    bf.set_defaults(func=cmd_backfill)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
