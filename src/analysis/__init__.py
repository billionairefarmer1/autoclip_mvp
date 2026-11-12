"""
Offline content analysis - the brain of the no-API-key clipping mode.

Nothing in this package talks to the network. It combines three cheap signals
that are already inside the video file:

* **speech** - timed transcript segments (from Whisper when available)
* **audio**  - short-term energy, loudness, speech/silence and spectral shape
* **vision** - scene changes so cuts land on visual boundaries

From those we build candidate clips, score them for short-form virality
(hook strength, completeness, pacing, energy, emphasis) and explain why each
clip was picked.
"""

from .audio_signals import AudioSignals, extract_audio_signals  # noqa: F401
from .scenes import SceneIndex, detect_scenes  # noqa: F401
from .candidates import (  # noqa: F401
    ClipCandidate,
    CandidateConfig,
    build_candidates,
)

__all__ = [
    "AudioSignals",
    "extract_audio_signals",
    "SceneIndex",
    "detect_scenes",
    "ClipCandidate",
    "CandidateConfig",
    "build_candidates",
]
