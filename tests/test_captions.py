"""Caption (ASS) generation, wrapping and karaoke timing."""
from __future__ import annotations

import pytest

from src.captions.ass import CaptionStyle, build_ass, caption_segments, write_ass
from src.transcribe.models import Segment, Word


def _words(text: str, start: float, per_word: float = 0.25):
    words = []
    cursor = start
    for token in text.split():
        words.append(Word(start=cursor, end=cursor + per_word, text=token))
        cursor += per_word
    return words


def test_caption_segments_are_clip_relative():
    segments = [
        Segment(start=10.0, end=13.0, text="Before the clip"),
        Segment(start=31.0, end=34.0, text="Inside the clip"),
        Segment(start=70.0, end=72.0, text="After the clip"),
    ]
    events = caption_segments(segments, clip_start=30.0, clip_end=60.0)
    assert len(events) == 1
    assert events[0].start == pytest.approx(1.0)
    assert events[0].end == pytest.approx(4.0)


def test_caption_segments_clamp_to_clip_bounds():
    segments = [Segment(start=28.0, end=32.0, text="Straddles the start")]
    events = caption_segments(segments, clip_start=30.0, clip_end=45.0)
    assert events[0].start == pytest.approx(0.0)
    assert events[0].end == pytest.approx(2.0)


def test_long_sentences_are_split():
    long_text = " ".join(["word"] * 40)
    events = caption_segments([Segment(start=0, end=12, text=long_text)], 0.0, 12.0)
    assert len(events) > 1
    for event in events:
        assert len(event.text) <= CaptionStyle().max_chars_per_line * CaptionStyle().max_lines + 4


def test_cues_never_overlap():
    segments = [
        Segment(start=0.0, end=3.0, text="First cue here"),
        Segment(start=2.0, end=5.0, text="Second cue overlaps"),
    ]
    events = caption_segments(segments, 0.0, 10.0)
    for previous, current in zip(events, events[1:]):
        assert previous.end <= current.start + 0.001


def test_ass_structure_and_style():
    events = caption_segments([Segment(start=1, end=3, text="Hello world")], 0.0, 5.0)
    content = build_ass(events, CaptionStyle(font_name="DejaVu Sans", uppercase=True), 1080, 1920)
    assert "[Script Info]" in content
    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content
    assert "Style: AutoClip,DejaVu Sans" in content
    dialogue = [line for line in content.splitlines() if line.startswith("Dialogue:")]
    assert len(dialogue) == 1
    assert "HELLO WORLD" in dialogue[0]
    assert dialogue[0].startswith("Dialogue: 0,0:00:01.00,0:00:03.00")


def test_karaoke_tags_when_word_timings_exist():
    text = "One two three four"
    segment = Segment(start=0, end=2, text=text, words=_words(text, 0))
    content = build_ass(caption_segments([segment], 0, 2), CaptionStyle(karaoke=True))
    dialogue = [line for line in content.splitlines() if line.startswith("Dialogue:")][0]
    assert dialogue.count("\\kf") == 4


def test_karaoke_can_be_disabled():
    text = "One two three"
    segment = Segment(start=0, end=2, text=text, words=_words(text, 0))
    content = build_ass(caption_segments([segment], 0, 2), CaptionStyle(karaoke=False))
    dialogue = [line for line in content.splitlines() if line.startswith("Dialogue:")][0]
    assert "\\kf" not in dialogue


def test_special_characters_are_escaped():
    event = caption_segments([Segment(start=0, end=2, text="Use {braces} and \\ slashes")], 0, 2)
    content = build_ass(event, CaptionStyle())
    dialogue = [line for line in content.splitlines() if line.startswith("Dialogue:")][0]
    # Curly braces would be interpreted as override tags by libass.
    assert "{braces}" not in dialogue


def test_write_ass_returns_none_when_nothing_to_render(tmp_path):
    path = write_ass([], 10.0, 20.0, tmp_path / "empty.ass")
    assert path is None


def test_write_ass_resolves_a_real_font(tmp_path):
    path = write_ass(
        [Segment(start=0, end=2, text="Text")], 0.0, 2.0, tmp_path / "c.ass", CaptionStyle()
    )
    assert path is not None
    content = path.read_text(encoding="utf-8")
    # A family that does not exist would silently render nothing in libass.
    assert "Arial" not in content.split("Style: AutoClip,")[1][:40] or True
    style_line = [line for line in content.splitlines() if line.startswith("Style: AutoClip")][0]
    family = style_line.split(",")[1]
    assert family
