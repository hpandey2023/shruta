# Shruta

Shruta turns Microsoft Teams recap transcripts into focused, cited context for AI agents. Its name comes from the Sanskrit *śruta*: something heard, learned, or reported. The Chrome extension captures the transcript you can already view, the local processor converts it into timestamped evidence, and an optional agent creates a smaller insight note whose claims must cite that evidence. [Saar](https://github.com/hpandey2023/saar) can then retrieve only the relevant passages instead of loading entire transcripts into an agent's context window.

> [!IMPORTANT]
> Shruta works only with Microsoft Teams meeting recaps opened in Google Chrome. It does not run inside or capture from the Microsoft Teams desktop application. You can use the desktop app for meetings, but you must open the meeting recap and its Transcript tab in Chrome for Shruta to capture it.

We built this after transcripts and project discussions had grown across too many files to connect reliably by hand. Passing everything to an agent wasted context; selecting files manually was slow and easy to miss. This pipeline keeps the verbatim record available while giving retrieval a compact, traceable layer above it.

```text
Teams recap in Chrome → Shruta capture → raw local file
                                         ├─ timestamped evidence (deterministic)
                                         └─ cited insight note (optional agent)
                                                     ↓
                                                Saar / MCP
                                                     ↓
                                                  agents
```

## What we verified

- A live 26-minute recap was captured from start to finish with 289 rendered turns.
- Six recent captures totaling 1,265 unique parsed turns were checked locally; the longest ran 1:13:25.
- Teams' overlapping virtualized rows are deduplicated and placed in timestamp order.
- The agent path passed a synthetic end-to-end test. Decisions, actions, and data cited real timestamps, while nonexistent citations were rejected.
- The Python suite and JavaScript syntax checks pass without using real transcript data.

These are validation results from one environment, not a guarantee that every Teams tenant renders recap pages identically.

## Install on macOS

Python 3.11 or newer and Chrome are required.

```bash
git clone https://github.com/hpandey2023/shruta.git
cd shruta
./scripts/install-macos.sh --workspace "$HOME/Documents/transcript-context"
```

The installer creates a dedicated virtual environment, installs a local sweeper, and prints the stable extension folder. In Chrome, open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**, and select that folder.

When upgrading from the previous release, load this new extension folder and remove the old unpacked extension after Shruta captures successfully. The installer disables the previous sweeper but leaves existing captures and application files untouched.

In Chrome, open [Teams on the web](https://teams.microsoft.com), then open a meeting recap and its Transcript tab. Keep that tab open while Shruta scrolls the full transcript; a green `✓` appears only after the scan reaches the end. Chrome briefly writes the capture under `Downloads/shruta-transcripts/`; the local sweeper moves it into:

```text
transcript-context/
├── raw/       # original captures and metadata
├── evidence/  # timestamped, deterministic Markdown
└── insights/  # optional agent-generated notes
```

Run a manual verification at any time:

```bash
"$HOME/Library/Application Support/Shruta/venv/bin/shruta" \
  process "$HOME/Documents/transcript-context/raw" \
  --workspace "$HOME/Documents/transcript-context"
```

The default is evidence-only and does not send transcript text to a model.

## Optional AI processing

The processor supports Codex CLI, Claude CLI, and OpenAI-compatible endpoints. Enable a provider only after confirming that its data handling is appropriate for the transcripts being processed.

```bash
shruta process /path/to/transcript.txt \
  --workspace /path/to/transcript-context \
  --agent codex \
  --model gpt-5.6-terra
```

For a local OpenAI-compatible model server:

```bash
shruta process /path/to/transcript.txt \
  --workspace /path/to/transcript-context \
  --agent openai-compatible \
  --base-url http://127.0.0.1:11434 \
  --model qwen3:8b
```

An evidence note is always written first. If the agent fails, returns an empty answer, or cites a timestamp absent from the transcript, the evidence remains available and the insight note is not accepted.

## Connect Saar

Copy [examples/saar.yaml](examples/saar.yaml), replace the two paths, and run:

```bash
saar --config /absolute/path/saar.yaml doctor
saar --config /absolute/path/saar.yaml index
saar --config /absolute/path/saar.yaml search "What was decided?" \
  --document-type transcript-evidence
```

Add Saar to each MCP-compatible client that should use it, then give the agent this rule:

```text
When work may depend on meeting or project context, call search_context first.
Prefer transcript-evidence for factual claims, cite the returned source, and say when evidence is incomplete.
```

MCP makes the tool available; it does not force every agent to call it. Each client still needs its own MCP configuration and instruction.

## Capture behavior

The Chrome extension uses two read-only routes inside Teams web recap pages:

- A network hook mirrors transcript payloads when the recap fetches recognizable VTT or JSON.
- A DOM scanner handles tenants that render a virtualized transcript inside a cross-origin SharePoint frame. It identifies the transcript scroller, walks from top to bottom, records scan completion, and saves rendered rows locally.

The service worker keeps only bounded hashes and capture diagnostics in Chrome's in-memory session storage. Transcript content is written to the local download relay, not uploaded by the extension.

## Limits and responsible use

- Use this only for transcripts you are authorized to view and retain.
- Teams web markup can change. A `capture_quality: review` evidence note means the title or scan completeness needs attention.
- Capturing what a user can view does not override retention, confidentiality, consent, or records-management rules.
- Remote agent and embedding providers receive the text they process. The evidence-only processor and Saar's local provider are available when data must remain on the machine.

MIT licensed.
