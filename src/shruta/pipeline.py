from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .agents import AgentError, run_agent
from .models import ParsedTranscript, TranscriptTurn
from .parser import parse_capture, seconds_to_clock

CAPTURE_SUFFIXES = {".vtt", ".json", ".txt"}
GENERIC_TITLES = {"calendar", "chat", "meeting", "recap", "transcript"}


@dataclass(slots=True)
class ProcessResult:
    raw_path: Path
    evidence_path: Path | None
    insights_path: Path | None
    status: str
    warnings: list[str]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_metadata(raw_path: Path) -> dict:
    sidecar = raw_path.with_suffix(".meta.json")
    if not sidecar.exists():
        return {}
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def derive_date_title(raw_path: Path, metadata: dict) -> tuple[str, str]:
    name = raw_path.stem
    date = str(metadata.get("date") or "").strip()
    if not date:
        match = re.match(r"^(\d{4}-\d{2}-\d{2})", name)
        date = match.group(1) if match else datetime.now().astimezone().date().isoformat()
    title = str(metadata.get("title") or "").strip()
    if not title:
        title = re.sub(r"^\d{4}-\d{2}-\d{2}(?:_\d{6})?_?", "", name)
        title = re.sub(r"[_-]+", " ", title).strip() or "Meeting"
    return date, title


def yaml_string(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _turn_line(turn: TranscriptTurn) -> str:
    timestamp = turn.timestamp or "?:??"
    return f"- [{timestamp}] {turn.speaker}: {turn.text}"


def assess_capture(parsed: ParsedTranscript, *, title: str, metadata: dict) -> list[str]:
    warnings = list(parsed.warnings)
    if title.casefold().strip() in GENERIC_TITLES:
        warnings.append("generic meeting title; correct the sidecar title for precise retrieval")
    source = str(metadata.get("source") or "")
    if source.startswith("dom-"):
        if "reachedBottom" not in metadata:
            warnings.append("legacy DOM capture has no recorded end-of-scan verification")
        elif not metadata.get("reachedBottom"):
            warnings.append("DOM scan did not confirm that it reached the transcript end")
    expected = metadata.get("turnCount")
    if isinstance(expected, int) and expected >= 5 and len(parsed.turns) < expected * 0.8:
        warnings.append(
            f"parsed {len(parsed.turns)} unique turns from {expected} captured rows; review overlap or loss"
        )
    return list(dict.fromkeys(warnings))


def render_evidence(
    parsed: ParsedTranscript,
    *,
    title: str,
    date: str,
    metadata: dict,
    scope: str,
    raw_sha256: str,
    window_minutes: int = 5,
) -> str:
    warnings = assess_capture(parsed, title=title, metadata=metadata)
    first = seconds_to_clock(parsed.first_seconds) or "unknown"
    last = seconds_to_clock(parsed.last_seconds) or "unknown"
    quality = "review" if warnings else "good"
    people = list(dict.fromkeys(turn.speaker for turn in parsed.turns if turn.speaker != "Unknown"))
    lines = [
        "---",
        f"title: {yaml_string(title)}",
        f"date: {date}",
        f"scope: {yaml_string(scope)}",
        "document_type: transcript-evidence",
        "status: active",
        f"capture_quality: {quality}",
        f"capture_format: {parsed.capture_format}",
        f"capture_source: {yaml_string(metadata.get('source', 'unknown'))}",
        f"source_file: {yaml_string(parsed.source_path.name)}",
        f"source_sha256: {raw_sha256}",
        f"turn_count: {len(parsed.turns)}",
        f"first_timestamp: {yaml_string(first)}",
        f"last_timestamp: {yaml_string(last)}",
    ]
    if people:
        lines.append("people:")
        lines.extend(f"  - {yaml_string(person)}" for person in people)
    lines.extend(
        [
            "---",
            "",
            f"# {title}",
            "",
            "This note is deterministic, timestamped evidence generated from the captured transcript.",
            "",
        ]
    )
    if warnings:
        lines.extend(["## Capture review", ""])
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")

    current_window: int | None = None
    untimed_open = False
    for turn in parsed.turns:
        if turn.seconds is None:
            if not untimed_open:
                lines.extend(["## Untimed", ""])
                untimed_open = True
        else:
            window = int(turn.seconds) // (window_minutes * 60)
            if window != current_window:
                if lines and lines[-1] != "":
                    lines.append("")
                start = window * window_minutes * 60
                end = start + window_minutes * 60 - 1
                lines.extend(
                    [
                        f"## {seconds_to_clock(start)}–{seconds_to_clock(end)}",
                        "",
                    ]
                )
                current_window = window
                untimed_open = False
        lines.append(_turn_line(turn))
    return "\n".join(lines).rstrip() + "\n"


def _transcript_for_agent(turns: Iterable[TranscriptTurn]) -> str:
    return "\n".join(_turn_line(turn) for turn in turns)


def build_agent_prompt(*, title: str, date: str, turns: list[TranscriptTurn], language: str) -> str:
    transcript = _transcript_for_agent(turns)
    return f"""Create a concise retrieval-ready insight note from the transcript below.

Return Markdown only, with no YAML frontmatter and no code fence. Write in {language}.

Use only these sections when they contain evidence:
# {title}
## Summary
## Key Decisions
## Action Items
## Key Data
## Open Questions

Evidence rules:
- Every decision, action item, data point, and open question must end with one or more exact
  transcript timestamp citations such as [12:34].
- Use only timestamps present in the transcript.
- Do not invent names, owners, deadlines, decisions, or numbers.
- Preserve uncertainty and disagreements.
- Keep the Summary to one compact paragraph with timestamp citations.
- Use a Markdown table for Action Items with columns Owner, Task, Deadline, Evidence.
- If the capture appears incomplete, say so in Summary.

Meeting date: {date}

===== TIMESTAMPED TRANSCRIPT =====
{transcript}
"""


_CITATION_RE = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]")


def validate_agent_output(output: str, parsed: ParsedTranscript) -> list[str]:
    errors: list[str] = []
    if len(output.strip()) < 120:
        errors.append("insight note is too short")
    if not output.lstrip().startswith("# "):
        errors.append("insight note must start with a title heading")
    valid_timestamps = {turn.timestamp for turn in parsed.turns if turn.timestamp}
    citations = _CITATION_RE.findall(output)
    if not citations:
        errors.append("insight note has no timestamp citations")
    invalid = sorted(set(citations) - valid_timestamps)
    if invalid:
        errors.append("insight note cites timestamps absent from evidence: " + ", ".join(invalid))
    return errors


def render_insights(
    body: str,
    *,
    title: str,
    date: str,
    scope: str,
    evidence_path: Path,
    raw_sha256: str,
    agent_provider: str,
    agent_model: str | None,
    people: list[str],
) -> str:
    body = re.sub(r"^---\s*.*?\s*---\s*", "", body, flags=re.DOTALL).strip()
    frontmatter = [
        "---",
        f"title: {yaml_string(title)}",
        f"date: {date}",
        f"scope: {yaml_string(scope)}",
        "document_type: transcript-insights",
        "status: active",
        f"source_evidence: {yaml_string(evidence_path.name)}",
        f"source_sha256: {raw_sha256}",
        f"agent_provider: {yaml_string(agent_provider)}",
        f"agent_model: {yaml_string(agent_model or 'default')}",
    ]
    if people:
        frontmatter.append("people:")
        frontmatter.extend(f"  - {yaml_string(person)}" for person in people)
    return "\n".join(
        [
            *frontmatter,
            "---",
            "",
            body,
            "",
        ]
    )


def process_capture(
    raw_path: Path,
    *,
    workspace: Path,
    scope: str = "transcripts",
    agent_provider: str = "none",
    agent_model: str | None = None,
    agent_reasoning: str = "medium",
    agent_base_url: str | None = None,
    language: str = "English",
    force: bool = False,
) -> ProcessResult:
    raw_path = raw_path.expanduser().resolve()
    workspace = workspace.expanduser().resolve()
    metadata = load_metadata(raw_path)
    date, title = derive_date_title(raw_path, metadata)
    parsed = parse_capture(raw_path)
    warnings = assess_capture(parsed, title=title, metadata=metadata)
    if not parsed.turns:
        return ProcessResult(raw_path, None, None, "failed", warnings)

    evidence_dir = workspace / "evidence"
    insights_dir = workspace / "insights"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    stem = raw_path.stem
    evidence_path = evidence_dir / f"{stem}.md"
    insights_path = insights_dir / f"{stem}.md"
    raw_sha256 = sha256_file(raw_path)

    if force or not evidence_path.exists():
        evidence = render_evidence(
            parsed,
            title=title,
            date=date,
            metadata=metadata,
            scope=str(metadata.get("scope") or scope),
            raw_sha256=raw_sha256,
        )
        evidence_path.write_text(evidence, encoding="utf-8")

    if agent_provider.casefold() == "none":
        return ProcessResult(raw_path, evidence_path, None, "evidence-only", warnings)
    if insights_path.exists() and not force:
        return ProcessResult(raw_path, evidence_path, insights_path, "already-processed", warnings)

    prompt = build_agent_prompt(title=title, date=date, turns=parsed.turns, language=language)
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            attempt_prompt = prompt
            if attempt and last_error:
                attempt_prompt = (
                    prompt
                    + "\n\nYour previous response failed validation: "
                    + str(last_error)
                    + "\nReturn a corrected note."
                )
            output = run_agent(
                attempt_prompt,
                provider=agent_provider,
                model=agent_model,
                reasoning=agent_reasoning,
                base_url=agent_base_url,
            )
            validation_errors = validate_agent_output(output, parsed)
            if validation_errors:
                raise AgentError("; ".join(validation_errors))
            insights_dir.mkdir(parents=True, exist_ok=True)
            insights = render_insights(
                output,
                title=title,
                date=date,
                scope=str(metadata.get("scope") or scope),
                evidence_path=evidence_path,
                raw_sha256=raw_sha256,
                agent_provider=agent_provider,
                agent_model=agent_model,
                people=list(
                    dict.fromkeys(
                        turn.speaker for turn in parsed.turns if turn.speaker != "Unknown"
                    )
                ),
            )
            insights_path.write_text(insights, encoding="utf-8")
            return ProcessResult(raw_path, evidence_path, insights_path, "processed", warnings)
        except Exception as exc:  # noqa: BLE001 - provider failures never discard evidence
            last_error = exc
    warnings.append(f"AI insight generation failed: {last_error}")
    return ProcessResult(raw_path, evidence_path, None, "evidence-only", warnings)


def iter_captures(raw_dir: Path) -> Iterable[Path]:
    if not raw_dir.exists():
        return []
    priority = {".vtt": 3, ".txt": 2, ".json": 1}
    selected: dict[str, Path] = {}
    for path in sorted(raw_dir.iterdir()):
        suffix = path.suffix.casefold()
        if not path.is_file() or suffix not in CAPTURE_SUFFIXES or path.name.endswith(".meta.json"):
            continue
        current = selected.get(path.stem)
        if current is None or priority[suffix] > priority[current.suffix.casefold()]:
            selected[path.stem] = path
    return iter(selected[stem] for stem in sorted(selected))


def sweep_downloads(download_dir: Path, raw_dir: Path) -> list[Path]:
    """Move complete Chrome capture groups into raw storage; metadata goes first."""
    download_dir = download_dir.expanduser()
    raw_dir = raw_dir.expanduser()
    raw_dir.mkdir(parents=True, exist_ok=True)
    groups: dict[str, list[Path]] = {}
    for content in sorted(download_dir.iterdir()) if download_dir.exists() else []:
        if content.suffix.casefold() not in CAPTURE_SUFFIXES or content.name.endswith(".meta.json"):
            continue
        groups.setdefault(content.stem, []).append(content)

    moved: list[Path] = []
    priority = {".vtt": 3, ".txt": 2, ".json": 1}
    for stem, contents in sorted(groups.items()):
        sidecar = download_dir / f"{stem}.meta.json"
        if sidecar.exists():
            shutil.move(str(sidecar), raw_dir / sidecar.name)
        destinations: list[Path] = []
        for content in contents:
            destination = raw_dir / content.name
            shutil.move(str(content), destination)
            destinations.append(destination)
        moved.append(max(destinations, key=lambda path: priority[path.suffix.casefold()]))
    return moved
