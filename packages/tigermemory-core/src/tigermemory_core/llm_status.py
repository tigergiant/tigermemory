from __future__ import annotations

import os
import pathlib
from typing import Any


def llm_env_path(repo_root: pathlib.Path) -> pathlib.Path:
    override = os.environ.get("TIGERMEMORY_OPENMEMORY_ENV", "").strip()
    if override:
        return pathlib.Path(override).expanduser().resolve()
    return repo_root / "runtime" / "openmemory" / ".env"


def llm_env_file_values(repo_root: pathlib.Path) -> dict[str, str]:
    env_path = llm_env_path(repo_root)
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
            if key:
                values[key] = value.strip().strip('"').strip("'")
    except OSError:
        return {}
    return values


def llm_env_value(repo_root: pathlib.Path, name: str, default: str = "") -> str:
    shell_value = os.environ.get(name, "").strip()
    if shell_value:
        return shell_value
    return llm_env_file_values(repo_root).get(name, default)


def mask_env_presence(repo_root: pathlib.Path, name: str) -> dict[str, object]:
    value = llm_env_value(repo_root, name)
    return {
        "name": name,
        "configured": bool(value.strip()),
    }


def llm_status_payload(repo_root: pathlib.Path) -> dict[str, Any]:
    deepseek_base = llm_env_value(repo_root, "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1/chat/completions")
    deepseek_model = llm_env_value(repo_root, "DEEPSEEK_MODEL", "deepseek-v4-flash")
    deepseek_admin_model = llm_env_value(repo_root, "DEEPSEEK_ADMIN_MODEL", "deepseek-v4-pro")
    openai_base = (
        llm_env_value(repo_root, "OPENAI_BASE_URL")
        or llm_env_value(repo_root, "OPENAI_API_BASE", "https://api.openai.com/v1")
    )
    openai_model = llm_env_value(repo_root, "OPENAI_MODEL", "")
    deepseek_configured = bool(llm_env_value(repo_root, "DEEPSEEK_API_KEY").strip())
    openai_configured = bool(llm_env_value(repo_root, "OPENAI_API_KEY").strip())
    return {
        "ok": True,
        "schema": "tigermemory-llm-status-v1",
        "recommended_provider": "deepseek",
        "llm_configured": deepseek_configured or openai_configured,
        "providers": [
            {
                "id": "deepseek",
                "configured": deepseek_configured,
                "api_key": mask_env_presence(repo_root, "DEEPSEEK_API_KEY"),
                "base_url": deepseek_base,
                "model": deepseek_model,
                "admin_model": deepseek_admin_model,
                "openai_compatible": True,
                "recommended": True,
            },
            {
                "id": "openai_compatible",
                "configured": openai_configured,
                "api_key": mask_env_presence(repo_root, "OPENAI_API_KEY"),
                "base_url": openai_base,
                "model": openai_model,
                "openai_compatible": True,
                "recommended": False,
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
        "offline_fallback": 'tm ask --offline --query "your question" --scope all',
        "next": "Set DEEPSEEK_API_KEY, then run tm llm status.",
    }
