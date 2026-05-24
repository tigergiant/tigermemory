#!/usr/bin/env python3
"""Shared dashboard preference storage helpers.

This module intentionally has no FastAPI or MCP dependency. Web routes and MCP
tools both call this small SQLite kernel, then perform their own transport or
proposal side effects.
Inputs: Local repo state, service health endpoints, lessons/wiki pages, Mem0 records, or dashboard preference files.
Outputs: Doctor/audit/onboarding/metrics reports, local UI helper effects, or JSON diagnostics.
Depends-on (must-have): tm_core helpers, local filesystem/git state, and configured local services when the command asks for live checks.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sqlite3
from typing import Any

import tm_core

REPO_ROOT = tm_core.REPO_ROOT
PREFS_DB = REPO_ROOT / "data" / "dashboard" / "user_prefs.sqlite"

DEFAULT_PREFERENCES: dict[str, Any] = {
    "communication_depth": "A",
    "exemptions": ["git", "ai", "tigermemory", "agent", "data-format"],
    "custom_terms": [],
    "progressive_term_frequency": False,
    "agents": ["cascade", "claude-code", "codex", "chatgpt", "kimi", "hermes", "openclaw"],
    "model_workflow": "开发任务交给 codex；Claude 4.7 仅做监督 / 仲裁 / 设计。",
    "command_timeout_budget": "10 / 30 / 60 / 120 秒",
}


def _relpath(path: pathlib.Path | str) -> str:
    p = pathlib.Path(path)
    if not p.is_absolute():
        return str(p).replace("\\", "/")
    try:
        return str(p.resolve().relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(p)


def _connect(db_path: pathlib.Path = PREFS_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_prefs (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS term_annotation_counter (
            term TEXT PRIMARY KEY,
            count INTEGER NOT NULL DEFAULT 0,
            graduated INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """
    )
    return conn


def get_user_preferences(db_path: pathlib.Path = PREFS_DB) -> dict[str, Any]:
    prefs = dict(DEFAULT_PREFERENCES)
    conn = _connect(db_path)
    try:
        for key, value_json in conn.execute("SELECT key, value_json FROM user_prefs"):
            try:
                prefs[str(key)] = json.loads(str(value_json))
            except json.JSONDecodeError:
                prefs[str(key)] = value_json
    finally:
        conn.close()
    return {
        "ok": True,
        "path": _relpath(db_path),
        "preferences": prefs,
        "defaults": DEFAULT_PREFERENCES,
    }


def update_user_preferences(updates: dict[str, Any], db_path: pathlib.Path = PREFS_DB) -> dict[str, Any]:
    if not isinstance(updates, dict):
        raise ValueError("preferences must be an object")
    allowed = set(DEFAULT_PREFERENCES)
    unknown = sorted(set(updates) - allowed)
    if unknown:
        raise ValueError(f"unknown preference key(s): {', '.join(unknown)}")
    now = dt.datetime.now(tm_core.TZ_CN).isoformat(timespec="seconds")
    conn = _connect(db_path)
    try:
        for key, value in updates.items():
            conn.execute(
                """
                INSERT INTO user_prefs(key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False), now),
            )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "updated": sorted(updates), "preferences": get_user_preferences(db_path)["preferences"]}


def preferences_markdown(prefs: dict[str, Any]) -> str:
    updated = dt.datetime.now(tm_core.TZ_CN).isoformat(timespec="seconds")
    lines = [
        "# 虎哥偏好设置",
        "",
        "## 摘要",
        "",
        "本页由 tigermemory dashboard 的 `/settings` 生成提案，供 claude-code 审核后固化为长期偏好。",
        "",
        "## 已验证现状",
        "",
        f"- 更新时间：`{updated}`",
        f"- 沟通深度档位：`{prefs.get('communication_depth')}`",
        f"- 渐进式降频：`{prefs.get('progressive_term_frequency')}`",
        f"- 模型分工偏好：{prefs.get('model_workflow')}",
        f"- 命令超时预算：{prefs.get('command_timeout_budget')}",
        f"- 适用 agent：{', '.join(str(item) for item in prefs.get('agents', []))}",
        f"- 豁免清单：{', '.join(str(item) for item in prefs.get('exemptions', []))}",
        "",
        "## 待确认",
        "",
        "- 本页需由 claude-code 或 human 审核后合入 `wiki/person/`，dashboard 不直接写 person 分区。",
        "",
        "## 原始 JSON",
        "",
        "```json",
        json.dumps(prefs, ensure_ascii=False, indent=2),
        "```",
    ]
    return "\n".join(lines) + "\n"


def preference_page_payload(prefs: dict[str, Any]) -> dict[str, str]:
    return {
        "agent": "cascade",
        "partition": "person",
        "slug": "tiger-preferences",
        "frontmatter": (
            "owner: claude-code\n"
            "status: active\n"
            "aliases: [\"虎哥偏好\", \"tiger preferences\", \"agent 沟通偏好\"]\n"
            "title: \"虎哥偏好设置\""
        ),
        "body": preferences_markdown(prefs),
        "action": "update" if (REPO_ROOT / "wiki" / "person" / "tiger-preferences.md").exists() else "create",
    }
