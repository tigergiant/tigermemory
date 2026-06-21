---
title: "Why DeepSeek Is The Default Starter Provider"
aliases: ["DeepSeek default", "LLM provider decision", "provider compatibility"]
updated: 2026-06-21
owner: human
status: active
public: true
---

# Why DeepSeek Is The Default Starter Provider

## Summary

TigerMemory recommends DeepSeek first because it is inexpensive and works through
an OpenAI-compatible chat-completions style endpoint. Other providers can be
added when they match the required API behavior.

## Decision

Use DeepSeek as the recommended first provider for the public starter.

## Rationale

- Low cost makes the Wiki Admin workflow practical for personal use.
- The OpenAI-compatible shape is easy for many tools to configure.
- JSON output still needs validation; provider compatibility is not the same as
  guaranteed identical behavior.

## Boundaries

- Do not print API keys.
- Do not send secrets to the LLM.
- Do not claim Anthropic-native compatibility unless a dedicated adapter exists
  and is tested.

## Sources

- Public TigerMemory starter template.
- `docs/provider-compatibility.md`.
