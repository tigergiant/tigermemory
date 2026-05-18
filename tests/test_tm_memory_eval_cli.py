from __future__ import annotations

import os
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_memory_eval  # type: ignore[import-not-found]


def test_load_eval_env_loads_only_embedding_vars(tmp_path, monkeypatch):
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MEM0_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join([
            "EMBEDDING_BASE_URL=http://127.0.0.1:19190/v1",
            "EMBEDDING_MODEL=qwen3-embedding",
            "OPENAI_API_KEY=test-key",
            "MEM0_API_KEY=should-not-load",
        ]),
        encoding="utf-8",
    )

    tm_memory_eval.load_eval_env(env_file)

    assert os.environ["EMBEDDING_BASE_URL"] == "http://127.0.0.1:19190/v1"
    assert os.environ["EMBEDDING_MODEL"] == "qwen3-embedding"
    assert os.environ["OPENAI_API_KEY"] == "test-key"
    assert "MEM0_API_KEY" not in os.environ


def test_load_eval_env_rewrites_docker_host_alias_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_memory_eval.os, "name", "nt", raising=False)
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "EMBEDDING_BASE_URL=http://host.docker.internal:19190/v1\n"
        "EMBEDDING_MODEL=qwen3-embedding\n"
        "OPENAI_API_KEY=test-key\n",
        encoding="utf-8",
    )

    tm_memory_eval.load_eval_env(env_file)

    assert os.environ["EMBEDDING_BASE_URL"] == "http://localhost:19190/v1"


def test_load_eval_env_preserves_docker_host_alias_off_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_memory_eval.os, "name", "posix", raising=False)
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "EMBEDDING_BASE_URL=http://host.docker.internal:19190/v1\n"
        "EMBEDDING_MODEL=qwen3-embedding\n"
        "OPENAI_API_KEY=test-key\n",
        encoding="utf-8",
    )

    tm_memory_eval.load_eval_env(env_file)

    assert os.environ["EMBEDDING_BASE_URL"] == "http://host.docker.internal:19190/v1"
