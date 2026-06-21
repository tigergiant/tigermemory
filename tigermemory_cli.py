#!/usr/bin/env python3
"""Umbrella `tm` command for the TigerMemory monorepo."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
import webbrowser


PROFILE_VALUES = {"local", "hybrid"}
ADMIN_PARTITIONS = (
    "projects",
    "areas",
    "resources",
    "decisions",
    "journal",
    "systems",
    "archive",
)
ADMIN_PROPOSAL_SCHEMA = "tigermemory-admin-proposal-v1"
ADMIN_PROPOSAL_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
PASSTHROUGH_COMMANDS = {
    "doctor": ("tools/tm_io.py", ["agent-doctor"]),
    "lessons": ("tools/tm_lessons.py", []),
    "persona": ("tools/tm_persona.py", []),
    "index": ("tools/tm_compile_index.py", []),
}

try:
    from tigermemory_core import WIKI_ADMIN_PUBLIC_PARTITIONS
    from tigermemory_core.llm_status import llm_status_payload
    from tigermemory_core.roots import (
        resolve_app_root,
        resolve_instance_root,
        subprocess_root_env,
    )
    ADMIN_PARTITIONS = tuple(WIKI_ADMIN_PUBLIC_PARTITIONS)
except Exception:
    llm_status_payload = None
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


def _dashboard_start_url(port: int = 9777) -> str:
    return f"http://127.0.0.1:{port}/start"


def _open_dashboard_start_later(url: str, delay_seconds: float = 0.8) -> None:
    def _open() -> None:
        time.sleep(delay_seconds)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=_open, name="tigermemory-dashboard-open", daemon=True).start()


def _root_hint(extra: str | None = None) -> dict[str, object]:
    expected = REPO_ROOT / "wiki" / "systems" / "agent-behavior-rules.md"
    hint: dict[str, object] = {
        "message": extra or "No local evidence matched. Check that you are running tm inside the intended TigerMemory checkout or set TIGERMEMORY_INSTANCE_ROOT.",
        "root": str(REPO_ROOT),
        "cwd": str(pathlib.Path.cwd().resolve()),
        "expected_starter_page": str(expected),
        "expected_starter_page_exists": expected.is_file(),
        "next": [
            "tm profile show",
            'tm search --scope wiki --query "agent behavior rules"',
            f"tm dashboard  # then open {_dashboard_start_url()}",
        ],
    }
    return hint


def _memory_count(payload: dict) -> int:
    try:
        return int(payload.get("count") or len(payload.get("items") or payload.get("results") or []))
    except Exception:
        return 0


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
    print("next=tm dashboard")
    print(f"start_url={_dashboard_start_url()}")
    print("guide=Open the start page for setup, system overview, and first commands.")
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
        print(f"root={REPO_ROOT}")
        print(f"cwd={pathlib.Path.cwd().resolve()}")
        print(f"env={env_value or ''}")
        print(f"file={file_value or ''}")
        print(f"path={_profile_path()}")
        print(f"dashboard={_dashboard_start_url()}")
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


def _mask_env_presence(name: str) -> dict[str, object]:
    value = _llm_env_value(name)
    return {
        "name": name,
        "configured": bool(value.strip()),
    }


def _llm_env_path() -> pathlib.Path:
    override = os.environ.get("TIGERMEMORY_OPENMEMORY_ENV", "").strip()
    if override:
        return pathlib.Path(override).expanduser().resolve()
    return REPO_ROOT / "runtime" / "openmemory" / ".env"


def _llm_env_file_values() -> dict[str, str]:
    env_path = _llm_env_path()
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            key = key.strip()
            if not key:
                continue
            values[key] = value.strip().strip('"').strip("'")
    except OSError:
        return {}
    return values


def _llm_env_value(name: str, default: str = "") -> str:
    shell_value = os.environ.get(name, "").strip()
    if shell_value:
        return shell_value
    return _llm_env_file_values().get(name, default)


def _llm_status_payload() -> dict[str, object]:
    if llm_status_payload is not None:
        return llm_status_payload(REPO_ROOT)
    raw_provider = _llm_env_value("TIGERMEMORY_LLM_PROVIDER", "deepseek").strip().lower()
    effective_provider = raw_provider if raw_provider in {"deepseek", "openai_compatible"} else "deepseek"
    deepseek_base = _llm_env_value("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1/chat/completions")
    deepseek_model = _llm_env_value("DEEPSEEK_MODEL", "deepseek-v4-flash")
    deepseek_admin_model = _llm_env_value("DEEPSEEK_ADMIN_MODEL", "deepseek-v4-pro")
    openai_base = _llm_env_value("OPENAI_BASE_URL") or _llm_env_value("OPENAI_API_BASE", "https://api.openai.com/v1/chat/completions")
    openai_model = _llm_env_value("OPENAI_MODEL", "")
    chat_configured = bool(_llm_env_value("DEEPSEEK_API_KEY").strip())
    legacy_openai_configured = bool(_llm_env_value("OPENAI_API_KEY").strip())
    deepseek_configured = chat_configured and effective_provider == "deepseek"
    openai_configured = (chat_configured and effective_provider == "openai_compatible") or legacy_openai_configured
    return {
        "ok": True,
        "schema": "tigermemory-llm-status-v1",
        "recommended_provider": "deepseek",
        "effective_provider": effective_provider,
        "llm_configured": chat_configured or legacy_openai_configured,
        "providers": [
            {
                "id": "deepseek",
                "configured": deepseek_configured,
                "api_key": _mask_env_presence("DEEPSEEK_API_KEY"),
                "base_url": deepseek_base,
                "model": deepseek_model,
                "admin_model": deepseek_admin_model,
                "openai_compatible": True,
                "recommended": True,
            },
            {
                "id": "openai_compatible",
                "configured": openai_configured,
                "api_key": _mask_env_presence("DEEPSEEK_API_KEY" if effective_provider == "openai_compatible" else "OPENAI_API_KEY"),
                "base_url": deepseek_base if effective_provider == "openai_compatible" else openai_base,
                "model": deepseek_model if effective_provider == "openai_compatible" else openai_model,
                "admin_model": deepseek_admin_model if effective_provider == "openai_compatible" else "",
                "openai_compatible": True,
                "recommended": False,
                "uses_chat_completions_slot": effective_provider == "openai_compatible",
            },
        ],
        "role_models": {
            "routine_json": {
                "provider": "deepseek",
                "env": "DEEPSEEK_MODEL",
                "model": deepseek_model,
                "default": "deepseek-v4-flash",
            },
            "wiki_admin": {
                "provider": "deepseek",
                "env": "DEEPSEEK_ADMIN_MODEL",
                "model": deepseek_admin_model,
                "default": "deepseek-v4-pro",
            },
        },
        "offline_fallback": "tm ask --offline --query \"your question\" --scope all",
        "next": "Set DEEPSEEK_API_KEY, then run tm llm status.",
    }


def cmd_llm(args: argparse.Namespace) -> int:
    if args.llm_command == "status":
        payload = _llm_status_payload()
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        print(f"recommended_provider={payload['recommended_provider']}")
        print(f"effective_provider={payload.get('effective_provider', 'deepseek')}")
        print(f"llm_configured={str(bool(payload['llm_configured'])).lower()}")
        for provider in payload["providers"]:
            print(
                "provider="
                f"{provider['id']} "
                f"configured={str(bool(provider['configured'])).lower()} "
                f"base_url={provider['base_url']} "
                f"model={provider['model']}"
            )
        for role, info in payload["role_models"].items():
            print(f"role_model={role} provider={info['provider']} env={info['env']} model={info['model']}")
        print(f"offline_fallback={payload['offline_fallback']}")
        print(f"next={payload['next']}")
        return 0
    if args.llm_command == "guide":
        print("purpose=configure TigerMemory's LLM-first Wiki Admin path")
        print("recommended_provider=deepseek")
        print("set=DEEPSEEK_API_KEY")
        print("default_model=deepseek-v4-flash")
        print("wiki_admin_model=deepseek-v4-pro")
        print("roles=DEEPSEEK_MODEL for routine JSON/routing; DEEPSEEK_ADMIN_MODEL for tm admin propose")
        print("optional=TIGERMEMORY_LLM_PROVIDER,DEEPSEEK_BASE_URL,DEEPSEEK_MODEL,DEEPSEEK_ADMIN_MODEL")
        print("compatible=openai_compatible providers use the same chat-completions slot; set TIGERMEMORY_LLM_PROVIDER=openai_compatible")
        print("verify=tm llm status --json")
        print("fallback=tm ask --offline returns local evidence only")
        print("security=keys stay in your shell or local runtime env; do not commit them")
        return 0
    print("llm command required: status|guide", file=sys.stderr)
    return 2


def _admin_proposals_dir() -> pathlib.Path:
    return REPO_ROOT / "runtime" / "tigermemory" / "admin-proposals"


def _admin_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _admin_read_source(args: argparse.Namespace) -> tuple[str | None, dict[str, object]]:
    if getattr(args, "source_file", None):
        path = pathlib.Path(args.source_file).expanduser()
        if not path.is_absolute():
            path = pathlib.Path.cwd() / path
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            print(f"cannot read source file: {e}", file=sys.stderr)
            return None, {}
        return text, {"kind": "file", "path": str(path), "sha256_12": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]}
    text = sys.stdin.read()
    if not text.strip():
        print("source text required on stdin or via --source-file", file=sys.stderr)
        return None, {}
    return text, {"kind": "stdin", "sha256_12": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]}


def _admin_proposal_id(title: str, text: str) -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha256(f"{title}\n{text}".encode("utf-8")).hexdigest()[:8]
    return f"{stamp}-{digest}"


def _admin_safe_json_path(proposal_id: str) -> pathlib.Path:
    if not ADMIN_PROPOSAL_ID_RE.fullmatch(proposal_id):
        raise ValueError("invalid proposal id")
    root = _admin_proposals_dir().resolve()
    path = (root / f"{proposal_id}.json").resolve()
    if root not in [path.parent, *path.parents]:
        raise ValueError("proposal id escapes proposal directory")
    return path


def _admin_load_proposal(proposal_id: str) -> dict:
    path = _admin_safe_json_path(proposal_id)
    if not path.is_file():
        raise FileNotFoundError(f"proposal not found: {proposal_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != ADMIN_PROPOSAL_SCHEMA:
        raise ValueError("unsupported proposal schema")
    data["_path"] = str(path)
    return data


def _admin_write_proposal(data: dict) -> pathlib.Path:
    proposal_id = str(data["id"])
    path = _admin_safe_json_path(proposal_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _admin_target_path(data: dict) -> pathlib.Path:
    target = str(data.get("target_path") or "")
    normalized = target.replace("\\", "/").strip("/")
    parts = normalized.split("/")
    if len(parts) < 3 or parts[0] != "wiki" or "/../" in f"/{normalized}/":
        raise ValueError("proposal target_path must stay under wiki/")
    if parts[1] not in ADMIN_PARTITIONS:
        raise ValueError(f"proposal target partition is not supported by the public Wiki Admin flow: {parts[1]}")
    route = data.get("route") if isinstance(data.get("route"), dict) else {}
    if route.get("auto_write_allowed") is not False:
        raise ValueError("proposal route must be human-review only with auto_write_allowed=false")
    path = (REPO_ROOT / normalized).resolve()
    root = REPO_ROOT.resolve()
    if root not in [path.parent, *path.parents]:
        raise ValueError("proposal target_path escapes instance root")
    return path


def _admin_print_or_json(payload: dict | list[dict], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if isinstance(payload, list):
        for item in payload:
            print(
                f"{item.get('id')} status={item.get('status')} "
                f"target={item.get('target_path', '')} title={item.get('title', '')}"
            )
        return
    for key, value in payload.items():
        if key.startswith("_"):
            continue
        if isinstance(value, (dict, list)):
            print(f"{key}={json.dumps(value, ensure_ascii=False)}")
        else:
            print(f"{key}={value}")


def cmd_admin(args: argparse.Namespace) -> int:
    if args.admin_command == "guide":
        print("purpose=LLM Wiki Admin proposal workflow")
        print("propose=cat notes.md | tm admin propose --partition projects --title \"My Notes\"")
        print("partitions=projects,areas,resources,decisions,journal,systems,archive")
        print("review=tm admin list; tm admin show <proposal-id>")
        print("approve=tm admin approve <proposal-id>")
        print("safety=propose only writes runtime proposals; approve is the explicit user action that writes wiki")
        print("fallback=tm ask --offline returns evidence only")
        return 0

    if args.admin_command == "propose":
        text, source = _admin_read_source(args)
        if text is None:
            return 2
        try:
            import tigermemory_core as tm_core

            proposal = tm_core.propose_wiki_admin_page(
                text,
                partition=args.partition,
                title=args.title,
                source=str(source.get("path") or source.get("kind") or "stdin"),
                source_refs=[source],
                input_kind="file_excerpt" if source.get("kind") == "file" else "manual_note",
                timeout=args.timeout,
            )
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
            return 4
        proposal_id = _admin_proposal_id(args.title, text)
        created_at = _admin_now_iso()
        proposal.update({
            "schema": ADMIN_PROPOSAL_SCHEMA,
            "id": proposal_id,
            "status": "pending",
            "created_at": created_at,
            "source": source,
            "user_review_required": True,
        })
        if args.dry_run:
            _admin_print_or_json(proposal, as_json=args.json)
            return 0
        path = _admin_write_proposal(proposal)
        proposal["proposal_path"] = str(path)
        _admin_print_or_json(proposal, as_json=args.json)
        return 0

    if args.admin_command == "list":
        root = _admin_proposals_dir()
        items: list[dict] = []
        for path in sorted(root.glob("*.json")) if root.is_dir() else []:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("schema") == ADMIN_PROPOSAL_SCHEMA:
                items.append({
                    "id": data.get("id") or path.stem,
                    "status": data.get("status", "unknown"),
                    "title": data.get("title", ""),
                    "target_path": data.get("target_path", ""),
                    "created_at": data.get("created_at", ""),
                })
        _admin_print_or_json(items, as_json=args.json)
        return 0

    if args.admin_command == "show":
        try:
            data = _admin_load_proposal(args.proposal_id)
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
            print(str(e), file=sys.stderr)
            return 2
        if args.json:
            _admin_print_or_json(data, as_json=True)
        else:
            _admin_print_or_json({
                "id": data.get("id"),
                "status": data.get("status"),
                "title": data.get("title"),
                "target_path": data.get("target_path"),
                "rationale": data.get("rationale"),
                "confidence": data.get("confidence"),
                "wiki_markdown_preview": str(data.get("wiki_markdown") or "")[:1200],
            }, as_json=False)
        return 0

    if args.admin_command == "approve":
        try:
            data = _admin_load_proposal(args.proposal_id)
            if data.get("status") != "pending":
                raise ValueError(f"proposal is not pending: {data.get('status')}")
            if data.get("should_write") is False:
                raise ValueError("proposal is marked should_write=false")
            target = _admin_target_path(data)
            if target.exists() and not args.force:
                raise ValueError(f"target exists; rerun with --force to overwrite: {data.get('target_path')}")
            wiki_markdown = str(data.get("wiki_markdown") or "").strip()
            if not wiki_markdown.startswith("---\n"):
                raise ValueError("proposal wiki_markdown is missing frontmatter")
            if args.dry_run:
                payload = {"ok": True, "dry_run": True, "id": data.get("id"), "target_path": data.get("target_path")}
                _admin_print_or_json(payload, as_json=args.json)
                return 0
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(wiki_markdown.rstrip() + "\n", encoding="utf-8")
            data["status"] = "approved"
            data["approved_at"] = _admin_now_iso()
            data["written_path"] = str(target)
            _admin_write_proposal(data)
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
            print(str(e), file=sys.stderr)
            return 2
        payload = {"ok": True, "id": data.get("id"), "status": data.get("status"), "target_path": data.get("target_path")}
        _admin_print_or_json(payload, as_json=args.json)
        return 0

    if args.admin_command == "reject":
        try:
            data = _admin_load_proposal(args.proposal_id)
            if data.get("status") != "pending":
                raise ValueError(f"proposal is not pending: {data.get('status')}")
            data["status"] = "rejected"
            data["rejected_at"] = _admin_now_iso()
            if args.reason:
                data["reject_reason"] = args.reason[:500]
            _admin_write_proposal(data)
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
            print(str(e), file=sys.stderr)
            return 2
        payload = {"ok": True, "id": data.get("id"), "status": data.get("status")}
        _admin_print_or_json(payload, as_json=args.json)
        return 0

    print("admin command required: guide|propose|list|show|approve|reject", file=sys.stderr)
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


def cmd_agent(args: argparse.Namespace) -> int:
    try:
        from tigermemory_config import agent_connect
    except Exception as exc:
        print(f"tm agent requires tigermemory_config.agent_connect: {exc}", file=sys.stderr)
        return 2
    forwarded: list[str] = [args.agent_command]
    for target in getattr(args, "target", None) or []:
        forwarded.extend(["--target", target])
    if getattr(args, "repo_root", None):
        forwarded.extend(["--repo-root", str(args.repo_root)])
    if getattr(args, "backup_root", None):
        forwarded.extend(["--backup-root", str(args.backup_root)])
    if getattr(args, "snapshot_id", None):
        forwarded.extend(["--snapshot-id", str(args.snapshot_id)])
    if getattr(args, "client", None):
        forwarded.extend(["--client", str(args.client)])
    if getattr(args, "yes", False):
        forwarded.append("--yes")
    if getattr(args, "dry_run", False):
        forwarded.append("--dry-run")
    if getattr(args, "json", False):
        forwarded.append("--json")
    return agent_connect.main(forwarded)


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
            payload = {
                "count": len(wiki_results),
                "results": wiki_results,
                "items": wiki_results,
                "search_backend": "wiki_hybrid",
            }
            if not wiki_results:
                payload["hint"] = _root_hint("No Wiki result matched. If you just installed TigerMemory, verify you are in the starter checkout and that starter Wiki pages exist.")
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        memory_payload = json.loads(tm_core.mem0_search(args.query, args.size))
        payload = {
            "query": args.query,
            "scope": "all",
            "memory": memory_payload,
            "wiki": {
                "count": len(wiki_results),
                "results": wiki_results,
                "items": wiki_results,
                "search_backend": "wiki_hybrid",
            },
        }
        if _memory_count(memory_payload) == 0 and not wiki_results:
            payload["hint"] = _root_hint("No memory or Wiki result matched. This often means the command is running from the wrong checkout or the starter Wiki has not been initialized.")
        print(json.dumps(payload, ensure_ascii=False))
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
            "summary": item.get("summary"),
            "tags": item.get("tags"),
            "key_facts": item.get("key_facts"),
            "aliases": item.get("aliases"),
        })
    return out


def cmd_ask(args: argparse.Namespace) -> int:
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
            if args.offline:
                wiki_results = tm_core.search_wiki(args.query, size=args.size, include_sources=True)
            else:
                wiki_results = tm_core.search_wiki_hybrid(args.query, size=args.size, include_sources=True)
        evidence = [
            *_ask_evidence_from_memory(memory_payload, args.size),
            *_ask_evidence_from_wiki(wiki_results, args.size),
        ]
        if args.offline:
            payload = {
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
            }
            if not evidence:
                payload["hint"] = _root_hint("Offline ask found no local evidence. Check the TigerMemory root directory, then open the dashboard start page for the first-run guide.")
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        answer = tm_core.answer_from_public_evidence(
            args.query,
            evidence[: max(1, args.size) * 2],
            timeout=args.timeout,
        )
        payload = {
            **answer,
            "scope": args.scope,
            "offline": False,
            "memory": memory_payload,
            "wiki": {
                "count": len(wiki_results),
                "items": wiki_results,
                "results": wiki_results,
                "search_backend": "wiki_hybrid",
            },
            "evidence": evidence[: max(1, args.size) * 2],
        }
        if not evidence:
            payload["hint"] = _root_hint("The answer path found no local evidence. Check the TigerMemory root directory before treating this as a knowledge miss.")
        print(json.dumps(payload, ensure_ascii=False))
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


def _update_app_root() -> pathlib.Path:
    if resolve_app_root is not None:
        return resolve_app_root()
    return REPO_ROOT


def _print_update_status(status: dict, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return
    print(f"source_mode={status.get('source_mode', '')}")
    print(f"app_root={status.get('app_root', '')}")
    print(f"branch={status.get('branch', '')}")
    print(f"head={status.get('head', '')}")
    print(f"upstream={status.get('upstream', '')}")
    print(f"behind={status.get('behind', 0)}")
    print(f"ahead={status.get('ahead', 0)}")
    print(f"dirty={str(bool(status.get('dirty'))).lower()}")
    print(f"update_available={str(bool(status.get('update_available'))).lower()}")
    print(f"safe_to_apply={str(bool(status.get('safe_to_apply'))).lower()}")
    print(f"recommended_action={status.get('recommended_action', '')}")
    for warning in status.get("warnings") or []:
        print(f"warning={warning}")


def cmd_update(args: argparse.Namespace) -> int:
    try:
        from tigermemory_update import apply_update, get_update_status
    except ModuleNotFoundError:
        print("tigermemory_update package is not installed; reinstall or run from a source checkout.", file=sys.stderr)
        return 2
    app_root = _update_app_root()
    if args.update_command in {"status", "check"}:
        status = get_update_status(app_root, refresh_remote=bool(args.refresh))
        _print_update_status(status, as_json=bool(args.json))
        return 0 if status.get("ok", False) else 4
    if args.update_command == "apply":
        result = apply_update(app_root, strategy=args.strategy, dry_run=bool(args.dry_run))
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"ok={str(bool(result.get('ok'))).lower()}")
            print(f"applied={str(bool(result.get('applied'))).lower()}")
            print(f"reason={result.get('reason', '')}")
            status = result.get("status")
            if isinstance(status, dict):
                print(f"recommended_action={status.get('recommended_action', '')}")
        return 0 if result.get("ok", False) else 4
    print("update command required: status|check|apply", file=sys.stderr)
    return 2


def cmd_dashboard(args: argparse.Namespace) -> int:
    forwarded = []
    if args.host:
        forwarded.extend(["--host", args.host])
    if args.port is not None:
        forwarded.extend(["--port", str(args.port)])
    start_url = _dashboard_start_url(args.port or 9777)
    tool_script = REPO_ROOT / "tools" / "tm_review_ui.py"
    if tool_script.is_file():
        print(f"dashboard_url={start_url}")
        if not getattr(args, "no_open", False):
            print("browser=opening")
            _open_dashboard_start_later(start_url)
        else:
            print("browser=disabled")
        return _run_python(str(tool_script), forwarded)
    try:
        import tigermemory_dashboard
    except ModuleNotFoundError:
        print(f"dashboard_url={start_url}")
        if getattr(args, "no_open", False):
            print("browser=disabled")
        else:
            print("browser=opening")
        return _run_python(str(tool_script), forwarded)
    if hasattr(tigermemory_dashboard, "main"):
        if getattr(args, "no_open", False):
            forwarded.append("--no-open")
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

    llm_p = sub.add_parser("llm", help="show or explain LLM provider configuration")
    llm_sub = llm_p.add_subparsers(dest="llm_command", required=True)
    llm_status = llm_sub.add_parser("status", help="show DeepSeek/OpenAI-compatible configuration without printing keys")
    llm_status.add_argument("--json", action="store_true")
    llm_status.set_defaults(func=cmd_llm)
    llm_guide = llm_sub.add_parser("guide", help="print the recommended LLM setup path")
    llm_guide.set_defaults(func=cmd_llm)

    admin_p = sub.add_parser("admin", help="LLM Wiki Admin proposal workflow")
    admin_sub = admin_p.add_subparsers(dest="admin_command", required=True)
    admin_guide = admin_sub.add_parser("guide", help="explain the proposal-first admin workflow")
    admin_guide.set_defaults(func=cmd_admin)
    admin_propose = admin_sub.add_parser("propose", help="draft a reviewable wiki proposal from source text")
    admin_propose.add_argument("--partition", required=True, choices=ADMIN_PARTITIONS)
    admin_propose.add_argument("--title", required=True)
    admin_propose.add_argument("--source-file", default=None)
    admin_propose.add_argument("--timeout", type=int, default=20)
    admin_propose.add_argument("--dry-run", action="store_true")
    admin_propose.add_argument("--json", action="store_true")
    admin_propose.set_defaults(func=cmd_admin)
    admin_list = admin_sub.add_parser("list", help="list pending and handled wiki admin proposals")
    admin_list.add_argument("--json", action="store_true")
    admin_list.set_defaults(func=cmd_admin)
    admin_show = admin_sub.add_parser("show", help="show a wiki admin proposal")
    admin_show.add_argument("proposal_id")
    admin_show.add_argument("--json", action="store_true")
    admin_show.set_defaults(func=cmd_admin)
    admin_approve = admin_sub.add_parser("approve", help="write an approved proposal to its wiki target")
    admin_approve.add_argument("proposal_id")
    admin_approve.add_argument("--dry-run", action="store_true")
    admin_approve.add_argument("--force", action="store_true", help="allow overwriting an existing target page")
    admin_approve.add_argument("--json", action="store_true")
    admin_approve.set_defaults(func=cmd_admin)
    admin_reject = admin_sub.add_parser("reject", help="mark a proposal as rejected")
    admin_reject.add_argument("proposal_id")
    admin_reject.add_argument("--reason", default="")
    admin_reject.add_argument("--json", action="store_true")
    admin_reject.set_defaults(func=cmd_admin)

    doctor_p = sub.add_parser("doctor", help="run agent doctor")
    doctor_p.add_argument("args", nargs=argparse.REMAINDER)
    doctor_p.set_defaults(func=cmd_doctor)

    agent_p = sub.add_parser("agent", help="connect AI tools to this TigerMemory project")
    agent_sub = agent_p.add_subparsers(dest="agent_command", required=True)
    for name in ("plan", "status", "apply"):
        p = agent_sub.add_parser(name)
        p.add_argument("--target", action="append", choices=["codex", "claude-code", "cursor", "hooks", "mcp", "all"], default=None)
        p.add_argument("--repo-root", type=pathlib.Path, default=None)
        p.add_argument("--json", action="store_true")
        if name == "apply":
            p.add_argument("--yes", action="store_true")
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("--backup-root", type=pathlib.Path, default=None)
        p.set_defaults(func=cmd_agent)
    agent_rollback = agent_sub.add_parser("rollback")
    agent_rollback.add_argument("--snapshot-id", required=True)
    agent_rollback.add_argument("--target", action="append", choices=["codex", "claude-code", "cursor", "hooks", "mcp", "all"], default=None)
    agent_rollback.add_argument("--repo-root", type=pathlib.Path, default=None)
    agent_rollback.add_argument("--backup-root", type=pathlib.Path, default=None)
    agent_rollback.add_argument("--yes", action="store_true")
    agent_rollback.add_argument("--dry-run", action="store_true")
    agent_rollback.add_argument("--json", action="store_true")
    agent_rollback.set_defaults(func=cmd_agent)
    agent_print = agent_sub.add_parser("print-config", help="print optional MCP config only")
    agent_print.add_argument("--client", choices=["codex", "claude-code", "claude-desktop", "cursor", "json"], default="codex")
    agent_print.add_argument("--json", action="store_true")
    agent_print.set_defaults(func=cmd_agent)

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

    ask_p = sub.add_parser("ask", help="answer from local evidence with the configured LLM; --offline returns evidence only")
    ask_p.add_argument("--query", required=True)
    ask_p.add_argument("--size", type=int, default=5)
    ask_p.add_argument("--scope", choices=["memory", "wiki", "all"], default="all", help="default: all")
    ask_p.add_argument("--db", default=None)
    ask_p.add_argument("--offline", action="store_true", help="return evidence only, without AI summary")
    ask_p.add_argument("--timeout", type=int, default=60)
    ask_p.set_defaults(func=cmd_ask)

    verify_p = sub.add_parser("verify", help="verify a memory id")
    verify_p.add_argument("--id", required=True)
    verify_p.add_argument("--terms", default=None)
    verify_p.add_argument("--digest-date", default=None)
    verify_p.add_argument("--db", default=None)
    verify_p.set_defaults(func=cmd_verify)

    update_p = sub.add_parser("update", help="check or apply source checkout updates")
    update_sub = update_p.add_subparsers(dest="update_command", required=True)
    update_status = update_sub.add_parser("status", help="show local update status without network refresh")
    update_status.add_argument("--json", action="store_true")
    update_status.add_argument("--refresh", action="store_true", help="fetch remote before reporting")
    update_status.set_defaults(func=cmd_update)
    update_check = update_sub.add_parser("check", help="fetch remote and check whether an update is available")
    update_check.add_argument("--json", action="store_true")
    update_check.add_argument("--refresh", action="store_true", default=True)
    update_check.set_defaults(func=cmd_update)
    update_apply = update_sub.add_parser("apply", help="apply a safe Git update to the source checkout")
    update_apply.add_argument("--dry-run", action="store_true")
    update_apply.add_argument("--strategy", choices=["ff-only", "rebase"], default="ff-only")
    update_apply.add_argument("--json", action="store_true")
    update_apply.set_defaults(func=cmd_update)

    dashboard_p = sub.add_parser("dashboard", help="start dashboard server")
    dashboard_p.add_argument("--host", default=None, help="bind host; default is the dashboard server's local host")
    dashboard_p.add_argument("--port", type=int, default=9777, help="bind port for public quick start; default: 9777")
    dashboard_p.add_argument("--no-open", action="store_true", help="start the dashboard without opening the browser")
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
