from __future__ import annotations

import html
import json
import re
from itertools import pairwise
from pathlib import Path
from typing import Any

from .models import ParsedTranscript, TranscriptTurn

_CLOCK_RE = re.compile(r"^(?P<a>\d{1,2}):(?P<b>\d{2})(?::(?P<c>\d{2}))?(?:[.,]\d+)?$")
_DURATION_PART_RE = re.compile(
    r"(?P<value>\d+)\s+(?P<unit>hours?|minutes?|seconds?)", re.IGNORECASE
)
_DURATION_ONLY_RE = re.compile(r"^(?:\d+\s+(?:hours?|minutes?|seconds?)(?:\s+|$))+$", re.IGNORECASE)
_NAME_DURATION_RE = re.compile(
    r"^(?P<name>.+?)\s+(?P<duration>(?:\d+\s+(?:hours?|minutes?|seconds?)"
    r"(?:\s+|$))+)$",
    re.IGNORECASE,
)
_VTT_TIMING_RE = re.compile(r"^\s*(\S+)\s+-->\s+(\S+)")
_VTT_VOICE_RE = re.compile(r"<v(?:\.[^\s>]*)?\s+([^>]+)>(.*?)(?:</v>)?$", re.DOTALL)

_SPEAKER_KEYS = (
    "speakerDisplayName",
    "displayName",
    "speakerName",
    "speaker",
    "userDisplayName",
    "name",
)
_TEXT_KEYS = ("text", "spokenText", "content", "displayText", "transcriptText", "caption")
_START_KEYS = ("startOffset", "startTime", "offset", "start", "startDateTime")

_NOISE_EXACT = {
    "download",
    "ai-generated content may be incorrect",
    "transcript",
}


def _clean_text(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", value))
    return re.sub(r"\s+", " ", value).strip()


def seconds_to_clock(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def clock_to_seconds(value: str | None) -> float | None:
    if not value:
        return None
    raw = value.strip().replace(",", ".")
    match = _CLOCK_RE.match(raw)
    if match:
        a, b, c = match.group("a"), match.group("b"), match.group("c")
        if c is None:
            return int(a) * 60 + int(b)
        return int(a) * 3600 + int(b) * 60 + int(c)
    iso = re.match(
        r"^PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?$",
        raw,
        re.IGNORECASE,
    )
    if iso:
        hours, minutes, seconds = (float(part or 0) for part in iso.groups())
        return hours * 3600 + minutes * 60 + seconds
    try:
        return float(raw)
    except ValueError:
        return None


def duration_to_seconds(value: str) -> float | None:
    parts = list(_DURATION_PART_RE.finditer(value))
    if not parts:
        return None
    total = 0
    for part in parts:
        amount = int(part.group("value"))
        unit = part.group("unit").lower()
        total += amount * (
            3600 if unit.startswith("hour") else 60 if unit.startswith("minute") else 1
        )
    return float(total)


def _deduplicate(
    turns: list[TranscriptTurn], *, chronological: bool = False
) -> list[TranscriptTurn]:
    seen: set[tuple[str, int | None, str]] = set()
    result: list[TranscriptTurn] = []
    for turn in turns:
        key = (
            turn.speaker.casefold(),
            int(turn.seconds) if turn.seconds is not None else None,
            re.sub(r"\s+", " ", turn.text).casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(turn)
    if chronological:
        # Teams virtualizes the transcript list and can re-render an overlapping
        # window while we scroll. Timestamps are the canonical order; Python's
        # stable sort preserves capture order for simultaneous speakers.
        result.sort(key=lambda turn: turn.seconds if turn.seconds is not None else float("inf"))
    return result


def parse_dom_timestamp_rows(text: str) -> list[TranscriptTurn]:
    """Parse Teams DOM text, including virtualized rows and whole-frame fallback text."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    turns: list[TranscriptTurn] = []
    current_speaker: str | None = None
    current_seconds: float | None = None
    current_text: list[str] = []
    pending_clock: float | None = None

    def flush() -> None:
        nonlocal current_speaker, current_seconds, current_text
        spoken = _clean_text(" ".join(current_text))
        if current_speaker and spoken:
            turns.append(
                TranscriptTurn(
                    speaker=current_speaker,
                    text=spoken,
                    seconds=current_seconds,
                    timestamp=seconds_to_clock(current_seconds),
                )
            )
        current_speaker = None
        current_seconds = None
        current_text = []

    for index, line in enumerate(lines):
        lowered = line.casefold()
        if lowered in _NOISE_EXACT or "use arrow keys to navigate" in lowered:
            continue
        if "don't have permission to download" in lowered or "started transcription" in lowered:
            continue
        if _CLOCK_RE.match(line):
            pending_clock = clock_to_seconds(line)
            continue
        name_duration = _NAME_DURATION_RE.match(line)
        if name_duration:
            flush()
            current_speaker = name_duration.group("name").strip()
            current_seconds = pending_clock
            if current_seconds is None:
                current_seconds = duration_to_seconds(name_duration.group("duration"))
            pending_clock = None
            continue
        if _DURATION_ONLY_RE.match(line):
            continue
        if len(line) <= 3 and line.isalpha() and line.upper() == line:
            continue
        # The standalone speaker row immediately preceding duration/name+duration is
        # redundant. It is safely ignored because a turn only begins at name+duration.
        if index + 1 < len(lines) and _DURATION_ONLY_RE.match(lines[index + 1]):
            continue
        if current_speaker is None:
            continue
        current_text.append(line)
    flush()
    return _deduplicate(turns, chronological=True)


def parse_vtt(text: str) -> list[TranscriptTurn]:
    turns: list[TranscriptTurn] = []
    cue_start: float | None = None
    cue_lines: list[str] = []
    previous_speaker = "Unknown"

    def flush() -> None:
        nonlocal cue_lines, previous_speaker
        if not cue_lines:
            return
        raw = " ".join(cue_lines).strip()
        voice = _VTT_VOICE_RE.search(raw)
        if voice:
            speaker = _clean_text(voice.group(1)) or "Unknown"
            spoken = _clean_text(voice.group(2))
        else:
            plain = _clean_text(raw)
            labelled = re.match(r"^([^:]{2,80}):\s*(.+)$", plain)
            if labelled:
                speaker, spoken = labelled.group(1).strip(), labelled.group(2).strip()
            else:
                speaker, spoken = previous_speaker, plain
        if spoken:
            turns.append(TranscriptTurn(speaker, spoken, cue_start, seconds_to_clock(cue_start)))
            previous_speaker = speaker
        cue_lines = []

    for raw_line in text.replace("\ufeff", "").splitlines():
        line = raw_line.strip()
        timing = _VTT_TIMING_RE.match(line)
        if timing:
            flush()
            cue_start = clock_to_seconds(timing.group(1))
            continue
        if not line:
            flush()
            continue
        if line == "WEBVTT" or line.isdigit() or line.startswith(("NOTE", "STYLE", "REGION")):
            continue
        if cue_start is not None:
            cue_lines.append(line)
    flush()
    return _deduplicate(turns)


def _pick(node: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = node.get(key)
        if value not in (None, ""):
            if isinstance(value, dict):
                nested = _pick(value, keys)
                if nested not in (None, ""):
                    return nested
            else:
                return value
    return None


def _find_turn_array(node: Any, depth: int = 0) -> list[dict[str, Any]] | None:
    if depth > 8:
        return None
    if isinstance(node, list):
        objects = [item for item in node if isinstance(item, dict)]
        good = [item for item in objects if _pick(item, _TEXT_KEYS)]
        best = good if len(good) >= 2 else None
        for item in node:
            candidate = _find_turn_array(item, depth + 1)
            if candidate and (best is None or len(candidate) > len(best)):
                best = candidate
        return best
    if isinstance(node, dict):
        best = None
        for value in node.values():
            candidate = _find_turn_array(value, depth + 1)
            if candidate and (best is None or len(candidate) > len(best)):
                best = candidate
        return best
    return None


def parse_json(text: str) -> list[TranscriptTurn]:
    payload = json.loads(text)
    entries = _find_turn_array(payload)
    if not entries:
        raise ValueError("JSON payload has no recognizable transcript turn array")
    turns: list[TranscriptTurn] = []
    for entry in entries:
        spoken = _clean_text(str(_pick(entry, _TEXT_KEYS) or ""))
        if not spoken:
            continue
        speaker = _clean_text(str(_pick(entry, _SPEAKER_KEYS) or "Unknown"))
        seconds = clock_to_seconds(str(_pick(entry, _START_KEYS) or ""))
        turns.append(TranscriptTurn(speaker, spoken, seconds, seconds_to_clock(seconds)))
    return _deduplicate(turns)


def parse_plain_text(text: str) -> list[TranscriptTurn]:
    """Preserve manual or phone transcripts that have no timestamp scaffold."""
    turns: list[TranscriptTurn] = []
    for raw_line in text.splitlines():
        line = _clean_text(raw_line)
        if not line:
            continue
        labelled = re.match(r"^([^:]{2,80}):\s*(.+)$", line)
        if labelled:
            turns.append(TranscriptTurn(labelled.group(1).strip(), labelled.group(2).strip()))
        else:
            turns.append(TranscriptTurn("Unknown", line))
    if not turns and _clean_text(text):
        turns.append(TranscriptTurn("Unknown", _clean_text(text)))
    return turns


def parse_capture(path: Path) -> ParsedTranscript:
    text = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.casefold()
    if suffix == ".vtt" or "-->" in text[:2000] or "<v " in text[:2000]:
        turns, capture_format = parse_vtt(text), "webvtt"
    elif suffix == ".json":
        turns, capture_format = parse_json(text), "json"
    elif suffix == ".txt":
        turns = parse_dom_timestamp_rows(text)
        capture_format = "teams-dom"
        if not turns:
            turns = parse_plain_text(text)
            capture_format = "plain-text"
    else:
        raise ValueError(f"unsupported capture extension: {path.suffix}")

    warnings: list[str] = []
    if not turns:
        warnings.append("no transcript turns were parsed")
    timed = [turn.seconds for turn in turns if turn.seconds is not None]
    if len(timed) < max(1, len(turns) // 2):
        warnings.append("fewer than half of the turns have timestamps")
    if any(current < previous for previous, current in pairwise(timed)):
        warnings.append("timestamps are not monotonic")
    if timed and timed[0] > 120:
        warnings.append(f"capture begins at {seconds_to_clock(timed[0])}, not near meeting start")
    return ParsedTranscript(path, turns, capture_format, warnings)
