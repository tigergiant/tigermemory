#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/tm_eval_runner.py — tigermemory 检索召回与性能简易评测工具。
专为零基础和初学开发设计，包含极其详细的中文注释和控制台中文指标科普。
评估本地 Wiki 文件全文检索与远程 Mem0 检索的时延与召回精度（Recall@K）。

使用方法:
    py tools/tm_eval_runner.py                  # 运行检索评测
    py tools/tm_eval_runner.py --suite custom   # 运行自定义评测集
"""

import os
import sys
import json
import time
from pathlib import Path

# 获取当前仓库根目录路径
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO_ROOT / "tools"))

# 导入 tigermemory 核心模块
try:
    import tm_core
except ImportError:
    print("❌ 错误：无法加载 tools/tm_core.py。请确保在仓库根目录下运行本脚本。")
    sys.exit(1)


def _configure_stdio() -> None:
    """避免在 Windows 控制台环境下输出中文字符时发生 GBK 编码崩溃"""
    if sys.version_info >= (3, 7):
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                try:
                    stream.reconfigure(errors="backslashreplace")
                except Exception:
                    pass


def load_or_create_eval_suite() -> list[dict]:
    """
    加载或自动生成演示评测数据集。
    数据集保存在 data/eval_suites.json，作为官方的开箱即用演示。
    """
    data_dir = REPO_ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    suite_file = data_dir / "eval_suites.json"

    # 如果数据集不存在，我们自动生成一个结构清晰、内容饱满的默认评测集
    if not suite_file.exists():
        default_suite = [
            {
                "id": 1,
                "query": "mojibake gbk powershell",
                "expected_path": "wiki/self-evolution/lessons/2026-04-23-powershell-gbk-mojibake.md",
                "expected_terms": ["mojibake", "gbk"],
                "description": "乱码通道编码拦截测试"
            },
            {
                "id": 2,
                "query": "stash mojibake check",
                "expected_path": "wiki/systems/incident-2026-04-23-stash-mojibake-mcp.md",
                "expected_terms": ["mojibake", "mcp"],
                "description": "2026-04-23事故与乱码测试"
            },
            {
                "id": 3,
                "query": "daily health debt",
                "expected_path": "wiki/operations/daily-health-known-debt.md",
                "expected_terms": ["health", "debt"],
                "description": "日常巡检已知债务清单"
            },
            {
                "id": 4,
                "query": "session protocol check",
                "expected_path": "wiki/systems/session-protocol.md",
                "expected_terms": ["session", "protocol"],
                "description": "工作区纪律会话协议"
            },
            {
                "id": 5,
                "query": "bypass commit msg pre-commit",
                "expected_path": "wiki/self-evolution/lessons/2026-04-22-no-verify-bypass.md",
                "expected_terms": ["bypass", "commit"],
                "description": "Git绕过风险与Hook拦截"
            }
        ]
        try:
            # 自动美化写入 JSON 文件
            suite_file.write_text(json.dumps(default_suite, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"📦 【初始化】自动在本地生成了默认评测数据集：\n   👉 {suite_file}\n")
        except Exception as e:
            print(f"⚠️ 警告：自动创建默认评测集文件失败，将使用内存临时数据。原因：{e}")
            return default_suite

    # 从硬盘加载评测集
    try:
        data = json.loads(suite_file.read_text(encoding="utf-8"))
        return data
    except Exception as e:
        print(f"❌ 错误：无法读取评测数据集文件，原因：{e}")
        return []


def run_wiki_eval(case: dict) -> tuple[int, float]:
    """
    对本地 Wiki Markdown 全文检索进行评估。
    返回: (命中排名 Rank, 检索用时毫秒数)
    Rank 说明：1 表示排第1命中，3 表示前3命中，5 表示前5命中，-1 表示未命中。
    """
    query = case["query"]
    expected = case["expected_path"].replace("\\", "/").lower()

    start_time = time.perf_counter()
    try:
        # 扫描本地 Wiki 目录，这里调用核心 search_wiki 方法
        # 默认只扫 wiki/ 和 sources/，不包含未审核的 inbox
        results = tm_core.search_wiki(query, size=5, include_sources=True, include_inbox=False)
    except Exception as e:
        print(f"⚠️ 警告：Wiki 检索执行失败 (Query: {query})，原因：{e}")
        return -1, 0.0
    duration_ms = (time.perf_counter() - start_time) * 1000.0

    # 判断预期文件在结果列表中的索引位置（排在第几名被召回）
    rank = -1
    for index, item in enumerate(results):
        item_path = item.get("path", "").replace("\\", "/").lower()
        if expected in item_path or item_path in expected:
            rank = index + 1
            break

    return rank, duration_ms


def run_mem0_eval(case: dict) -> tuple[bool, float]:
    """
    对 Mem0 云记忆接口检索进行评估。
    返回: (是否匹配关键词, 检索用时毫秒数)
    """
    query = case["query"]
    expected_terms = case.get("expected_terms", [])

    start_time = time.perf_counter()
    try:
        # 调用核心 search_memories 接口
        raw_resp = tm_core.mem0_search(query, size=5)
        resp_data = json.loads(raw_resp)
        results = resp_data.get("items") or resp_data.get("results") or []
    except Exception:
        # 如果 Mem0 服务未开启，我们友好地返回 False 和耗时 0
        return False, 0.0
    duration_ms = (time.perf_counter() - start_time) * 1000.0

    # 简单分析：返回的记忆内容文本里，是否包含我们预期的任意一个关键字
    matched = False
    for item in results:
        text = str(item.get("text") or "").lower()
        if any(term.lower() in text for term in expected_terms):
            matched = True
            break

    return matched, duration_ms


def print_scientific_explanations():
    """用通俗的语言向初学者解释评测指标的意义"""
    print("\n" + "=" * 60)
    print("        📊 检索指标通俗大讲堂 (新手专属科普) 📊        ")
    print("=" * 60)
    print("💡 1. 什么是 Recall@K (K 召回率)？")
    print("   👉 意思是：把搜索结果的前 K 个答案拿出来，这里面【包含正确答案】的概率。")
    print("   - Recall@1: 代表第一眼就相中正确答案的准确度（最苛刻，也最重要！）。")
    print("   - Recall@3: 允许答案排在第1到第3名，容错率稍高。")
    print("   - Recall@5: 允许排在前5名。如果 Recall@5 都是 0%，说明搜索彻底失灵。")
    print("\n💡 2. 什么是 Latency (检索时延)？")
    print("   👉 代表 AI 搜索一次需要消耗的时间，单位是毫秒 (ms)。")
    print("   - 100 毫秒以内：如闪电般快速，用户几乎无感；")
    print("   - 100 - 500 毫秒：合格的响应，体验平滑；")
    print("   - 500 毫秒以上：略显迟钝，网络或者模型可能在高负荷运转。")
    print("=" * 60 + "\n")


def print_suggestions(wiki_recall_1, wiki_latency, mem0_active):
    """根据测试结果给开发者提供针对性的 Obsidian 或数据治理优化建议"""
    print("💡 【数据治理与调优建议】：")
    if wiki_recall_1 < 0.6:
        print("   📌 【Wiki 优化建议】：当前 Recall@1 第一精确命中率较低。")
        print("     👉 原因：Wiki Markdown 文件名和别名不够丰富。")
        print("     👉 解决：建议在 Markdown 正文顶部 frontmatter 增加 `aliases: [别名1, 别名2]` 字段，并确保文件首级 H1 标题包含核心关键词！")
    else:
        print("   📌 【Wiki 表现优秀】：第一精确命中率表现良好，继续保持高质量的 Markdown 知识归档！")

    if wiki_latency > 200.0:
        print("   📌 【时延预警】：本地 Wiki 检索平均耗时较长。")
        print("     👉 解决：清理 .tmp/ 隔离区中冗余的大型文本残余，或者减少一次性全文搜索文件的大小预算。")

    if not mem0_active:
        print("   📌 【Mem0 提醒】：检测到 Mem0 记忆引擎处于离线或异常状态。")
        print("     👉 说明：当前主要依靠本地 Wiki FTS 静态索引提供查询支持。")
        print("     👉 解决：在后台开启 openmemory 服务，能够获得动态、实时的对话对话级记忆能力。")
    else:
        print("   📌 【Mem0 表现正常】：Mem0 动态事件记忆已接入，双路检索系统状态良好！")
    print("-" * 60)


def main():
    _configure_stdio()

    print("=" * 60)
    print("      🐅 tigermemory 检索与时延双路评测器 (Eval Runner) 🐅      ")
    print("=" * 60)

    # 1. 载入评测样本集
    cases = load_or_create_eval_suite()
    if not cases:
        print("❌ 错误：评测样本集为空，无法继续。")
        return

    print(f"📊 成功载入 {len(cases)} 个评测样本，正在对 Wiki FTS 和 Mem0 双通道执行评测...\n")

    # 记录评测结果数据
    wiki_ranks = []
    wiki_durations = []
    mem0_matches = []
    mem0_durations = []
    mem0_active = True

    # 漂亮的控制台 ASCII 表格头
    print(f"{'ID':<4} | {'测试问题描述':<22} | {'Wiki召回排名':<12} | {'Wiki时延':<10} | {'Mem0匹配':<8} | {'Mem0时延':<10}")
    print("-" * 84)

    for case in cases:
        cid = case["id"]
        desc = case["description"]
        
        # 运行本地 Wiki FTS 全文搜索测试
        rank, wiki_ms = run_wiki_eval(case)
        wiki_ranks.append(rank)
        wiki_durations.append(wiki_ms)

        # 运行 Mem0 语义/局部测试
        matched, mem0_ms = run_mem0_eval(case)
        if mem0_ms == 0.0:
            mem0_active = False
        mem0_matches.append(matched)
        mem0_durations.append(mem0_ms)

        # 格式化打印每一行的数据
        rank_str = f"Rank {rank}" if rank > 0 else "Not Found"
        wiki_time_str = f"{wiki_ms:.1f}ms"
        mem0_match_str = "SUCCESS" if matched else "FAILED"
        if not mem0_active:
            mem0_match_str = "OFFLINE"
            mem0_time_str = "--"
        else:
            mem0_time_str = f"{mem0_ms:.1f}ms"

        # 针对中文对齐的特殊处理，保持表格美观
        padding_len = 22 - len(desc.encode('utf-8')) + len(desc)
        desc_padded = desc + " " * max(0, padding_len)

        print(f"{cid:<4} | {desc_padded} | {rank_str:<12} | {wiki_time_str:<10} | {mem0_match_str:<8} | {mem0_time_str:<10}")

    print("-" * 84)

    # 2. 计算并汇总核心评估指标
    total = len(cases)
    
    # Wiki Recall 计算
    recall_1 = sum(1 for r in wiki_ranks if r == 1) / total
    recall_3 = sum(1 for r in wiki_ranks if 0 < r <= 3) / total
    recall_5 = sum(1 for r in wiki_ranks if 0 < r <= 5) / total
    avg_wiki_latency = sum(wiki_durations) / total

    # Mem0 Recall 计算
    mem0_accuracy = sum(1 for m in mem0_matches if m) / total if mem0_active else 0.0
    avg_mem0_latency = sum(mem0_durations) / total if mem0_active else 0.0

    # 打印最终统计看板
    print("\n🏆 【最终测试统计看板 - Summary Dashboard】 🏆")
    print("=" * 60)
    print(f"📖 【本地 Wiki 静态长文检索】：")
    print(f"   👉 🎯 Recall@1 (精确定位率) : {recall_1 * 100:.1f}%")
    print(f"   👉 🎯 Recall@3 (前3召回率)  : {recall_3 * 100:.1f}%")
    print(f"   👉 🎯 Recall@5 (前5召回率)  : {recall_5 * 100:.1f}%")
    print(f"   👉 ⚡ Average Latency (时延) : {avg_wiki_latency:.1f} 毫秒")
    print("-" * 60)
    print(f"🧠 【Mem0 动态事件记忆层】：")
    if mem0_active:
        print(f"   👉 🎯 Keyword Recall (词命中率) : {mem0_accuracy * 100:.1f}%")
        print(f"   👉 ⚡ Average Latency (时延)    : {avg_mem0_latency:.1f} 毫秒")
    else:
        print("   👉 🔴 状态 : 处于 OFFLINE 离线状态（已自动静默降级）")
    print("=" * 60 + "\n")

    # 3. 打印科普建议与治理指南
    print_suggestions(recall_1, avg_wiki_latency, mem0_active)
    print_scientific_explanations()


if __name__ == "__main__":
    main()
