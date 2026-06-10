#!/usr/bin/env python3
"""
tm_compile_systemd_inventory.py — deterministic compiler for
wiki/systems/services-inventory.md.

Reads systemd unit files in deploy/mcp/ to produce a single inventory page
listing the long-lived services tigermemory operates (dashboard 1998,
tm-http 8790, tm-mcp 9766, tm-openai-mcp 9776, OAuth facade VPS), the
timer-driven oneshot jobs, a port quick-lookup, and the external Mem0
dependency surfaced via Environment=MEM0_URL=...

Why this exists
---------------
2026-05-23 lesson `2026-05-23-cascade-single-dir-non-existence-claim`:
cascade reported "dashboard frontend doesn't exist" after only checking
one directory, because the onboarding tool's agent ecosystem section did
not list the dashboard / tm-http / tm-mcp services. Auto-compiling from
deploy/mcp/ (the actual systemd ground truth) prevents this drift forever.

CLI mirrors tools/tm_compile_index.py:

  tm_compile_systemd_inventory.py check    # exit 1 if drift
  tm_compile_systemd_inventory.py diff     # print diff, exit 0
  tm_compile_systemd_inventory.py write    # rewrite wiki/systems/services-inventory.md

Exit codes:
  0 no diff (check) / success (write|diff)
  1 diff present (check only)
  2 bad usage / validation failure
Inputs: Repository markdown/python files, frontmatter, section text, git diff inputs, or CLI path arguments.
Outputs: Deterministic reports, rewritten generated files, validation errors, or patch proposals.
Depends-on (must-have): Python stdlib plus tm_core/path parsers; no Mem0 write path unless explicitly invoked by caller.
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import os
import pathlib
import re
import sys
from dataclasses import dataclass, field
from typing import Iterable

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEPLOY_MCP = REPO_ROOT / "deploy" / "mcp"
OUTPUT_PAGE = REPO_ROOT / "wiki" / "systems" / "services-inventory.md"
# Fixed UTC+8 offset (Asia/Shanghai, no DST). Avoids the zoneinfo / tzdata
# dependency that Windows Python lacks by default.
TZ_CN = datetime.timezone(datetime.timedelta(hours=8))

# Path prefixes stripped from ExecStart so the rendered "entry" column shows
# repo-relative paths. Deployment users can override the prefix without
# hard-coding a contributor home directory into the source tree.
PATH_PREFIXES = tuple(
    prefix
    for prefix in (
        os.environ.get("TIGERMEMORY_DEPLOY_PREFIX"),
        "/srv/tigermemory/",
        "/opt/tigermemory/",
        "/root/tigermemory/",
    )
    if prefix
)
HOME_DEPLOY_RE = re.compile(r"^/home/[^/]+/tigermemory/")

PORT_RE = re.compile(r"--port\s+(\d+)")
ENV_KV_RE = re.compile(r"^Environment=(\w+)=(.+)$")


# ---------- data ----------


@dataclass
class Unit:
    """Parsed systemd unit (service or timer)."""
    name: str                         # e.g. "tm-dashboard"
    kind: str                         # "service" | "timer"
    description: str = ""
    exec_start: str = ""
    type_: str = "simple"             # systemd Type= field, defaults to simple
    after: list[str] = field(default_factory=list)
    on_boot: str = ""                 # for timers
    on_active: str = ""               # for timers
    bound_unit: str = ""              # timer's Unit= target
    env: dict[str, str] = field(default_factory=dict)


# ---------- parsing ----------


def parse_unit(path: pathlib.Path) -> Unit:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lstrip(".")
    unit = Unit(name=path.stem, kind=suffix)
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("Description="):
            unit.description = line.split("=", 1)[1].strip()
        elif line.startswith("ExecStart="):
            unit.exec_start = line.split("=", 1)[1].strip()
        elif line.startswith("Type="):
            unit.type_ = line.split("=", 1)[1].strip()
        elif line.startswith("After="):
            unit.after = line.split("=", 1)[1].strip().split()
        elif line.startswith("OnBootSec="):
            unit.on_boot = line.split("=", 1)[1].strip()
        elif line.startswith("OnUnitActiveSec="):
            unit.on_active = line.split("=", 1)[1].strip()
        elif line.startswith("OnUnitInactiveSec=") and not unit.on_active:
            unit.on_active = line.split("=", 1)[1].strip()
        elif line.startswith("Unit=") and unit.kind == "timer":
            unit.bound_unit = line.split("=", 1)[1].strip()
        else:
            m = ENV_KV_RE.match(line)
            if m:
                unit.env[m.group(1)] = m.group(2).strip()
    return unit


def list_units(kind: str) -> list[Unit]:
    """List all units of given kind ('service' | 'timer'), sorted by name."""
    if not DEPLOY_MCP.is_dir():
        return []
    paths = sorted(DEPLOY_MCP.glob(f"*.{kind}"))
    return [parse_unit(p) for p in paths]


# ---------- extraction ----------


def repo_relative(absolute_path: str) -> str:
    """Strip the WSL/VPS deployment prefix to surface a repo-relative path."""
    for pref in PATH_PREFIXES:
        if absolute_path.startswith(pref):
            return absolute_path[len(pref):]
    if HOME_DEPLOY_RE.match(absolute_path):
        return HOME_DEPLOY_RE.sub("", absolute_path, count=1)
    return absolute_path


def extract_entry(exec_start: str) -> str:
    """Return the repo-relative entry script from ExecStart.

    ExecStart typically looks like:
      /home/<user>/tigermemory/<venv>/bin/python /home/<user>/tigermemory/tools/tm_review_ui.py --host 0.0.0.0 --port 1998
    or:
      /home/<user>/tigermemory/deploy/mcp/tm_mcp_auto_update.sh --http ...

    The first .py / .sh token *under* the tigermemory tree is the entry.
    Venv pythons and unrelated absolute paths are ignored.
    """
    for token in exec_start.split():
        if (any(token.startswith(p) for p in PATH_PREFIXES) or HOME_DEPLOY_RE.match(token)) and (
            token.endswith(".py") or token.endswith(".sh")
        ):
            return repo_relative(token)
    # Fallback: if nothing matched, surface the first non-flag token verbatim.
    for token in exec_start.split():
        if token.startswith("-"):
            continue
        return token
    return ""


def extract_port(exec_start: str) -> str:
    m = PORT_RE.search(exec_start)
    return m.group(1) if m else ""


def extract_mem0_url(units: Iterable[Unit]) -> str:
    """Return the most-common Environment=MEM0_URL value across units."""
    counts: dict[str, int] = {}
    for u in units:
        v = u.env.get("MEM0_URL")
        if v:
            counts[v] = counts.get(v, 0) + 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda kv: kv[1])[0]


# ---------- rendering ----------


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a GitHub Markdown table from headers + rows."""
    head = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join(["---"] * len(headers)) + " |"
    body = "\n".join("| " + " | ".join(r) + " |" for r in rows)
    return "\n".join([head, sep, body])


def render_services_section(services: list[Unit]) -> str:
    """Render the production services table (long-lived services first)."""
    rows: list[list[str]] = []
    for u in services:
        port = extract_port(u.exec_start) or "(none)"
        if "vps" in u.name.lower() and port and port != "(none)":
            port = f"{port} (VPS)"
        entry = extract_entry(u.exec_start) or "(see unit file)"
        rows.append([
            u.name,
            port,
            u.description or "(no Description=)",
            entry,
            f"deploy/mcp/{u.name}.service",
        ])
    return _md_table(
        ["服务", "端口", "用途 (Description=)", "入口代码", "unit 文件"],
        rows,
    )


def render_timers_section(timers: list[Unit]) -> str:
    rows: list[list[str]] = []
    for u in timers:
        cadence_parts = []
        if u.on_boot:
            cadence_parts.append(f"OnBoot {u.on_boot}")
        if u.on_active:
            cadence_parts.append(f"every {u.on_active}")
        cadence = " / ".join(cadence_parts) or "(see unit file)"
        bound = u.bound_unit or f"{u.name}.service"
        rows.append([
            f"{u.name}.timer",
            bound,
            cadence,
            u.description or "(no Description=)",
        ])
    return _md_table(
        ["timer", "关联 service", "触发节奏", "用途 (Description=)"],
        rows,
    )


def render_oneshot_section(oneshots: list[Unit]) -> str:
    rows = [
        [u.name, extract_entry(u.exec_start) or "(see unit)",
         u.description or "(no Description=)"]
        for u in oneshots
    ]
    return _md_table(
        ["服务", "入口代码", "用途 (Description=)"],
        rows,
    )


def render_port_lookup(services: list[Unit], mem0_url: str) -> str:
    """Render a port → service lookup table, including external Mem0."""
    rows: list[list[str]] = []
    seen: set[str] = set()
    for u in sorted(services, key=lambda s: extract_port(s.exec_start) or "9999"):
        port = extract_port(u.exec_start)
        if not port:
            continue
        suffix = " (VPS)" if "vps" in u.name.lower() else ""
        key = port + suffix
        if key in seen:
            continue
        seen.add(key)
        rows.append([
            f"{port}{suffix}",
            u.name,
            u.description or "(no Description=)",
        ])
    if mem0_url:
        m = re.match(r"https?://([^:/]+):(\d+)", mem0_url)
        if m:
            host, port = m.group(1), m.group(2)
            if port == "9765":
                service_name = "Mem0 auth gateway"
                note = (
                    f"Caddy 鉴权网关（外部依赖，{host}）。"
                    f"由 docker / 独立部署提供，反代 OpenMemory 后端 :8765。"
                    f"服务 unit 通过 `MEM0_URL={mem0_url}` 引用。"
                )
            else:
                service_name = "OpenMemory (Mem0)"
                note = (
                    f"OpenMemory / Mem0 后端（外部依赖，{host}）。"
                    f"由 docker / 独立部署提供，不在 deploy/mcp/。"
                    f"服务 unit 通过 `MEM0_URL={mem0_url}` 引用。"
                )
            rows.append([port, service_name, note])
    rows.sort(key=lambda r: (int(r[0].split()[0]), " (VPS)" in r[0]))
    return _md_table(["端口", "服务", "备注"], rows)


def render_page(today_iso: str) -> str:
    services = list_units("service")
    long_lived = [u for u in services if u.type_ != "oneshot"]
    oneshot = [u for u in services if u.type_ == "oneshot"]
    timers = list_units("timer")
    mem0_url = extract_mem0_url(services)

    services_table = render_services_section(long_lived)
    timers_table = render_timers_section(timers)
    port_table = render_port_lookup(long_lived, mem0_url)

    oneshot_block = ""
    if oneshot:
        oneshot_block = (
            "\n\n## Timer 驱动的 oneshot 服务\n\n" + render_oneshot_section(oneshot)
        )

    frontmatter = (
        "---\n"
        "owner: cascade\n"
        "status: active\n"
        f"updated: {today_iso}\n"
        'aliases: ["运行时服务清单", "tigermemory services inventory", '
        '"dashboard 端口 1998", "tm-http 端口 8790", "tm-mcp 端口 9766", '
        '"tm-openai-mcp 端口 9776", "deploy systemd inventory", '
        '"tigermemory 在跑什么服务", "哪些端口被用了"]\n'
        'title: "Services Inventory"\n'
        "---\n"
    )

    body = f"""# Services Inventory

> ⚠️ **本页由 `tools/tm_compile_systemd_inventory.py` 自动生成**。手动修改会在下次 compile 时被覆盖。修改服务实际配置请编辑 `deploy/mcp/*.service` / `*.timer`，然后跑 `python3 tools/tm_compile_systemd_inventory.py write`；daily-lint CI 也会兜底刷新。

## 摘要

tigermemory 在 WSL2（主开发机）与 VPS（公网入口）上长驻的所有 systemd service 与 timer 清单，从 `deploy/mcp/*.service` / `*.timer` 自动抽取。陌生 agent 通过 `get_agent_onboarding` 即可在 5min 快照里看到本页 "生产服务清单" 节，避免类似 2026-05-23 "dashboard 不存在" 类误判。

## 生产服务清单

下表是 `Type=simple` 的长驻服务（systemd 一直在跑）。端口从 ExecStart 的 `--port N` flag 抽取，入口代码是 `.py` / `.sh` 文件相对仓库根的路径。

{services_table}{oneshot_block}

## 周期任务（systemd timers）

下表是 `*.timer` unit 与其关联 service 的触发节奏。

{timers_table}

## 端口快查

{port_table}

## 编译规则

- **Source**: `deploy/mcp/*.service` 与 `deploy/mcp/*.timer`（systemd unit 文件，git 跟踪）。
- **抽取规则**: 从 `Description=` 取用途；从 `ExecStart=` 解析入口路径与 `--port N`；从 `OnBootSec=` / `OnUnitActiveSec=` / `OnUnitInactiveSec=` 取 timer 节奏；从 `Environment=MEM0_URL=...` 抽取外部依赖 OpenMemory 行。
- **CI**: `.github/workflows/daily-lint.yml` 每天 22:00 SH 调 `python3 tools/tm_compile_systemd_inventory.py write`，drift 会以 `[linter] compile:` commit 推回。
- **本地刷新**: 改 unit 文件后跑 `python3 tools/tm_compile_systemd_inventory.py write`，或 `... check` 看是否 drift。

## 来源

- 编译器: `tools/tm_compile_systemd_inventory.py`
- Unit 源: `deploy/mcp/*.service` / `deploy/mcp/*.timer`
- 相关 lesson: [2026-05-23-cascade-single-dir-non-existence-claim](../self-evolution/lessons/2026-05-23-cascade-single-dir-non-existence-claim.md)
- Onboarding 入口: `tools/tm_persona.py` (`compile_snapshot --depth 5min` 嵌入本页 "生产服务清单" 节)
- CI 工作流: `.github/workflows/daily-lint.yml`
"""
    return frontmatter + body


# ---------- CLI ----------


def compile_page() -> tuple[str, str]:
    """Return (new_text, old_text) for OUTPUT_PAGE."""
    today_iso = datetime.datetime.now(TZ_CN).strftime("%Y-%m-%d")
    new_text = render_page(today_iso)
    old_text = OUTPUT_PAGE.read_text(encoding="utf-8") if OUTPUT_PAGE.exists() else ""
    return new_text, old_text


def _diff(old: str, new: str, label: str) -> Iterable[str]:
    return difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{label}",
        tofile=f"b/{label}",
    )


def _strip_updated(text: str) -> str:
    """Strip the frontmatter `updated:` line so date drift doesn't trigger
    diff. The page is auto-regenerated daily; `updated:` should only change
    when content actually changes, not just because today's date moved on."""
    return re.sub(r"^updated:.*$\n?", "", text, count=1, flags=re.MULTILINE)


def cmd_check(_args: argparse.Namespace) -> int:
    new, old = compile_page()
    if _strip_updated(new) != _strip_updated(old):
        print("DIFF wiki/systems/services-inventory.md", file=sys.stderr)
        sys.stderr.writelines(_diff(old, new, "wiki/systems/services-inventory.md"))
        return 1
    return 0


def cmd_diff(_args: argparse.Namespace) -> int:
    new, old = compile_page()
    if _strip_updated(new) != _strip_updated(old):
        sys.stdout.writelines(_diff(old, new, "wiki/systems/services-inventory.md"))
    return 0


def cmd_write(_args: argparse.Namespace) -> int:
    new, old = compile_page()
    if _strip_updated(new) == _strip_updated(old):
        print("NO CHANGES")
        return 0
    OUTPUT_PAGE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PAGE.write_text(new, encoding="utf-8")
    print(f"WROTE: {OUTPUT_PAGE.relative_to(REPO_ROOT)}")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(prog="tm_compile_systemd_inventory.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("check", cmd_check), ("diff", cmd_diff), ("write", cmd_write)):
        sp = sub.add_parser(name)
        sp.set_defaults(func=fn)
    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
