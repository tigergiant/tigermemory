"""Regression tests for tools/_bootstrap_paths.py — the single source of
truth for ``packages/*/src`` sys.path injection.

These tests lock in the fix for the 2026-05-25 ``tigermemory-mcp:
handshaking with MCP server failed: connection closed: initialize
response`` regression where codex extracted new ``packages/<name>/``
subdirectories but did not update the seven scattered ``PYTHONPATH``
settings in launcher scripts and systemd units.

After this fix:
- Any newly added ``packages/<name>/src`` is auto-discovered;
- Launcher scripts and systemd units carry **no** hardcoded
  ``packages/<name>/src`` strings;
- Each entry-point ``tools/X.py`` imports ``_bootstrap_paths`` before
  it imports any ``tigermemory_*`` or ``tigerledger`` package.
"""
from __future__ import annotations

import importlib
import pathlib
import re
import sys

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
PACKAGES_DIR = REPO_ROOT / "packages"
ENTRY_POINTS = (
    "tm_mcp.py",
    "tm_http.py",
    "tm_mcp_openai.py",
    "tm_review_ui.py",
)
LAUNCHER_SHELL_SCRIPTS = (
    "deploy/mcp/tm_mcp_auto_update.sh",
    "deploy/mcp/tm_openai_mcp_auto_update.sh",
    "deploy/mcp/tm_openai_mcp_vps_start.sh",
)
LAUNCHER_SYSTEMD_UNITS = (
    "deploy/mcp/tm-mcp.service",
    "deploy/mcp/tm-http.service",
    "deploy/mcp/tm-dashboard.service",
    "deploy/mcp/tm-openai-mcp.service",
)
PACKAGE_PATH_PATTERN = re.compile(
    r"packages/[A-Za-z0-9_\-]+/src",
)


@pytest.fixture
def fresh_bootstrap_import():
    """Reload _bootstrap_paths from a clean state, then restore sys.path/modules."""
    snapshot_path = list(sys.path)
    snapshot_modules = {k: v for k, v in sys.modules.items() if k == "_bootstrap_paths"}

    if str(TOOLS_DIR) not in sys.path:
        sys.path.insert(0, str(TOOLS_DIR))
    sys.modules.pop("_bootstrap_paths", None)
    module = importlib.import_module("_bootstrap_paths")

    try:
        yield module
    finally:
        sys.path[:] = snapshot_path
        sys.modules.pop("_bootstrap_paths", None)
        for name, mod in snapshot_modules.items():
            sys.modules[name] = mod


def test_bootstrap_module_exists():
    assert (TOOLS_DIR / "_bootstrap_paths.py").is_file(), (
        "tools/_bootstrap_paths.py must exist as the single source of truth "
        "for packages/*/src sys.path injection"
    )


def test_bootstrap_paths_scans_every_packages_src(fresh_bootstrap_import):
    """After import, sys.path must include every existing packages/<name>/src."""
    expected = sorted(
        str(pkg / "src")
        for pkg in PACKAGES_DIR.iterdir()
        if pkg.is_dir() and (pkg / "src").is_dir()
    )
    assert expected, (
        f"Expected at least one packages/*/src under {PACKAGES_DIR}; "
        "none found. The bootstrap test cannot validate against an empty set."
    )
    missing = [path for path in expected if path not in sys.path]
    assert not missing, (
        f"_bootstrap_paths failed to inject these packages/*/src paths: {missing}"
    )


def test_bootstrap_idempotent(fresh_bootstrap_import):
    """Calling _bootstrap() twice must not produce duplicate sys.path entries."""
    fresh_bootstrap_import._bootstrap()
    fresh_bootstrap_import._bootstrap()
    counts = {p: sys.path.count(p) for p in sys.path if "packages" in p and "src" in p}
    duplicates = {p: c for p, c in counts.items() if c > 1}
    assert not duplicates, f"Duplicate sys.path entries after re-bootstrap: {duplicates}"


@pytest.mark.parametrize("entry_point", ENTRY_POINTS)
def test_entry_point_imports_bootstrap_before_packages(entry_point):
    """Every entry-point (launched as `python tools/X.py`) must import
    _bootstrap_paths before any tigermemory_* or tigerledger import."""
    source = (TOOLS_DIR / entry_point).read_text(encoding="utf-8")

    bootstrap_match = re.search(r"^import _bootstrap_paths\b", source, re.MULTILINE)
    assert bootstrap_match, (
        f"{entry_point}: missing top-level `import _bootstrap_paths` line. "
        f"Add it after `from __future__` and stdlib imports, before any "
        f"`import tigermemory_*` or `import tigerledger`."
    )

    pkg_import_match = re.search(
        r"^(?:import|from)\s+(?:tigermemory_[a-z_]+|tigerledger)\b",
        source,
        re.MULTILINE,
    )
    if pkg_import_match:
        assert bootstrap_match.start() < pkg_import_match.start(), (
            f"{entry_point}: `import _bootstrap_paths` must appear before the first "
            f"`import tigermemory_*` / `import tigerledger`. Current order has "
            f"bootstrap at offset {bootstrap_match.start()} and first package import "
            f"at offset {pkg_import_match.start()}."
        )


@pytest.mark.parametrize(
    "launcher", LAUNCHER_SHELL_SCRIPTS + LAUNCHER_SYSTEMD_UNITS
)
def test_launcher_has_no_hardcoded_packages_src_pythonpath(launcher):
    """No launcher / systemd unit may hardcode a packages/<name>/src path.

    Sys.path injection is now centralized in tools/_bootstrap_paths.py.
    Any reappearance of a hardcoded ``packages/.../src`` in a launcher is
    a regression that brings back the lockstep-update bug.
    """
    path = REPO_ROOT / launcher
    if not path.is_file():
        pytest.skip(f"{launcher} not present in this checkout")
    text = path.read_text(encoding="utf-8")
    matches = PACKAGE_PATH_PATTERN.findall(text)
    assert not matches, (
        f"{launcher}: hardcoded packages/*/src path(s) found: {matches}. "
        f"Remove the hardcoded PYTHONPATH; entry-points handle sys.path via "
        f"`import _bootstrap_paths`."
    )
