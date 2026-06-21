# Provider Compatibility

TigerMemory public starter is LLM-first. DeepSeek is the recommended first
provider because it is inexpensive and exposes an OpenAI-compatible
chat-completions style interface.

## Supported Starter Path

- `DEEPSEEK_API_KEY`
- Optional `DEEPSEEK_BASE_URL`
- Optional `DEEPSEEK_MODEL`
- Optional `DEEPSEEK_ADMIN_MODEL`

Routine routing and JSON tasks should prefer a low-cost model such as
`deepseek-v4-flash`. Durable Wiki Admin proposal drafts should prefer a stronger
model such as `deepseek-v4-pro`.

## Compatibility Boundary

OpenAI-compatible means TigerMemory can call a chat-completions style endpoint
with JSON validation. It does not mean every provider behaves identically.

Anthropic-native APIs are not claimed as a starter guarantee unless a dedicated
adapter is implemented and tested. Users can still use other providers through a
tested OpenAI-compatible gateway if that gateway preserves the expected request
and response shape.

## Safety Rules

- Do not print API keys.
- Do not store API keys in Wiki pages.
- Do not send secrets, passwords, private keys, identity numbers, or private
  personal records to the LLM.
- Treat model output as a proposal until the human approves durable Wiki writes.
