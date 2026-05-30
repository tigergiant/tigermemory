#!/usr/bin/env python3
"""Umbrella `tm` command for the TigerMemory monorepo."""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys


PROFILE_VALUES = {"local", "hybrid"}
PASSTHROUGH_COMMANDS = {
    "doctor": ("tools/tm_io.py", ["agent-doctor"]),
    "publish": ("tools/tm_io.py", ["publish"]),
    "lessons": ("tools/tm_lessons.py", []),
    "persona": ("tools/tm_persona.py", []),
    "index": ("tools/tm_compile_index.py", []),
}


def _configure_stdio() -> None:
    if sys.version_info >= (3, 7):
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _detect_repo_root() -> pathlib.Path:
    try:
        from tigermemory_config import _detect_repo_root as config_detect_repo_root

        root = pathlib.Path(config_detect_repo_root()).resolve()
        if (root / "tools").is_dir() and (root / "wiki").is_dir():
            return root
    except Exception:
        pass
    here = pathlib.Path(__file__).resolve()
    for ancestor in [here.parent, *here.parents]:
        if (ancestor / "tools").is_dir() and (ancestor / "wiki").is_dir():
            return ancestor
    return pathlib.Path.cwd().resolve()


REPO_ROOT = _detect_repo_root()


def _package_src_paths() -> list[str]:
    packages_dir = REPO_ROOT / "packages"
    if not packages_dir.is_dir():
        return []
    return [
        str(src)
        for src in sorted(packages_dir.glob("*/src"))
        if src.is_dir()
    ]


def _subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    prefix = os.pathsep.join(_package_src_paths())
    if prefix:
        env["PYTHONPATH"] = prefix + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _run_python(rel_path: str, args: list[str]) -> int:
    script = REPO_ROOT / rel_path
    if not script.is_file():
        print(f"missing tool script: {script}", file=sys.stderr)
        return 2
    return subprocess.call([sys.executable, str(script), *args], cwd=REPO_ROOT, env=_subprocess_env())


def _profile_path() -> pathlib.Path:
    return REPO_ROOT / "runtime" / "tigermemory" / "profile.env"


def _read_profile_file() -> str | None:
    path = _profile_path()
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "TIGERMEMORY_PROFILE":
            return value.strip()
    return None


def _write_profile_file(profile: str) -> pathlib.Path:
    path = _profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# TigerMemory runtime profile. Process env TIGERMEMORY_PROFILE still wins.\n"
        f"TIGERMEMORY_PROFILE={profile}\n",
        encoding="utf-8",
    )
    return path


def cmd_init(args: argparse.Namespace) -> int:
    (REPO_ROOT / "data" / "tigermemory").mkdir(parents=True, exist_ok=True)
    (REPO_ROOT / "runtime" / "tigermemory").mkdir(parents=True, exist_ok=True)
    if args.profile:
        _write_profile_file(args.profile)
    print(f"root={REPO_ROOT}")
    print("created=data/tigermemory runtime/tigermemory")
    if args.profile:
        print(f"profile={args.profile}")
    else:
        print("profile=unchanged")
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    if args.profile_command == "set":
        path = _write_profile_file(args.value)
        print(f"profile={args.value}")
        print(f"written={path}")
        return 0
    if args.profile_command == "show":
        env_value = os.environ.get("TIGERMEMORY_PROFILE")
        file_value = _read_profile_file()
        effective = env_value or file_value or "hybrid"
        print(f"effective={effective}")
        print(f"env={env_value or ''}")
        print(f"file={file_value or ''}")
        print(f"path={_profile_path()}")
        return 0
    print("profile command required: show|set", file=sys.stderr)
    return 2


def cmd_doctor(args: argparse.Namespace) -> int:
    return _run_python("tools/tm_io.py", ["agent-doctor", *args.args])


def cmd_publish(args: argparse.Namespace) -> int:
    return _run_python("tools/tm_io.py", ["publish", *args.args])


def cmd_lessons(args: argparse.Namespace) -> int:
    return _run_python("tools/tm_lessons.py", args.args)


def cmd_persona(args: argparse.Namespace) -> int:
    return _run_python("tools/tm_persona.py", args.args)


def cmd_index(args: argparse.Namespace) -> int:
    return _run_python("tools/tm_compile_index.py", args.args)


def cmd_write_memory(args: argparse.Namespace) -> int:
    forwarded = ["mem0-write", "--agent", args.agent, "--topic", args.topic]
    if args.db:
        forwarded.extend(["--db", args.db])
    return _run_python("tools/tm_io.py", forwarded)


def cmd_search(args: argparse.Namespace) -> int:
    forwarded = ["mem0-search", "--query", args.query, "--size", str(args.size)]
    if args.db:
        forwarded.extend(["--db", args.db])
    return _run_python("tools/tm_io.py", forwarded)


def cmd_dashboard(args: argparse.Namespace) -> int:
    forwarded = []
    if args.host:
        forwarded.extend(["--host", args.host])
    if args.port:
        forwarded.extend(["--port", str(args.port)])
    return _run_python("tools/tm_review_ui.py", forwarded)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tm", description="TigerMemory umbrella command")
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init", help="create local runtime directories and optionally set profile")
    init_p.add_argument("--profile", choices=sorted(PROFILE_VALUES), default=None)
    init_p.set_defaults(func=cmd_init)

    profile_p = sub.add_parser("profile", help="show or set runtime profile")
    profile_sub = profile_p.add_subparsers(dest="profile_command", required=True)
    profile_show = profile_sub.add_parser("show")
    profile_show.set_defaults(func=cmd_profile)
    profile_set = profile_sub.add_parser("set")
    profile_set.add_argument("value", choices=sorted(PROFILE_VALUES))
    profile_set.set_defaults(func=cmd_profile)

    doctor_p = sub.add_parser("doctor", help="run agent doctor")
    doctor_p.add_argument("args", nargs=argparse.REMAINDER)
    doctor_p.set_defaults(func=cmd_doctor)

    publish_p = sub.add_parser("publish", help="run publish snapshot/audit")
    publish_p.add_argument("args", nargs=argparse.REMAINDER)
    publish_p.set_defaults(func=cmd_publish)

    lessons_p = sub.add_parser("lessons", help="run lessons CLI")
    lessons_p.add_argument("args", nargs=argparse.REMAINDER)
    lessons_p.set_defaults(func=cmd_lessons)

    persona_p = sub.add_parser("persona", help="run persona onboarding CLI")
    persona_p.add_argument("args", nargs=argparse.REMAINDER)
    persona_p.set_defaults(func=cmd_persona)

    index_p = sub.add_parser("index", help="run index compiler CLI")
    index_p.add_argument("args", nargs=argparse.REMAINDER)
    index_p.set_defaults(func=cmd_index)

    write_p = sub.add_parser("write-memory", help="write memory text from stdin")
    write_p.add_argument("--agent", required=True)
    write_p.add_argument("--topic", required=True)
    write_p.add_argument("--db", default=None)
    write_p.set_defaults(func=cmd_write_memory)

    search_p = sub.add_parser("search", help="search memory backend")
    search_p.add_argument("--query", required=True)
    search_p.add_argument("--size", type=int, default=5)
    search_p.add_argument("--db", default=None)
    search_p.set_defaults(func=cmd_search)

    dashboard_p = sub.add_parser("dashboard", help="start dashboard server")
    dashboard_p.add_argument("--host", default=None)
    dashboard_p.add_argument("--port", type=int, default=None)
    dashboard_p.set_defaults(func=cmd_dashboard)

    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args and raw_args[0] in PASSTHROUGH_COMMANDS:
        script, prefix = PASSTHROUGH_COMMANDS[raw_args[0]]
        return _run_python(script, [*prefix, *raw_args[1:]])
    parser = build_parser()
    args = parser.parse_args(raw_args)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
