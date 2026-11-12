"""
The offline clipping pipeline.

``analyze()``   video -> transcript + signals + scored clips (no network, no API key)

``render_all()`` scored clips -> finished vertical/horizontal MP4 files

Both are generator-free, progress-callback driven functions so they can be
called from the CLI, from FastAPI background tasks, or from tests. Intermediate
state is persisted as JSON in the project's ``metadata`` directory, which is
what the web UI reads.
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from ..analysis.audio_signals import AudioSignals, extract_audio_signals
from ..analysis.candidates import CandidateConfig, ClipCandidate, build_candidates
from ..analysis.scenes import SceneIndex, detect_scenes, save_scene_index
from ..captions.ass import CaptionStyle
from ..export.renderer import RenderResult, RenderSpec, make_thumbnail, prepare_captions, render_clip
from ..media.ffmpeg import MediaInfo, probe
from ..reframe.tracker import FaceTracker, CropKeyframe, plan_crop_path
from ..transcribe.engine import TranscriptionOptions, transcribe
from ..transcribe.models import Transcript, format_timestamp
from ..transcribe.srt import transcript_from_plain_text, write_srt

logger = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[float, str], None]]


def _report(progress: ProgressCb, fraction: float, message: str) -> None:
    if progress:
        try:
            progress(max(0.0, min(1.0, fraction)), message)
        except Exception:  # noqa: BLE001 - a UI callback must never break a job
            logger.debug("progress callback raised", exc_info=True)


@dataclass
class AnalyzeOptions:
    """Everything the user can control during analysis."""

    candidates: CandidateConfig = field(default_factory=CandidateConfig)
    transcription: TranscriptionOptions = field(default_factory=TranscriptionOptions)
    srt_path: Optional[str] = None
    transcript_text: Optional[str] = None
    detect_scenes: bool = True
    scene_threshold: float = 0.32
    language: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Optional[Dict]) -> "AnalyzeOptions":
        data = data or {}
        return cls(
            candidates=CandidateConfig.from_dict(data.get("clip_settings") or data),
            transcription=TranscriptionOptions.from_dict(data),
            srt_path=data.get("srt_path"),
            transcript_text=data.get("transcript_text"),
            detect_scenes=bool(data.get("detect_scenes", True)),
            scene_threshold=float(data.get("scene_threshold", 0.32)),
            language=data.get("language"),
        )


@dataclass
class AnalysisResult:
    video: str
    media: Dict
    clips: List[ClipCandidate]
    transcript_source: str
    transcript_language: str
    transcript_segments: int
    scenes: int
    duration: float
    warnings: List[str] = field(default_factory=list)
    generated_at: str = ""

    def to_dict(self) -> Dict:
        return {
            "video": self.video,
            "media": self.media,
            "clips": [c.to_dict() for c in self.clips],
            "transcript_source": self.transcript_source,
            "transcript_language": self.transcript_language,
            "transcript_segments": self.transcript_segments,
            "scenes": self.scenes,
            "duration": self.duration,
            "warnings": self.warnings,
            "generated_at": self.generated_at,
        }


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
def analyze(
    video_path: str | Path,
    work_dir: str | Path,
    options: Optional[AnalyzeOptions] = None,
    progress: ProgressCb = None,
) -> AnalysisResult:
    """Analyse a video and produce scored clip candidates."""
    options = options or AnalyzeOptions()
    video_path = Path(video_path)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    warnings: List[str] = []

    if not video_path.is_file():
        raise FileNotFoundError(f"Video not found: {video_path}")

    _report(progress, 0.02, "Reading video metadata")
    media = probe(video_path)
    if not media.has_video:
        raise ValueError("This file has no video stream")
    if media.duration <= 0:
        raise ValueError("Could not read the video's duration")

    # --- transcript -------------------------------------------------------
    _report(progress, 0.06, "Preparing transcript")
    transcript = Transcript()
    if options.transcript_text and options.transcript_text.strip():
        transcript = transcript_from_plain_text(options.transcript_text, media.duration)
        write_srt(transcript.segments, work_dir / "transcript.srt")
        warnings.append("Transcript was pasted without timings; clip boundaries are approximate.")
    else:
        def _asr_progress(fraction: float, message: str) -> None:
            _report(progress, 0.06 + 0.44 * fraction, message)

        transcript = transcribe(
            video_path,
            options=options.transcription,
            progress=_asr_progress,
            srt_path=options.srt_path,
        )
        if transcript.segments:
            write_srt(transcript.segments, work_dir / "transcript.srt")

    if transcript.segments:
        logger.info("Transcript: %d segments (%s)", len(transcript.segments), transcript.source)
    else:
        warnings.append(
            "No speech transcription was available, so clips are chosen from audio energy and "
            "scene changes. Add an .srt file or install a Whisper model for better picks."
        )

    (work_dir / "transcript.json").write_text(
        json.dumps(transcript.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # --- audio signals ----------------------------------------------------
    _report(progress, 0.55, "Analysing audio energy and speech")
    signals = extract_audio_signals(video_path)

    # --- scenes -----------------------------------------------------------
    scenes = SceneIndex(duration=media.duration)
    if options.detect_scenes:
        _report(progress, 0.68, "Detecting scene changes")
        scenes = detect_scenes(video_path, threshold=options.scene_threshold, duration=media.duration)
        save_scene_index(scenes, work_dir / "scenes.json")

    # --- candidates -------------------------------------------------------
    _report(progress, 0.82, "Scoring clip candidates")
    clips = build_candidates(
        transcript.segments,
        signals,
        scenes,
        duration=media.duration,
        config=options.candidates,
    )

    if not clips:
        warnings.append(
            "No clips met the length and quality thresholds. Try lowering the minimum score, "
            "shrinking the minimum duration, or using a video with clearer speech."
        )

    _report(progress, 0.95, "Saving results")
    result = AnalysisResult(
        video=str(video_path),
        media=media.to_dict(),
        clips=clips,
        transcript_source=transcript.source or "none",
        transcript_language=transcript.language,
        transcript_segments=len(transcript.segments),
        scenes=len(scenes.cuts),
        duration=media.duration,
        warnings=warnings,
        generated_at=datetime.now().isoformat(timespec="seconds"),
    )
    (work_dir / "analysis.json").write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _report(progress, 1.0, "Analysis complete")
    return result


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
@dataclass
class RenderOptions:
    """Output settings shared by every clip in a job."""

    mode: str = "vertical"  # vertical | horizontal | raw
    width: int = 1080
    height: int = 1920
    fps: int = 30
    captions: bool = True
    caption_style: CaptionStyle = field(default_factory=CaptionStyle)
    auto_reframe: bool = True
    blurred_background: bool = True
    loudnorm: bool = True
    prefer_hardware: bool = True
    crf: int = 20
    encoder: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Optional[Dict]) -> "RenderOptions":
        data = data or {}
        options = cls()
        for key in (
            "mode",
            "width",
            "height",
            "fps",
            "captions",
            "auto_reframe",
            "blurred_background",
            "loudnorm",
            "prefer_hardware",
            "crf",
            "encoder",
        ):
            if key in data and data[key] is not None:
                setattr(options, key, data[key])
        options.caption_style = CaptionStyle.from_dict(data.get("caption_style") or data)
        try:
            options.width = int(options.width)
            options.height = int(options.height)
            options.fps = int(options.fps)
            options.crf = int(options.crf)
        except (TypeError, ValueError):
            options = cls()
        return options


def render_clips(
    video_path: str | Path,
    clips: Sequence[ClipCandidate | Dict],
    out_dir: str | Path,
    work_dir: str | Path,
    transcript: Optional[Transcript] = None,
    options: Optional[RenderOptions] = None,
    progress: ProgressCb = None,
) -> List[Dict]:
    """Render the given clips and return one metadata dict per output file.

    Rendering is per-clip resilient: one failure is reported in that clip's
    entry and does not abort the rest of the batch.
    """
    options = options or RenderOptions()
    out_dir = Path(out_dir)
    work_dir = Path(work_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    if transcript is None:
        transcript_file = Path(work_dir) / "transcript.json"
        if transcript_file.is_file():
            transcript = Transcript.from_dict(json.loads(transcript_file.read_text(encoding="utf-8")))
        else:
            transcript = Transcript()

    media = probe(video_path)
    tracker = FaceTracker() if options.auto_reframe and options.mode == "vertical" else None
    if tracker is not None and not tracker.available():
        logger.info("OpenCV face detection unavailable; using centred crops")

    total = max(1, len(clips))
    results: List[Dict] = []

    for index, raw_clip in enumerate(clips):
        clip = raw_clip if isinstance(raw_clip, ClipCandidate) else ClipCandidate.from_dict(raw_clip)
        base_fraction = index / total

        def _clip_progress(fraction: float, base: float = base_fraction, name: str = clip.id) -> None:
            _report(progress, base + fraction / total, f"Rendering {name}")

        _clip_progress(0.01)
        entry: Dict = {
            "id": clip.id,
            "title": clip.title,
            "start": round(clip.start, 3),
            "end": round(clip.end, 3),
            "duration": round(clip.duration, 3),
            "score": round(clip.score, 4),
            "breakdown": clip.breakdown,
            "reasons": clip.reasons,
            "hook": clip.hook,
            "hook_text": clip.hook_text,
            "text": clip.text,
            "words": clip.words,
            "has_transcript": clip.has_transcript,
            "mode": options.mode,
            "status": "pending",
        }

        try:
            crop_mode = "crop"
            keyframes: List[CropKeyframe] = []
            if tracker is not None:
                _clip_progress(0.05)
                track = tracker.track(video_path, clip.start, clip.end, sample_fps=2.0)
                keyframes, crop_mode = plan_crop_path(
                    track,
                    target_width=options.width,
                    target_height=options.height,
                    clip_start=clip.start,
                    clip_end=clip.end,
                )
                if crop_mode == "blur" and not options.blurred_background:
                    # Fall back to a centred crop when the user disabled blur.
                    keyframes, crop_mode = [], "crop"

            captions_path = None
            if options.captions and transcript.segments:
                clip_work = work_dir / clip.id
                clip_work.mkdir(parents=True, exist_ok=True)
                captions_path = prepare_captions(
                    transcript.segments,
                    clip.start,
                    clip.end,
                    clip_work,
                    style=options.caption_style,
                    width=options.width,
                    height=options.height,
                )

            safe_id = clip.id.replace("/", "_")
            output = out_dir / f"{safe_id}_{_slugify(clip.title)}.mp4"
            spec = RenderSpec(
                source=str(video_path),
                output=str(output),
                start=clip.start,
                end=clip.end,
                mode=options.mode,
                width=options.width,
                height=options.height,
                fps=options.fps,
                crop_keyframes=keyframes,
                crop_mode=crop_mode,
                captions_path=captions_path,
                caption_style=options.caption_style,
                encoder=options.encoder,
                prefer_hardware=options.prefer_hardware,
                crf=options.crf,
                loudnorm=options.loudnorm,
            )
            result = render_clip(spec, progress=_clip_progress)
            thumbnail = make_thumbnail(result.output, out_dir / f"{safe_id}_thumb.jpg", at=0.6)

            entry.update(
                {
                    "status": "done",
                    "file": str(result.output),
                    "filename": Path(result.output).name,
                    "size": result.size,
                    "width": result.width,
                    "height": result.height,
                    "encoder": result.encoder,
                    "hardware": result.hardware,
                    "captions": result.captions,
                    "crop_mode": crop_mode,
                    "crop_keyframes": [k.to_dict() for k in keyframes],
                    "thumbnail": thumbnail,
                }
            )
        except Exception as exc:  # noqa: BLE001 - keep rendering the rest
            logger.warning("Clip %s failed to render: %s", clip.id, exc)
            entry.update({"status": "error", "error": str(exc)})

        results.append(entry)
        _clip_progress(1.0)

    (out_dir / "clips.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _report(progress, 1.0, "Rendering complete")
    return results


def _slugify(text: str) -> str:
    from ..utils.video_processor import VideoProcessor

    cleaned = VideoProcessor.sanitize_filename(text or "clip")
    cleaned = cleaned.replace(" ", "_")
    return (cleaned[:60] or "clip").strip("._")


def load_analysis(work_dir: str | Path) -> Optional[Dict]:
    """Read a previously saved analysis.json."""
    path = Path(work_dir) / "analysis.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def format_time(seconds: float) -> str:
    return format_timestamp(seconds)
