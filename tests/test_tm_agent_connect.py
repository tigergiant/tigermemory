# -*- coding: utf-8 -*-
"""
tests/test_tm_agent_connect.py — 针对 tm_agent_connect.py 一键连接工具的单元测试。
全面覆盖跨平台配置探测、MCP JSON 补丁模拟、WSL 桥接路径映射等。
"""

import os
import sys
import unittest
import json
from unittest.mock import patch, MagicMock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

# 导入待测核心函数
import tm_agent_connect


class TestAgentConnect(unittest.TestCase):
    def test_generate_mcp_config_windows(self):
        # 原生 Windows 模式
        config = tm_agent_connect.generate_mcp_config(run_mode="windows")
        self.assertEqual(config["command"], "py" if tm_agent_connect.OS_TYPE == "Windows" else "python3")
        self.assertIn("--stdio", config["args"])

    @patch("subprocess.check_output")
    def test_generate_mcp_config_wsl(self, mock_subprocess):
        # WSL 桥接模式下，当 Windows 作为宿主时，自动调用 wslpath
        mock_subprocess.return_value = "/mnt/d/tigermemory/tools/tm_mcp.py\n"
        
        with patch("tm_agent_connect.OS_TYPE", "Windows"):
            config = tm_agent_connect.generate_mcp_config(run_mode="wsl")
            self.assertEqual(config["command"], "wsl")
            # 确认 bash -c 里面包含转换后的 wsl 路径
            self.assertIn("bash", config["args"])
            self.assertIn("-c", config["args"])
            self.assertIn("/mnt/d/tigermemory/tools/tm_mcp.py", config["args"][-1])

    @patch("tm_agent_connect.OS_TYPE", "Linux")
    @patch("pathlib.Path.home")
    def test_detect_config_paths_linux(self, mock_home):
        mock_home.return_value = Path("/home/testuser")
        
        paths = tm_agent_connect.detect_config_paths()
        
        self.assertEqual(paths["claude_desktop"], Path("/home/testuser/.config/Claude/claude_desktop_config.json"))
        self.assertEqual(paths["cursor"], Path("/home/testuser/.cursor/mcp.json"))

    @patch("tm_agent_connect.OS_TYPE", "Darwin")
    @patch("pathlib.Path.home")
    def test_detect_config_paths_mac(self, mock_home):
        mock_home.return_value = Path("/Users/testuser")
        
        paths = tm_agent_connect.detect_config_paths()
        
        self.assertEqual(paths["claude_desktop"], Path("/Users/testuser/Library/Application Support/Claude/claude_desktop_config.json"))
        self.assertEqual(paths["cursor"], Path("/Users/testuser/.cursor/mcp.json"))

    def test_patch_json_config_dry_run(self):
        # 验证 dry_run 模式下决不写入实体文件
        temp_file = Path("nonexistent_temp_file_for_dry_run.json")
        if temp_file.exists():
            temp_file.unlink()

        server_config = {
            "command": "python",
            "args": ["-m", "mcp"]
        }

        # 即使目标文件不存在且 dry_run=True，也不会被物理创建
        success = tm_agent_connect.patch_json_config(
            file_path=temp_file,
            server_name="test_server",
            server_config=server_config,
            dry_run=True,
            force=True
        )
        
        self.assertTrue(success)
        self.assertFalse(temp_file.exists())

    @patch("tm_agent_connect.patch_json_config")
    @patch("tm_agent_connect.detect_config_paths")
    @patch("sys.exit")
    @patch("tm_agent_connect.print_title")
    def test_client_all_default_behavior(self, mock_print_title, mock_sys_exit, mock_detect_paths, mock_patch):
        # 默认 --client all 不破坏现有 Claude + Cursor 路径逻辑
        mock_detect_paths.return_value = {
            "claude_desktop": Path("/fake/claude.json"),
            "cursor": Path("/fake/cursor.json"),
            "wsl_detected": False
        }
        import argparse
        with patch("argparse.ArgumentParser.parse_args") as mock_parse:
            mock_parse.return_value = argparse.Namespace(
                dry_run=True, force=False, force_without_backup=False,
                mode="auto", client="all", print_config=None
            )
            tm_agent_connect.main()

        # 验证 patch_json_config 被调用了两次（一次 claude，一次 cursor）
        self.assertEqual(mock_patch.call_count, 2)
        mock_patch.assert_any_call(Path("/fake/claude.json"), "tigermemory", unittest.mock.ANY, True, False, False)
        mock_patch.assert_any_call(Path("/fake/cursor.json"), "tigermemory", unittest.mock.ANY, True, False, False)

    @patch("tm_agent_connect.patch_json_config")
    @patch("tm_agent_connect.detect_config_paths")
    @patch("tm_agent_connect.print_title")
    def test_client_claude_desktop_only(self, mock_print_title, mock_detect_paths, mock_patch):
        # --client claude-desktop 只处理 Claude Desktop
        mock_detect_paths.return_value = {
            "claude_desktop": Path("/fake/claude.json"),
            "cursor": Path("/fake/cursor.json"),
            "wsl_detected": False
        }
        import argparse
        with patch("argparse.ArgumentParser.parse_args") as mock_parse:
            mock_parse.return_value = argparse.Namespace(
                dry_run=True, force=False, force_without_backup=False,
                mode="auto", client="claude-desktop", print_config=None
            )
            tm_agent_connect.main()

        # 验证只调用了 claude 路径
        mock_patch.assert_called_once_with(Path("/fake/claude.json"), "tigermemory", unittest.mock.ANY, True, False, False)

    @patch("tm_agent_connect.patch_json_config")
    @patch("tm_agent_connect.detect_config_paths")
    @patch("tm_agent_connect.print_title")
    def test_client_cursor_only(self, mock_print_title, mock_detect_paths, mock_patch):
        # --client cursor 只处理 Cursor
        mock_detect_paths.return_value = {
            "claude_desktop": Path("/fake/claude.json"),
            "cursor": Path("/fake/cursor.json"),
            "wsl_detected": False
        }
        import argparse
        with patch("argparse.ArgumentParser.parse_args") as mock_parse:
            mock_parse.return_value = argparse.Namespace(
                dry_run=True, force=False, force_without_backup=False,
                mode="auto", client="cursor", print_config=None
            )
            tm_agent_connect.main()

        # 验证只调用了 cursor 路径
        mock_patch.assert_called_once_with(Path("/fake/cursor.json"), "tigermemory", unittest.mock.ANY, True, False, False)

    @patch("tm_agent_connect.patch_json_config")
    @patch("tm_agent_connect.detect_config_paths")
    @patch("tm_agent_connect.print_title")
    def test_client_generic_no_patch(self, mock_print_title, mock_detect_paths, mock_patch):
        # --client generic 不写任何物理配置，不调用 patch_json_config
        mock_detect_paths.return_value = {
            "claude_desktop": Path("/fake/claude.json"),
            "cursor": Path("/fake/cursor.json"),
            "wsl_detected": False
        }
        import argparse
        with patch("argparse.ArgumentParser.parse_args") as mock_parse:
            mock_parse.return_value = argparse.Namespace(
                dry_run=True, force=False, force_without_backup=False,
                mode="auto", client="generic", print_config=None
            )
            tm_agent_connect.main()

        # 验证完全没调用 patch_json_config
        mock_patch.assert_not_called()

    @patch("tm_agent_connect.patch_json_config")
    @patch("tm_agent_connect.detect_config_paths")
    @patch("tm_agent_connect.is_wsl_runtime", return_value=False)
    def test_print_config_stdio(self, mock_is_wsl, mock_detect_paths, mock_patch):
        # --print-config stdio 输出 JSON 且不调用 patch_json_config
        import io
        import argparse
        captured_output = io.StringIO()
        with patch("sys.stdout", captured_output):
            with patch("argparse.ArgumentParser.parse_args") as mock_parse:
                mock_parse.return_value = argparse.Namespace(
                    dry_run=True, force=False, force_without_backup=False,
                    mode="auto", client="all", print_config="stdio"
                )
                with self.assertRaises(SystemExit) as cm:
                    tm_agent_connect.main()
                self.assertEqual(cm.exception.code, 0)

        # 验证完全没调用 patch_json_config
        mock_patch.assert_not_called()
        mock_detect_paths.assert_not_called()
        mock_is_wsl.assert_called_once()

        # 验证输出的是合法的 JSON
        output_str = captured_output.getvalue().strip()
        data = json.loads(output_str)
        self.assertIn("mcpServers", data)
        self.assertIn("tigermemory", data["mcpServers"])
        self.assertIn("command", data["mcpServers"]["tigermemory"])
        self.assertIn("args", data["mcpServers"]["tigermemory"])

    @patch("tm_agent_connect.patch_json_config")
    @patch("tm_agent_connect.detect_config_paths")
    @patch("tm_agent_connect.is_wsl_runtime", return_value=False)
    def test_print_config_http_placeholder_only(self, mock_is_wsl, mock_detect_paths, mock_patch):
        # --print-config http 输出占位 token 且不读取/打印真实 token
        import io
        import argparse
        captured_output = io.StringIO()
        with patch("sys.stdout", captured_output):
            with patch("argparse.ArgumentParser.parse_args") as mock_parse:
                mock_parse.return_value = argparse.Namespace(
                    dry_run=True, force=False, force_without_backup=False,
                    mode="auto", client="all", print_config="http"
                )
                with self.assertRaises(SystemExit) as cm:
                    tm_agent_connect.main()
                self.assertEqual(cm.exception.code, 0)

        mock_patch.assert_not_called()
        mock_detect_paths.assert_not_called()
        mock_is_wsl.assert_not_called()

        output_str = captured_output.getvalue().strip()
        data = json.loads(output_str)
        self.assertIn("mcpServers", data)
        server_cfg = data["mcpServers"]["tigermemory"]
        self.assertEqual(server_cfg["url"], "https://tm.doodiu.cloud/mcp")
        self.assertEqual(server_cfg["headers"]["Authorization"], "Bearer <TM_MCP_API_KEY>")
