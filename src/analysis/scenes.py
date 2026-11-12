"""
Scene change detection.

Two implementations, picked at runtime:

* **ffmpeg select/scene** - zero extra dependencies, works with any build.
  Fast (decodes at a low resolution) and accurate enough for "cut here" hints.
* **opencv** - used only when ``ffmpeg`` scene detection fails.

Clips that start right after a scene change feel intentional, so candidate
boundaries are snapped toward nearby cuts.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ..media.ffmpeg import get_ffmpeg

logger = logging.getLogger(__name__)

_PTS_RE = re.compile(r"pts_time:([0-9.]+)")


@dataclass
class SceneIndex:
    cuts: List[float] = field(default_factory=list)
    duration: float = 0.0

    def nearest(self, timestamp: float, tolerance: float = 1.2) -> Optional[float]:
        """Return the closest cut within ``tolerance`` seconds."""
        best: Optional[float] = None
        best_delta = tolerance
        for cut in self.cuts:
            delta = abs(cut - timestamp)
            if delta <= best_delta:
                best_delta = delta
                best = cut
        return best

    def next_cut(self, timestamp: float, max_gap: float = 3.0) -> Optional[float]:
        for cut in self.cuts:
            if timestamp < cut <= timestamp + max_gap:
                return cut
        return None

    def previous_cut(self, timestamp: float, max_gap: float = 3.0) -> Optional[float]:
        for cut in reversed(self.cuts):
            if timestamp - max_gap <= cut < timestamp:
                return cut
        return None

    def to_dict(self) -> Dict:
        return {"duration": round(self.duration, 3), "cuts": [round(c, 3) for c in self.cuts]}

    @classmethod
    def from_dict(cls, data: Optional[Dict]) -> "SceneIndex":
        data = data or {}
        return cls(cuts=list(data.get("cuts") or []), duration=float(data.get("duration") or 0.0))


def detect_scenes(
    video_path: str | Path,
    threshold: float = 0.32,
    max_width: int = 320,
    duration: float = 0.0,
) -> SceneIndex:
    """Detect hard cuts in ``video_path``.

    Frames are downscaled to ``max_width`` before comparing, which keeps this
    fast even for 4K sources.
    """
    video_path = Path(video_path)
    cuts: List[float] = []
    try:
        args = [
            get_ffmpeg(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "info",
            "-i",
            str(video_path),
            "-an",
            "-sn",
            "-vf",
            f"scale={max_width}:-2,select='gt(scene,{threshold})',metadata=print:file=-",
            "-f",
            "null",
            "-",
        ]
        result = subprocess.run(args, capture_output=True, text=True, timeout=3600)
        text = (result.stdout or "") + (result.stderr or "")
        for match in _PTS_RE.finditer(text):
            try:
                cuts.append(float(match.group(1)))
            except ValueError:
                continue
    except Exception as exc:  # noqa: BLE001
        logger.debug("ffmpeg scene detection failed: %s", exc)

    if not cuts:
        cuts = _detect_scenes_opencv(video_path, threshold)

    # De-duplicate and sort.
    seen = set()
    unique: List[float] = []
    for cut in sorted(cuts):
        key = round(cut, 2)
        if key in seen:
            continue
        seen.add(key)
        unique.append(cut)

    logger.info("Detected %d scene cuts", len(unique))
    return SceneIndex(cuts=unique, duration=duration)


def _detect_scenes_opencv(video_path: Path, threshold: float) -> List[float]:
    try:
        import cv2  # type: ignore
    except Exception:  # noqa: BLE001
        return []
    try:
        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        cuts: List[float] = []
        previous = None
        index = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            small = cv2.resize(grey, (160, 90))
            if previous is not None:
                diff = float(cv2.absdiff(small, previous).mean()) / 255.0
                if diff > threshold / 2:
                    cuts.append(index / fps)
            previous = small
            index += 1
        cap.release()
        return cuts
    except Exception as exc:  # noqa: BLE001
        logger.debug("opencv scene detection failed: %s", exc)
        return []


def save_scene_index(index: SceneIndex, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path
