import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_spec_capsule


VALID_CAPSULE = """# Spec Capsule

## 问题
AGENTS.md 的高风险规则修改缺少审阅卡片。

## 证据
来自日报 proposal 和用户要求。

## 约束
Codex 不能直接修改 AGENTS.md。

## 方案
写入 inbox 提案并由有权限 agent 修改。

## 验收
lint 通过，proposal 可在 Review UI 看到。

## 回滚
撤回 proposal 或 revert 对应提交。

## 是否需要虎哥确认
需要，因为这是元规则入口。
"""


def test_parse_capsule_text_requires_all_sections():
    result = tm_spec_capsule.parse_capsule_text(VALID_CAPSULE)

    assert result["ok"] is True
    assert result["summary"]["problem"].startswith("AGENTS.md")
    assert result["needs_tiger_confirmation"] is True


def test_parse_capsule_text_reports_missing_sections():
    result = tm_spec_capsule.parse_capsule_text("## 问题\n只有问题。\n")

    assert result["ok"] is False
    assert "证据" in result["missing_sections"]
    assert "回滚" in result["missing_sections"]


def test_parse_capsule_text_ignores_template_inside_code_fence():
    guide = """# Guide

```markdown
## 问题
<template>

## 证据
<template>

## 约束
<template>

## 方案
<template>

## 验收
<template>

## 回滚
<template>

## 是否需要虎哥确认
不需要
```
"""

    result = tm_spec_capsule.parse_capsule_text(guide)

    assert result["ok"] is False
    assert "问题" in result["missing_sections"]


def test_validate_proposal_dir_requires_capsule_when_flagged(tmp_path):
    pdir = tmp_path / "proposal-2026-06-14-001"
    pdir.mkdir()
    (pdir / "proposal.json").write_text(
        json.dumps({"type": "wiki-doc", "requires_spec_capsule": True}),
        encoding="utf-8",
    )
    (pdir / "patch").write_text(
        "diff --git a/wiki/systems/x.md b/wiki/systems/x.md\n+++ b/wiki/systems/x.md\n+new\n",
        encoding="utf-8",
    )

    result = tm_spec_capsule.validate_proposal_dir(pdir)

    assert result["required"] is True
    assert result["ok"] is False

    (pdir / "spec-capsule.md").write_text(VALID_CAPSULE, encoding="utf-8")

    result = tm_spec_capsule.validate_proposal_dir(pdir)

    assert result["required"] is True
    assert result["ok"] is True


def test_route_prompt_patch_is_high_risk_even_without_flag(tmp_path):
    pdir = tmp_path / "proposal-2026-06-14-002"
    pdir.mkdir()
    (pdir / "proposal.json").write_text(json.dumps({"type": "prompt-tuning"}), encoding="utf-8")
    (pdir / "patch").write_text(
        "diff --git a/tools/tm_route.py b/tools/tm_route.py\n+++ b/tools/tm_route.py\n+ROUTE_PROMPT = 'x'\n",
        encoding="utf-8",
    )

    result = tm_spec_capsule.validate_proposal_dir(pdir)

    assert result["required"] is True
    assert result["ok"] is False


def test_codex_automation_patch_is_high_risk_without_literal_user_path(tmp_path):
    pdir = tmp_path / "proposal-2026-06-14-003"
    pdir.mkdir()
    codex_automation = "C:" + "/Users/Example/.codex/automations/daily/task.toml"
    (pdir / "proposal.json").write_text(json.dumps({"type": "prompt-tuning"}), encoding="utf-8")
    (pdir / "patch").write_text(
        f"diff --git a/{codex_automation} b/{codex_automation}\n+++ b/{codex_automation}\n+enabled=true\n",
        encoding="utf-8",
    )

    result = tm_spec_capsule.validate_proposal_dir(pdir)

    assert result["required"] is True
    assert result["ok"] is False
