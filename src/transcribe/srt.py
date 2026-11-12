"""SRT / VTT parsing and writing without third-party dependencies."""
from __future__ import annotations

import re
from pathlib import Path
from typing import List

from .models import Segment, Transcript, format_timestamp, parse_timestamp

_TIMECODE_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}|\d{1,2}:\d{2}[,.]\d{1,3})\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}|\d{1,2}:\d{2}[,.]\d{1,3})"
)
_TAG_RE = re.compile(r"<[^>]+>|\{[^}]*\}")
_INDEX_RE = re.compile(r"^\d+$")


def _clean_text(block_lines: List[str]) -> str:
    text = " ".join(line.strip() for line in block_lines if line.strip())
    text = _TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_srt_text(content: str) -> Transcript:
    """Parse SRT/VTT text into a :class:`Transcript`.

    Tolerates: BOM, CRLF, missing indices, VTT headers and cues without
    indices, overlapping cues, and inline markup.
    """
    content = (content or "").replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = content.split("\n")
    segments: List[Segment] = []
    i = 0
    total = len(lines)

    while i < total:
        line = lines[i].strip()
        if not line or line.upper().startswith("WEBVTT") or line.startswith("NOTE "):
            i += 1
            continue
        match = _TIMECODE_RE.search(line)
        if not match:
            i += 1
            continue
        start = parse_timestamp(match.group("start"))
        end = parse_timestamp(match.group("end"))
        i += 1
        block: List[str] = []
        while i < total and lines[i].strip():
            if _TIMECODE_RE.search(lines[i]):
                break
            block.append(lines[i])
            i += 1
        text = _clean_text(block)
        if text and end > start:
            segments.append(Segment(start=start, end=end, text=text))

    segments.sort(key=lambda s: s.start)
    return Transcript(segments=segments, source="user_srt")


def parse_srt_file(path: str | Path) -> Transcript:
    path = Path(path)
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "gb18030", "latin-1"):
        try:
            return parse_srt_text(path.read_text(encoding=encoding))
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(f"Could not decode subtitle file: {path}")


def transcript_from_plain_text(text: str, duration: float) -> Transcript:
    """Distribute a pasted transcript over the video's duration.

    Used when a user pastes a script/transcript without timestamps: timings are
    proportional to sentence length, which is good enough to pick clip
    boundaries, and the UI flags that timings are approximate.
    """
    sentences = [s.strip() for s in re.split(r"(?<=[.!?。！？\n])\s+", text) if s.strip()]
    if not sentences:
        return Transcript(source="user_text", duration=duration)
    weights = [max(1, len(re.sub(r"\s+", "", s))) for s in sentences]
    total_weight = sum(weights)
    segments: List[Segment] = []
    cursor = 0.0
    for sentence, weight in zip(sentences, weights):
        span = duration * weight / total_weight if total_weight else 0.0
        segments.append(Segment(start=cursor, end=min(duration, cursor + span), text=sentence))
        cursor += span
    return Transcript(segments=segments, source="user_text", duration=duration)


def write_srt(segments: List[Segment], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    for index, segment in enumerate(segments, start=1):
        lines.append(str(index))
        lines.append(
            f"{format_timestamp(segment.start, srt=True)} --> {format_timestamp(segment.end, srt=True)}"
        )
        lines.append(segment.text.strip())
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
