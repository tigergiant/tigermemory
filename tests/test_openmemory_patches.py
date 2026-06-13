from __future__ import annotations

import pathlib
import re


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PATCH_FILES = [
    REPO_ROOT / "deploy/openmemory/patches/app/routers/memories.py",
    REPO_ROOT / "runtime/openmemory/patches/app/routers/memories.py",
]
DEPLOY_CATEGORIZATION_PATCH = REPO_ROOT / "deploy/openmemory/patches/app/utils/categorization.py"
RUNTIME_CATEGORIZATION_PATCH = REPO_ROOT / "runtime/openmemory/patches/app/utils/categorization.py"
CATEGORY_PATCH_FILES = [
    DEPLOY_CATEGORIZATION_PATCH,
    *([RUNTIME_CATEGORIZATION_PATCH] if RUNTIME_CATEGORIZATION_PATCH.exists() else []),
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


def test_categories_patch_does_not_load_all_memories():
    text = (REPO_ROOT / "deploy/openmemory/patches/app/routers/memories.py").read_text(encoding="utf-8")

    assert ".join(Category.memories)" in text
    assert ".distinct()" in text
    assert "db.query(Memory).filter(Memory.user_id == user.id" not in text
    assert "for memory in memories for category in memory.categories" not in text


def test_categorization_patch_defaults_to_nonblocking_empty_categories():
    for path in CATEGORY_PATCH_FILES:
        text = path.read_text(encoding="utf-8")
        assert "OPENMEMORY_ENABLE_AUTO_CATEGORIZATION" in text
        assert 'os.getenv("OPENMEMORY_ENABLE_AUTO_CATEGORIZATION", "false")' in text
        assert "return []" in text

        body = re.search(r"def get_categories_for_memory\(memory: str\) -> List\[str\]:[\s\S]+$", text)
        assert body is not None
        assert body.group(0).index("_auto_categorization_enabled()") < body.group(0).index("openai_client.chat.completions.create")


def test_runtime_categorization_patch_matches_deploy_copy():
    if not RUNTIME_CATEGORIZATION_PATCH.exists():
        return

    deploy_text = DEPLOY_CATEGORIZATION_PATCH.read_text(encoding="utf-8")
    runtime_text = RUNTIME_CATEGORIZATION_PATCH.read_text(encoding="utf-8")

    assert runtime_text == deploy_text


def test_memory_client_config_session_closes_on_exception_path():
    text = (REPO_ROOT / "deploy/openmemory/patches/app/utils/memory.py").read_text(encoding="utf-8")
    load_block = text.split("# tigermemory patch: env-var override for embedder", 1)[0]

    assert "db = SessionLocal()" in load_block
    assert "finally:\n                db.close()" in load_block
