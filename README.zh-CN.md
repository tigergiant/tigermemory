<div align="center">

[English](README.md) | [中文](README.zh-CN.md)

# TigerMemory

**你的个人 AI 大脑 —— 本地优先，证据说话，完全归你。**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](#)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](#贡献)

让 AI 帮你把碎片笔记变成结构化知识库。所有内容以 Markdown + Git 存在你自己的机器上，AI 读取本地证据后回答问题 —— 每句话都标注来源，不再胡说八道。

没有云端锁定，没有数据上传，没有供应商绑架。你的知识，你的规则，你做主。

</div>

---

## 这个项目的起源

TigerMemory 最初是一个人的 AI 工程系统。

2026 年 4 月，虎哥（一个重度使用 AI 编程工具的开发者）发现自己的知识散落在各处：和 Claude 的对话、和 Codex 的对话、和 ChatGPT 的对话，每次换一个 AI 工具就得从头喂一遍背景。笔记在封闭云笔记里，搬不出来；AI 记忆功能偷偷上传数据到云端，不知道它存了什么、删没删干净。

他想要一个东西：**一个本地优先的 AI 大脑，让所有 AI 工具都能读同一份知识，但数据永远在自己机器上。**

不是又一个笔记 App，不是又一个 AI 聊天机器人。而是一个让 AI 帮你维护知识库的系统 —— AI 起草，你批准，知识只进不出（除非你主动开源）。

从 4 月到 7 月，859 次 commit，从一个自用工程系统演进到可以开源的框架。核心思路始终没变：**LLM Wiki 是主权资产，TigerMemory 只是运行时，可以被替换，但你的知识不随它消失。**

## 为什么需要 TigerMemory？

你可能已经遇到过这些问题：

| 痛点 | TigerMemory 的解法 |
|---|---|
| AI 聊天机器人对你自己的事胡编乱造 | AI **只从你的本地证据回答**，附引用路径 |
| 笔记散落在各种封闭云笔记里，搬不出来 | 一切都是 **Markdown + Git**，开放、可移植、永久归你 |
| 换一个 AI 工具就得从头喂一遍背景 | Wiki 是**共享事实源**，任何 AI 工具都能读 |
| AI "记忆"功能偷偷上传你的私人数据 | TigerMemory **完全本地运行**，数据不离开你的机器 |
| AI 直接改你的笔记，你不知道它改了啥 | AI 起草提案 → **你审阅 → 你批准** → 才写入 Wiki |

## 核心特性

- **证据优先回答** — `tm ask` 先检索本地记忆和 Wiki，再让 AI 基于证据回答，每条结论标注来源页
- **提案-审批工作流** — AI 帮你整理笔记、生成 Wiki 页面草稿，但只有你点批准才写入
- **纯 Markdown + Git** — 不用学新格式，不用装新 App，你的笔记用 VS Code、Obsidian、任何编辑器都能打开
- **本地 SQLite 记忆** — 对话级短期记忆存在本地数据库，FTS5 全文检索，无需向量数据库
- **兼容任意 AI 工具** — Claude、Codex、ChatGPT、Cursor 都能通过命令行读写你的 Wiki
- **DeepSeek 优先** — 推荐用 DeepSeek 作为 LLM provider，成本低、API 兼容 OpenAI 格式
- **零基建依赖** — 默认 `local` 模式不需要 Docker、WSL、Qdrant、Caddy，装好 Python + Git 就能跑（`hybrid` 高级模式才用到这些）
- **Git 安全更新** — `tm update` 只做 fast-forward，不 reset、不 clean、不碰你的数据

## 快速开始

**你需要：** Python 3.10+、Git、一个 LLM API Key（推荐 [DeepSeek](https://platform.deepseek.com/)）

```powershell
# 1. 安装
py -m pip install -e .

# 2. 初始化本地工作区
tm init

# 3. 打开浏览器配置向导
tm dashboard
```

浏览器会自动打开 `http://127.0.0.1:9777/start`，向导带你走完：选模式 → 粘贴 API Key → 选回复风格。你的 Key 存在本地 `runtime/` 目录，不进 Git，不会被打印回显。

**就这三步，你现在拥有一个本地 AI Wiki 管理器了。**

## 5 分钟体验完整流程

```powershell
# 写一条记忆到本地
"记住：我喜欢暗色模式和简洁的回答" | tm write-memory --agent human --topic systems

# 搜回来
tm search --query "偏好" --scope all

# 让 AI 回答 —— 它会先读你的本地证据，再带引用回答
tm ask --query "我有什么偏好？" --scope all
```

再试试 Wiki Admin 核心流程：

```powershell
# 1. 随手记一条笔记
echo "项目目标：用 AI 建一个个人食谱合集，自动标注食材和营养" > notes.md

# 2. 让 AI 把它整理成规范的 Wiki 提案
cat notes.md | tm admin propose --partition projects --title "食谱合集"

# 3. 查看提案（AI 建议了分类路径和页面结构）
tm admin list
tm admin show "<proposal-id>"

# 4. 批准 → 写入 wiki/projects/
tm admin approve "<proposal-id>"

# 5. 提问 —— AI 引用刚创建的页面回答
tm ask --query "我的食谱项目是做什么的？" --scope wiki
```

**核心理念：AI 起草，你批准。** 没有你点头，任何东西都不会进入你的 Wiki。

## 工作原理

```
  你的笔记 ──→ AI 生成提案 ──→ 你审阅 ──→ Wiki 页面
      │                                         │
      │          tm ask ──→ AI 读取证据 ──→ 带引用回答
      │               ↑                           │
      └───── 本地 SQLite 记忆 ←──────────────────┘
```

**三层架构，全部本地：**

| 层 | 作用 | 存储 |
|---|---|---|
| **Wiki**（长期层） | 持久知识，Markdown 格式 | Git 版本控制的文件 |
| **Memory**（短期层） | 对话上下文和临时笔记 | 本地 SQLite + FTS5 |
| **LLM**（智能层） | 整理、提案、回答 | DeepSeek 或任意 OpenAI 兼容 API |

**七个 Wiki 分区，分类不纠结：**

| 分区 | 放什么 |
|---|---|
| `projects/` | 正在做的项目、实验、构建 |
| `areas/` | 长期职责、习惯、复盘问题 |
| `resources/` | 参考资料、教程、可复用笔记 |
| `decisions/` | 重要决策 + 理由 |
| `journal/` | 带日期的周报、反思 |
| `systems/` | 工具配置、AI 规则、工作流 |
| `archive/` | 已完成或过期的内容 |

## 没有 API Key 也能用

没 Key？TigerMemory 依然是一个好用的本地笔记搜索工具：

```powershell
# 只返回本地证据，不调用 AI
tm ask --offline --query "本地记忆" --scope all
tm search --scope wiki --query "AI 行为规则"
```

写入、搜索、验证记忆都不需要外部 API。等你准备好再用 LLM 体验完整的 AI Wiki 管理流程。

## 连接你的 AI 工具

TigerMemory 兼容任何能跑本地命令的 AI 工具 —— Claude、Codex、ChatGPT、Cursor 等。安全默认：只读 + 人工审批。

```powershell
# 查看当前 AI 工具状态
tm agent status

# 安装项目规则文件
tm agent apply --yes

# 你的 AI 现在可以从本地证据搜索和回答了
tm ask --query "我的 AI 助手应该遵守什么规则？" --scope wiki
```

详细配置见 [连接 AI 工具指南](docs/connect-your-ai-tools.md)。

## CLI 命令速查

| 命令 | 作用 |
|---|---|
| `tm init` | 初始化本地工作区 |
| `tm dashboard` | 打开 Web 控制台（端口 9777） |
| `tm ask --query "..."` | 带 AI + 本地证据 + 引用回答 |
| `tm ask --offline` | 纯证据模式，不调模型 |
| `tm search --query "..."` | 搜索记忆 + Wiki |
| `tm write-memory` | 写入本地记忆 |
| `tm admin propose` | AI 起草 Wiki 页面供你审阅 |
| `tm admin approve` | 你批准 → 写入 Wiki |
| `tm admin list` | 查看待审提案 |
| `tm update status` | 检查框架更新 |
| `tm llm status` | 检查 LLM 配置状态 |
| `tm doctor` | 本地诊断 |

完整 CLI 契约（每个命令的输入/输出/退出码）：[public-core-contract.md](wiki/systems/public-core-contract.md)

## 配置参考

| 环境变量 | 作用 | 默认值 |
|---|---|---|
| `TIGERMEMORY_INSTANCE_ROOT` | 你的数据目录 | 当前 checkout |
| `TIGERMEMORY_PROFILE` | `local`（基础）或 `hybrid`（高级） | `local` |
| `DEEPSEEK_API_KEY` | 你的 LLM API Key | — |
| `DEEPSEEK_MODEL` | 日常任务模型 | `deepseek-v4-flash` |
| `DEEPSEEK_ADMIN_MODEL` | Wiki Admin 提案模型 | `deepseek-v4-pro` |

高级：任意 OpenAI 兼容的 chat-completions 端点都可以用。详见 [Provider 兼容性文档](docs/provider-compatibility.md)。

## 安全更新

```powershell
tm update status    # 检查是否有更新
tm update apply     # 安全 fast-forward 更新
```

更新器只碰源码，不碰你的数据（`wiki/`、`data/`、`runtime/`）。不会 `git reset`，不会 `git clean`，不会偷偷 stash。

## 常见问题

**Q：数据会被上传到云端吗？**
不会。TigerMemory 完全在本地运行。你的 Wiki 是本地 Markdown 文件，记忆是本地 SQLite 数据库。唯一的外部调用是你配置的 LLM API（DeepSeek 等），只有提问时才会发送查询。

**Q：不用 DeepSeek 行不行？**
可以。任何兼容 OpenAI chat-completions 格式的 API 都能用，比如 OpenAI 本身、通义千问、Moonshot 等。DeepSeek 是推荐项因为性价比高。

**Q：和 Obsidian 有什么区别？**
Obsidian 是笔记编辑器，TigerMemory 是 AI 知识管理系统。你可以用 Obsidian 编辑 TigerMemory 的 Wiki 文件，两者是互补关系不是替代关系。

**Q：和 Mem0 有什么区别？**
Mem0 是 AI 记忆层，专注于让 AI 记住对话上下文。TigerMemory 是完整的个人知识库 + AI 管理：不仅有记忆层，还有 Git 版本控制的 Wiki 长期层、提案-审批工作流、证据优先回答机制。

**Q：`npm install tigermemory` 是这个项目吗？**
不是。npm 上 `tigermemory` 是另一个项目。本项目用 `pip` 从源码安装。

## 项目活跃度

<div align="center">

<img src="https://github-readme-activity-graph.vercel.app/graph?username=tigergiant&repo=tigermemory&theme=github-compact&hide_border=true&area=true&color=4493f8&line=4493f8&point=ffffff" width="100%" alt="GitHub Activity Graph">

</div>

<div align="center">

<img src="https://img.shields.io/github/commit-activity/m/tigergiant/tigermemory?style=for-the-badge&color=4493f8" alt="Commit Activity">
<img src="https://img.shields.io/github/languages/count/tigergiant/tigermemory?style=for-the-badge&color=blueviolet" alt="Languages">
<img src="https://img.shields.io/github/repo-size/tigergiant/tigermemory?style=for-the-badge&color=success" alt="Repo Size">
<img src="https://img.shields.io/github/last-commit/tigergiant/tigermemory?style=for-the-badge&color=orange" alt="Last Commit">

</div>

> 从 2026 年 4 月持续至今，每天都在用 —— 不是玩具，是日用的生产工具。

## 贡献

欢迎提交 Issue 和 PR！

- 发现 Bug → [提 Issue](https://github.com/tigergiant/tigermemory/issues)
- 有想法 → [发 PR](https://github.com/tigergiant/tigermemory/pulls)
- 想讨论 → 直接在 Issue 里说

## 许可证

MIT License，详见 [LICENSE](LICENSE)。你可以自由使用、修改、分发，包括商用。

第三方依赖版权声明（FastAPI、Pydantic、Tailwind CSS、Mermaid 等）：[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

---

<div align="center">

**TigerMemory** — 你的知识，你的机器，你做主。

</div>
