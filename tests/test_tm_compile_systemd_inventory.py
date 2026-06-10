from __future__ import annotations

import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import tm_compile_systemd_inventory as inventory  # noqa: E402


def _service() -> inventory.Unit:
    return inventory.Unit(
        name="tm-http",
        kind="service",
        description="tigermemory HTTP REST server",
        exec_start="/home/giant/tigermemory/tools/tm_http.py --port 8790",
    )


def test_port_lookup_labels_9765_as_mem0_auth_gateway():
    table = inventory.render_port_lookup(
        [_service()],
        "http://127.0.0.1:9765",
    )

    assert "9765" in table
    assert "Mem0 auth gateway" in table
    assert "反代 OpenMemory 后端 :8765" in table
    assert "OpenMemory (Mem0) | Caddy" not in table


def test_port_lookup_keeps_8765_as_openmemory_backend():
    table = inventory.render_port_lookup(
        [_service()],
        "http://localhost:8765",
    )

    assert "8765" in table
    assert "OpenMemory (Mem0)" in table
    assert "Mem0 auth gateway" not in table
