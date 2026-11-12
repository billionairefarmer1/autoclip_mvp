"""Transcript data model shared by every stage of the pipeline."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional


@dataclass
class Word:
    start: float
    end: float
    text: str

    def to_dict(self) -> Dict:
        return {"start": round(self.start, 3), "end": round(self.end, 3), "text": self.text}


@dataclass
class Segment:
    """One timed utterance."""

    start: float
    end: float
    text: str
    words: List[Word] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def char_count(self) -> int:
        return len(re.sub(r"\s+", "", self.text))

    def to_dict(self) -> Dict:
        data = {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
        }
        if self.words:
            data["words"] = [w.to_dict() for w in self.words]
        return data

    @classmethod
    def from_dict(cls, data: Dict) -> "Segment":
        words = [
            Word(start=float(w["start"]), end=float(w["end"]), text=w.get("text", ""))
            for w in data.get("words") or []
        ]
        return cls(
            start=float(data["start"]),
            end=float(data["end"]),
            text=(data.get("text") or "").strip(),
            words=words,
        )


@dataclass
class Transcript:
    segments: List[Segment] = field(default_factory=list)
    language: str = ""
    source: str = ""
    duration: float = 0.0

    def __bool__(self) -> bool:
        return bool(self.segments)

    def to_dict(self) -> Dict:
        return {
            "language": self.language,
            "source": self.source,
            "duration": round(self.duration, 3),
            "segments": [s.to_dict() for s in self.segments],
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict]) -> "Transcript":
        if not data:
            return cls()
        return cls(
            segments=[Segment.from_dict(s) for s in data.get("segments") or []],
            language=data.get("language") or "",
            source=data.get("source") or "",
            duration=float(data.get("duration") or 0.0),
        )

    def extend(self, segments: Iterable[Segment]) -> None:
        self.segments.extend(segments)
        self.segments.sort(key=lambda s: s.start)

    def text_with_timestamps(self, start: float = 0.0, end: Optional[float] = None) -> str:
        """``[00:12] some words`` style transcript used by the analyzers."""
        lines = []
        for seg in self.segments:
            if seg.end < start or (end is not None and seg.start > end):
                continue
            lines.append(f"[{format_timestamp(seg.start)}] {seg.text}")
        return "\n".join(lines)

    def plain_text(self) -> str:
        return "\n".join(seg.text for seg in self.segments)

    def slice(self, start: float, end: float) -> List[Segment]:
        return [s for s in self.segments if s.end > start and s.start < end]


def format_timestamp(seconds: float, srt: bool = False) -> str:
    seconds = max(0.0, float(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if srt:
        whole = int(secs)
        millis = int(round((secs - whole) * 1000))
        if millis >= 1000:  # rounding spill-over
            whole += 1
            millis -= 1000
        return f"{int(hours):02d}:{int(minutes):02d}:{whole:02d},{millis:03d}"
    return f"{int(hours):02d}:{int(minutes):02d}:{int(secs):02d}"


def parse_timestamp(value: str) -> float:
    """Parse ``HH:MM:SS,mmm``, ``MM:SS`` or a bare number of seconds."""
    value = (value or "").strip().replace(",", ".")
    if not value:
        return 0.0
    if ":" not in value:
        try:
            return float(value)
        except ValueError:
            return 0.0
    parts = value.split(":")
    try:
        parts = [float(p) for p in parts]
    except ValueError:
        return 0.0
    total = 0.0
    for part in parts:
        total = total * 60 + part
    return total
