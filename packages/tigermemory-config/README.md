# tigermemory-config

`tigermemory-config` is TigerMemory's Gate 3 configuration helper. It has two layers:

- a read-only configuration explainer
- Runtime Config Manager v0 for approved OpenClaw / Hermes policy entrypoints

The explainer scans a local repository for common IDE and agent configuration surfaces, then explains them in Chinese:

- what tool or agent the file likely targets
- whether it is a soft prompt, partial control, or hook-backed guard
- which user-visible controls it appears to express
- what risks remain

The explainer never writes config files, applies diffs, calls IDEs, invokes LLMs, or touches the network. The manager writes only when explicitly invoked with `manager apply --yes`.

```powershell
pip install -e ./packages/tigermemory-config
tigermemory-config --root D:\tigermemory --json
tigermemory-config explain --root D:\tigermemory --json
tigermemory-config manager plan --runtime openclaw --runtime hermes --json
tigermemory-config manager apply --runtime openclaw --runtime hermes --yes
tigermemory-config manager verify --snapshot-id <snapshot-id> --json
tigermemory-config manager rollback --snapshot-id <snapshot-id> --runtime openclaw --dry-run --json
```

Manager v0 applies only to OpenClaw / Hermes and writes managed blocks only to text policy files. Hermes `config.yaml` is backed up and verified readable, but v0 does not write policy into YAML comments.
