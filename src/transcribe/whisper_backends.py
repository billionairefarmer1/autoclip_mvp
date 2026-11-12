"""Concrete transcription backends (all optional at runtime)."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ..media.ffmpeg import FFmpegError, get_ffmpeg, probe
from .models import Segment, Transcript, Word

logger = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[float, str], None]]


class BackendUnavailable(RuntimeError):
    """Raised when a backend is not installed/configured."""


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def extract_audio(video_path: str | Path, output_wav: str | Path, sample_rate: int = 16000) -> Path:
    """Extract a mono 16 kHz WAV suitable for speech recognition."""
    video_path = Path(video_path)
    output_wav = Path(output_wav)
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        "-y",
        str(output_wav),
    ]
    try:
        from ..media.ffmpeg import run

        run(args, timeout=1800)
    except FFmpegError as exc:
        raise BackendUnavailable(f"Could not extract audio for transcription: {exc}") from exc
    if not output_wav.is_file():
        raise BackendUnavailable("Audio extraction produced no file")
    return output_wav


def _segments_from_whisper_result(result: Dict) -> List[Segment]:
    segments: List[Segment] = []
    for item in result.get("segments") or []:
        words = [
            Word(start=float(w.get("start") or 0.0), end=float(w.get("end") or 0.0), text=(w.get("word") or "").strip())
            for w in item.get("words") or []
            if (w.get("word") or "").strip()
        ]
        text = (item.get("text") or "").strip()
        if not text:
            continue
        segments.append(
            Segment(
                start=float(item.get("start") or 0.0),
                end=float(item.get("end") or 0.0),
                text=text,
                words=words,
            )
        )
    return segments


# --------------------------------------------------------------------------- #
# faster-whisper (CPU friendly)
# --------------------------------------------------------------------------- #
_MODEL_CACHE: Dict[str, object] = {}


def faster_whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def transcribe_faster_whisper(
    video_path: str | Path,
    model_size: str = "base",
    language: Optional[str] = None,
    device: str = "auto",
    compute_type: str = "int8",
    download_root: Optional[str] = None,
    progress: ProgressCb = None,
    word_timestamps: bool = True,
) -> Transcript:
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:  # noqa: BLE001
        raise BackendUnavailable(
            "faster-whisper is not installed. Run: pip install faster-whisper"
        ) from exc

    media = probe(video_path)
    if not media.has_audio:
        raise BackendUnavailable("The video has no audio track to transcribe")

    if progress:
        progress(0.02, "Preparing audio")

    cache_key = f"{model_size}:{device}:{compute_type}"
    model = _MODEL_CACHE.get(cache_key)
    if model is None:
        if progress:
            progress(0.05, f"Loading Whisper '{model_size}' model (first run downloads it)")
        resolved_device = "cuda" if device == "auto" and _cuda_available() else "cpu"
        if device != "auto":
            resolved_device = device
        try:
            model = WhisperModel(
                model_size,
                device=resolved_device,
                compute_type=compute_type if resolved_device == "cpu" else "float16",
                download_root=download_root,
            )
        except Exception as exc:  # noqa: BLE001
            raise BackendUnavailable(f"Could not load Whisper model '{model_size}': {exc}") from exc
        _MODEL_CACHE[cache_key] = model

    with tempfile.TemporaryDirectory(prefix="autoclip-asr-") as tmpdir:
        wav = extract_audio(video_path, Path(tmpdir) / "audio.wav")
        if progress:
            progress(0.12, "Transcribing speech")

        try:
            segment_iter, info = model.transcribe(
                str(wav),
                language=language or None,
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 400},
                word_timestamps=word_timestamps,
                condition_on_previous_text=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise BackendUnavailable(f"Whisper transcription failed: {exc}") from exc

        total = 0.0
        try:
            total = float(getattr(info, "duration", 0.0) or media.duration or 0.0)
        except (TypeError, ValueError):
            total = media.duration

        segments: List[Segment] = []
        for item in segment_iter:
            words = []
            for word in getattr(item, "words", None) or []:
                text = (getattr(word, "word", "") or "").strip()
                if text:
                    words.append(Word(start=float(word.start), end=float(word.end), text=text))
            text = (item.text or "").strip()
            if not text:
                continue
            segments.append(
                Segment(start=float(item.start), end=float(item.end), text=text, words=words)
            )
            if progress and total:
                progress(0.12 + 0.85 * min(1.0, float(item.end) / total), "Transcribing speech")

    if progress:
        progress(1.0, "Transcription complete")
    return Transcript(
        segments=segments,
        language=getattr(info, "language", "") or (language or ""),
        source="faster_whisper",
        duration=total or media.duration,
    )


def _cuda_available() -> bool:
    try:
        import torch  # type: ignore

        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# openai-whisper (needs torch)
# --------------------------------------------------------------------------- #
def openai_whisper_available() -> bool:
    try:
        import whisper  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def transcribe_openai_whisper(
    video_path: str | Path,
    model_size: str = "base",
    language: Optional[str] = None,
    download_root: Optional[str] = None,
    progress: ProgressCb = None,
) -> Transcript:
    try:
        import whisper  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise BackendUnavailable("openai-whisper is not installed. Run: pip install openai-whisper") from exc

    media = probe(video_path)
    if not media.has_audio:
        raise BackendUnavailable("The video has no audio track to transcribe")

    if progress:
        progress(0.05, f"Loading Whisper '{model_size}' model")
    model = _MODEL_CACHE.get(f"openai:{model_size}")
    if model is None:
        model = whisper.load_model(model_size, download_root=download_root)
        _MODEL_CACHE[f"openai:{model_size}"] = model

    with tempfile.TemporaryDirectory(prefix="autoclip-asr-") as tmpdir:
        wav = extract_audio(video_path, Path(tmpdir) / "audio.wav")
        if progress:
            progress(0.15, "Transcribing speech")
        result = model.transcribe(str(wav), language=language or None, word_timestamps=False)

    if progress:
        progress(1.0, "Transcription complete")
    return Transcript(
        segments=_segments_from_whisper_result(result),
        language=result.get("language", "") or (language or ""),
        source="openai_whisper",
        duration=media.duration,
    )


# --------------------------------------------------------------------------- #
# whisper.cpp
# --------------------------------------------------------------------------- #
def transcribe_whisper_cpp(
    video_path: str | Path,
    binary_path: str,
    model_path: Optional[str] = None,
    language: Optional[str] = None,
    progress: ProgressCb = None,
) -> Transcript:
    binary = shutil.which(binary_path) or (binary_path if Path(binary_path).is_file() else None)
    if not binary:
        raise BackendUnavailable(f"whisper.cpp binary not found: {binary_path}")
    if not model_path or not Path(model_path).is_file():
        raise BackendUnavailable("whisper.cpp needs a GGML model file (set whisper_cpp_model in settings)")

    media = probe(video_path)
    with tempfile.TemporaryDirectory(prefix="autoclip-asr-") as tmpdir:
        wav = extract_audio(video_path, Path(tmpdir) / "audio.wav")
        out_prefix = Path(tmpdir) / "out"
        cmd = [
            binary,
            "-m",
            model_path,
            "-f",
            str(wav),
            "-oj",
            "-of",
            str(out_prefix),
        ]
        if language:
            cmd += ["-l", language]
        if progress:
            progress(0.15, "Transcribing speech (whisper.cpp)")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        if result.returncode != 0:
            raise BackendUnavailable(f"whisper.cpp failed: {result.stderr[-400:]}")
        json_file = Path(f"{out_prefix}.json")
        if not json_file.is_file():
            raise BackendUnavailable("whisper.cpp produced no JSON output")
        data = json.loads(json_file.read_text(encoding="utf-8"))

    segments: List[Segment] = []
    for item in data.get("transcription") or []:
        offsets = item.get("offsets") or {}
        start_ms = float(offsets.get("from") or 0.0)
        end_ms = float(offsets.get("to") or 0.0)
        text = (item.get("text") or "").strip()
        if text:
            segments.append(Segment(start=start_ms / 1000, end=end_ms / 1000, text=text))
    if progress:
        progress(1.0, "Transcription complete")
    return Transcript(segments=segments, source="whisper_cpp", duration=media.duration)


# --------------------------------------------------------------------------- #
# OpenAI-compatible API (Groq, OpenAI, local servers...)
# --------------------------------------------------------------------------- #
def transcribe_openai_api(
    video_path: str | Path,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    model: str = "whisper-1",
    language: Optional[str] = None,
    timeout: int = 900,
    progress: ProgressCb = None,
) -> Transcript:
    try:
        from openai import OpenAI
    except Exception as exc:  # noqa: BLE001
        raise BackendUnavailable("openai package is required for the API backend") from exc
    if not api_key:
        raise BackendUnavailable("No API key configured for the cloud transcription backend")

    media = probe(video_path)
    size_mb = media.size / (1024 * 1024)
    if size_mb > 24:
        raise BackendUnavailable(
            f"File is {size_mb:.0f} MB; most cloud endpoints cap uploads at 25 MB. "
            "Use a local Whisper model for long videos."
        )

    if progress:
        progress(0.1, "Uploading audio for transcription")
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
    try:
        with open(video_path, "rb") as handle:
            response = client.audio.transcriptions.create(
                model=model,
                file=handle,
                language=language or None,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
    except Exception as exc:  # noqa: BLE001
        raise BackendUnavailable(f"Cloud transcription failed: {exc}") from exc

    if progress:
        progress(1.0, "Transcription complete")
    data = response.model_dump() if hasattr(response, "model_dump") else dict(response)
    segments = _segments_from_whisper_result(data)
    return Transcript(
        segments=segments,
        language=data.get("language") or (language or ""),
        source="openai_api",
        duration=media.duration,
    )
