from __future__ import annotations

import pathlib
import re


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PATCH_FILES = [
    REPO_ROOT / "deploy/openmemory/patches/app/routers/memories.py",
    REPO_ROOT / "runtime/openmemory/patches/app/routers/memories.py",
]


def test_openmemory_memories_patch_preserves_tigermemory_write_contract():
    for path in PATCH_FILES:
        text = path.read_text(encoding="utf-8")

        assert "infer=request.infer" in text
        assert "**(request.metadata or {})" in text
        assert "def add_raw_memory" in text
        assert "if request.infer is False" in text
        assert "memory_client._create_memory" in text
        assert "def memory_response_payload" in text
        assert "return memory_response_payload(memory)" in text
        create_body = re.search(r"async def create_memory\([\s\S]+?# Get memory by ID", text)
        assert create_body is not None
        assert "return memory\n" not in create_body.group(0)
