#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/tm_eval_runner.py — tigermemory 检索召回与性能简易评测工具。
专为零基础和初学开发设计，包含极其详细的中文注释和控制台中文指标科普。
评估本地 Wiki 文件全文检索与远程 Mem0 检索的时延与召回精度（Recall@K）。

使用方法:
    py tools/tm_eval_runner.py                  # 运行检索评测
    py tools/tm_eval_runner.py --suite custom   # 运行自定义评测集
Inputs: CLI args, fixture cases, trace JSONL, wiki/Mem0 data, or local index files as selected by the command.
Outputs: Search/eval/trace/index reports printed to stdout or written to the requested output path.
Depends-on (must-have): tm_core search/memory helpers, local Markdown/JSONL files, and optional configured LLM or embedding providers.
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


def _visual_width(s: str) -> int:
    """估算字符串在等宽字体下的视觉占用宽度。CJK 占 2 cell，其余占 1。"""
    width = 0
    for ch in s:
        code = ord(ch)
        # CJK Unified Ideographs, Hiragana, Katakana, Hangul, Full-width punctuation
        if (
            0x1100 <= code <= 0x115F      # Hangul Jamo
            or 0x2E80 <= code <= 0x9FFF   # CJK
            or 0xA000 <= code <= 0xA4CF   # Yi syllables
            or 0xAC00 <= code <= 0xD7A3   # Hangul syllables
            or 0xF900 <= code <= 0xFAFF   # CJK compat
            or 0xFE30 <= code <= 0xFE4F   # CJK compat forms
            or 0xFF00 <= code <= 0xFF60   # Full-width forms (part)
            or 0xFFE0 <= code <= 0xFFE6   # Full-width signs
        ):
            width += 2
        else:
            width += 1
    return width

def _pad_visual(s: str, target: int) -> str:
    return s + " " * max(0, target - _visual_width(s))


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

_configure_stdio()


def load_or_create_eval_suite(suite_type: str = "default") -> list[dict]:
    """
    加载或自动生成演示评测数据集。
    支持 default 与 custom 模式。
    """
    data_dir = REPO_ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    if suite_type == "custom":
        suite_file = data_dir / "eval_suites_custom.json"
        if not suite_file.exists():
            print("❌ 错误：未找到自定义评测集文件！")
            print(f"   👉 建议：请在以下路径创建您的自定义评测 JSON 文件：\n      {suite_file}")
            print("   👉 文件格式示例：")
            demo_format = [
                {
                    "id": 1,
                    "query": "自定义搜索词",
                    "expected_path": "wiki/partition/your-file.md",
                    "expected_terms": ["核心词1", "核心词2"],
                    "description": "自定义用例描述"
                }
            ]
            print(json.dumps(demo_format, ensure_ascii=False, indent=2))
            return []
    else:
        suite_file = data_dir / "eval_suites.json"

    # 如果默认数据集不存在，我们自动生成一个结构清晰、内容饱满的默认评测集
    if suite_type != "custom" and not suite_file.exists():
        # 智能动态检测并挑取 3 个新分区的真实用例文件作为 demo
        brand_path = "wiki/brand/ipfb-copywriting-skill.md"
        if not (REPO_ROOT / brand_path).exists():
            brand_dir = REPO_ROOT / "wiki" / "brand"
            if brand_dir.exists():
                files = [f for f in os.listdir(brand_dir) if f.endswith(".md") and f != "index.md"]
                if files:
                    brand_path = f"wiki/brand/{files[0]}"

        ops_path = "wiki/operations/cron-daily-report.md"
        if not (REPO_ROOT / ops_path).exists():
            ops_dir = REPO_ROOT / "wiki" / "operations"
            if ops_dir.exists():
                files = [f for f in os.listdir(ops_dir) if f.endswith(".md") and f != "index.md"]
                if files:
                    ops_path = f"wiki/operations/{files[0]}"

        sys_path = "wiki/systems/mem0-audit.md"
        if not (REPO_ROOT / sys_path).exists():
            sys_dir = REPO_ROOT / "wiki" / "systems"
            if sys_dir.exists():
                files = [f for f in os.listdir(sys_dir) if f.endswith(".md") and f != "index.md" and not f.startswith("lessons")]
                if files:
                    sys_path = f"wiki/systems/{files[0]}"

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
            },
            {
                "id": 6,
                "query": "IPFB 文案 准则",
                "expected_path": brand_path,
                "expected_terms": ["ipfb", "文案", "准则"],
                "description": "IPFB品牌文案撰写规范"
            },
            {
                "id": 7,
                "query": "cron 日报 流程",
                "expected_path": ops_path,
                "expected_terms": ["cron", "日报", "流程"],
                "description": "定时任务日报归档流程"
            },
            {
                "id": 8,
                "query": "Mem0 重复 候选",
                "expected_path": sys_path,
                "expected_terms": ["mem0", "重复", "候选"],
                "description": "Mem0记忆重复实体审核"
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


def run_wiki_eval(case: dict, mode: str = "lexical") -> tuple[int, float, bool]:
    """
    对本地 Wiki Markdown 全文检索进行评估。
    mode 支持: "lexical" 或 "hybrid"
    返回: (命中排名 Rank, 检索用时毫秒数, 是否降级 degraded)
    Rank 说明：1 表示排第1命中，3 表示前3命中，5 表示前5命中，-1 表示未命中。
    degraded 说明：在 hybrid 模式下，如果 embedding index 或服务不可用导致降级，则为 True；lexical 模式下固定为 False。
    """
    query = case["query"]
    expected = case["expected_path"].replace("\\", "/").lower()

    start_time = time.perf_counter()
    degraded = False
    try:
        if mode == "hybrid":
            # 扫描本地 Wiki 目录，调用 search_wiki_hybrid 方法
            results = tm_core.search_wiki_hybrid(query, size=5, include_sources=True, include_inbox=False, explain=True)
            # 检查是否有 degraded 为 True 的命中项
            if results:
                for item in results:
                    breakdown = item.get("score_breakdown")
                    if isinstance(breakdown, dict) and breakdown.get("degraded") is True:
                        degraded = True
                        break
            else:
                # 结果为空时辅助检查是否退化
                try:
                    import tm_embed_index
                except Exception:
                    degraded = True
        else:
            # 默认调用词法 search_wiki 方法
            results = tm_core.search_wiki(query, size=5, include_sources=True, include_inbox=False)
    except Exception as e:
        print(f"⚠️ 警告：Wiki 检索执行失败 (Query: {query}, Mode: {mode})，原因：{e}")
        return -1, 0.0, (mode == "hybrid")
    duration_ms = (time.perf_counter() - start_time) * 1000.0

    # 判断预期文件在结果列表中的索引位置（排在第几名被召回）
    rank = -1
    for index, item in enumerate(results):
        item_path = item.get("path", "").replace("\\", "/").lower()
        if expected in item_path or item_path in expected:
            rank = index + 1
            break

    return rank, duration_ms, degraded


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
        # 兼容读取 content, memory, text 字段
        text = str(item.get("content") or item.get("memory") or item.get("text") or "").lower()
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


def print_suggestions(wiki_recall_1, wiki_latency, mem0_active, mem0_accuracy=0.0):
    """根据测试结果给开发者提供针对性的 Obsidian 或数据治理优化建议"""
    print("💡 【数据治理与调优建议】：")

    # Wiki 召回率精确评定
    if wiki_recall_1 < 0.6:
        print("   📌 【Wiki 优化建议】：当前 Recall@1 第一精确命中率较低 (低于 60%)。")
        print("     👉 原因：Wiki Markdown 文件名和别名不够丰富，或没有针对性覆盖。")
        print("     👉 解决：建议在 Markdown 正文顶部 frontmatter 增加 `aliases: [别名1, 别名2]` 字段，并确保文件首级 H1 标题包含核心关键词！")
    elif wiki_recall_1 < 0.8:
        print("   📌 【Wiki 优化建议】：当前 Recall@1 第一精确命中率属于中等水平 (60%~80%)。")
        print("     👉 解决：建议针对个别失败问题，在对应 Wiki 文件名、aliases 或开篇首段补充测试词的同义词。")
    else:
        print("   📌 【Wiki 表现优秀】：第一精确命中率极佳 (80%以上)，继续保持高质量的 Markdown 知识归档！")

    # Wiki 时延评价
    if wiki_latency > 200.0:
        print("   📌 【Wiki 时延预警】：本地 Wiki 检索平均耗时较长。")
        print("     👉 解决：清理 .tmp/ 隔离区中冗余的大型文本残余，或者减少一次性全文搜索文件的大小预算。")

    # Mem0 评价 (区分服务在线状态与检索有效性)
    if not mem0_active:
        print("   📌 【Mem0 提醒】：检测到 Mem0 记忆引擎处于离线或异常状态。")
        print("     👉 说明：当前主要依靠本地 Wiki FTS 静态索引提供查询支持。")
        print("     👉 解决：在后台开启 openmemory 服务，能够获得动态、实时的对话级记忆能力。")
    else:
        if mem0_accuracy == 0.0:
            print("   📌 【Mem0 检索异常预警】：Mem0 服务在线，但 Keyword 命中率为 0%！")
            print("     👉 原因：可能存在以下情况之一：")
            print("        1. 本地 Mem0/OpenMemory 中尚未注入该评测集对应的近期事实记忆；")
            print("        2. 检索结果中的内容提取字段 (content/memory/text) 发生漂移；")
            print("        3. 检索召回策略或阈值设置过高。")
            print("     👉 解决：建议先通过 'py tools/tm_agent_doctor.py' 诊断 Mem0 基础连通性，并使用 write_memory 注入对应测试事实。")
        elif mem0_accuracy < 0.6:
            print("   📌 【Mem0 表现中等】：Mem0 检索命中率较低 (低于 60%)。可通过添加更精确的事件描述来增强召回。")
        else:
            print("   📌 【Mem0 表现正常】：Mem0 动态事件记忆已接入，且 Keyword 召回表现良好，双路检索系统状态极佳！")
    print("-" * 60)


def main():
    _configure_stdio()

    import argparse
    parser = argparse.ArgumentParser(
        description="🐅 tigermemory 检索与时延双路评测器 (Eval Runner) 🐅"
    )
    parser.add_argument(
        "--suite",
        choices=["default", "custom"],
        default="default",
        help="选择评测样本集类型 (default 或 custom)"
    )
    parser.add_argument(
        "--skip-mem0",
        action="store_true",
        help="跳过 Mem0 动态事件通道评测 (强制为离线模式)"
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="跳过 Mem0 动态事件通道评测 (强制为离线模式，等价于 --skip-mem0)"
    )
    parser.add_argument(
        "--recall",
        choices=["lexical", "hybrid", "both"],
        default="lexical",
        help="选择 Wiki 检索召回模式 (lexical, hybrid, both)"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("      🐅 tigermemory 检索与时延双路评测器 (Eval Runner) 🐅      ")
    print("=" * 60)

    # 1. 载入评测样本集
    cases = load_or_create_eval_suite(args.suite)
    if not cases:
        print("❌ 错误：评测样本集为空，无法继续。")
        return

    print(f"📊 成功载入 {len(cases)} 个评测样本，正在对 Wiki FTS ({args.recall}) 和 Mem0 双通道执行评测...\n")

    # 记录评测结果数据
    wiki_lex_ranks = []
    wiki_lex_durations = []
    wiki_hyb_ranks = []
    wiki_hyb_durations = []
    hyb_degraded_flags = []

    mem0_matches = []
    mem0_durations = []
    mem0_active = not (args.skip_mem0 or args.offline)

    # 动态确定表格列
    is_both = args.recall == "both"
    is_hybrid = args.recall == "hybrid"
    is_lexical = args.recall == "lexical"

    header_id = _pad_visual("ID", 4)
    header_desc = _pad_visual("测试问题描述", 22)
    header_mem0_match = _pad_visual("Mem0匹配", 8)
    header_mem0_time = _pad_visual("Mem0时延", 10)

    if is_both:
        header_lex_rank = _pad_visual("WikiLex排名", 12)
        header_lex_time = _pad_visual("Lex时延", 10)
        header_hyb_rank = _pad_visual("WikiHyb排名", 12)
        header_hyb_time = _pad_visual("Hyb时延", 10)
        cols = [header_id, header_desc, header_lex_rank, header_lex_time, header_hyb_rank, header_hyb_time, header_mem0_match, header_mem0_time]
    else:
        rank_title = "WikiHyb排名" if is_hybrid else "Wiki召回排名"
        time_title = "Hyb时延" if is_hybrid else "Wiki时延"
        header_wiki_rank = _pad_visual(rank_title, 12)
        header_wiki_time = _pad_visual(time_title, 10)
        cols = [header_id, header_desc, header_wiki_rank, header_wiki_time, header_mem0_match, header_mem0_time]

    table_header_str = " | ".join(cols)
    print(table_header_str)
    print("-" * len(table_header_str))

    for case in cases:
        cid = case["id"]
        desc = case["description"]

        # 运行本地 Wiki FTS 全文搜索测试
        if is_both:
            lex_rank, lex_ms, _ = run_wiki_eval(case, mode="lexical")
            hyb_rank, hyb_ms, hyb_degraded = run_wiki_eval(case, mode="hybrid")
            wiki_lex_ranks.append(lex_rank)
            wiki_lex_durations.append(lex_ms)
            wiki_hyb_ranks.append(hyb_rank)
            wiki_hyb_durations.append(hyb_ms)
            hyb_degraded_flags.append(hyb_degraded)
        elif is_hybrid:
            hyb_rank, hyb_ms, hyb_degraded = run_wiki_eval(case, mode="hybrid")
            wiki_hyb_ranks.append(hyb_rank)
            wiki_hyb_durations.append(hyb_ms)
            hyb_degraded_flags.append(hyb_degraded)
        else:
            lex_rank, lex_ms, _ = run_wiki_eval(case, mode="lexical")
            wiki_lex_ranks.append(lex_rank)
            wiki_lex_durations.append(lex_ms)

        # 运行 Mem0 语义/局部测试
        if mem0_active:
            matched, mem0_ms = run_mem0_eval(case)
            if mem0_ms == 0.0:
                mem0_active = False
        else:
            matched, mem0_ms = False, 0.0
        mem0_matches.append(matched)
        mem0_durations.append(mem0_ms)

        # 格式化打印每一行的数据
        col_id = _pad_visual(str(cid), 4)
        col_desc = _pad_visual(desc, 22)

        mem0_match_str = "SUCCESS" if matched else "FAILED"
        if not mem0_active:
            mem0_match_str = "OFFLINE"
            mem0_time_str = "--"
        else:
            mem0_time_str = f"{mem0_ms:.1f}ms"
        col_mem0_match = _pad_visual(mem0_match_str, 8)
        col_mem0_time = _pad_visual(mem0_time_str, 10)

        if is_both:
            lex_rank_str = f"Rank {lex_rank}" if lex_rank > 0 else "Not Found"
            lex_time_str = f"{lex_ms:.1f}ms"
            hyb_rank_str = f"Rank {hyb_rank}" if hyb_rank > 0 else "Not Found"
            if hyb_degraded:
                hyb_rank_str += "*"
            hyb_time_str = f"{hyb_ms:.1f}ms"

            col_lex_rank = _pad_visual(lex_rank_str, 12)
            col_lex_time = _pad_visual(lex_time_str, 10)
            col_hyb_rank = _pad_visual(hyb_rank_str, 12)
            col_hyb_time = _pad_visual(hyb_time_str, 10)

            print(f"{col_id} | {col_desc} | {col_lex_rank} | {col_lex_time} | {col_hyb_rank} | {col_hyb_time} | {col_mem0_match} | {col_mem0_time}")
        else:
            current_rank = hyb_rank if is_hybrid else lex_rank
            current_ms = hyb_ms if is_hybrid else lex_ms
            rank_str = f"Rank {current_rank}" if current_rank > 0 else "Not Found"
            if is_hybrid and hyb_degraded:
                rank_str += "*"
            time_str = f"{current_ms:.1f}ms"

            col_wiki_rank = _pad_visual(rank_str, 12)
            col_wiki_time = _pad_visual(time_str, 10)

            print(f"{col_id} | {col_desc} | {col_wiki_rank} | {col_wiki_time} | {col_mem0_match} | {col_mem0_time}")

    print("-" * len(table_header_str))
    if is_hybrid or is_both:
        print("* 注：带有 * 号的 Rank 代表 Hybrid 检索在运行时由于索引缺失或服务不可用，已优雅降级为词法检索。")

    # 2. 计算并汇总核心评估指标
    total = len(cases)

    def calc_metrics(ranks, durations):
        r1 = sum(1 for r in ranks if r == 1) / total
        r3 = sum(1 for r in ranks if 0 < r <= 3) / total
        r5 = sum(1 for r in ranks if 0 < r <= 5) / total
        avg_lat = sum(durations) / total
        return r1, r3, r5, avg_lat

    # Mem0 Recall 计算
    mem0_accuracy = sum(1 for m in mem0_matches if m) / total if mem0_active else 0.0
    avg_mem0_latency = sum(mem0_durations) / total if mem0_active else 0.0

    # 打印最终统计看板
    print("\n🏆 【最终测试统计看板 - Summary Dashboard】 🏆")
    print("=" * 60)

    if is_both:
        r1_lex, r3_lex, r5_lex, lat_lex = calc_metrics(wiki_lex_ranks, wiki_lex_durations)
        r1_hyb, r3_hyb, r5_hyb, lat_hyb = calc_metrics(wiki_hyb_ranks, wiki_hyb_durations)
        degraded_count = sum(1 for d in hyb_degraded_flags if d)

        print(f"📖 【本地 Wiki 静态长文检索 - 双路对比】：")
        print(f"   📊 1. Lexical 词法召回：")
        print(f"      👉 🎯 Recall@1 (精确定位率) : {r1_lex * 100:.1f}%")
        print(f"      👉 🎯 Recall@3 (前3召回率)  : {r3_lex * 100:.1f}%")
        print(f"      👉 🎯 Recall@5 (前5召回率)  : {r5_lex * 100:.1f}%")
        print(f"      👉 ⚡ Average Latency (时延) : {lat_lex:.1f} 毫秒")
        print(f"   📊 2. Hybrid 混合召回：")
        print(f"      👉 🎯 Recall@1 (精确定位率) : {r1_hyb * 100:.1f}%")
        print(f"      👉 🎯 Recall@3 (前3召回率)  : {r3_hyb * 100:.1f}%")
        print(f"      👉 🎯 Recall@5 (前5召回率)  : {r5_hyb * 100:.1f}%")
        print(f"      👉 ⚡ Average Latency (时延) : {lat_hyb:.1f} 毫秒")
        print(f"      👉 ⚠️ Degraded count (降级数) : {degraded_count} (共 {total} 个用例)")

        target_recall_1 = r1_hyb
        target_latency = lat_hyb
    else:
        if is_hybrid:
            r1, r3, r5, lat = calc_metrics(wiki_hyb_ranks, wiki_hyb_durations)
            degraded_count = sum(1 for d in hyb_degraded_flags if d)
            mode_name = "Hybrid 混合召回"
        else:
            r1, r3, r5, lat = calc_metrics(wiki_lex_ranks, wiki_lex_durations)
            mode_name = "Lexical 词法召回"

        print(f"📖 【本地 Wiki 静态长文检索 - {mode_name}】：")
        print(f"   👉 🎯 Recall@1 (精确定位率) : {r1 * 100:.1f}%")
        print(f"   👉 🎯 Recall@3 (前3召回率)  : {r3 * 100:.1f}%")
        print(f"   👉 🎯 Recall@5 (前5召回率)  : {r5 * 100:.1f}%")
        print(f"   👉 ⚡ Average Latency (时延) : {lat:.1f} 毫秒")
        if is_hybrid:
            print(f"   👉 ⚠️ Degraded count (降级数) : {degraded_count} (共 {total} 个用例)")

        target_recall_1 = r1
        target_latency = lat

    print("-" * 60)
    print(f"🧠 【Mem0 动态事件记忆层】：")
    if mem0_active:
        print(f"   👉 🎯 Keyword Recall (词命中率) : {mem0_accuracy * 100:.1f}%")
        print(f"   👉 ⚡ Average Latency (时延)    : {avg_mem0_latency:.1f} 毫秒")
    else:
        print("   👉 🔴 状态 : 处于 OFFLINE 离线状态（已自动静默降级）")
    print("=" * 60 + "\n")

    # 3. 打印科普建议与治理指南
    print_suggestions(target_recall_1, target_latency, mem0_active, mem0_accuracy)
    print_scientific_explanations()


if __name__ == "__main__":
    main()
