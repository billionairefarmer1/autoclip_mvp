"""
ASS subtitle generation with optional word-level karaoke highlighting.

Timings are emitted relative to the *clip*, not the source video, so the same
helper works for a full-length render and a 20-second cut.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from ..transcribe.models import Segment, Word

logger = logging.getLogger(__name__)

# ASS colours are &HBBGGRR (not RGB).
PRESET_COLOURS = {
    "white": "&H00FFFFFF",
    "yellow": "&H0000FFFF",
    "green": "&H0000FF00",
    "cyan": "&H00FFFF00",
    "orange": "&H000080FF",
    "pink": "&H00B469FF",
    "red": "&H000000FF",
}


@dataclass
class CaptionStyle:
    """Styling for burned-in captions."""

    font_name: str = "Arial"  # resolved to an installed family at render time
    fontsdir: Optional[str] = None
    font_size: int = 74
    primary_colour: str = "white"
    highlight_colour: str = "yellow"
    outline_colour: str = "black"
    outline: float = 5.0
    shadow: float = 1.0
    bold: bool = True
    uppercase: bool = False
    margin_v: int = 340  # pixels from the bottom in a 1920-tall frame
    margin_h: int = 90
    max_chars_per_line: int = 26
    max_lines: int = 2
    max_duration: float = 4.2
    min_duration: float = 0.7
    karaoke: bool = True

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "CaptionStyle":
        data = data or {}
        style = cls()
        mapping = {
            "caption_font": "font_name",
            "caption_font_size": "font_size",
            "caption_colour": "primary_colour",
            "caption_highlight_colour": "highlight_colour",
            "caption_uppercase": "uppercase",
            "caption_karaoke": "karaoke",
            "caption_margin_v": "margin_v",
            "caption_max_chars": "max_chars_per_line",
        }
        for source, target in mapping.items():
            if source in data and data[source] is not None:
                setattr(style, target, data[source])
        try:
            style.font_size = int(style.font_size)
            style.margin_v = int(style.margin_v)
            style.max_chars_per_line = int(style.max_chars_per_line)
        except (TypeError, ValueError):
            pass
        style.uppercase = bool(style.uppercase)
        style.karaoke = bool(style.karaoke)
        return style

    def resolved(self) -> "CaptionStyle":
        """Return a copy whose ``font_name``/``fontsdir`` are known to exist."""
        from .fonts import resolve_font

        if self.fontsdir and Path(self.fontsdir).is_dir():
            return self
        font = resolve_font(self.font_name)
        clone = CaptionStyle(**{**self.__dict__, "font_name": font.family, "fontsdir": font.fontsdir})
        return clone

    def colour(self, name: str) -> str:
        return PRESET_COLOURS.get(str(name).lower(), PRESET_COLOURS["white"])

    def to_dict(self) -> dict:
        return {
            "font_name": self.font_name,
            "font_size": self.font_size,
            "primary_colour": self.primary_colour,
            "highlight_colour": self.highlight_colour,
            "outline": self.outline,
            "uppercase": self.uppercase,
            "margin_v": self.margin_v,
            "max_chars_per_line": self.max_chars_per_line,
            "karaoke": self.karaoke,
        }


@dataclass
class _CaptionEvent:
    start: float
    end: float
    text: str
    words: List[Word] = field(default_factory=list)


def caption_segments(
    segments: Sequence[Segment],
    clip_start: float,
    clip_end: float,
    style: Optional[CaptionStyle] = None,
) -> List[_CaptionEvent]:
    """Select and reshape transcript segments to fit the clip."""
    style = style or CaptionStyle()
    events: List[_CaptionEvent] = []

    for segment in segments:
        if segment.end <= clip_start or segment.start >= clip_end:
            continue
        text = segment.text.strip()
        if not text:
            continue

        # Shift to clip-relative time and clamp to the clip bounds.
        start = max(0.0, segment.start - clip_start)
        end = min(clip_end - clip_start, segment.end - clip_start)
        if end - start < 0.12:
            continue

        words = [
            Word(
                start=max(0.0, w.start - clip_start),
                end=min(clip_end - clip_start, w.end - clip_start),
                text=w.text,
            )
            for w in segment.words
            if w.end > clip_start and w.start < clip_end
        ]

        # Split long segments so captions keep up with speech.
        pieces = _split_text(text, style)
        if len(pieces) == 1:
            events.append(_CaptionEvent(start=start, end=end, text=pieces[0], words=words))
            continue

        total_chars = sum(len(p) for p in pieces) or 1
        cursor = start
        for piece in pieces:
            share = (end - start) * len(piece) / total_chars
            piece_end = min(end, cursor + share)
            piece_words = [
                w for w in words if w.start >= cursor - 0.05 and w.start < piece_end + 0.05
            ]
            events.append(
                _CaptionEvent(
                    start=cursor,
                    end=max(cursor + style.min_duration, piece_end),
                    text=piece,
                    words=piece_words,
                )
            )
            cursor = piece_end

    events.sort(key=lambda e: e.start)
    # Remove accidental overlaps so two cues never stack.
    for previous, current in zip(events[:-1], events[1:]):
        if previous.end > current.start:
            previous.end = max(previous.start + 0.2, current.start)
    return events


def _split_text(text: str, style: CaptionStyle) -> List[str]:
    """Break a long sentence into caption-sized chunks at natural boundaries."""
    text = re.sub(r"\s+", " ", text).strip()
    limit = style.max_chars_per_line * style.max_lines
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    # Prefer splitting after punctuation, then at conjunctions.
    tokens = re.findall(r"\S+\s*", text)
    for token in tokens:
        token_len = len(token)
        if current and current_len + token_len > limit:
            chunks.append("".join(current).strip())
            current, current_len = [], 0
        current.append(token)
        current_len += token_len
        if token.rstrip().endswith((",", ".", "!", "?", ";", ":")) and current_len > limit * 0.55:
            chunks.append("".join(current).strip())
            current, current_len = [], 0
    if current:
        chunks.append("".join(current).strip())
    return [c for c in chunks if c]


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "").replace("}", "").replace("\n", " ")


def _wrap(text: str, style: CaptionStyle) -> str:
    """Break text into at most ``max_lines`` balanced lines.

    Note: karaoke override tags (``{\\kf..}``) look like words when split, so
    callers pass already-split word units. This helper only inserts the ASS
    newline escape and never removes content.
    """
    units = text.split()
    if not units:
        return text
    limit = style.max_chars_per_line
    total_chars = sum(len(_visible(u)) for u in units) + max(0, len(units) - 1)
    if total_chars <= limit or style.max_lines <= 1:
        return " ".join(units)

    lines: List[str] = []
    current: List[str] = []
    current_len = 0
    for unit in units:
        length = len(_visible(unit))
        if current and current_len + length + 1 > limit:
            lines.append(" ".join(current))
            current, current_len = [], 0
            if len(lines) == style.max_lines - 1:
                # Last line takes the remainder.
                current.append(unit)
                current_len = length
                continue
        current.append(unit)
        current_len += length + 1
    if current:
        lines.append(" ".join(current))
    return "\\N".join(lines) if len(lines) > 1 else lines[0]


_TAG_RE = re.compile(r"\{[^}]*\}")


def _visible(token: str) -> str:
    """Length of a token ignoring ASS override tags."""
    return _TAG_RE.sub("", token)


def _karaoke_text(event: _CaptionEvent, style: CaptionStyle) -> str:
    """Build per-word highlight tags using ``\\k`` karaoke durations.

    Falls back to plain text when word timings are missing or unreliable.
    """
    if not style.karaoke or not event.words:
        return _wrap(_escape(_case(event.text, style)), style)

    words = [w for w in event.words if w.text.strip()]
    if len(words) < 2:
        return _wrap(_escape(_case(event.text, style)), style)

    span = max(0.05, event.end - event.start)
    parts: List[str] = []
    cursor = event.start
    for index, word in enumerate(words):
        start = max(event.start, min(event.end, word.start if index else event.start))
        end = min(event.end, max(start + 0.02, word.end))
        if end <= start:
            end = min(event.end, start + span / len(words))
        lead = max(0, int(round((start - cursor) * 100)))
        duration = max(1, int(round((end - start) * 100)))
        if lead:
            parts.append(f"{{\\k{lead}}}")
        parts.append(f"{{\\kf{duration}}}{_escape(_case(word.text, style))} ")
        cursor = end

    text = "".join(parts).strip()
    return _wrap(text, style)


def _case(text: str, style: CaptionStyle) -> str:
    return text.upper() if style.uppercase else text


def build_ass(
    events: Sequence[_CaptionEvent],
    style: CaptionStyle,
    width: int = 1080,
    height: int = 1920,
) -> str:
    """Render ASS file content for the given caption events."""
    primary = style.colour(style.primary_colour)
    highlight = style.colour(style.highlight_colour)
    outline_col = style.colour(style.outline_colour)

    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        (
            f"Style: AutoClip,{style.font_name},{style.font_size},{primary},{highlight},"
            f"{outline_col},&H64000000,{-1 if style.bold else 0},0,0,0,100,100,0,0,"
            f"1,{style.outline},{style.shadow},2,{style.margin_h},{style.margin_h},"
            f"{style.margin_v},1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    lines = list(header)
    for event in events:
        text = _karaoke_text(event, style)
        if not text.strip():
            continue
        lines.append(
            f"Dialogue: 0,{_ass_time(event.start)},{_ass_time(event.end)},AutoClip,,0,0,0,,{text}"
        )
    lines.append("")
    return "\n".join(lines)


def write_ass(
    segments: Sequence[Segment],
    clip_start: float,
    clip_end: float,
    output_path: str | Path,
    style: Optional[CaptionStyle] = None,
    width: int = 1080,
    height: int = 1920,
) -> Optional[Path]:
    """Write an .ass file for the clip; returns ``None`` when there is nothing to show."""
    style = (style or CaptionStyle()).resolved()
    events = caption_segments(segments, clip_start, clip_end, style)
    if not events:
        return None
    content = build_ass(events, style, width=width, height=height)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    logger.debug("Wrote %d caption cues to %s", len(events), path)
    return path
