from __future__ import annotations

import inspect
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import tm_http  # type: ignore[import-not-found]
import tm_mcp  # type: ignore[import-not-found]
import tm_mcp_openai  # type: ignore[import-not-found]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


search_sig = inspect.signature(tm_mcp.search_tigermemory)
answer_sig = inspect.signature(tm_mcp.memory_answer)
openai_sig = inspect.signature(tm_mcp_openai._memory_answer_via_core)

require("follow_backlinks" in search_sig.parameters, "tm_mcp.search_tigermemory missing follow_backlinks")
require("expand_partition" in search_sig.parameters, "tm_mcp.search_tigermemory missing expand_partition")
require("task_context" in answer_sig.parameters, "tm_mcp.memory_answer missing task_context")
require("task_context" in openai_sig.parameters, "tm_mcp_openai._memory_answer_via_core missing task_context")
require("task_context" in tm_http.MemoryAnswerRequest.model_fields, "MemoryAnswerRequest missing task_context")

print("mcp/http signature checks OK")
