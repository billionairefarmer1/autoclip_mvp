"""
Clip rendering - turn a scored clip into a finished, upload-ready video file.

Handles, in one FFmpeg pass:
* frame-accurate trim
* 9:16 reframing (face-following crop, or blurred background fill)
* burned-in captions rendered from the transcript
* loudness normalisation to short-form platform targets
* optional fades, resolution and frame-rate caps
* hardware encoder detection with safe software fallback
"""

from .encoder import EncoderChoice, available_encoders, pick_encoder  # noqa: F401
from .renderer import (  # noqa: F401
    RenderError,
    RenderSpec,
    RenderResult,
    build_filter_graph,
    render_clip,
    make_thumbnail,
)

__all__ = [
    "EncoderChoice",
    "available_encoders",
    "pick_encoder",
    "RenderError",
    "RenderSpec",
    "RenderResult",
    "build_filter_graph",
    "render_clip",
    "make_thumbnail",
]
