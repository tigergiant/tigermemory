# tigermemory-lessons

`tigermemory-lessons` is the standalone package for TigerMemory's deterministic prevention-lesson search.

It scans `wiki/self-evolution/lessons/*.md`, scores matches by title, aliases, and body hits, and appends the same local preflight usage log as the legacy `tools/tm_lessons.py` entrypoint.

## Usage

```powershell
pip install -e .\packages\tigermemory-core
pip install -e .\packages\tigermemory-lessons
tigermemory-lessons search "commit push"
py tools\tm_lessons.py search "commit push"
```

The `tools/tm_lessons.py` script remains as a compatibility shim for existing docs, hooks, and agent onboarding instructions.
