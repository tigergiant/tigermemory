#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/tm_api.py — tigermemory 极简 Dashboard 零外部依赖 API 与静态服务器。
本脚本基于 Python 标准库 http.server 编写，专为中国零基础与入门级开发者设计。
包含极其丰富的中文注释，支持自动跨域（CORS）以及自动在默认浏览器中打开可视化仪表盘。

使用方法:
    py tools/tm_api.py
"""

import os
import sys
import json
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# 获取当前仓库根目录路径，确保在不同目录下运行均能正确定位
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO_ROOT / "tools"))

# 导入已经优化加固好的评测与连接核心逻辑
try:
    import tm_core
    import tm_eval_runner
    import tm_agent_connect
except ImportError as e:
    print(f"❌ 导入核心模块失败，请确保在 tigermemory 仓库根目录下运行本服务。原因：{e}")
    sys.exit(1)

# 配置端口号，避开已占用的 8765/9765 端口
PORT = 8766


class TigerMemoryAPIHandler(BaseHTTPRequestHandler):
    """
    tigermemory 专属轻量级 HTTP 请求处理器。
    不仅提供双路评测、IDE 连接的 JSON 数据 API，还顺带把 dashboard 目录下的静态网页文件一起伺服了！
    """

    def log_message(self, format, *args):
        """覆盖默认日志打印，使控制台输出更具极客感和呼吸感"""
        sys.stdout.write(f"🔔 [API Request] - [{self.log_date_time_string()}] {format % args}\n")

    def end_headers(self):
        """
        重写发送 HTTP 响应头结束的方法，统一注入 CORS 跨域安全头，
        防止在本地打开 file:/// 协议的 HTML 文件时出现跨域拦截问题。
        """
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        """支持预检请求，确保前后端跨域完全顺畅"""
        self.send_response(200, "OK")
        self.end_headers()

    def do_GET(self):
        """处理所有的 GET 请求，区分 API 路由与静态网页文件路由"""
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        # ----------------- 1. API 路由入口 -----------------
        if path == "/api/status":
            self._handle_api_status()
        elif path == "/api/eval":
            self._handle_api_eval(parsed_url.query)
        # ----------------- 2. 静态网页服务 -----------------
        else:
            self._handle_static_files(path)

    def do_POST(self):
        """处理所有的 POST 请求，如一键注入 IDE 连接配置"""
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path == "/api/connect":
            self._handle_api_connect()
        else:
            self.send_error_json(404, "接口不存在")

    # ======================================================
    # 具体业务接口处理逻辑
    # ======================================================

    def _handle_api_status(self):
        """获取当前系统 AI 编辑器（Claude Desktop & Cursor）的 MCP 配置状态"""
        try:
            # 检测系统中配置文件的路径
            paths = tm_agent_connect.detect_config_paths()
            
            status_data = {
                "system": {
                    "os": tm_agent_connect.OS_TYPE,
                    "wsl_detected": paths.get("wsl_detected", False)
                },
                "clients": {
                    "claude_desktop": self._get_client_config_status(paths.get("claude_desktop")),
                    "cursor": self._get_client_config_status(paths.get("cursor"))
                }
            }
            self.send_response_json(200, status_data)
        except Exception as e:
            self.send_error_json(500, f"获取状态失败：{e}")

    def _get_client_config_status(self, config_path):
        """辅助函数：检查单一客户端配置文件的存在性以及是否包含配置"""
        if not config_path:
            return {"detected": False, "path": "", "installed": False, "existing_config": None}
        
        path_obj = Path(config_path)
        detected = path_obj.exists()
        installed = False
        existing_config = None
        
        if detected:
            try:
                with open(path_obj, "r", encoding="utf-8") as f:
                    data = json.load(f)
                mcp_servers = data.get("mcpServers", {})
                if "tigermemory" in mcp_servers:
                    installed = True
                    existing_config = mcp_servers["tigermemory"]
            except Exception:
                pass
                
        return {
            "detected": True,
            "path": str(path_obj.as_posix()),
            "installed": installed,
            "existing_config": existing_config
        }

    def _handle_api_eval(self, query_str):
        """执行 Wiki 全文检索与 Mem0 云脑双通道检索评测，并将控制台指标转为精细的 JSON 返回给前端"""
        # 解析参数
        params = urllib.parse.parse_qs(query_str)
        suite_type = params.get("suite", ["default"])[0]
        skip_mem0 = params.get("skip_mem0", ["false"])[0].lower() == "true"

        # 1. 载入评测样本集
        cases = tm_eval_runner.load_or_create_eval_suite(suite_type)
        if not cases:
            self.send_error_json(400, "评测样本集为空或无法载入")
            return

        wiki_ranks = []
        wiki_durations = []
        mem0_matches = []
        mem0_durations = []
        mem0_active = not skip_mem0

        case_results = []

        # 2. 依次运行评测用例
        for case in cases:
            cid = case["id"]
            desc = case["description"]
            query = case["query"]
            expected = case["expected_path"]

            # Wiki 评测
            rank, wiki_ms = tm_eval_runner.run_wiki_eval(case)
            wiki_ranks.append(rank)
            wiki_durations.append(wiki_ms)

            # Mem0 评测
            matched = False
            mem0_ms = 0.0
            if mem0_active:
                matched, mem0_ms = tm_eval_runner.run_mem0_eval(case)
                if mem0_ms == 0.0:
                    mem0_active = False  # 如果接口请求彻底失败，自动判定为 OFFLINE
            
            mem0_matches.append(matched)
            mem0_durations.append(mem0_ms)

            case_results.append({
                "id": cid,
                "description": desc,
                "query": query,
                "expected_path": expected,
                "wiki": {
                    "rank": rank,
                    "rank_str": f"Rank {rank}" if rank > 0 else "Not Found",
                    "duration_ms": round(wiki_ms, 1)
                },
                "mem0": {
                    "matched": matched,
                    "match_str": "SUCCESS" if matched else "FAILED" if mem0_active else "OFFLINE",
                    "duration_ms": round(mem0_ms, 1) if mem0_active else None
                }
            })

        # 3. 计算最终统计看板指标
        total = len(cases)
        wiki_recall_1 = sum(1 for r in wiki_ranks if r == 1) / total
        wiki_recall_3 = sum(1 for r in wiki_ranks if 0 < r <= 3) / total
        wiki_recall_5 = sum(1 for r in wiki_ranks if 0 < r <= 5) / total
        avg_wiki_latency = sum(wiki_durations) / total

        mem0_accuracy = sum(1 for m in mem0_matches if m) / total if mem0_active else 0.0
        avg_mem0_latency = sum(mem0_durations) / total if mem0_active else 0.0

        # 返回精细控制的前端数据
        evaluation_report = {
            "summary": {
                "total_cases": total,
                "wiki": {
                    "recall_1": round(wiki_recall_1 * 100, 1),
                    "recall_3": round(wiki_recall_3 * 100, 1),
                    "recall_5": round(wiki_recall_5 * 100, 1),
                    "avg_latency_ms": round(avg_wiki_latency, 1)
                },
                "mem0": {
                    "active": mem0_active,
                    "accuracy": round(mem0_accuracy * 100, 1) if mem0_active else 0.0,
                    "avg_latency_ms": round(avg_mem0_latency, 1) if mem0_active else 0.0
                }
            },
            "cases": case_results
        }
        self.send_response_json(200, evaluation_report)

    def _handle_api_connect(self):
        """一键向 Claude Desktop 或 Cursor 注入并安全地配置 MCP 连接"""
        try:
            # 读取 POST 传入的 JSON 数据
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length).decode("utf-8")
            body = json.loads(post_data) if post_data else {}
        except Exception:
            self.send_error_json(400, "无效的 JSON 数据包")
            return

        mode = body.get("mode", "auto")
        dry_run = body.get("dry_run", False)
        force = body.get("force", True)
        force_without_backup = body.get("force_without_backup", False)

        try:
            # 1. 扫描配置文件
            paths = tm_agent_connect.detect_config_paths()
            is_wsl = paths["wsl_detected"]

            # 2. 智能判断桥接模式
            run_mode = "windows"
            if mode == "auto":
                run_mode = "wsl" if is_wsl else "windows"
            else:
                run_mode = mode

            # 3. 生成相应的 MCP 配置字典
            server_config = tm_agent_connect.generate_mcp_config(run_mode)

            results = {}

            # 4. 执行对 Claude Desktop 与 Cursor 的原子安全写入
            for client_key in ["claude_desktop", "cursor"]:
                config_path = paths.get(client_key)
                if config_path:
                    success = tm_agent_connect.patch_json_config(
                        file_path=Path(config_path),
                        server_name="tigermemory",
                        server_config=server_config,
                        dry_run=dry_run,
                        force=force,
                        force_without_backup=force_without_backup
                    )
                    results[client_key] = {
                        "path": str(Path(config_path).as_posix()),
                        "success": success
                    }
                else:
                    results[client_key] = {
                        "path": None,
                        "success": False,
                        "reason": "未在系统中探测到此客户端配置文件"
                    }

            self.send_response_json(200, {
                "run_mode": run_mode,
                "server_config": server_config,
                "results": results
            })
        except Exception as e:
            self.send_error_json(500, f"IDE 连接配置写入失败，原因：{e}")

    def _handle_static_files(self, path):
        """静态文件分发服务：将 HTML/CSS/JS 优雅地推送给浏览器"""
        # 默认路由映射至 index.html
        if path == "/" or path == "":
            path = "/index.html"

        # 统一映射到本地仓库的 dashboard 目录下
        file_path = REPO_ROOT / "dashboard" / path.lstrip("/")

        # 防止通过路径穿越漏洞访问 dashboard 以外的系统敏感文件
        try:
            resolved_file = file_path.resolve()
            resolved_dashboard = (REPO_ROOT / "dashboard").resolve()
            if not str(resolved_file).startswith(str(resolved_dashboard)):
                self.send_error_json(403, "禁止穿越访问敏感目录")
                return
        except Exception:
            self.send_error_json(404, "文件不存在")
            return

        if not resolved_file.exists() or not resolved_file.is_file():
            self.send_error_json(404, "您要查找的静态网页文件未找到")
            return

        # 根据文件后缀名自适应返回正确的 Content-Type 响应头
        suffix = resolved_file.suffix.lower()
        content_type = "text/plain; charset=utf-8"
        if suffix == ".html":
            content_type = "text/html; charset=utf-8"
        elif suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif suffix == ".json":
            content_type = "application/json; charset=utf-8"
        elif suffix in [".png", ".jpg", ".jpeg", ".webp"]:
            content_type = f"image/{suffix.lstrip('.')}"

        try:
            # 以二进制读取并写回
            content = resolved_file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error_json(500, f"读取静态文件失败：{e}")

    # ======================================================
    # 通用辅助响应响应器
    # ======================================================

    def send_response_json(self, status_code, data):
        """向客户端返回标准的 JSON 数据格式"""
        try:
            content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            # 如果发生错误，直接当做普通异常抛出
            sys.stderr.write(f"⚠️ 无法串行化响应数据：{e}\n")

    def send_error_json(self, status_code, message):
        """向客户端返回清晰的 JSON 格式错误报告"""
        self.send_response_json(status_code, {
            "error": True,
            "code": status_code,
            "message": message
        })


def run_api_server():
    """起飞运行轻量级 Web 服务器"""
    server_address = ("", PORT)
    httpd = HTTPServer(server_address, TigerMemoryAPIHandler)
    
    # 炫酷的 ASCII Art 极客标志，在控制台打印时瞬间拉满美感
    print("\n" + "=" * 60)
    print("       🐅  tigermemory 极客 Dashboard 服务已在后台起飞 🚀      ")
    print("=" * 60)
    print(f"📡 API 与静态网页服务正在监听本地端口： http://localhost:{PORT}")
    print("💡 这是一个【零外部依赖】的微服务，专为零基础与新手入门设计！")
    print("👉 您可以直接在默认浏览器中访问以下网址查看超炫仪表盘：")
    print(f"   ✨ 🚀  http://localhost:{PORT}  🚀 ✨")
    print("-" * 60)
    print("🔔 服务运行日志如下 (按 Ctrl+C 可安全中止)：")
    print("=" * 60 + "\n")

    # 自动在系统默认的浏览器中弹出 Dashboard 页面，体验丝滑！
    try:
        webbrowser.open(f"http://localhost:{PORT}")
    except Exception:
        pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n\n🏁 收到中止指令，tigermemory Dashboard 服务已平稳安全关闭。")
        sys.exit(0)


if __name__ == "__main__":
    # 配置鲁棒性 stdio
    try:
        tm_core.configure_stdio()
    except Exception:
        pass
    
    run_api_server()
