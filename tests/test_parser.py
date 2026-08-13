from pathlib import Path

from shruta.parser import (
    parse_capture,
    parse_dom_timestamp_rows,
    parse_json,
    parse_plain_text,
    parse_vtt,
)


def test_dom_parser_orders_and_deduplicates_virtualized_rows():
    text = """Alex Example
0 minutes 3 seconds
0:03
Alex Example 0 minutes 3 seconds
Opening point.
AE
Blair Example
0 minutes 8 seconds
0:08
Blair Example 0 minutes 8 seconds
Reply.
Alex Example
0 minutes 3 seconds
0:03
Alex Example 0 minutes 3 seconds
Opening point.
Alex Example 0 minutes 12 seconds
Final point.
"""
    turns = parse_dom_timestamp_rows(text)
    assert [(turn.speaker, turn.timestamp, turn.text) for turn in turns] == [
        ("Alex Example", "0:03", "Opening point."),
        ("Blair Example", "0:08", "Reply."),
        ("Alex Example", "0:12", "Final point."),
    ]


def test_vtt_parser_preserves_speaker_and_timestamp():
    turns = parse_vtt(
        """WEBVTT

00:00:03.000 --> 00:00:06.000
<v Alex Example>Hello there.</v>

00:00:06.000 --> 00:00:08.000
Second line.
"""
    )
    assert turns[0].speaker == "Alex Example"
    assert turns[0].timestamp == "0:03"
    assert turns[1].speaker == "Alex Example"


def test_json_parser_finds_nested_turn_array():
    turns = parse_json(
        '{"data":{"entries":['
        '{"speakerDisplayName":"Alex","text":"One","startTime":"0:03"},'
        '{"speakerDisplayName":"Blair","text":"Two","startTime":"0:07"}'
        "]}}"
    )
    assert [(turn.speaker, turn.timestamp) for turn in turns] == [
        ("Alex", "0:03"),
        ("Blair", "0:07"),
    ]


def test_parse_capture_warns_when_capture_starts_late(tmp_path: Path):
    path = tmp_path / "2026-08-12_sample.txt"
    path.write_text(
        "Alex Example\n3 minutes 10 seconds\n3:10\n"
        "Alex Example 3 minutes 10 seconds\nLate start.\n",
        encoding="utf-8",
    )
    parsed = parse_capture(path)
    assert any("not near meeting start" in warning for warning in parsed.warnings)


def test_plain_text_is_preserved_as_untimed_evidence():
    turns = parse_plain_text("A manually captured phone conversation without timestamps.")
    assert len(turns) == 1
    assert turns[0].speaker == "Unknown"
    assert turns[0].text.startswith("A manually captured")
