"""
Automatic 9:16 reframing.

Turns a landscape source into a vertical clip that keeps the speaker in frame:

1. sample frames inside the clip
2. detect faces with OpenCV's bundled Haar cascades (no downloads, no GPU)
3. smooth the detections into a crop path (hysteresis + EMA + clamping)
4. express the path as an FFmpeg ``crop`` expression, or fall back to a
   blurred-background composition when no face is found
"""

from .tracker import FaceTracker, FaceTrack, plan_crop_path, CropKeyframe  # noqa: F401
from .expressions import crop_expression  # noqa: F401

__all__ = [
    "FaceTracker",
    "FaceTrack",
    "plan_crop_path",
    "CropKeyframe",
    "crop_expression",
]
