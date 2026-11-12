"""
Transcription - turn a video's speech into timed segments.

AutoClip is designed to run fully offline on a normal PC, so the preferred
backends are local Whisper implementations. Every backend is optional: if none
is available the pipeline still runs using audio-only analysis, and the UI
tells the user how to enable transcription.

Available backends (auto-detected, best first):

* ``user_srt``      - the user supplied a subtitle file
* ``user_text``     - the user pasted a transcript
* ``faster_whisper``- CTranslate2 Whisper, fastest CPU inference
* ``openai_whisper``- reference implementation (needs torch)
* ``whisper_cpp``   - a ``whisper-cli`` / ``main`` binary in settings or PATH
* ``openai_api``    - any OpenAI-compatible ``/audio/transcriptions`` endpoint
"""
from .models import Segment, Transcript, Word
from .srt import parse_srt_file, parse_srt_text, write_srt
from .engine import (
    TranscriptionError,
    TranscriptionOptions,
    available_backends,
    transcribe,
    backend_status,
)

__all__ = [
    "Segment",
    "Transcript",
    "Word",
    "parse_srt_file",
    "parse_srt_text",
    "write_srt",
    "TranscriptionError",
    "TranscriptionOptions",
    "available_backends",
    "transcribe",
    "backend_status",
]
