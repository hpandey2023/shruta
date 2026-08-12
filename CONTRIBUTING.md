# Contributing

Use synthetic transcripts for development, tests, issues, and pull requests. Never commit meeting URLs, tenant names, credentials, private paths, or real transcript content.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/pytest
node --check extension/background.js
node --check extension/capture.js
node --check extension/hook.js
```

Capture changes should preserve these invariants:

- The page's requests and responses are never modified.
- Metadata downloads before transcript content so folder watchers see complete groups.
- Raw captures are never silently deleted.
- AI output cannot replace deterministic evidence.
- Tests and examples contain only synthetic data.
