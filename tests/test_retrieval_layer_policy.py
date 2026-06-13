from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "wiki" / "systems" / "memory-retrieval-layer-policy.md"


def test_retrieval_layer_policy_exists_and_names_all_experimental_switches():
    text = POLICY.read_text(encoding="utf-8")

    for env_name, default_value in {
        "TM_EMBED_SUMMARY_WEIGHT": "0",
        "TM_HYBRID_MAP_ARM": "0",
        "TM_ANSWER_WIKI_MAP_BRIDGE": "0",
        "TM_ANSWER_WIKI_MAP": "0",
    }.items():
        assert env_name in text
        assert f"{env_name}={default_value}" in text

    assert "默认关闭" in text
    assert "opt-in" in text
    assert "25 问" in text
    assert "P3.10 holdout" in text


def test_retrieval_layer_policy_keeps_public_and_offline_paths_basic():
    text = POLICY.read_text(encoding="utf-8")

    assert "tm ask --offline" in text
    assert "不调用 Mem0" in text
    assert "不调用在线模型" in text
    assert "证据片段" in text
