"""
Face detection and crop-path planning for vertical reframing.

Detection is intentionally lightweight: OpenCV's Haar cascades ship inside
``opencv-python-headless`` so there is nothing to download at runtime and it
runs acceptably on a laptop CPU. If OpenCV is unavailable the planner simply
falls back to a static centre crop, which is the right answer for most
talking-head and podcast footage anyway.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


@dataclass
class FaceSample:
    time: float  # seconds relative to clip start
    cx: float  # face centre x in source pixels
    cy: float
    width: float
    height: float
    confidence: float


@dataclass
class FaceTrack:
    samples: List[FaceSample]
    source_width: int
    source_height: int

    @property
    def has_faces(self) -> bool:
        return any(s.confidence > 0 for s in self.samples)

    @property
    def coverage(self) -> float:
        """Fraction of sampled frames with a detection."""
        if not self.samples:
            return 0.0
        detected = sum(1 for s in self.samples if s.confidence > 0)
        return detected / len(self.samples)

    @property
    def face_scale(self) -> float:
        """Median face height relative to frame height."""
        heights = [s.height for s in self.samples if s.confidence > 0]
        if not heights or not self.source_height:
            return 0.0
        heights.sort()
        return heights[len(heights) // 2] / self.source_height


@dataclass
class CropKeyframe:
    time: float
    x: float
    y: float
    width: float
    height: float

    def to_dict(self) -> dict:
        return {
            "time": round(self.time, 3),
            "x": round(self.x, 1),
            "y": round(self.y, 1),
            "width": round(self.width, 1),
            "height": round(self.height, 1),
        }


class FaceTracker:
    """Samples a video region and returns a :class:`FaceTrack`."""

    def __init__(self) -> None:
        self._cascades = None

    def _load_cascades(self):
        if self._cascades is not None:
            return self._cascades
        try:
            import cv2  # type: ignore
        except Exception:  # noqa: BLE001
            self._cascades = []
            return self._cascades

        cascade_dir = getattr(cv2.data, "haarcascades", "")
        cascade_files = [
            ("haarcascade_frontalface_default.xml", 1.0),
            ("haarcascade_frontalface_alt2.xml", 1.1),
            ("haarcascade_profileface.xml", 0.8),
        ]
        loaded = []
        for filename, weight in cascade_files:
            path = os.path.join(cascade_dir, filename)
            if not os.path.isfile(path):
                continue
            cascade = cv2.CascadeClassifier(path)
            if not cascade.empty():
                loaded.append((cascade, weight, filename))
        self._cascades = loaded
        return loaded

    def available(self) -> bool:
        return bool(self._load_cascades())

    def track(
        self,
        video_path: str | Path,
        start: float,
        end: float,
        sample_fps: float = 2.0,
        analyze_width: int = 640,
    ) -> FaceTrack:
        """Detect faces between ``start`` and ``end`` seconds."""
        try:
            import cv2  # type: ignore
        except Exception:  # noqa: BLE001
            return FaceTrack(samples=[], source_width=0, source_height=0)

        cascades = self._load_cascades()
        clip_duration = max(0.0, end - start)
        if not cascades or clip_duration <= 0:
            return FaceTrack(samples=[], source_width=0, source_height=0)

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return FaceTrack(samples=[], source_width=0, source_height=0)

        try:
            src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            if src_w <= 0 or src_h <= 0:
                return FaceTrack(samples=[], source_width=0, source_height=0)

            scale = analyze_width / float(src_w) if src_w > analyze_width else 1.0
            step = max(1, int(round(fps / max(0.2, sample_fps))))
            start_frame = int(round(start * fps))
            end_frame = int(round(end * fps))
            cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

            samples: List[FaceSample] = []
            frame_index = start_frame
            while frame_index <= end_frame:
                ok, frame = cap.read()
                if not ok:
                    break
                if (frame_index - start_frame) % step != 0:
                    frame_index += 1
                    continue

                if scale != 1.0:
                    small = cv2.resize(frame, (int(src_w * scale), int(src_h * scale)))
                else:
                    small = frame
                grey = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                grey = cv2.equalizeHist(grey)
                small_h = grey.shape[0]
                min_size = max(24, int(small_h * 0.08))

                best = None  # (weight*area, x, y, w, h, weight)
                for cascade, weight, filename in cascades:
                    faces = cascade.detectMultiScale(
                        grey, scaleFactor=1.1, minNeighbors=5, minSize=(min_size, min_size)
                    )
                    for (x, y, w, h) in faces:
                        score = weight * float(w * h)
                        if best is None or score > best[0]:
                            best = (score, int(x), int(y), int(w), int(h), weight)

                if best is not None:
                    _, fx, fy, fw, fh, weight = best
                    inv = 1.0 / scale
                    samples.append(
                        FaceSample(
                            time=(frame_index - start_frame) / fps,
                            cx=(fx + fw / 2) * inv,
                            cy=(fy + fh / 2) * inv,
                            width=fw * inv,
                            height=fh * inv,
                            confidence=weight,
                        )
                    )
                else:
                    samples.append(
                        FaceSample(
                            time=(frame_index - start_frame) / fps,
                            cx=src_w / 2,
                            cy=src_h / 2,
                            width=0,
                            height=0,
                            confidence=0.0,
                        )
                    )
                frame_index += 1
        finally:
            cap.release()

        return FaceTrack(samples=samples, source_width=src_w, source_height=src_h)


def plan_crop_path(
    track: FaceTrack,
    target_width: int,
    target_height: int,
    clip_start: float = 0.0,
    clip_end: float = 0.0,
    max_keyframes: int = 10,
    smoothing: float = 0.35,
) -> Tuple[List[CropKeyframe], str]:
    """Plan a crop path for the target aspect ratio.

    Returns ``(keyframes, mode)`` where mode is one of ``"crop"`` (source can
    fill the frame) or ``"blur"`` (source is too narrow - use blurred bars).

    The crop keeps full source height when the source is landscape, then slides
    horizontally to follow faces. Vertical movement is added only when the
    source is portrait and needs a vertical slide instead.
    """
    src_w = track.source_width
    src_h = track.source_height
    if src_w <= 0 or src_h <= 0:
        return [], "blur"

    clip_duration = max(0.1, clip_end - clip_start) if clip_end > clip_start else 0.1
    target_aspect = target_width / target_height
    src_aspect = src_w / src_h

    if src_aspect < target_aspect:
        # Source is narrower than 9:16 (already portrait/skinny): fill width.
        crop_w = float(src_w)
        crop_h = crop_w / target_aspect
        if crop_h > src_h:
            crop_h = float(src_h)
            crop_w = crop_h * target_aspect
            if crop_w > src_w:
                return [], "blur"
        axis = "y"
    else:
        crop_h = float(src_h)
        crop_w = crop_h * target_aspect
        if crop_w > src_w:
            crop_w = float(src_w)
        axis = "x"

    if not track.has_faces or track.coverage < 0.12:
        # No usable detection: centre crop is the safest choice.
        keyframe = CropKeyframe(
            time=0.0,
            x=(src_w - crop_w) / 2,
            y=(src_h - crop_h) / 2,
            width=crop_w,
            height=crop_h,
        )
        return [keyframe], "crop"

    # --- choose how tight to crop ----------------------------------------
    # Big faces (typical selfie / streamer / talking-head videos) fill the
    # frame better when we zoom in a little; small faces need the wider shot.
    scale = track.face_scale
    if axis == "x":
        if scale >= 0.22:
            zoom = 1.0
        elif scale >= 0.14:
            zoom = 0.88
        else:
            zoom = 0.78
        crop_w = _even(max(160.0, crop_w * zoom))
        crop_h = _even(max(160.0, crop_w / target_aspect))
        if crop_h > src_h:
            crop_h = _even(float(src_h))
            crop_w = _even(crop_h * target_aspect)
    else:
        if 0.0 < scale < 0.16:
            crop_h = _even(max(160.0, crop_h * 0.85))
            crop_w = _even(max(160.0, crop_h * target_aspect))
            if crop_w > src_w:
                crop_w = _even(float(src_w))
                crop_h = _even(crop_w / target_aspect)

    crop_w = float(min(crop_w, src_w))
    crop_h = float(min(crop_h, src_h))

    # --- smooth the face centre over time with an EMA --------------------
    max_x = max(0.0, src_w - crop_w)
    max_y = max(0.0, src_h - crop_h)

    centres: List[Tuple[float, float, float]] = []  # (time, target_x, target_y)
    smoothed_x = None
    smoothed_y = None
    for sample in track.samples:
        if sample.confidence <= 0:
            # Hold the previous position through dropouts (brief occlusion).
            target_x = smoothed_x if smoothed_x is not None else (src_w - crop_w) / 2
            target_y = smoothed_y if smoothed_y is not None else (src_h - crop_h) / 2
        else:
            # Faces look natural slightly above centre; bias the crop so the
            # eyes sit near the upper third.
            target_x = sample.cx - crop_w / 2
            target_y = sample.cy - crop_h * 0.42
            target_x = min(max(0.0, target_x), max_x)
            target_y = min(max(0.0, target_y), max_y)

        if smoothed_x is None:
            smoothed_x, smoothed_y = target_x, target_y
        else:
            smoothed_x += smoothing * (target_x - smoothed_x)
            smoothed_y = (smoothed_y or 0.0) + smoothing * (target_y - (smoothed_y or 0.0))
        centres.append((sample.time, smoothed_x, smoothed_y or 0.0))

    if not centres:
        return [], "blur"

    # --- reduce to a handful of keyframes, keeping the shape of the motion --
    reduced = _decimate(centres, max_keyframes)
    keyframes = [
        CropKeyframe(
            time=float(t) - float(reduced[0][0]),
            x=float(_even(min(max(0.0, x), max_x))),
            y=float(_even(min(max(0.0, y), max_y))),
            width=_even(crop_w),
            height=_even(crop_h),
        )
        for t, x, y in reduced
    ]

    return keyframes, "crop"


def _decimate(points: Sequence[Tuple[float, float, float]], target: int) -> List[Tuple[float, float, float]]:
    """Pick at most ``target`` points, preserving extremes of motion."""
    points = list(points)
    if len(points) <= target:
        return points
    # Always keep first and last; sample the rest evenly by time.
    indices = {0, len(points) - 1}
    stride = max(1, len(points) // (target - 1))
    for index in range(0, len(points), stride):
        indices.add(index)
    return [points[i] for i in sorted(indices)][:target]


def _even(value: float) -> float:
    """Round down to an even integer (H.264 chroma requirement)."""
    return float(int(value) // 2 * 2)
