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
    "lessons": ("tools/tm_lessons.py", []),
    "persona": ("tools/tm_persona.py", []),
    "index": ("tools/tm_compile_index.py", []),
}

try:
    from tigermemory_core.roots import (
        resolve_app_root,
        resolve_instance_root,
        subprocess_root_env,
    )
except Exception:
    resolve_app_root = None
    resolve_instance_root = None
    subprocess_root_env = None


def _configure_stdio() -> None:
    if sys.version_info >= (3, 7):
        for stream in (sys.stdin, sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _detect_repo_root() -> pathlib.Path:
    if resolve_instance_root is not None:
        if os.environ.get("TIGERMEMORY_INSTANCE_ROOT") or os.environ.get("TIGERMEMORY_ROOT"):
            return resolve_instance_root()
    explicit = os.environ.get("TIGERMEMORY_ROOT")
    if explicit:
        return pathlib.Path(explicit).resolve()
    here = pathlib.Path(__file__).resolve()
    for ancestor in [here.parent, *here.parents]:
        if (
            (ancestor / "tools").is_dir()
            and (ancestor / "wiki").is_dir()
            and (ancestor / "tigermemory_cli.py").resolve() == here
        ):
            return ancestor
    cwd = pathlib.Path.cwd().resolve()
    for ancestor in [cwd, *cwd.parents]:
        if (ancestor / "tools").is_dir() and (ancestor / "wiki").is_dir():
            return ancestor
    try:
        from tigermemory_config import _detect_repo_root as config_detect_repo_root

        root = pathlib.Path(config_detect_repo_root()).resolve()
        if (root / "tools").is_dir() and (root / "wiki").is_dir():
            return root
    except Exception:
        pass
    return cwd


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
    if resolve_instance_root is not None:
        try:
            instance_root = resolve_instance_root()
        except Exception:
            instance_root = REPO_ROOT
        env.update(subprocess_root_env(instance_root))
    else:
        env.setdefault("TIGERMEMORY_ROOT", str(REPO_ROOT))
    env["TIGERMEMORY_INSTANCE_ROOT"] = env["TIGERMEMORY_ROOT"]
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _run_python(rel_path: str, args: list[str], *, cwd: pathlib.Path | None = None) -> int:
    path = pathlib.Path(rel_path)
    script = path if path.is_absolute() else REPO_ROOT / path
    if not script.is_file():
        print(f"missing tool script: {script}", file=sys.stderr)
        return 2
    return subprocess.call(
        [sys.executable, str(script), *args],
        cwd=str(cwd or REPO_ROOT),
        env=_subprocess_env(),
    )


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


def _read_required_stdin(label: str) -> str | None:
    text = sys.stdin.read().strip().lstrip("\ufeff").strip()
    if not text:
        print(f"{label} required on stdin", file=sys.stderr)
        return None
    return text


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


def _effective_profile() -> tuple[str, str | None, str | None]:
    env_value = os.environ.get("TIGERMEMORY_PROFILE")
    file_value = _read_profile_file()
    return env_value or file_value or "hybrid", env_value, file_value


def cmd_profile(args: argparse.Namespace) -> int:
    if args.profile_command == "set":
        path = _write_profile_file(args.value)
        print(f"profile={args.value}")
        print(f"written={path}")
        return 0
    if args.profile_command == "show":
        effective, env_value, file_value = _effective_profile()
        print(f"effective={effective}")
        print(f"env={env_value or ''}")
        print(f"file={file_value or ''}")
        print(f"path={_profile_path()}")
        return 0
    if args.profile_command == "guide":
        effective, _env_value, _file_value = _effective_profile()
        target = args.value or effective
        print(f"current={effective}")
        print(f"target={target}")
        if target == "local":
            print("mode=basic")
            print("requires=Python>=3.10, Markdown wiki, local SQLite")
            print("does_not_require=WSL,Docker,OpenMemory,Qdrant,Caddy,npm")
            print("next=tm init --profile local")
            print("verify=tm profile show; tm search --query \"your text\"")
            return 0
        print("mode=advanced")
        print("requires=OpenMemory/Mem0 reachable on MEM0_URL, MEM0_API_KEY, optional Qdrant/Caddy")
        print("before_switch=backup existing OpenMemory data; read deploy/openmemory/README.md")
        print("switch=tm profile set hybrid")
        print("verify=tm doctor --skip-l2; tm search --query \"memory_type: session-handoff\"")
        print("rollback=tm profile set local")
        return 0
    print("profile command required: show|set|guide", file=sys.stderr)
    return 2


def cmd_doctor(args: argparse.Namespace) -> int:
    return _run_python("tools/tm_io.py", ["agent-doctor", *args.args])


def cmd_publish(args: argparse.Namespace) -> int:
    if resolve_app_root is not None:
        app_root = resolve_app_root()
    else:
        app_root = REPO_ROOT
    checkout_script = app_root / "tools" / "tm_io.py"
    if checkout_script.is_file():
        return _run_python(str(checkout_script), ["publish", *args.args], cwd=app_root)
    print(
        "tm publish is a maintainer-only export command; run it from a TigerMemory source checkout "
        "or set TIGERMEMORY_APP_ROOT to that checkout.",
        file=sys.stderr,
    )
    return 2


def cmd_lessons(args: argparse.Namespace) -> int:
    return _run_python("tools/tm_lessons.py", args.args)


def cmd_persona(args: argparse.Namespace) -> int:
    return _run_python("tools/tm_persona.py", args.args)


def cmd_index(args: argparse.Namespace) -> int:
    return _run_python("tools/tm_compile_index.py", args.args)


def cmd_write_memory(args: argparse.Namespace) -> int:
    if args.db:
        os.environ["TIGERMEMORY_LOCAL_DB"] = args.db
    text = _read_required_stdin("text")
    if text is None:
        return 2
    try:
        import tigermemory_core as tm_core

        print(tm_core.mem0_write(args.agent, args.topic, text))
        return 0
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 4


def cmd_search(args: argparse.Namespace) -> int:
    if args.db:
        os.environ["TIGERMEMORY_LOCAL_DB"] = args.db
    try:
        import json
        import tigermemory_core as tm_core

        if args.scope == "memory":
            print(tm_core.mem0_search(args.query, args.size))
            return 0
        wiki_results = tm_core.search_wiki_hybrid(args.query, size=args.size)
        if args.scope == "wiki":
            print(json.dumps({
                "count": len(wiki_results),
                "results": wiki_results,
                "items": wiki_results,
                "search_backend": "wiki_hybrid",
            }, ensure_ascii=False))
            return 0
        memory_payload = json.loads(tm_core.mem0_search(args.query, args.size))
        print(json.dumps({
            "query": args.query,
            "scope": "all",
            "memory": memory_payload,
            "wiki": {
                "count": len(wiki_results),
                "results": wiki_results,
                "items": wiki_results,
                "search_backend": "wiki_hybrid",
            },
        }, ensure_ascii=False))
        return 0
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 4


def _ask_evidence_from_memory(payload: dict, limit: int) -> list[dict]:
    out: list[dict] = []
    for item in (payload.get("items") or payload.get("results") or [])[:limit]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("content") or "")
        out.append({
            "source": "memory",
            "id": item.get("id"),
            "topic": item.get("topic"),
            "source_agent": item.get("source_agent") or item.get("source"),
            "snippet": text[:260],
        })
    return out


def _ask_evidence_from_wiki(results: list[dict], limit: int) -> list[dict]:
    out: list[dict] = []
    for item in results[:limit]:
        if not isinstance(item, dict):
            continue
        out.append({
            "source": "wiki",
            "path": item.get("path"),
            "title": item.get("title"),
            "snippet": item.get("snippet"),
            "score": item.get("score"),
        })
    return out


def cmd_ask(args: argparse.Namespace) -> int:
    if not args.offline:
        print("tm ask currently supports --offline only; use it to return local evidence without AI summary.", file=sys.stderr)
        return 2
    if args.db:
        os.environ["TIGERMEMORY_LOCAL_DB"] = args.db
    os.environ["TIGERMEMORY_PROFILE"] = "local"
    try:
        import json
        import tigermemory_core as tm_core

        memory_payload: dict = {"count": 0, "items": [], "results": [], "search_backend": "local"}
        wiki_results: list[dict] = []
        if args.scope in {"memory", "all"}:
            memory_payload = json.loads(tm_core.mem0_search(args.query, args.size))
        if args.scope in {"wiki", "all"}:
            wiki_results = tm_core.search_wiki(args.query, size=args.size, include_sources=True)
        evidence = [
            *_ask_evidence_from_memory(memory_payload, args.size),
            *_ask_evidence_from_wiki(wiki_results, args.size),
        ]
        print(json.dumps({
            "query": args.query,
            "scope": args.scope,
            "offline": True,
            "answer": "离线模式只返回本地依据，不生成 AI 总结。",
            "memory": memory_payload,
            "wiki": {
                "count": len(wiki_results),
                "items": wiki_results,
                "results": wiki_results,
                "search_backend": "wiki_lexical",
            },
            "evidence": evidence[: max(1, args.size) * 2],
        }, ensure_ascii=False))
        return 0
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 4


def cmd_verify(args: argparse.Namespace) -> int:
    if args.db:
        os.environ["TIGERMEMORY_LOCAL_DB"] = args.db
    try:
        import json
        import tigermemory_core as tm_core

        result = tm_core.verify_memory_id(args.id, key_terms=args.terms, digest_date=args.digest_date)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 4


def cmd_dashboard(args: argparse.Namespace) -> int:
    forwarded = []
    if args.host:
        forwarded.extend(["--host", args.host])
    if args.port is not None:
        forwarded.extend(["--port", str(args.port)])
    tool_script = REPO_ROOT / "tools" / "tm_review_ui.py"
    if tool_script.is_file():
        return _run_python(str(tool_script), forwarded)
    try:
        import tigermemory_dashboard
    except ModuleNotFoundError:
        return _run_python(str(tool_script), forwarded)
    if hasattr(tigermemory_dashboard, "main"):
        return tigermemory_dashboard.main(forwarded)
    print("tm dashboard requires tigermemory_dashboard.main", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tm", description="TigerMemory umbrella command")
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init", help="create local runtime directories and optionally set profile")
    init_p.add_argument("--profile", choices=sorted(PROFILE_VALUES), default="local")
    init_p.set_defaults(func=cmd_init)

    profile_p = sub.add_parser("profile", help="show or set runtime profile")
    profile_sub = profile_p.add_subparsers(dest="profile_command", required=True)
    profile_show = profile_sub.add_parser("show")
    profile_show.set_defaults(func=cmd_profile)
    profile_set = profile_sub.add_parser("set")
    profile_set.add_argument("value", choices=sorted(PROFILE_VALUES))
    profile_set.set_defaults(func=cmd_profile)
    profile_guide = profile_sub.add_parser("guide", help="print local/basic or hybrid/advanced upgrade guidance")
    profile_guide.add_argument("value", choices=sorted(PROFILE_VALUES), nargs="?")
    profile_guide.set_defaults(func=cmd_profile)

    doctor_p = sub.add_parser("doctor", help="run agent doctor")
    doctor_p.add_argument("args", nargs=argparse.REMAINDER)
    doctor_p.set_defaults(func=cmd_doctor)

    publish_p = sub.add_parser("publish", help="maintainer-only public export/audit command")
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

    search_p = sub.add_parser("search", help="search local memory and/or Markdown wiki")
    search_p.add_argument("--query", required=True)
    search_p.add_argument("--size", type=int, default=5)
    search_p.add_argument("--scope", choices=["memory", "wiki", "all"], default="memory", help="default: memory")
    search_p.add_argument("--db", default=None)
    search_p.set_defaults(func=cmd_search)

    ask_p = sub.add_parser("ask", help="answer from local evidence; offline mode does not call an AI model")
    ask_p.add_argument("--query", required=True)
    ask_p.add_argument("--size", type=int, default=5)
    ask_p.add_argument("--scope", choices=["memory", "wiki", "all"], default="all", help="default: all")
    ask_p.add_argument("--db", default=None)
    ask_p.add_argument("--offline", action="store_true", help="required: return evidence only, without AI summary")
    ask_p.set_defaults(func=cmd_ask)

    verify_p = sub.add_parser("verify", help="verify a memory id")
    verify_p.add_argument("--id", required=True)
    verify_p.add_argument("--terms", default=None)
    verify_p.add_argument("--digest-date", default=None)
    verify_p.add_argument("--db", default=None)
    verify_p.set_defaults(func=cmd_verify)

    dashboard_p = sub.add_parser("dashboard", help="start dashboard server")
    dashboard_p.add_argument("--host", default=None, help="bind host; default is tm_review_ui.py's local host")
    dashboard_p.add_argument("--port", type=int, default=9777, help="bind port for public quick start; default: 9777")
    dashboard_p.set_defaults(func=cmd_dashboard)

    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args and raw_args[0] in PASSTHROUGH_COMMANDS:
        script, prefix = PASSTHROUGH_COMMANDS[raw_args[0]]
        return _run_python(script, [*prefix, *raw_args[1:]])
    parser = build_parser()
    args, extra_args = parser.parse_known_args(raw_args)
    if args.command != "publish":
        if extra_args:
            parser.error(f"unrecognized arguments: {' '.join(extra_args)}")
    else:
        # Accept passthrough-style flags after `publish`.
        # The child script is the real compatibility boundary in true-split mode,
        # so we intentionally forward flags here (including legacy publish args).
        combined = getattr(args, "args", [])
        setattr(args, "args", extra_args + combined)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
