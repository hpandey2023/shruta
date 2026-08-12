from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TranscriptTurn:
    speaker: str
    text: str
    seconds: float | None = None
    timestamp: str | None = None


@dataclass(slots=True)
class ParsedTranscript:
    source_path: Path
    turns: list[TranscriptTurn]
    capture_format: str
    warnings: list[str] = field(default_factory=list)

    @property
    def first_seconds(self) -> float | None:
        return next((turn.seconds for turn in self.turns if turn.seconds is not None), None)

    @property
    def last_seconds(self) -> float | None:
        return next(
            (turn.seconds for turn in reversed(self.turns) if turn.seconds is not None), None
        )
