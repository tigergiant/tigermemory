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

每个分区维护自己的 index.md，格式：

```markdown
# 分区名

- [页面标题](page-file.md) — 一行摘要
```
