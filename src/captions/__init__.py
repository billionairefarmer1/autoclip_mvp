"""
Burned-in captions.

Generates Advanced SubStation Alpha (``.ass``) files from transcript segments
and hands them to FFmpeg's ``ass`` filter. ASS is used (rather than SRT)
because it supports styling, positioning, outlined text and word-level
karaoke highlighting - the look short-form viewers expect.
"""

from .ass import (  # noqa: F401
    CaptionStyle,
    build_ass,
    caption_segments,
    write_ass,
)

__all__ = ["CaptionStyle", "build_ass", "caption_segments", "write_ass"]
