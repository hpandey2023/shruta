import json
from pathlib import Path

from shruta.models import ParsedTranscript, TranscriptTurn
from shruta.pipeline import iter_captures, process_capture, validate_agent_output


def _write_capture(raw_dir: Path, stem: str) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{stem}.txt"
    path.write_text(
        "Alex Example\n0 minutes 3 seconds\n0:03\n"
        "Alex Example 0 minutes 3 seconds\nWe approved option A.\n"
        "Blair Example\n0 minutes 8 seconds\n0:08\n"
        "Blair Example 0 minutes 8 seconds\nI will finish it Friday.\n",
        encoding="utf-8",
    )
    path.with_suffix(".meta.json").write_text(
        json.dumps({"title": "Weekly Review", "source": "dom-timestamp-rows-auto"}),
        encoding="utf-8",
    )
    return path


def test_evidence_is_deterministic_and_cited(tmp_path: Path):
    raw = _write_capture(tmp_path / "raw", "2026-08-12_090000_Weekly_Review")
    result = process_capture(raw, workspace=tmp_path, agent_provider="none")
    assert result.status == "evidence-only"
    text = result.evidence_path.read_text(encoding="utf-8")
    assert "document_type: transcript-evidence" in text
    assert "turn_count: 2" in text
    assert "- [0:03] Alex Example: We approved option A." in text
    assert "source_sha256:" in text


def test_same_title_captures_do_not_overwrite(tmp_path: Path):
    one = _write_capture(tmp_path / "raw", "2026-08-12_090000_Weekly_Review")
    two = _write_capture(tmp_path / "raw", "2026-08-12_100000_Weekly_Review")
    first = process_capture(one, workspace=tmp_path, agent_provider="none")
    second = process_capture(two, workspace=tmp_path, agent_provider="none")
    assert first.evidence_path != second.evidence_path
    assert first.evidence_path.exists() and second.evidence_path.exists()


def test_vtt_is_preferred_when_network_capture_also_has_raw_json(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "capture.json").write_text('{"entries": []}', encoding="utf-8")
    (raw_dir / "capture.vtt").write_text("WEBVTT\n", encoding="utf-8")
    assert list(iter_captures(raw_dir)) == [raw_dir / "capture.vtt"]


def test_agent_citations_must_exist_in_evidence(tmp_path: Path):
    parsed = ParsedTranscript(
        tmp_path / "raw.txt",
        [TranscriptTurn("Alex", "Approved", 3, "0:03")],
        "teams-dom",
    )
    assert not validate_agent_output("# Note\n\n## Summary\n" + "A" * 120 + " [0:03]", parsed)
    assert any(
        "absent" in error
        for error in validate_agent_output("# Note\n\n## Summary\n" + "A" * 120 + " [9:59]", parsed)
    )
