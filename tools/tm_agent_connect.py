#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/tm_agent_connect.py — tigermemory 一键 IDE 连接向导工具。
专为零基础和初学开发者设计，包含极其详细的中文注释和控制台中文引导。
支持一键备份并配置 Claude Desktop、Cursor 等开发工具，使其能够直接调用 tigermemory MCP 服务。

使用方法:
    py tools/tm_agent_connect.py             # 默认交互式安装
    py tools/tm_agent_connect.py --dry-run   # 仅预览要写入的配置，不实际更改文件
    py tools/tm_agent_connect.py --force     # 强制覆盖已有配置而不进行二次确认
"""

import os
import sys
import json
import shutil
import platform
import argparse
import subprocess
from datetime import datetime
from pathlib import Path

# ==========================================
# 辅助说明：帮助零基础的小白开发者理解 MCP 基础概念
# ==========================================
# MCP (Model Context Protocol) 是一种让 AI 助手（如 Claude, Cursor）可以调用你本地工具的协议。
# 通过在本机注入配置，可以让 AI 获得调用 'search_wiki', 'verify_memory_id' 等强大工具的能力。

# 获取当前仓库根目录路径
REPO_ROOT = Path(__file__).resolve().parent.parent

# 支持的配置源标识 (Windows, macOS, Linux)
OS_TYPE = platform.system()


def _configure_stdio() -> None:
    """
    避免在 Windows 系统的默认命令行环境 (GBK/cp936 编码) 下，
    因为输出 Unicode 字符或 Emoji 表情时导致程序崩溃。
    """
    if sys.version_info >= (3, 7):
        for stream in (sys.stdout, sys.stderr):
            try:
                # 优先尝试将控制台流重新配置为 UTF-8 编码，并带有安全替换符以防崩溃
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                try:
                    # 备用方案：在较旧或限制环境中，只配置错误安全机制为 backslashreplace 避免崩溃
                    stream.reconfigure(errors="backslashreplace")
                except Exception:
                    pass


def print_title():
    """在控制台打印一个漂亮的大标题"""
    print("=" * 60)
    print("      🐅 tigermemory 一键 IDE 连接向导 (零基础友好版) 🐅      ")
    print("=" * 60)
    print("💡 这是一个可以帮助您自动配置 AI 编辑器（如 Claude Desktop 或 Cursor）")
    print("使其可以无缝访问本地 tigermemory 记忆库的自动化工具。\n")


def get_windows_appdata_from_wsl():
    """
    当我们在 WSL 虚拟机内部运行时，获取 Windows 主机侧的 APPDATA 路径。
    我们使用 Windows 的 cmd.exe 来查询环境变量，再通过 wslpath 转换回 Linux 路径，非常稳定！
    """
    try:
        # 在 WSL 中调用 cmd.exe 打印 %APPDATA% 环境变量
        # cmd.exe 的输出可能带回车符，所以要 strip() 干净
        raw_appdata = subprocess.check_output(
            ["cmd.exe", "/c", "echo %APPDATA%"],
            stderr=subprocess.DEVNULL,
            text=True
        ).strip()
        if raw_appdata:
            # 使用 wslpath 命令将 Windows 路径（例如 C:\\Users\\...）转换为 Linux 挂载路径（例如 /mnt/c/Users/...）
            wsl_path = subprocess.check_output(
                ["wslpath", raw_appdata],
                stderr=subprocess.DEVNULL,
                text=True
            ).strip()
            return Path(wsl_path)
    except Exception:
        # 如果调用失败，说明可能并不在带有 Windows 回连的 WSL 环境中，或者 cmd.exe 不在 PATH
        pass
    return None


def detect_config_paths():
    """
    探测当前系统中 Claude Desktop 和 Cursor 的配置文件路径。
    支持 Windows、macOS 以及 WSL2 (从 WSL 映射到 Windows)！
    """
    paths = {
        "claude_desktop": None,
        "cursor": None,
        "wsl_detected": False
    }

    # 1. 检查我们是否在 WSL (Windows Subsystem for Linux) 内部运行
    is_wsl = False
    if OS_TYPE == "Linux":
        # 如果 /proc/version 中含有 Microsoft 或 WSL 字符串，说明是 WSL 环境
        try:
            version_text = Path("/proc/version").read_text(encoding="utf-8").lower()
            if "microsoft" in version_text or "wsl" in version_text:
                is_wsl = True
                paths["wsl_detected"] = True
        except Exception:
            pass

    # 2. 根据不同的操作系统类型定位配置文件路径
    if is_wsl:
        # 如果是 WSL 环境，我们尝试去访问挂载的 Windows appdata 目录
        win_appdata = get_windows_appdata_from_wsl()
        if win_appdata:
            paths["claude_desktop"] = win_appdata / "Claude" / "claude_desktop_config.json"
            paths["cursor"] = win_appdata / "Cursor" / "User" / "settings.json"
        else:
            # 备用方案：如果拿不到 Windows 的 APPDATA，则尝试找默认的 /mnt/c 挂载路径下的常规位置
            # 例如通过默认用户名获取（但这通常不精确，所以优先使用前面的 cmd.exe 查询方式）
            print("⚠️ 提示：在 WSL 中无法通过 cmd.exe 获取 Windows 的 APPDATA 路径，尝试使用默认路径...")
    elif OS_TYPE == "Windows":
        # 在 Windows 环境下，直接通过环境变量 APPDATA 获取
        appdata = Path(os.environ.get("APPDATA", ""))
        if appdata:
            paths["claude_desktop"] = appdata / "Claude" / "claude_desktop_config.json"
            paths["cursor"] = appdata / "Cursor" / "User" / "settings.json"
    elif OS_TYPE == "Darwin":
        # 在 macOS (Darwin) 环境下，配置文件在用户的 Library 目录下
        home = Path.home()
        paths["claude_desktop"] = home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        paths["cursor"] = home / "Library" / "Application Support" / "Cursor" / "User" / "settings.json"
    else:
        # 常规 Linux 环境
        home = Path.home()
        paths["claude_desktop"] = home / ".config" / "Claude" / "claude_desktop_config.json"
        paths["cursor"] = home / ".config" / "Cursor" / "User" / "settings.json"

    return paths


def generate_mcp_config(run_mode):
    """
    根据运行模式（Windows 运行或 WSL2 运行）生成要注入的 MCP 服务器配置 JSON 片段。
    
    run_mode 的选项：
    - 'windows': 纯 Windows 环境运行配置（Windows 版 Python 直接执行文件）
    - 'wsl': 从 Windows 调用 WSL 来运行（用 wsl bash -c 执行 WSL 中的 Python）
    """
    # 查找 python 可执行文件的路径或别名
    # Windows 环境下如果使用 python3 可能打不开，使用 py 或 python 更安全
    python_cmd = "py" if OS_TYPE == "Windows" else "python3"

    # 将路径转换为绝对路径并用正斜杠，防止 Windows JSON 转义引起路径断裂
    script_path = (REPO_ROOT / "tools" / "tm_mcp.py").as_posix()

    if run_mode == "wsl":
        # WSL 桥接模式下，Windows 侧的 Claude 启动配置是调用 "wsl" 并运行 linux 里的 python3
        # 我们假设用户在 WSL 中的代码仓是映射在 ~/tigermemory 里，或者我们可以用绝对路径
        wsl_script_path = script_path
        return {
            "command": "wsl",
            "args": [
                "bash",
                "-c",
                f"export TM_AGENT=claude-code && python3 {wsl_script_path} --stdio"
            ]
        }
    else:
        # 纯 Windows、macOS 或 Linux 本地运行模式
        return {
            "command": python_cmd,
            "args": [
                script_path,
                "--stdio"
            ]
        }


def backup_file(file_path: Path):
    """
    为现有的配置文件创建备份。
    备份文件名格式为：settings.json.bak_20260522_120000，非常清晰！
    """
    if not file_path.exists():
        return None
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = file_path.with_name(f"{file_path.name}.bak_{timestamp}")
    try:
        shutil.copy2(file_path, backup_path)
        print(f"📦 【备份成功】已将原配置文件备份至：\n   👉 {backup_path}")
        return backup_path
    except Exception as e:
        print(f"❌ 【备份失败】无法创建文件备份，原因：{e}")
        return None


def patch_json_config(file_path: Path, server_name: str, server_config: dict, dry_run: bool, force: bool):
    """
    读取、解析并把 MCP 配置安全地“打补丁”注入到目标的 JSON 配置文件中。
    支持优雅的自动创建父文件夹、自动处理 JSON 语法、支持强制写入与提示确认。
    """
    print("-" * 60)
    print(f"📁 正在处理配置文件：{file_path}")

    # 1. 如果文件不存在，我们准备创建一个全新的干净 JSON 结构
    if not file_path.exists():
        print("💡 提示：该配置文件目前在您的系统中不存在，我们将为您自动创建它。")
        config_data = {}
    else:
        # 如果存在，则尝试安全读取
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
        except json.JSONDecodeError as jde:
            print(f"❌ 警告：配置文件 JSON 格式损坏 (已为您跳过)，错误信息：{jde}")
            return False
        except Exception as e:
            print(f"❌ 警告：无法读取该文件，原因：{e}")
            return False

    # 2. 确保 JSON 中存在 "mcpServers" 这个主节点（如果不存在则新建空字典）
    # 无论是 Claude 还是 Cursor，均支持在 "mcpServers" 字典中配置多个 MCP 客户端
    if "mcpServers" not in config_data:
        config_data["mcpServers"] = {}

    # 3. 检查是否已经存在同名配置
    existing = config_data["mcpServers"].get(server_name)
    if existing:
        print(f"⚠️  警告：配置中已存在同名节点 '{server_name}'！")
        print(f"🔄 当前配置值为：{json.dumps(existing, ensure_ascii=False, indent=2)}")
        print(f"✨ 准备注入的值为：{json.dumps(server_config, ensure_ascii=False, indent=2)}")
        
        if dry_run:
            print("🔍 【预览模式】跳过写入。")
            return True
            
        # 如果不是强制覆盖模式，并且在交互式环境下，我们向用户发出确认提示
        if not force:
            # 检查当前是否在交互式 TTY 终端
            if sys.stdin.isatty():
                ans = input("❓ 是否确认覆盖已有的 'tigermemory' 配置？[y/N]: ").strip().lower()
                if ans not in ("y", "yes"):
                    print("🚫 【已取消】保留原有配置，未作任何修改。")
                    return False
            else:
                print("📝 【静默覆盖】未检测到 TTY 终端交互，已按照安全原则自动覆盖并备份。")

    # 4. 将我们的 MCP 配置项写入到 mcpServers 字典中
    config_data["mcpServers"][server_name] = server_config

    # 如果是预览模式，我们就只在屏幕上打出来，不改变硬盘上的文件
    if dry_run:
        print("\n🔍 【配置修改预览】如果在正式运行下，写入的文件内容如下：")
        print(json.dumps(config_data, ensure_ascii=False, indent=2))
        print("-" * 60)
        return True

    # 5. 正式写入文件（先做备份！）
    # 确保父级文件夹已经创建好
    file_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 备份原文件
    backup_file(file_path)

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
        print("🎉 【配置注入成功！】已成功将 tigermemory 写入您的编辑器配置文件中！")
        print("-" * 60)
        return True
    except Exception as e:
        print(f"❌ 【写入失败】写入文件时出错，原因：{e}")
        return False


def main():
    # 配置终端输入输出流
    _configure_stdio()

    # 配置命令行解析器，对命令行开发者极其友好！
    parser = argparse.ArgumentParser(
        description="tigermemory 一键 IDE 自动化连接向导。帮您一键配置 AI 编辑器中的 MCP 连接。",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅生成并预览配置 JSON 片段，不执行任何写硬盘或备份操作（只读安全）"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="如果发现已有同名配置，强制直接覆盖，跳过交互式二次确认（适合脚本静默运行）"
    )
    parser.add_argument(
        "--mode",
        choices=["windows", "wsl", "auto"],
        default="auto",
        help="配置注入模式：'windows' 为 Windows 直接运行模式；'wsl' 为通过 Windows 启动 wsl 桥接模式。'auto' 会自动根据当前系统智能判断。"
    )
    
    args = parser.parse_args()

    print_title()
    
    # 1. 扫描与探测当前系统的配置文件
    print("🔍 正在扫描系统，寻找已安装的 AI 助手配置文件...")
    paths = detect_config_paths()
    
    claude_path = paths["claude_desktop"]
    cursor_path = paths["cursor"]
    is_wsl = paths["wsl_detected"]

    # 2. 智能决策运行模式
    run_mode = "windows"
    if args.mode == "auto":
        if is_wsl:
            run_mode = "wsl"
            print("🐳 检测到当前运行在 WSL 环境下，我们将自动配置 Windows -> WSL 的 MCP 桥接模式！")
        else:
            run_mode = "windows"
            print(f"💻 检测到当前操作系统为：{OS_TYPE}，我们将配置原生的本地直接执行模式！")
    else:
        run_mode = args.mode
        print(f"🛠️ 用户手动指定了运行模式：{run_mode}")

    # 3. 生成对应的 MCP 服务描述配置
    server_name = "tigermemory"
    server_config = generate_mcp_config(run_mode)
    
    print("\n✨ 自动生成的 MCP 配置文件块如下：")
    print(json.dumps(server_config, ensure_ascii=False, indent=2))
    print()

    # 4. 对 Claude Desktop 执行注入操作
    if claude_path:
        patch_json_config(claude_path, server_name, server_config, args.dry_run, args.force)
    else:
        print("❓ 未找到 Claude Desktop 配置文件默认路径，已自动跳过 Claude 配置。")

    # 5. 对 Cursor 执行注入操作
    if cursor_path:
        patch_json_config(cursor_path, server_name, server_config, args.dry_run, args.force)
    else:
        print("❓ 未找到 Cursor 配置文件默认路径，已自动跳过 Cursor 配置。")

    # 6. 结束成功引导
    print("\n" + "=" * 60)
    if args.dry_run:
        print("🏁 【预览结束】以上为预览内容。如果您确认无误，可去掉 --dry-run 运行以正式写入！")
    else:
        print("🏁 【配置向导全部完成！】")
        print("📢 接下来您需要做的是：")
        print("   1️⃣ 重启您的 AI 客户端（如彻底关闭并重启 Claude Desktop / Cursor）。")
        print("   2️⃣ 在对话中输入：'帮我调用 check_worktree 查看本地工作区'")
        print("   3️⃣ 检查 AI 是否能够成功读出本地分支状态。如果可以，说明已大功告成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
