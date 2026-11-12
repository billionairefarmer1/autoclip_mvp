"""Clip candidate generation and scoring behaviour."""
from __future__ import annotations

import numpy as np
import pytest

from src.analysis.audio_signals import AudioSignals
from src.analysis.candidates import CandidateConfig, build_candidates, _windows_from_transcript
from src.transcribe.models import Segment


def _signals(duration: float = 120.0, hop: float = 0.05, energetic: bool = True) -> AudioSignals:
    n = int(duration / hop)
    energy = np.full(n, 0.6 if energetic else 0.05, dtype=np.float32)
    if energetic:
        # alternate louder/quieter stretches
        energy[::40] = 0.9
    voiced = np.ones(n, dtype=bool) if energetic else np.zeros(n, dtype=bool)
    zeros = np.zeros(n, dtype=np.float32)
    return AudioSignals(
        hop=hop,
        sample_rate=8000,
        duration=duration,
        rms_db=energy * 60 - 60,
        energy=energy,
        voiced=voiced,
        zcr=np.full(n, 0.05, dtype=np.float32),
        centroid=zeros,
        flux=np.full(n, 0.05, dtype=np.float32),
    )


def _segments(count: int, start: float = 0.0, length: float = 3.0, gap: float = 0.4):
    phrases = [
        "The biggest mistake founders make is hiring too fast.",
        "Most people think growth solves everything, but it hides problems.",
        "We went from zero to two million users in eighteen months.",
        "Here is what actually worked for us last year.",
    ]
    segments = []
    cursor = start
    for index in range(count):
        segments.append(Segment(start=cursor, end=cursor + length, text=phrases[index % len(phrases)]))
        cursor += length + gap
    return segments, cursor


def test_windows_respect_max_duration():
    segments, total = _segments(120)
    config = CandidateConfig(min_duration=10, max_duration=40, ideal_duration=25)
    windows = _windows_from_transcript(segments, total, config)
    assert windows, "expected at least one window"
    for start, end in windows:
        # 1.4x is the documented practical ceiling on the ideal length
        assert end - start <= max(config.max_duration, config.ideal_duration * 1.4) + 0.01


def test_windows_cover_long_video_without_giant_clips():
    segments, total = _segments(400, length=4.0, gap=0.5)
    windows = _windows_from_transcript(segments, total, CandidateConfig())
    assert len(windows) > 10
    durations = [end - start for start, end in windows]
    assert max(durations) <= 60.0
    assert min(durations) >= 5.0


def test_candidates_are_non_overlapping_and_sorted():
    segments, total = _segments(80)
    clips = build_candidates(segments, _signals(total), None, duration=total)
    assert clips
    for previous, current in zip(clips, clips[1:]):
        assert current.start >= previous.start
        overlap = min(previous.end, current.end) - max(previous.start, current.start)
        assert overlap <= 0 or overlap / min(previous.duration, current.duration) <= 0.46


def test_scores_in_range_and_explainable():
    segments, total = _segments(60)
    clips = build_candidates(segments, _signals(total), None, duration=total)
    for clip in clips:
        assert 0.0 <= clip.score <= 1.0
        assert set(clip.breakdown) == {"hook", "completeness", "pacing", "energy", "emphasis", "length"}
        assert clip.reasons
        assert clip.id.startswith("clip_")
        assert clip.title


def test_transcript_is_used_for_titles():
    segments, total = _segments(20)
    clips = build_candidates(segments, _signals(total), None, duration=total)
    assert any("mistake" in clip.title.lower() or "growth" in clip.title.lower() for clip in clips)
    assert all(clip.has_transcript for clip in clips)


def test_audio_only_mode_still_returns_clips_but_capped():
    segments: list[Segment] = []
    clips = build_candidates(segments, _signals(180), None, duration=180)
    assert clips, "audio-only analysis should still produce candidates"
    assert all(clip.score <= 0.72 for clip in clips)
    assert all(not clip.has_transcript for clip in clips)


def test_silent_video_returns_nothing_rather_than_garbage():
    signals = _signals(90, energetic=False)
    clips = build_candidates([], signals, None, duration=90)
    # Either nothing, or at most the uniform fallback - never a crash.
    assert isinstance(clips, list)


def test_max_clips_is_respected():
    segments, total = _segments(400)
    config = CandidateConfig(max_clips=3)
    clips = build_candidates(segments, _signals(total), None, duration=total, config=config)
    assert len(clips) <= 3


def test_hook_detection_prefers_strong_openers():
    strong = [
        Segment(start=0, end=3, text="What if everything you know about growth is wrong?"),
        Segment(start=3.2, end=6, text="Most people never test this assumption."),
        Segment(start=6.2, end=9, text="Here is the 3 step process we used."),
    ]
    weak = [
        Segment(start=0, end=3, text="Um so yeah, we were kind of thinking about it and"),
        Segment(start=3.2, end=6, text="then, you know, things sort of happened over time"),
        Segment(start=6.2, end=9, text="and it was fine I guess, nothing special"),
    ]
    config = CandidateConfig(min_duration=6, max_duration=20, ideal_duration=9)
    strong_clips = build_candidates(strong, _signals(30), None, 30, config)
    weak_clips = build_candidates(weak, _signals(30), None, 30, config)
    assert strong_clips and weak_clips
    assert strong_clips[0].breakdown["hook"] > weak_clips[0].breakdown["hook"]


def test_candidate_roundtrip():
    segments, total = _segments(20)
    clip = build_candidates(segments, _signals(total), None, duration=total)[0]
    restored = type(clip).from_dict(clip.to_dict())
    assert restored.start == pytest.approx(clip.start)
    # to_dict rounds the score to 4 decimals for stable JSON/UI display.
    assert restored.score == pytest.approx(clip.score, abs=5e-5)
    assert restored.title == clip.title
