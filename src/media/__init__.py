"""
Media layer - FFmpeg discovery, probing and process helpers.

Everything in AutoClip that touches video goes through this package so that
the tool works on a normal Windows/macOS/Linux desktop where ffmpeg is not
necessarily installed or on ``PATH``.
"""

from .ffmpeg import (  # noqa: F401
    FFmpegError,
    FFmpegNotFoundError,
    MediaInfo,
    ffmpeg_version,
    get_ffmpeg,
    get_ffprobe,
    has_audio_stream,
    probe,
    run,
    run_with_progress,
)

__all__ = [
    "FFmpegError",
    "FFmpegNotFoundError",
    "MediaInfo",
    "ffmpeg_version",
    "get_ffmpeg",
    "get_ffprobe",
    "has_audio_stream",
    "probe",
    "run",
    "run_with_progress",
]
