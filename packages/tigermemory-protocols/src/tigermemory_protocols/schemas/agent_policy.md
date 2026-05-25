# Agent Policy Protocol v0.1

`agent_policy` describes a portable policy payload for controlling agent
behavior across IDEs, MCP clients, and future configuration assistants.

This protocol is an internal draft. It does not install or apply any policy in
v0.1.

## Required Fields

| Field | Why It Is Required |
|---|---|
| `policy_id` | Gives the policy a stable reference for audit and conflict reports. |
| `applies_to` | States whether the policy targets all agents or selected agents. |
| `rules` | Holds the actual behavior constraints. |

`applies_to` accepts either the literal string `all` or an array of regular
agent names. Special data-source identities such as `mem0` are excluded because
they do not execute agent behavior.

## Rule Groups

`before_answer` is for checks that should happen before the agent replies, such
as reading onboarding, checking a local service, or loading relevant wiki pages.

`forbidden` is for actions or claims that must not happen, such as bypassing
commit hooks or inventing verification results.

`required` is for evidence or operations that must be included, such as running
a named smoke test or reporting remaining dirty files.

The three rule groups are optional inside `rules` so a policy can be narrow.
The `rules` object itself is required so an empty metadata-only policy cannot
pretend to control behavior.

## Conflict Ordering

`precedence` is an integer used when multiple policies apply to the same agent
or task. Higher precedence wins if a future policy engine needs to resolve
conflicts.

v0.1 only stores `precedence`; it does not implement conflict resolution.

## Versioning

`version` uses a semver-like string. It is optional in v0.1 because many
policies will start as local drafts, but adding it early helps future migration.

## Valid Example

```yaml
policy_id: "read-wiki-before-answer"
applies_to: [codex, cascade]
description: "Agents must load relevant TigerMemory context before answering."
precedence: 50
version: "0.1.0"
rules:
  before_answer:
    - "Run get_agent_onboarding or equivalent local snapshot."
  forbidden:
    - "Do not claim a service is down without checking the live endpoint."
  required:
    - "Report verified state separately from inference."
```

## Invalid Example

This instance is invalid because `applies_to` contains an unknown agent:

```yaml
policy_id: "bad-agent"
applies_to: [unknown-agent]
rules:
  required:
    - "Use evidence."
```

## v0.1 Notes

This protocol is intentionally narrower than a full IDE configuration compiler.
It records behavior rules in a portable shape, while actual IDE-specific
translation remains a later Gate 3 task.
