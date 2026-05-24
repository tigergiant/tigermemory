# tigermemory-config

`tigermemory-config` is the first read-only configuration interpreter for TigerMemory's Gate 3 work. It scans a local repository for common IDE and agent configuration surfaces, then explains them in Chinese:

- what tool or agent the file likely targets
- whether it is a soft prompt, partial control, or hook-backed guard
- which user-visible controls it appears to express
- what risks remain

It never writes config files, applies diffs, calls IDEs, invokes LLMs, or touches the network.

```powershell
pip install -e ./packages/tigermemory-config
tigermemory-config --root D:\tigermemory --json
```

This is Phase 1 of the product vision's three-step configuration assistant: explain current config first; suggest diffs and apply changes only in later phases.
