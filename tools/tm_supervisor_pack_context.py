from __future__ import annotations

import argparse
import json
import pathlib
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
NEW_PROJECT_ROOT = pathlib.Path(r"C:\Users\Giant\Documents\New project")
if str(NEW_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEW_PROJECT_ROOT))

from common.supervisor_context_packer import write_supervisor_pack  # noqa: E402


OUT_DIR = REPO_ROOT / ".supervisor" / "context-packs"

PROFILE_INCLUDES = {
    "dev-supervisor": [
        "wiki/operations/project-canvas.md",
        "wiki/systems/tigermemory-development-supervisor.md",
        "wiki/operations/development-supervisor-ledger.md",
        "wiki/systems/tigermemory-project-map-for-claude.md",
        "tools/tm_dev_supervisor_context_pack.py",
        "tools/tm_dev_supervisor_review.py",
        "tools/tm_stage_accept.py",
    ],
    "diff-review": [
        "wiki/operations/project-canvas.md",
        "wiki/operations/development-supervisor-ledger.md",
        "tools/tm_dev_supervisor_context_pack.py",
        "tools/tm_dev_supervisor_review.py",
        "tools/tm_stage_accept.py",
    ],
    "memory-answer": [
        "wiki/operations/project-canvas.md",
        "wiki/systems/memory-answer-development-plan.md",
        "wiki/systems/tigermemory-development-supervisor.md",
        "wiki/operations/development-supervisor-ledger.md",
        "tools/tm_answer*.py",
        "tools/tm_dev_supervisor_context_pack.py",
        "tools/tm_dev_supervisor_review.py",
    ],
}

PROFILE_IGNORES = {
    "dev-supervisor": [
        "sources/internal-analysis/development-reviews/**",
        ".supervisor/claude-sessions.json",
        ".supervisor/claude-limits.json",
    ],
    "diff-review": [
        "sources/internal-analysis/development-reviews/**",
        ".supervisor/**",
    ],
    "memory-answer": [
        "sources/internal-analysis/development-reviews/**",
        ".supervisor/**",
        "data/**",
    ],
}

TIGERMEMORY_POLICY_HEADER = """
- This bundle is for TigerMemory development-supervisor review only.
- Use it as bounded context; do not infer that the whole repo was reviewed.
- official_review remains the only formal review channel for architecture/release/acceptance.
- api_test output is draft evidence only.
- Do not output secrets, OAuth credentials, Bearer tokens, cookies, session ids, or private connector secrets.
- If evidence is missing, return Missing Evidence before expanding scope.
""".strip()


def build_pack(
    *,
    stage: str,
    objective: str,
    profile: str,
    files: list[str],
    includes: list[str],
    output_dir: pathlib.Path = OUT_DIR,
    approx_token_budget: int = 120_000,
):
    profile_includes = PROFILE_INCLUDES[profile]
    include_patterns = list(dict.fromkeys([*profile_includes, *files, *includes]))
    ignore_patterns = PROFILE_IGNORES.get(profile, [])
    return write_supervisor_pack(
        root=REPO_ROOT,
        output_dir=output_dir,
        stage=stage,
        objective=objective,
        profile=profile,
        include_patterns=include_patterns,
        ignore_patterns=ignore_patterns,
        policy_header=TIGERMEMORY_POLICY_HEADER,
        approx_token_budget=approx_token_budget,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a safe TigerMemory development-supervisor review bundle.")
    parser.add_argument("--stage", required=True)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--profile", choices=sorted(PROFILE_INCLUDES), default="dev-supervisor")
    parser.add_argument("--file", action="append", default=[], help="Specific file or directory to include.")
    parser.add_argument("--include", action="append", default=[], help="Additional glob include pattern.")
    parser.add_argument("--output-dir", default=str(OUT_DIR))
    parser.add_argument("--token-budget", type=int, default=120_000)
    parser.add_argument("--print-json", action="store_true")
    args = parser.parse_args(argv)

    result = build_pack(
        stage=args.stage,
        objective=args.objective,
        profile=args.profile,
        files=args.file,
        includes=args.include,
        output_dir=pathlib.Path(args.output_dir),
        approx_token_budget=args.token_budget,
    )
    payload = {
        "ok": True,
        "pack_dir": str(result.pack_dir),
        "bundle": str(result.bundle_path),
        "manifest": str(result.manifest_path),
        "token_tree": str(result.token_tree_path),
        "secret_scan": str(result.secret_scan_path),
        "budget_status": result.manifest["budget"]["status"],
        "included_files": result.manifest["totals"]["included_files"],
        "excluded_sensitive_files": result.manifest["totals"]["excluded_sensitive_files"],
        "approx_tokens": result.manifest["totals"]["approx_tokens"],
    }
    if args.print_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"bundle: {result.bundle_path}")
        print(f"manifest: {result.manifest_path}")
        print(f"token_tree: {result.token_tree_path}")
        print(f"secret_scan: {result.secret_scan_path}")
        print(f"budget_status: {payload['budget_status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
