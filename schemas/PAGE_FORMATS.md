# PAGE_FORMATS

Wiki 页面格式规范。

## 最小模板

```markdown
---
owner: claude-code
status: active
updated: 2026-04-16
---

# 页面标题

## 摘要

200 字以内，说明这页解决什么问题，当前结论是什么。

## 已验证现状

## 推断

## 待确认

## 规划

## 来源
```

## 字段说明

### frontmatter

| 字段 | 必填 | 说明 |
|------|------|------|
| owner | 是 | 主要维护者（AGENTS.md §3 常规 agent 8 项）：claude-code / codex / openclaw / hermes / deerflow / human / linter / kimi |
| status | 是 | active / draft / archived |
| updated | 是 | 最后更新日期，YYYY-MM-DD |
| subtopic | 否 | 可选，二级分类标签，字符串数组。partition 是第 1 层，subtopic 是第 2 层。命名建议用短语，例如 `["memory-engine", "retrieval"]`。可一页多 subtopic |

**关于 subtopic（Memory Tree 第 2 层）**：partition 是分区，subtopic 是同分区内的子主题。同一页可以归多个 subtopic（用数组表达）。subtopic 不需要预定义词表；agent 写页面时按内容自由判断，由后续审计工具周期统一。落到磁盘时使用 YAML 数组，例如 `subtopic: ["memory-engine", "retrieval"]`。

### 正文分节

| 节 | 必填 | 说明 |
|----|------|------|
| 摘要 | 是 | 200 字以内，概括页面核心内容和结论 |
| 已验证现状 | 按需 | 经过确认的事实 |
| 推断 | 按需 | 基于已有信息的合理推断 |
| 待确认 | 按需 | 需要进一步验证的内容 |
| 规划 | 按需 | 未来计划，不要写成已落地 |
| 来源 | 是 | 引用的原始资料、对话、文档链接 |

## 链接规范

使用标准 Markdown 链接，不使用 Wikilinks：

```markdown
[页面标题](wiki/systems/ai-cloud-brain.md)
```

## inbox 文件格式

inbox 文件由系统路由写入，frontmatter 必须包含 `routed_by: tigermemory`：

```markdown
---
owner: <agent>
status: draft
updated: YYYY-MM-DD
routed_by: tigermemory                # 必填，pre-commit hook 检查
route_score: 65                      # 可选，0-100
route_decision_reason: "..."          # 可选，路由决策理由
route_topic_inferred: systems         # 可选，LLM 判断的 topic
---

# 标题

正文...
```

### inbox frontmatter 字段

| 字段 | 必填 | 说明 |
|------|------|------|
| owner | 是 | 写入 agent |
| status | 是 | draft |
| updated | 是 | YYYY-MM-DD |
| routed_by | 是 | 必须是 `tigermemory`；pre-commit 检查 |
| route_score | 否 | 0-100，LLM 路由打分 |
| route_decision_reason | 否 | 路由决策理由 |
| route_topic_inferred | 否 | LLM 推断的 topic |

人工紧急编辑 inbox 时可用 `routed_by: human-direct` 作为逃生舱。

## index.md 规范

每个分区维护自己的 `index.md`。它不是普通知识页，也不是草稿堆，而是该分区的入口页，用来回答"这个分区有哪些页面 / 从哪里开始 / 目录怎么组织"这类导航问题。具体事实、规则和操作细节仍以链接到的具体页面为准。

顶层分区 index 由 `tools/tm_compile_index.py` 维护；后续新建或重编译时必须使用同一标准：

```markdown
---
owner: codex
status: active
updated: YYYY-MM-DD
aliases: ["分区名", "分区名目录", "分区名索引", "分区名入口", "分区名有哪些页面"]
subtopic: ["navigation", "index"]
title: "分区名分区入口"
description: "分区的目录和导航页，用于回答有哪些页面、从哪里开始、分区怎么组织；具体事实以具体页面为准。"
---

# 分区名分区入口

## 摘要

本页是该分区的目录和导航页，用于回答"有哪些页面 / 从哪里开始 / 分区怎么组织"这类问题。具体事实、规则和操作细节应继续阅读下方具体页面。

## 来源

- 本页 `## 页面` 部分由 `tools/tm_compile_index.py` 自动编译。
- 本页作为分区入口页参与自然语言召回；具体事实以链接到的具体页面为准。

## 页面

- [页面标题](page-file.md) — 一行摘要
```

编译规则：

- `## 页面` 及其后面的列表由编译器覆盖，人工内容应放在 `## 页面` 之前。
- `status: draft` / `status: archived` 的页面不进入主 index，也不进入 `index-by-subtopic.md` 预览，避免审核提案、半成品和历史归档污染自然语言召回。
- 当 index 内容实际变化时，`updated` 必须刷新为当前 Asia/Shanghai 日期；无内容变化时不为了日期产生每日抖动。
- 二级目录如果维护自己的 `index.md`，也应采用同样的"目录说明页"语义：写清用途、落点规则、来源边界，不全文堆放原始材料。
