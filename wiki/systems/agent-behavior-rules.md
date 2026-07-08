---
title: "Agent Behavior Rules"
aliases: ["AI assistant rules", "agent rules", "working with AI"]
updated: 2026-06-21
owner: human
status: active
public: true
---

# Agent Behavior Rules

## Summary

This page describes how an AI assistant should behave when working with this
TigerMemory instance.

## Rules

- Read local evidence before making claims about this workspace.
- Prefer reviewable proposals before changing durable Wiki facts.
- Keep secrets and private identifiers out of prompts and files.
- Explain uncertainty in plain language.
- Cite the local pages or files used as evidence.

## Suggested Workflow

1. Search local memory and Wiki pages.
2. Answer only from evidence.
3. If the answer reveals a durable fact, propose a Wiki update.
4. Let the human approve long-term changes.

## Sources

- Public TigerMemory starter template.
