from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
VALID_PREFIXES = {"commit", "test", "build", "review", "draft_review", "file", "runtime"}
API_TEST_CHANNEL = "claude-api-test"


def _resolve_path(value: str) -> pathlib.Path:
    raw = value.strip()
    if re.match(r"^[A-Za-z]:\\", raw):
        path_text = raw
    else:
        path_text = raw.split(":", 1)[0] if ":" in raw else raw
    path = pathlib.Path(path_text)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def _read_frontmatter(path: pathlib.Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    data: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip("\"'")
    return data


def classify_review_evidence(item: str, value: str, *, draft: bool) -> dict:
    path = _resolve_path(value)
    result = {
        "raw": item,
        "ok": path.exists(),
        "kind": "draft_review" if draft else "review",
        "path": str(path),
        "counts_for_acceptance": False if draft else True,
        "reason": "ok" if path.exists() else "path does not exist",
    }
    if not path.exists():
        return result
    frontmatter = _read_frontmatter(path)
    channel = frontmatter.get("channel")
    if channel:
        result["channel"] = channel
    if draft:
        result["reason"] = "draft review recorded; does not count toward formal acceptance"
        return result
    if channel == API_TEST_CHANNEL:
        result["ok"] = False
        result["counts_for_acceptance"] = False
        result["reason"] = "claude-api-test review is draft evidence; use draft_review:<path> or provide official_review evidence"
    return result


def classify_evidence(item: str) -> dict:
    if ":" not in item:
        return {"raw": item, "ok": False, "kind": "unknown", "reason": "missing typed prefix"}
    prefix, value = item.split(":", 1)
    kind = prefix.strip().lower()
    value = value.strip()
    if kind not in VALID_PREFIXES:
        return {"raw": item, "ok": False, "kind": kind, "reason": "unsupported evidence kind"}
    if not value:
        return {"raw": item, "ok": False, "kind": kind, "reason": "empty evidence value"}
    if kind == "commit":
        return {
            "raw": item,
            "ok": bool(COMMIT_RE.match(value)),
            "kind": kind,
            "reason": "ok" if COMMIT_RE.match(value) else "commit must be 7-40 hex chars",
        }
    if kind == "review":
        return classify_review_evidence(item, value, draft=False)
    if kind == "draft_review":
        return classify_review_evidence(item, value, draft=True)
    if kind == "file":
        path = _resolve_path(value)
        return {
            "raw": item,
            "ok": path.exists(),
            "kind": kind,
            "path": str(path),
            "counts_for_acceptance": True,
            "reason": "ok" if path.exists() else "path does not exist",
        }
    if kind == "test":
        lowered = value.lower()
        ok = any(marker in lowered for marker in ("passed", "success", "ok", "green"))
        return {
            "raw": item,
            "ok": ok,
            "kind": kind,
            "reason": "ok" if ok else "test evidence must include passed/success/ok/green",
        }
    return {"raw": item, "ok": True, "kind": kind, "reason": "ok"}


def validate_acceptance(stage: str, summary: str, evidence: list[str]) -> dict:
    results = [classify_evidence(item) for item in evidence]
    accepted = [item for item in results if item["ok"] and item.get("counts_for_acceptance", True)]
    auxiliary = [item for item in results if item["ok"] and not item.get("counts_for_acceptance", True)]
    return {
        "ok": bool(stage.strip()) and bool(summary.strip()) and bool(accepted),
        "stage": stage,
        "summary": summary,
        "accepted_count": len(accepted),
        "auxiliary_count": len(auxiliary),
        "rejected_count": len([item for item in results if not item["ok"]]),
        "accepted_kinds": sorted({item["kind"] for item in accepted}),
        "auxiliary_kinds": sorted({item["kind"] for item in auxiliary}),
        "evidence": results,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate TigerMemory supervisor stage acceptance evidence")
    parser.add_argument("--stage", required=True, help="Stage name being accepted")
    parser.add_argument("--summary", required=True, help="Plain-language completion summary")
    parser.add_argument(
        "--evidence",
        action="append",
        default=[],
        help=(
            "Typed objective evidence, e.g. commit:<sha>, test:<cmd> => 13 passed, "
            "review:<path>, draft_review:<path>, file:<path>:<line>"
        ),
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = validate_acceptance(args.stage, args.summary, args.evidence)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
