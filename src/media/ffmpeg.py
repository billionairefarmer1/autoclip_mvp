"""
FFmpeg / FFprobe discovery, media probing and subprocess helpers.

Design goals
------------
* Work on a stock Windows / macOS / Linux PC with **no manual install**:
  binaries are looked up in this order:

  1. explicit path from ``data/settings.json`` (``ffmpeg_path`` / ``ffprobe_path``)
  2. ``AUTOCLIP_FFMPEG`` / ``FFMPEG_BINARY`` environment variables
  3. ``imageio-ffmpeg`` bundled static build (installed with pip)
  4. ``ffmpeg`` on ``PATH``
  5. common install locations for each OS

* Never fail silently: raise :class:`FFmpegNotFoundError` with an actionable
  message that the UI can show to the user.

* ``run_with_progress`` parses ``-progress pipe:1`` output so long renders can
  report a real percentage instead of a fake spinner.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Hide the console window that would otherwise flash on Windows.
if os.name == "nt":  # pragma: no cover - platform specific
    _CREATIONFLAGS = 0x08000000  # CREATE_NO_WINDOW
else:
    _CREATIONFLAGS = 0

_FFMPEG_CACHE: Optional[str] = None
_FFPROBE_CACHE: Optional[str] = None

_REPO_ROOT = Path(__file__).resolve().parents[2]


class FFmpegError(RuntimeError):
    """Raised when an ffmpeg/ffprobe invocation fails."""

    def __init__(self, message: str, stderr: str = "", command: Optional[Sequence[str]] = None):
        super().__init__(message)
        self.stderr = stderr
        self.command = list(command) if command else []


class FFmpegNotFoundError(FFmpegError):
    """Raised when no usable ffmpeg binary can be located."""


def _which(name: str) -> Optional[str]:
    found = shutil.which(name)
    if found:
        return found
    # Windows: sometimes only "ffmpeg.exe" resolves, and user-local installs
    # are not always on PATH.
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "ffmpeg" / "bin" / f"{name}.exe",
            Path("C:/ffmpeg/bin") / f"{name}.exe",
            Path("C:/Program Files/ffmpeg/bin") / f"{name}.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    return None


def _from_imageio(name: str) -> Optional[str]:
    """imageio-ffmpeg ships a static ffmpeg build; it has no ffprobe."""
    if name != "ffmpeg":
        return None
    try:
        import imageio_ffmpeg  # type: ignore

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        return exe if exe and Path(exe).is_file() else None
    except Exception:  # pragma: no cover - optional dependency
        return None


def _from_settings(key: str) -> Optional[str]:
    """Read an explicit binary path from data/settings.json without importing
    the whole ConfigManager (avoids import cycles)."""
    settings_file = _REPO_ROOT / "data" / "settings.json"
    try:
        if settings_file.is_file():
            data = json.loads(settings_file.read_text(encoding="utf-8"))
            value = data.get(key)
            if value and Path(str(value)).is_file():
                return str(value)
    except Exception:
        pass
    return None


def _candidate_paths(name: str) -> Iterable[Optional[str]]:
    yield _from_settings(f"{name}_path")
    yield os.environ.get(f"AUTOCLIP_{name.upper()}")
    yield os.environ.get(f"{name.upper()}_BINARY")
    yield _from_imageio(name)
    yield _which(name)


def get_ffmpeg() -> str:
    """Return the absolute path of a usable ffmpeg binary."""
    global _FFMPEG_CACHE
    if _FFMPEG_CACHE and Path(_FFMPEG_CACHE).is_file():
        return _FFMPEG_CACHE
    for candidate in _candidate_paths("ffmpeg"):
        if candidate and Path(candidate).is_file():
            _FFMPEG_CACHE = candidate
            logger.debug("Using ffmpeg binary: %s", candidate)
            return candidate
    raise FFmpegNotFoundError(
        "FFmpeg was not found. Run `pip install -r requirements.txt` (which installs a "
        "bundled copy) or install FFmpeg from https://ffmpeg.org/download.html and add it "
        "to your PATH, then restart AutoClip."
    )


def get_ffprobe() -> str:
    """Return the path of ffprobe, falling back to ffmpeg if unavailable.

    A bundled imageio-ffmpeg build has no ffprobe, so we degrade gracefully:
    media metadata then comes from ``ffmpeg -i`` parsing (see :func:`probe`).
    """
    global _FFPROBE_CACHE
    if _FFPROBE_CACHE and Path(_FFPROBE_CACHE).is_file():
        return _FFPROBE_CACHE
    for candidate in _candidate_paths("ffprobe"):
        if candidate and Path(candidate).is_file():
            _FFPROBE_CACHE = candidate
            return candidate
    # Last resort: ffmpeg itself understands most of what we need.
    _FFPROBE_CACHE = get_ffmpeg()
    return _FFPROBE_CACHE


def ffmpeg_version() -> str:
    try:
        out = subprocess.run(
            [get_ffmpeg(), "-hide_banner", "-version"],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=_CREATIONFLAGS,
        )
        return (out.stdout or "").splitlines()[0] if out.stdout else "unknown"
    except Exception as exc:  # pragma: no cover - environment specific
        return f"unavailable ({exc})"


def _run_kwargs() -> Dict[str, object]:
    return {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "creationflags": _CREATIONFLAGS,
    }


def run(args: Sequence[str], timeout: Optional[int] = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run ffmpeg with the given arguments (binary path is prepended)."""
    cmd = [get_ffmpeg(), *[str(a) for a in args]]
    logger.debug("ffmpeg %s", " ".join(cmd[1:]))
    try:
        result = subprocess.run(cmd, timeout=timeout, **_run_kwargs())  # type: ignore[arg-type]
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(f"FFmpeg timed out after {timeout}s", command=cmd) from exc
    if check and result.returncode != 0:
        tail = "\n".join((result.stderr or "").strip().splitlines()[-12:])
        raise FFmpegError(
            f"FFmpeg failed (exit {result.returncode})", stderr=tail, command=cmd
        )
    return result


def run_with_progress(
    args: Sequence[str],
    duration: float,
    on_progress: Optional[Callable[[float], None]] = None,
    timeout: Optional[int] = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run ffmpeg and stream ``-progress`` output to ``on_progress``.

    ``on_progress`` receives a 0..1 fraction of :paramref:`duration` seconds.
    Both ``out_time_us`` and ``out_time_ms`` are checked because ffmpeg has
    historically reported microseconds under the ``_ms`` name.
    """
    cmd = [get_ffmpeg(), "-nostdin", "-progress", "pipe:1", "-loglevel", "error", *[str(a) for a in args]]
    logger.debug("ffmpeg(progress) %s", " ".join(cmd[1:]))

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_CREATIONFLAGS,
    )

    assert process.stdout is not None
    stderr_chunks: List[str] = []

    def _drain_stderr() -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            stderr_chunks.append(line)

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

    deadline = time.monotonic() + timeout if timeout else None
    try:
        for line in process.stdout:
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key in ("out_time_us", "out_time_ms") or key == "out_time":
                seconds = _parse_progress_seconds(key, value)
                if seconds is not None and duration > 0 and on_progress:
                    try:
                        on_progress(max(0.0, min(0.999, seconds / duration)))
                    except Exception:  # noqa: BLE001 - progress must never break a render
                        logger.debug("progress callback failed", exc_info=True)
            elif key == "progress" and value == "end" and on_progress:
                try:
                    on_progress(1.0)
                except Exception:  # noqa: BLE001
                    pass
            if deadline and time.monotonic() > deadline:
                process.kill()
                raise FFmpegError(f"FFmpeg timed out after {timeout}s", command=cmd)
        returncode = process.wait()
    finally:
        if process.poll() is None:
            process.kill()
        stderr_thread.join(timeout=5)

    stderr = "".join(stderr_chunks)
    if check and returncode != 0:
        tail = "\n".join(stderr.strip().splitlines()[-12:])
        raise FFmpegError(f"FFmpeg failed (exit {returncode})", stderr=tail, command=cmd)
    return subprocess.CompletedProcess(cmd, returncode, "", stderr)


def _parse_progress_seconds(key: str, value: str) -> Optional[float]:
    try:
        if key == "out_time":
            # HH:MM:SS.micro
            match = re.match(r"(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", value)
            if not match:
                return None
            h, m, s = match.groups()
            return int(h) * 3600 + int(m) * 60 + float(s)
        raw = float(value)
        # out_time_us is microseconds; out_time_ms is (confusingly) also
        # microseconds on current ffmpeg, but seconds on very old ones.
        if key == "out_time_us":
            return raw / 1_000_000
        return raw / 1_000_000 if raw > 10_000 else raw
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Probing
# --------------------------------------------------------------------------- #
@dataclass
class MediaInfo:
    """Normalised media metadata used across the clipping pipeline."""

    path: str
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    video_codec: str = ""
    audio_codec: str = ""
    has_audio: bool = False
    has_video: bool = False
    bitrate: int = 0
    size: int = 0
    rotation: int = 0
    sample_rate: int = 0
    channels: int = 0
    raw: Dict = field(default_factory=dict)

    @property
    def is_portrait(self) -> bool:
        return self.height > self.width

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height if self.height else 0.0

    @property
    def display_width(self) -> int:
        """Width after applying rotation metadata."""
        return self.height if self.rotation in (90, 270) else self.width

    @property
    def display_height(self) -> int:
        """Height after applying rotation metadata."""
        return self.width if self.rotation in (90, 270) else self.height

    def to_dict(self) -> Dict:
        return {
            "path": self.path,
            "duration": round(self.duration, 3),
            "width": self.width,
            "height": self.height,
            "display_width": self.display_width,
            "display_height": self.display_height,
            "fps": round(self.fps, 3),
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "has_audio": self.has_audio,
            "has_video": self.has_video,
            "bitrate": self.bitrate,
            "size": self.size,
            "rotation": self.rotation,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
        }


def _parse_fraction(value: Optional[str]) -> float:
    if not value:
        return 0.0
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def probe(path: str | Path) -> MediaInfo:
    """Return normalised metadata for a media file.

    Uses ffprobe when available and falls back to parsing ``ffmpeg -i`` output,
    which is what happens with the pip-bundled static ffmpeg build.
    """
    path = Path(path)
    info = MediaInfo(path=str(path))
    if not path.is_file():
        raise FFmpegError(f"Media file does not exist: {path}")

    ffprobe = get_ffprobe()
    used_ffprobe = Path(ffprobe).name.lower().startswith("ffprobe")

    if used_ffprobe:
        cmd = [
            ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        try:
            result = subprocess.run(cmd, timeout=120, **_run_kwargs())  # type: ignore[arg-type]
            if result.returncode == 0 and result.stdout:
                data = json.loads(result.stdout)
                info.raw = data
                _apply_probe_json(info, data)
                return info
        except Exception as exc:  # noqa: BLE001
            logger.debug("ffprobe failed for %s: %s", path, exc)

    _probe_with_ffmpeg(info)
    return info


def _apply_probe_json(info: MediaInfo, data: Dict) -> None:
    fmt = data.get("format") or {}
    info.duration = float(fmt.get("duration") or 0.0)
    info.size = int(float(fmt.get("size") or 0))
    info.bitrate = int(float(fmt.get("bit_rate") or 0))

    for stream in data.get("streams") or []:
        codec_type = stream.get("codec_type")
        if codec_type == "video" and not info.has_video:
            info.has_video = True
            info.video_codec = stream.get("codec_name") or ""
            info.width = int(stream.get("width") or 0)
            info.height = int(stream.get("height") or 0)
            info.fps = _parse_fraction(stream.get("avg_frame_rate")) or _parse_fraction(
                stream.get("r_frame_rate")
            )
            rotation = 0
            for side_data in stream.get("side_data_list") or []:
                if "rotation" in side_data:
                    try:
                        rotation = int(float(side_data["rotation"]))
                    except (TypeError, ValueError):
                        pass
            if not rotation:
                try:
                    rotation = int(float((stream.get("tags") or {}).get("rotate", 0)))
                except (TypeError, ValueError):
                    rotation = 0
            info.rotation = abs(rotation) % 360
            if not info.duration:
                try:
                    info.duration = float(stream.get("duration") or 0.0)
                except (TypeError, ValueError):
                    pass
        elif codec_type == "audio" and not info.has_audio:
            info.has_audio = True
            info.audio_codec = stream.get("codec_name") or ""
            info.sample_rate = int(float(stream.get("sample_rate") or 0))
            info.channels = int(stream.get("channels") or 0)


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_VIDEO_RE = re.compile(r"Stream #\d+:\d+.*?: Video: (\w+).*?, (\d+)x(\d+)")
_FPS_RE = re.compile(r"([\d.]+) fps")
_AUDIO_RE = re.compile(r"Stream #\d+:\d+.*?: Audio: (\w+)(.*)")
_SAMPLE_RATE_RE = re.compile(r"(\d+) Hz")
_CHANNELS_RE = re.compile(r"stereo|mono|5\.1")
_SAR_RE = re.compile(r"\[SAR (\d+):(\d+) DAR (\d+):(\d+)\]")
_ROTATE_RE = re.compile(r"rotate\s*:\s*(\d+)")


def _probe_with_ffmpeg(info: MediaInfo) -> None:
    """Parse ``ffmpeg -i file`` stderr when ffprobe is unavailable."""
    cmd = [get_ffmpeg(), "-hide_banner", "-i", info.path]
    result = subprocess.run(cmd, timeout=120, **_run_kwargs())  # type: ignore[arg-type]
    text = result.stderr or ""
    info.raw = {"ffmpeg_stderr": text}

    match = _DURATION_RE.search(text)
    if match:
        h, m, s = match.groups()
        info.duration = int(h) * 3600 + int(m) * 60 + float(s)

    for line in text.splitlines():
        if "Stream #" not in line:
            continue
        video = _VIDEO_RE.search(line)
        if video and not info.has_video:
            info.has_video = True
            info.video_codec = video.group(1)
            info.width = int(video.group(2))
            info.height = int(video.group(3))
            fps = _FPS_RE.search(line)
            if fps:
                info.fps = _parse_fraction(fps.group(1))
            sar = _SAR_RE.search(line)
            if sar and int(sar.group(1)) and int(sar.group(2)):
                info.width = int(round(info.width * int(sar.group(1)) / int(sar.group(2))))
            continue
        audio = _AUDIO_RE.search(line)
        if audio and not info.has_audio:
            info.has_audio = True
            info.audio_codec = audio.group(1)
            sr = _SAMPLE_RATE_RE.search(line)
            if sr:
                info.sample_rate = int(sr.group(1))
            channels = _CHANNELS_RE.search(line)
            info.channels = 2 if channels and channels.group(0) == "stereo" else 1

    rotate = _ROTATE_RE.search(text)
    if rotate:
        info.rotation = abs(int(rotate.group(1))) % 360

    try:
        info.size = Path(info.path).stat().st_size
    except OSError:
        info.size = 0


def has_audio_stream(path: str | Path) -> bool:
    try:
        return probe(path).has_audio
    except Exception:  # noqa: BLE001
        return False
