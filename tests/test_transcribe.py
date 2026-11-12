"""Transcript parsing, SRT round-trips and time formatting."""
from __future__ import annotations

import pytest

from src.transcribe.models import Segment, Word, format_timestamp, parse_timestamp
from src.transcribe.srt import parse_srt_text, transcript_from_plain_text, write_srt


def test_parse_basic_srt():
    content = """1
00:00:01,000 --> 00:00:04,500
Hello and welcome to the show.

2
00:00:05,000 --> 00:00:08,000
Today we talk about clipping.
"""
    transcript = parse_srt_text(content)
    assert len(transcript.segments) == 2
    assert transcript.segments[0].start == pytest.approx(1.0)
    assert transcript.segments[0].end == pytest.approx(4.5)
    assert transcript.segments[1].text.startswith("Today")


def test_parse_srt_tolerates_bom_crlf_and_markup():
    content = (
        "\ufeff1\r\n00:00:00,500 --> 00:00:02,000\r\n<i>Hello</i> there\r\n\r\n"
        "2\r\n00:00:02,100 --> 00:00:03,000\r\nSecond line\r\n"
    )
    transcript = parse_srt_text(content)
    assert len(transcript.segments) == 2
    assert transcript.segments[0].text == "Hello there"


def test_parse_srt_skips_invalid_and_sorts():
    content = """2
00:00:10,000 --> 00:00:12,000
Later line

1
00:00:01,000 --> 00:00:02,000
Earlier line

broken block without a timecode
"""
    transcript = parse_srt_text(content)
    assert [s.text for s in transcript.segments] == ["Earlier line", "Later line"]


def test_vtt_style_timecodes():
    content = """WEBVTT

00:01.000 --> 00:03.000
Short form timecode
"""
    transcript = parse_srt_text(content)
    assert len(transcript.segments) == 1
    assert transcript.segments[0].start == pytest.approx(1.0)


def test_write_srt_roundtrip(tmp_path):
    segments = [
        Segment(start=1.25, end=3.75, text="First"),
        Segment(start=4.0, end=6.5, text="Second"),
    ]
    path = write_srt(segments, tmp_path / "out.srt")
    parsed = parse_srt_text(path.read_text(encoding="utf-8"))
    assert len(parsed.segments) == 2
    assert parsed.segments[0].start == pytest.approx(1.25, abs=0.002)
    assert parsed.segments[1].text == "Second"


def test_transcript_from_plain_text_spreads_over_duration():
    text = "First sentence. Second sentence. Third sentence."
    transcript = transcript_from_plain_text(text, 90.0)
    assert len(transcript.segments) == 3
    assert transcript.segments[0].start == 0.0
    assert transcript.segments[-1].end == pytest.approx(90.0, abs=0.5)
    # Monotonic, non-overlapping
    for previous, current in zip(transcript.segments[:-1], transcript.segments[1:]):
        assert current.start >= previous.start
        assert previous.end <= current.end


def test_timestamp_helpers():
    assert format_timestamp(3661.5) == "01:01:01"
    assert format_timestamp(3661.5, srt=True).startswith("01:01:01,500")
    assert parse_timestamp("00:01:05,250") == pytest.approx(65.25)
    assert parse_timestamp("12.5") == pytest.approx(12.5)
    assert parse_timestamp("") == 0.0


def test_segment_metrics():
    segment = Segment(start=0, end=2, text="one two\nthree", words=[Word(0, 0.5, "one")])
    assert segment.duration == pytest.approx(2.0)
    assert segment.char_count == len("onetwothree")
    data = segment.to_dict()
    assert data["words"][0]["text"] == "one"
    restored = Segment.from_dict(data)
    assert restored.text == segment.text
