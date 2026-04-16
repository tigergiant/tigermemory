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
| owner | 是 | 主要维护者：claude-code / openclaw / hermes / deerflow / human |
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

## index.md 规范

每个分区维护自己的 index.md，格式：

```markdown
# 分区名

- [页面标题](page-file.md) — 一行摘要
```
