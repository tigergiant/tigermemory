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
