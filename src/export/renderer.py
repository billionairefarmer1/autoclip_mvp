"""
Render a clip to a finished file.

The filter graph is built as a list of stages and rendered in a single FFmpeg
invocation. Everything is optional, so the same code path covers:

* a quick raw cut (``raw`` mode, stream copy when possible)
* a 9:16 reframe with burned captions (``vertical`` mode)
* a 16:9 cut with captions (``horizontal`` mode)
* blurred-background fill for sources that cannot fill a 9:16 frame
"""
from __future__ import annotations

import logging
import math
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..captions.ass import CaptionStyle, write_ass
from ..media.ffmpeg import FFmpegError, MediaInfo, probe, run, run_with_progress
from ..reframe.expressions import crop_expression
from ..reframe.tracker import CropKeyframe
from ..transcribe.models import Segment
from .encoder import EncoderChoice, pick_encoder

logger = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[float], None]]


class RenderError(RuntimeError):
    """Raised when a clip cannot be rendered."""


@dataclass
class RenderSpec:
    """Everything needed to render one clip."""

    source: str
    output: str
    start: float
    end: float
    mode: str = "vertical"  # vertical | horizontal | raw
    width: int = 1080
    height: int = 1920
    fps: int = 30
    crop_keyframes: List[CropKeyframe] = field(default_factory=list)
    crop_mode: str = "crop"  # crop | blur
    captions_path: Optional[str] = None
    caption_style: Optional[CaptionStyle] = None
    encoder: Optional[str] = None
    prefer_hardware: bool = True
    crf: int = 20
    audio_bitrate: str = "192k"
    loudnorm: bool = True
    loudness_target: float = -14.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    trim_silence: bool = False
    keep_original_audio: bool = True

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @classmethod
    def from_dict(cls, data: Dict) -> "RenderSpec":
        known = {
            "source",
            "output",
            "start",
            "end",
            "mode",
            "width",
            "height",
            "fps",
            "crop_mode",
            "captions_path",
            "encoder",
            "prefer_hardware",
            "crf",
            "audio_bitrate",
            "loudnorm",
            "loudness_target",
            "fade_in",
            "fade_out",
            "trim_silence",
            "keep_original_audio",
        }
        kwargs = {k: v for k, v in data.items() if k in known}
        spec = cls(**kwargs)
        if data.get("crop_keyframes"):
            spec.crop_keyframes = [
                CropKeyframe(
                    time=float(k["time"]),
                    x=float(k["x"]),
                    y=float(k["y"]),
                    width=float(k["width"]),
                    height=float(k["height"]),
                )
                for k in data["crop_keyframes"]
            ]
        if data.get("caption_style"):
            spec.caption_style = CaptionStyle.from_dict(data["caption_style"])
        return spec


@dataclass
class RenderResult:
    output: str
    duration: float
    width: int
    height: int
    size: int
    encoder: str
    hardware: bool
    captions: bool
    mode: str
    thumbnail: Optional[str] = None

    def to_dict(self) -> Dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Filter graph
# --------------------------------------------------------------------------- #
def build_filter_graph(spec: RenderSpec, media: MediaInfo) -> Tuple[str, List[str]]:
    """Return ``(filter_complex, extra_output_args)`` for the spec.

    Video stages are chained; audio is normalised separately.
    """
    stages: List[str] = []

    if spec.mode == "raw":
        # Trim only. We still emit a minimal chain so a re-encode fallback has
        # a valid filter graph (stream copy is tried first, and usually wins).
        stages = ["format=yuv420p"]
        if spec.fps:
            stages.append(f"fps={spec.fps}")
        return ",".join(stages), []

    src_w = media.display_width or media.width
    src_h = media.display_height or media.height
    if src_w <= 0 or src_h <= 0:
        raise RenderError("Source video has no readable dimensions")

    # Normalise rotation so crop coordinates refer to what the viewer sees.
    if media.rotation in (90, 270):
        stages.append("transpose=clock" if media.rotation == 90 else "transpose=cclock")

    # Raw mode keeps the source resolution, so width/height may be 0 on purpose.
    target_aspect = (spec.width / spec.height) if spec.width and spec.height else src_w / src_h

    if spec.mode == "vertical":
        if spec.crop_keyframes:
            # Clamp every keyframe to the real frame: a stale or hand-edited
            # crop path must never ask ffmpeg for pixels outside the source.
            spec.crop_keyframes = [
                CropKeyframe(
                    time=frame.time,
                    x=min(max(0.0, frame.x), max(0.0, src_w - 2)),
                    y=min(max(0.0, frame.y), max(0.0, src_h - 2)),
                    width=min(frame.width, src_w),
                    height=min(frame.height, src_h),
                )
                for frame in spec.crop_keyframes
            ]
            for frame in spec.crop_keyframes:
                frame.x = min(frame.x, max(0.0, src_w - frame.width))
                frame.y = min(frame.y, max(0.0, src_h - frame.height))
        if spec.crop_mode == "blur":
            stages.extend(_blur_fill_stages(spec))
        elif spec.crop_keyframes:
            stages.append(crop_expression(spec.crop_keyframes))
            stages.append(f"scale={spec.width}:{spec.height}:flags=lanczos")
            stages.append("setsar=1")
        else:
            # Centre crop to the target aspect, then scale.
            stages.append(
                f"crop='min(iw\\,ih*{target_aspect:.6f})':'min(ih\\,iw/{target_aspect:.6f})'"
            )
            stages.append(f"scale={spec.width}:{spec.height}:flags=lanczos")
            stages.append("setsar=1")
    elif spec.mode == "horizontal":
        stages.append(f"scale={spec.width}:{spec.height}:force_original_aspect_ratio=decrease")
        stages.append(f"pad={spec.width}:{spec.height}:(ow-iw)/2:(oh-ih)/2:color=black")
        stages.append("setsar=1")
    elif spec.width and spec.height:
        stages.append(f"scale={spec.width}:-2")

    if spec.fps:
        stages.append(f"fps={spec.fps}")

    if spec.captions_path:
        from ..captions.fonts import ass_fontsdir_option

        # The ass filter needs a path it can parse, plus an explicit fontsdir:
        # libass silently drops text for families it cannot resolve.
        escaped = _escape_filter_path(spec.captions_path)
        font_dir = (spec.caption_style.fontsdir if spec.caption_style else None)
        if not font_dir:
            from ..captions.fonts import resolve_font

            font_dir = resolve_font(
                spec.caption_style.font_name if spec.caption_style else None
            ).fontsdir
        stages.append(f"ass={escaped}{ass_fontsdir_option(font_dir)}")

    if spec.fade_in > 0:
        stages.append(f"fade=t=in:st=0:d={spec.fade_in:.2f}")
    if spec.fade_out > 0:
        start = max(0.0, spec.duration - spec.fade_out)
        stages.append(f"fade=t=out:st={start:.2f}:d={spec.fade_out:.2f}")

    stages.append("format=yuv420p")
    video_chain = ",".join(stages)

    extra: List[str] = []
    return video_chain, extra


def _blur_fill_stages(spec: RenderSpec) -> List[str]:
    """Blurred-background composition for sources narrower than the target.

    Not expressible as a single linear chain because it needs two branches, so
    this is handled specially in :func:`_build_complex_graph`.
    """
    return ["__BLUR_FILL__"]


def _escape_filter_path(path: str) -> str:
    """Escape a filesystem path for use inside an FFmpeg filter argument."""
    value = str(path).replace("\\", "/")
    value = value.replace(":", "\\:").replace("'", "\\'")
    return f"'{value}'"


def _build_complex_graph(
    spec: RenderSpec, media: MediaInfo
) -> Tuple[List[str], str, str]:
    """Return ``(args_fragment, video_label, audio_label)``.

    Kept separate from :func:`build_filter_graph` so the simple linear case
    stays readable.
    """
    # Only used by the blur-fill branch; guard against raw mode (0x0 output).
    target_aspect = (
        spec.width / spec.height
        if spec.width and spec.height
        else (media.width / media.height if media.height else 1.0)
    )
    video_chain, _ = build_filter_graph(spec, media)

    if "__BLUR_FILL__" not in video_chain:
        graph = f"[0:v]{video_chain}[v]"
        return ["-filter_complex", graph], "[v]", "[0:a]"

    # Two branches: blurred background + fitted foreground.
    bg = (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={spec.width}:{spec.height}:force_original_aspect_ratio=increase,"
        f"crop={spec.width}:{spec.height},gblur=sigma=28,eq=brightness=-0.06[bgv];"
        f"[fg]scale={spec.width}:{spec.height}:force_original_aspect_ratio=decrease[fgs]"
    )
    tail_stages = [
        s
        for s in video_chain.split(",")
        if s and s != "__BLUR_FILL__" and not s.startswith(("crop=", "scale="))
    ]
    tail = ("," + ",".join(tail_stages)) if tail_stages else ""
    graph = f"{bg};[bgv][fgs]overlay=(W-w)/2:(H-h)/2{tail}[v]"
    return ["-filter_complex", graph], "[v]", "[0:a]"


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def render_clip(
    spec: RenderSpec,
    progress: ProgressCb = None,
    timeout: Optional[int] = None,
) -> RenderResult:
    """Render one clip to ``spec.output``."""
    source = Path(spec.source)
    if not source.is_file():
        raise RenderError(f"Source video not found: {source}")
    if spec.end <= spec.start:
        raise RenderError("Clip end must be after clip start")

    # Sensible defaults per output shape, so callers only pass ``mode``.
    if spec.mode == "horizontal":
        if (spec.width, spec.height) == (1080, 1920):
            spec.width, spec.height = 1920, 1080
    elif spec.mode == "raw":
        if (spec.width, spec.height) == (1080, 1920):
            spec.width, spec.height = 0, 0  # keep source resolution

    media = probe(source)
    duration = min(spec.duration, max(0.2, media.duration - spec.start) if media.duration else spec.duration)
    if duration <= 0.2:
        raise RenderError("Clip is outside the source video's duration")

    output = Path(spec.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    encoder = pick_encoder(prefer_hardware=spec.prefer_hardware, requested=spec.encoder)

    def _attempt(choice: EncoderChoice, use_captions: bool) -> RenderResult:
        return _render_once(spec, media, choice, duration, use_captions, progress, timeout)

    errors: List[str] = []
    attempts: List[Tuple[EncoderChoice, bool]] = [(encoder, True)]
    if spec.crop_mode == "blur" or spec.mode == "vertical":
        pass
    # Fallbacks, in order of preference:
    if encoder.hardware:
        attempts.append((pick_encoder(prefer_hardware=False), True))
    if spec.captions_path:
        attempts.append((encoder, False))
        attempts.append((pick_encoder(prefer_hardware=False), False))

    seen = set()
    last_error: Optional[Exception] = None
    for choice, use_captions in attempts:
        key = (choice.name, use_captions)
        if key in seen:
            continue
        seen.add(key)
        try:
            return _attempt(choice, use_captions)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(
                "Render attempt failed (encoder=%s captions=%s): %s",
                choice.name,
                use_captions,
                str(exc)[:300],
            )
            errors.append(f"{choice.name}{'' if use_captions else ' (no captions)'}: {exc}")
            # Retrying is only useful for clean, actionable failures.
            if "not found" in str(exc).lower():
                break

    raise RenderError("Could not render clip. " + " | ".join(errors[-2:]) if errors else "Could not render clip")


def _render_once(
    spec: RenderSpec,
    media: MediaInfo,
    encoder: EncoderChoice,
    duration: float,
    use_captions: bool,
    progress: ProgressCb,
    timeout: Optional[int],
) -> RenderResult:
    # Work on a copy of the spec so mutations (caption removal) don't leak.
    local = RenderSpec(
        **{
            **spec.__dict__,
            "captions_path": spec.captions_path if use_captions else None,
        }
    )

    args: List[str] = ["-hide_banner", "-loglevel", "error", "-y"]

    has_audio = media.has_audio

    # All *input* options must be declared before the inputs themselves, so the
    # silent-audio generator (used when the source has no audio track) is added
    # while we are still building the input list.
    input_args = ["-ss", f"{spec.start:.3f}", "-i", str(spec.source)]
    if not has_audio:
        input_args += [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
        ]
    # ``-t`` is an output option here: it must come after every input.
    args += input_args + ["-t", f"{duration:.3f}"]

    if spec.mode == "raw":
        # Stream copy is instant, but it can only cut on keyframes - so the
        # result may be a second or two longer than requested. We try it, verify
        # the real duration, and fall back to a frame-accurate re-encode when the
        # overshoot is significant. Everything from here on is the fallback path.
        copy_args = [
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            *input_args,
            "-t",
            f"{duration:.3f}",
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            str(spec.output),
        ]
        try:
            run(copy_args, timeout=timeout or 7200)
            copied = Path(spec.output)
            if copied.is_file() and copied.stat().st_size > 0:
                info = probe(copied)
                # Keyframe spacing is typically 1-2 s; anything beyond that is
                # a visibly wrong cut, so re-encode for frame accuracy instead.
                tolerance = 0.75
                if abs((info.duration or duration) - duration) <= tolerance and info.has_video:
                    return RenderResult(
                        output=str(spec.output),
                        duration=round(info.duration or duration, 3),
                        width=info.width,
                        height=info.height,
                        size=copied.stat().st_size,
                        encoder="copy",
                        hardware=False,
                        captions=False,
                        mode=spec.mode,
                    )
                logger.info(
                    "Stream copy trim landed at %.2fs instead of %.2fs; re-encoding for an exact cut",
                    info.duration or 0.0,
                    duration,
                )
        except FFmpegError:
            logger.info("Stream copy trim failed, re-encoding instead")

    graph_args, video_label, audio_label = _build_complex_graph(local, media)

    args += graph_args
    args += ["-map", video_label]
    if has_audio:
        if local.loudnorm:
            # Two-pass loudness normalisation is more accurate, but one-pass
            # dynamic mode is fast and good enough for social video.
            args += [
                "-af",
                f"loudnorm=I={local.loudness_target}:TP=-1.5:LRA=11,aresample=48000",
            ]
        else:
            args += ["-af", "aresample=48000"]
        args += ["-map", "0:a:0"]
    else:
        # Some players refuse files without audio; we generated a silent track.
        args += ["-map", "1:a", "-shortest"]

    args += ["-c:v", encoder.name, *encoder.args]
    if encoder.hardware:
        args += ["-b:v", "8M", "-maxrate", "12M", "-bufsize", "16M"]
    else:
        args += ["-crf", str(local.crf)]
    args += ["-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1"]
    args += ["-movflags", "+faststart"]
    # ``fps`` already normalised the frame rate in the filter chain; repeating
    # ``-r`` here would resample timestamps and stretch the clip's duration.
    if not local.fps:
        args += ["-r", str(local.fps or 30)]

    # Always encode audio: for silent sources this writes the generated track.
    args += ["-c:a", "aac", "-b:a", local.audio_bitrate, "-ar", "48000", "-ac", "2"]

    args += [str(spec.output)]

    run_with_progress(args, duration=duration, on_progress=progress, timeout=timeout or 7200)

    if not Path(spec.output).is_file() or Path(spec.output).stat().st_size == 0:
        raise RenderError("FFmpeg produced no output file")

    info = probe(spec.output)
    result = RenderResult(
        output=str(spec.output),
        duration=round(info.duration or duration, 3),
        width=info.width or local.width,
        height=info.height or local.height,
        size=Path(spec.output).stat().st_size,
        encoder=encoder.name,
        hardware=encoder.hardware,
        captions=bool(local.captions_path),
        mode=local.mode,
    )
    logger.info(
        "Rendered %s (%.1fs, %s%s)",
        spec.output,
        result.duration,
        encoder.name,
        ", captions" if result.captions else "",
    )
    return result


def make_thumbnail(video_path: str | Path, output_path: str | Path, at: float = 0.5) -> Optional[str]:
    """Extract a single frame as a JPEG thumbnail at ``at`` seconds."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        run(
            [
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{max(0.0, at):.3f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-q:v",
                "3",
                "-y",
                str(output_path),
            ],
            timeout=120,
        )
        return str(output_path) if output_path.is_file() else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("Thumbnail generation failed: %s", exc)
        return None


def prepare_captions(
    segments: Sequence[Segment],
    clip_start: float,
    clip_end: float,
    work_dir: str | Path,
    style: Optional[CaptionStyle] = None,
    width: int = 1080,
    height: int = 1920,
) -> Optional[str]:
    """Write an .ass file for the clip, returning its path (or ``None``)."""
    style = style or CaptionStyle()
    path = Path(work_dir) / "captions.ass"
    result = write_ass(segments, clip_start, clip_end, path, style=style, width=width, height=height)
    return str(result) if result else None
