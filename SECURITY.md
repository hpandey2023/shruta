# Security and privacy

## Data flow

The Chrome extension reads transcript material from supported Teams recap pages opened in Google Chrome and writes it under the browser's local Downloads directory. It does not run inside the Microsoft Teams desktop application. It contains no analytics, remote logging, or upload endpoint.

The Python processor is local by default. `--agent none` produces deterministic evidence without sending transcript text to a model. Codex, Claude, and remote OpenAI-compatible providers may transmit the prompt and transcript to their configured service. A locally hosted compatible endpoint can keep processing on the machine.

Saar provider choices have a separate data boundary. Local FastEmbed or Ollama configurations stay local; remote embedding or reranking providers receive the text sent to them.

## Reporting a vulnerability

Open a GitHub security advisory for vulnerabilities. Do not include real transcripts, meeting URLs, credentials, private paths, tenant names, or provider keys in reports or reproductions. Use the synthetic fixture in `tests/fixtures/`.
