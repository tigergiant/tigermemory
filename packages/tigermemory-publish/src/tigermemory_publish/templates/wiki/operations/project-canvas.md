---
title: "Project Canvas"
updated: 2026-05-31
owner: human
status: active
public: true
---

# Project Canvas

## Summary

This starter canvas is safe for public snapshots. Replace it with your own
project stages after `tm init`.

## Current State

```mermaid
stateDiagram-v2
    [*] --> P0_Setup: done

    state P0_Setup {
        install: done
        local_profile: done
        first_note: todo
    }

    P0_Setup --> P1_Organize: next

    state P1_Organize {
        wiki_pages: todo
        memory_review: todo
        dashboard_check: todo
    }

    P1_Organize --> P2_Integrate: later

    state P2_Integrate {
        hybrid_profile: optional
        team_workflows: optional
        publishing_guard: optional
    }
```

## 活跃模块

| 模块 | 状态 | 最后更新 | 负责 |
|---|---|---|---|
| Local Setup | ✅ local profile ready after `tm init` | starter | human |
| First Memory | ⚪ write and verify your first local memory | starter | human |
| Wiki Organization | ⚪ add project notes under `wiki/` | starter | human |
| Hybrid Integrations | ⚪ optional OpenMemory and multi-IDE setup | starter | human |

## Current Blockers

- none

## Sources

- Public TigerMemory starter template.
