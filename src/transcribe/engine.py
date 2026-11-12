"""Transcription front door: backend detection and dispatch."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .models import Transcript
from . import whisper_backends as wb

logger = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[float, str], None]]


class TranscriptionError(RuntimeError):
    """Transcription failed for a backend that was otherwise available."""


@dataclass
class TranscriptionOptions:
    """User-facing transcription settings (mirrors data/settings.json)."""

    backend: str = "auto"
    model_size: str = "base"
    language: Optional[str] = None
    device: str = "auto"
    compute_type: str = "int8"
    model_dir: Optional[str] = None
    whisper_cpp_binary: Optional[str] = None
    whisper_cpp_model: Optional[str] = None
    api_key: Optional[str] = None
    api_base_url: str = "https://api.openai.com/v1"
    api_model: str = "whisper-1"

    @classmethod
    def from_dict(cls, data: Optional[Dict]) -> "TranscriptionOptions":
        data = data or {}
        return cls(
            backend=str(data.get("transcription_backend") or data.get("backend") or "auto"),
            model_size=str(data.get("whisper_model") or data.get("model_size") or "base"),
            language=(data.get("transcription_language") or data.get("language") or None) or None,
            device=str(data.get("whisper_device") or data.get("device") or "auto"),
            compute_type=str(data.get("whisper_compute_type") or data.get("compute_type") or "int8"),
            model_dir=data.get("whisper_model_dir") or data.get("model_dir"),
            whisper_cpp_binary=data.get("whisper_cpp_binary"),
            whisper_cpp_model=data.get("whisper_cpp_model"),
            api_key=data.get("transcription_api_key") or data.get("api_key"),
            api_base_url=data.get("transcription_api_base_url") or data.get("api_base_url") or "https://api.openai.com/v1",
            api_model=data.get("transcription_api_model") or data.get("api_model") or "whisper-1",
        )


def backend_status() -> Dict[str, object]:
    """Report which backends are usable, so the UI can guide the user."""
    fw = wb.faster_whisper_available()
    ow = wb.openai_whisper_available()
    cpp_binary = os.getenv("WHISPER_CPP_BINARY") or None
    return {
        "faster_whisper": {
            "available": fw,
            "label": "faster-whisper (recommended, CPU friendly)",
            "install": "pip install faster-whisper",
        },
        "openai_whisper": {
            "available": ow,
            "label": "openai-whisper (needs PyTorch)",
            "install": "pip install openai-whisper",
        },
        "whisper_cpp": {
            "available": bool(cpp_binary),
            "label": "whisper.cpp binary",
            "install": "Set whisper_cpp_binary and whisper_cpp_model in settings",
        },
        "openai_api": {
            "available": True,
            "label": "Cloud speech-to-text API (optional)",
            "install": "Set transcription_api_key in settings",
        },
        "user_srt": {"available": True, "label": "Upload or auto-detect subtitles", "install": ""},
        "user_text": {"available": True, "label": "Paste a transcript", "install": ""},
        "audio_only": {
            "available": True,
            "label": "No speech model - score clips from audio and scene changes only",
            "install": "",
        },
    }


def available_backends() -> List[str]:
    """List auto-detectable *speech* backends in priority order."""
    order: List[str] = []
    if wb.faster_whisper_available():
        order.append("faster_whisper")
    if wb.openai_whisper_available():
        order.append("openai_whisper")
    return order


def transcribe(
    video_path: str | Path,
    options: Optional[TranscriptionOptions] = None,
    progress: ProgressCb = None,
    srt_path: Optional[str | Path] = None,
) -> Transcript:
    """Transcribe ``video_path`` with the best available backend.

    ``srt_path`` short-circuits everything (the user's own subtitles always win).
    """
    options = options or TranscriptionOptions.from_dict(None)

    if srt_path and Path(srt_path).is_file():
        from .srt import parse_srt_file

        transcript = parse_srt_file(srt_path)
        transcript.duration = transcript.duration or _duration(video_path)
        if transcript.segments:
            logger.info("Using supplied subtitles: %s (%d segments)", srt_path, len(transcript.segments))
            if progress:
                progress(1.0, "Using supplied subtitles")
            return transcript

    requested = (options.backend or "auto").strip().lower()
    candidates: List[str] = []
    if requested in ("", "auto"):
        candidates = available_backends()
        # An explicitly configured cloud endpoint is a reasonable last resort.
        if options.api_key and options.api_key not in ("", "your-api-key-here"):
            candidates.append("openai_api")
    else:
        candidates = [requested]

    errors: List[str] = []
    for backend in candidates:
        try:
            if backend == "faster_whisper":
                return wb.transcribe_faster_whisper(
                    video_path,
                    model_size=options.model_size,
                    language=options.language,
                    device=options.device,
                    compute_type=options.compute_type,
                    download_root=options.model_dir,
                    progress=progress,
                )
            if backend == "openai_whisper":
                return wb.transcribe_openai_whisper(
                    video_path,
                    model_size=options.model_size,
                    language=options.language,
                    download_root=options.model_dir,
                    progress=progress,
                )
            if backend == "whisper_cpp":
                if not options.whisper_cpp_binary:
                    raise wb.BackendUnavailable("whisper_cpp_binary is not configured")
                return wb.transcribe_whisper_cpp(
                    video_path,
                    binary_path=options.whisper_cpp_binary,
                    model_path=options.whisper_cpp_model,
                    language=options.language,
                    progress=progress,
                )
            if backend == "openai_api":
                return wb.transcribe_openai_api(
                    video_path,
                    api_key=options.api_key or "",
                    base_url=options.api_base_url,
                    model=options.api_model,
                    language=options.language,
                    progress=progress,
                )
            errors.append(f"Unknown transcription backend: {backend}")
        except wb.BackendUnavailable as exc:
            logger.info("Transcription backend '%s' unavailable: %s", backend, exc)
            errors.append(f"{backend}: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Transcription backend '%s' failed: %s", backend, exc)
            errors.append(f"{backend}: {exc}")

    detail = "; ".join(errors) if errors else "no speech recognition engine installed"
    logger.info("No transcription available (%s) - continuing with audio analysis only", detail)
    if progress:
        progress(1.0, "No speech model available - using audio analysis")
    transcript = Transcript(source="none", duration=_duration(video_path))
    transcript.metadata = {"errors": errors}  # type: ignore[attr-defined]
    return transcript


def _duration(video_path: str | Path) -> float:
    try:
        from ..media.ffmpeg import probe

        return probe(video_path).duration
    except Exception:  # noqa: BLE001
        return 0.0
