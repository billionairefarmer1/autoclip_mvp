"""Convert crop keyframes into an FFmpeg crop filter expression."""
from __future__ import annotations

from typing import Sequence

from .tracker import CropKeyframe


def crop_expression(keyframes: Sequence[CropKeyframe], variable: str = "t") -> str:
    """Build a ``crop=w:h:x:y`` argument string from keyframes.

    A single keyframe becomes a static crop. Multiple keyframes become a
    piecewise-linear interpolation on the ``t`` variable, which FFmpeg evaluates
    per frame - so the crop follows the speaker smoothly without rendering
    intermediate videos.
    """
    if not keyframes:
        return "crop=iw:ih:0:0"

    first = keyframes[0]
    width = int(first.width)
    height = int(first.height)

    if len(keyframes) == 1:
        return f"crop={width}:{height}:{int(first.x)}:{int(first.y)}"

    x_expr = _interpolate([(k.time, k.x) for k in keyframes], variable)
    y_expr = _interpolate([(k.time, k.y) for k in keyframes], variable)
    # The expressions contain commas (inside if/lt), which ffmpeg's filter
    # parser would otherwise treat as filter separators - quoting is required.
    return f"crop={width}:{height}:'{x_expr}':'{y_expr}'"


def _interpolate(points: Sequence[tuple[float, float]], variable: str) -> str:
    """Piecewise linear interpolation as a nested FFmpeg expression.

    Built from the last segment backwards so the expression stays readable:
    ``if(lt(t,t1), lerp0, if(lt(t,t2), lerp1, ...))``
    """
    if not points:
        return "0"
    if len(points) == 1:
        return f"{points[0][1]:.1f}"

    expression = f"{points[-1][1]:.1f}"
    for index in range(len(points) - 2, -1, -1):
        t0, v0 = points[index]
        t1, v1 = points[index + 1]
        span = max(0.001, t1 - t0)
        slope = (v1 - v0) / span
        segment = f"({v0:.1f}+({slope:.4f})*({variable}-{t0:.3f}))"
        expression = f"if(lt({variable},{t1:.3f}),{segment},{expression})"
    return expression
