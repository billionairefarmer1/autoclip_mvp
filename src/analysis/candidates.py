"""
Clip candidate generation and scoring.

Why this exists
---------------
The original AutoClip relied on an LLM to decide what a good clip is. That
requires an API key and costs money per video. This module gets most of the way
there offline, using signals that are already in the file:

1. **Cut points** - spoken sentences (or audio pauses when there is no
   transcript) are the atoms; nothing is cut mid-word.
2. **Windows** - consecutive atoms are packed into clips between
   ``min_duration`` and ``max_duration``.
3. **Boundary snapping** - start/end are nudged onto scene cuts and silences so
   clips begin and end cleanly.
4. **Scoring** - a weighted blend of hook strength, self-containedness, pacing,
   energy, emphasis and length fit. Each component is explainable, so the UI can
   show *why* a clip was picked.

The scoring is deliberately transparent (see ``ClipCandidate.breakdown``)
rather than a black box: users can tune the weights in settings.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np

from ..transcribe.models import Segment
from .audio_signals import AudioSignals
from .scenes import SceneIndex

logger = logging.getLogger(__name__)

# Words that tend to open a thought in spoken content.
HOOK_OPENERS = (
    "the biggest", "the best", "the worst", "the secret", "the truth", "here's",
    "here is", "what if", "most people", "nobody", "everyone", "you need",
    "you should", "never", "always", "imagine", "listen", "look", "so",
    "because", "actually", "honestly", "let me", "i learned", "i realized",
    "the problem", "the reason", "this is why", "how to", "why",
)

# Words that suggest a payoff or a conclusion (good place to end).
PAYOFF_MARKERS = (
    "that's why", "that is why", "so that's", "which means", "the result",
    "in the end", "the lesson", "so the key", "and that changed", "and that's",
)

# Filler that makes a clip feel unfinished if it ends there.
DANGLING_ENDERS = (
    "and", "but", "so", "because", "which", "that", "if", "when", "or", "the",
    "a", "an", "to", "of", "then", "with", "for", "like",
)

QUESTION_STARTS = ("why", "how", "what", "when", "who", "where", "do you", "have you", "did you")


@dataclass
class CandidateConfig:
    """Tunable knobs for candidate generation and scoring."""

    min_duration: float = 12.0
    max_duration: float = 60.0
    ideal_duration: float = 28.0
    max_clips: int = 24
    min_score: float = 0.0
    snap_to_scenes: bool = True
    snap_tolerance: float = 1.5
    overlap_penalty: float = 0.35
    # Scoring weights (normalised at scoring time).
    weight_hook: float = 1.4
    weight_completeness: float = 1.2
    weight_pacing: float = 1.0
    weight_energy: float = 0.8
    weight_emphasis: float = 0.7
    weight_length: float = 0.9

    @classmethod
    def from_dict(cls, data: Optional[Dict]) -> "CandidateConfig":
        data = data or {}
        config = cls()
        for key, value in data.items():
            if hasattr(config, key) and value is not None:
                try:
                    setattr(config, key, type(getattr(config, key))(value))
                except (TypeError, ValueError):
                    logger.warning("Ignoring invalid value for %s: %r", key, value)
        return config


@dataclass
class ClipCandidate:
    id: str
    start: float
    end: float
    text: str
    score: float
    breakdown: Dict[str, float] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    words: int = 0
    has_transcript: bool = True
    title: str = ""
    hook: str = ""
    hook_text: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> Dict:
        data = asdict(self)
        data["duration"] = round(self.duration, 3)
        data["start"] = round(self.start, 3)
        data["end"] = round(self.end, 3)
        data["score"] = round(self.score, 4)
        return data

    @classmethod
    def from_dict(cls, data: Dict) -> "ClipCandidate":
        return cls(
            id=str(data.get("id")),
            start=float(data.get("start") or 0),
            end=float(data.get("end") or 0),
            text=data.get("text") or "",
            score=float(data.get("score") or 0),
            breakdown=data.get("breakdown") or {},
            reasons=list(data.get("reasons") or []),
            words=int(data.get("words") or 0),
            has_transcript=bool(data.get("has_transcript", True)),
            title=data.get("title") or "",
            hook=data.get("hook") or "",
            hook_text=data.get("hook_text") or "",
        )


# --------------------------------------------------------------------------- #
# Candidate generation
# --------------------------------------------------------------------------- #
def build_candidates(
    transcript_segments: Sequence[Segment],
    audio: AudioSignals,
    scenes: Optional[SceneIndex] = None,
    duration: float = 0.0,
    config: Optional[CandidateConfig] = None,
) -> List[ClipCandidate]:
    """Turn transcript segments into scored, de-overlapped clip candidates."""
    config = config or CandidateConfig()
    total_duration = duration or audio.duration or (
        transcript_segments[-1].end if transcript_segments else 0.0
    )
    if total_duration <= 0:
        return []

    if transcript_segments:
        windows = _windows_from_transcript(transcript_segments, total_duration, config)
    else:
        windows = _windows_from_audio(audio, total_duration, config)

    candidates: List[ClipCandidate] = []
    for index, (start, end) in enumerate(windows):
        start, end = _snap_bounds(start, end, scenes, audio, total_duration, config)
        if end - start < max(4.0, config.min_duration * 0.6):
            continue
        segments = [s for s in transcript_segments if s.end > start and s.start < end]
        text = " ".join(s.text.strip() for s in segments).strip()
        candidate = _score_window(
            index=index,
            start=start,
            end=end,
            text=text,
            segments=segments,
            audio=audio,
            config=config,
        )
        candidates.append(candidate)

    candidates = _select_non_overlapping(candidates, config)
    for order, candidate in enumerate(candidates):
        candidate.id = f"clip_{order + 1:03d}"
    return candidates


def _windows_from_transcript(
    segments: Sequence[Segment], duration: float, config: CandidateConfig
) -> List[tuple[float, float]]:
    """Greedy packing: accumulate whole sentences into clip-sized windows.

    Starts are biased toward sentence boundaries that follow a pause, because
    those are natural entry points.
    """
    windows: List[tuple[float, float]] = []
    segments = [s for s in segments if s.text.strip()]
    if not segments:
        return windows

    i = 0
    count = len(segments)
    while i < count:
        start = segments[i].start
        end = segments[i].end
        # Practical ceiling: clips that run far past the ideal length are
        # rarely the ones people post, even if the speech never pauses.
        hard_limit = min(config.max_duration, config.ideal_duration * 1.4)

        j = i + 1
        while j < count:
            gap = segments[j].start - end
            # Never exceed the hard limit, whatever the gaps look like.
            if end - start >= hard_limit:
                break
            if j > i + 1:
                # Once we are past the minimum, prefer to end at a natural
                # pause: dead air is what makes a clip feel boring.
                if segments[j - 1].end - start >= config.min_duration and gap > 0.45:
                    break
                # Past the ideal length, only continue into unbroken speech.
                if end - start > config.ideal_duration and gap > 0.2:
                    break
            next_end = segments[j].end
            if next_end - start > hard_limit:
                break
            end = next_end
            j += 1
        if end - start >= config.min_duration * 0.75:
            windows.append((start, end))
            # Start the next window near the end of this one, creating overlap
            # so no good moment is lost to arbitrary boundaries.
            overlap_target = start + (end - start) * 0.55
            next_i = j
            for k in range(j - 1, i, -1):
                if segments[k].start <= overlap_target:
                    next_i = k + 1
                    break
            i = max(next_i, i + 1)
        else:
            i += 1

    # Also emit windows aligned to pauses (a fresh thought after silence).
    pauses = _pause_indices(segments)
    for pause_index in pauses:
        start = segments[pause_index].start
        end = start
        for seg in segments[pause_index:]:
            if seg.end - start > config.ideal_duration:
                break
            end = seg.end
            if end - start >= config.min_duration:
                windows.append((start, end))
                break
        if len(windows) > 400:  # keep this bounded on very long videos
            break

    return windows


def _pause_indices(segments: Sequence[Segment], min_gap: float = 0.6) -> List[int]:
    indices: List[int] = []
    for index in range(1, len(segments)):
        if segments[index].start - segments[index - 1].end >= min_gap:
            indices.append(index)
    return indices


def _windows_from_audio(
    audio: AudioSignals, duration: float, config: CandidateConfig
) -> List[tuple[float, float]]:
    """No transcript: slide a window over the audio and keep lively stretches."""
    if len(audio.energy) == 0:
        # Last resort: uniform windows so the tool still produces something.
        step = config.ideal_duration
        return [
            (t, min(duration, t + config.ideal_duration))
            for t in np.arange(0, max(0.0, duration - config.min_duration), step)
        ]

    step = max(4.0, config.ideal_duration * 0.5)
    windows: List[tuple[float, float]] = []
    t = 0.0
    while t + config.min_duration <= duration:
        end = min(duration, t + config.ideal_duration)
        stats = audio.window(t, end)
        if stats["speech_ratio"] > 0.45 and stats["mean_energy"] > 0.18:
            windows.append((t, end))
        t += step

    if not windows:
        step = config.ideal_duration
        windows = [
            (t, min(duration, t + config.ideal_duration))
            for t in np.arange(0, max(0.0, duration - config.min_duration), step)
        ]
    return windows


def _snap_bounds(
    start: float,
    end: float,
    scenes: Optional[SceneIndex],
    audio: AudioSignals,
    duration: float,
    config: CandidateConfig,
) -> tuple[float, float]:
    """Nudge boundaries onto scene cuts or silences so cuts feel intentional."""
    if config.snap_to_scenes and scenes and scenes.cuts:
        cut = scenes.nearest(start, config.snap_tolerance)
        if cut is not None and cut < end - 3.0:
            start = cut
        cut = scenes.nearest(end, config.snap_tolerance)
        if cut is not None and cut > start + 3.0:
            end = cut

    # Prefer starting in silence (no clipped first syllable).
    silences = audio.silence_regions(0.25) if len(audio.voiced) else []
    for silence_start, silence_end in silences:
        if abs(silence_start - start) <= 0.6 and silence_start < end - 3.0:
            start = silence_start
            break
        if silence_start < start < silence_end and silence_end < end - 3.0:
            start = min(silence_end, start + 0.4)
            break

    start = max(0.0, min(start, max(0.0, duration - 2.0)))
    end = min(duration, max(end, start + 2.0))
    return round(start, 3), round(end, 3)


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def _score_window(
    index: int,
    start: float,
    end: float,
    text: str,
    segments: Sequence[Segment],
    audio: AudioSignals,
    config: CandidateConfig,
) -> ClipCandidate:
    duration = end - start
    words = len(text.split()) if text else 0
    has_transcript = bool(text)

    stats = audio.window(start, end)
    length_fit = _length_fit(duration, config)
    pacing = _pacing_score(text, duration, words, stats)
    energy = _energy_score(stats)
    hook = _hook_score(text, segments, start)
    emphasis = _emphasis_score(audio, start, end)
    completeness = _completeness_score(text, segments, start, end, duration)

    weights = {
        "hook": config.weight_hook,
        "completeness": config.weight_completeness,
        "pacing": config.weight_pacing,
        "energy": config.weight_energy,
        "emphasis": config.weight_emphasis,
        "length": config.weight_length,
    }
    total_weight = sum(weights.values()) or 1.0
    components = {
        "hook": hook,
        "completeness": completeness,
        "pacing": pacing,
        "energy": energy,
        "emphasis": emphasis,
        "length": length_fit,
    }
    score = sum(components[k] * w for k, w in weights.items()) / total_weight

    if not has_transcript:
        # Without words we cannot judge hooks or endings; lean on signals only
        # and cap the score so those clips do not outrank transcribed ones.
        score = min(score, 0.72)

    reasons = _build_reasons(components, stats, duration, text)
    hook_text, title = _make_title_hook(text, segments, start, end)

    return ClipCandidate(
        id=f"tmp_{index}",
        start=start,
        end=end,
        text=text,
        score=float(score),
        breakdown={k: round(v, 4) for k, v in components.items()},
        reasons=reasons,
        words=words,
        has_transcript=has_transcript,
        title=title,
        hook=_hook_score_label(hook),
        hook_text=hook_text,
    )


def _length_fit(duration: float, config: CandidateConfig) -> float:
    if duration <= 0:
        return 0.0
    if config.min_duration * 0.85 <= duration <= config.max_duration:
        # Gaussian around the sweet spot, floored so usable clips stay usable.
        sigma = max(6.0, (config.max_duration - config.min_duration) / 3)
        value = np.exp(-0.5 * ((duration - config.ideal_duration) / sigma) ** 2)
        return float(max(0.45, value))
    if duration < config.min_duration * 0.85:
        return float(max(0.0, duration / max(1.0, config.min_duration * 0.85)) * 0.5)
    return float(max(0.25, 1.0 - (duration - config.max_duration) / (config.max_duration * 1.5)))


def _pacing_score(text: str, duration: float, words: int, stats: Dict[str, float]) -> float:
    """Speech density: dead air is the #1 reason a clip feels boring."""
    wps = words / duration if duration else 0.0
    # 2.2-4.0 words/sec is energetic but comprehensible.
    if words:
        density = float(np.clip((wps - 1.0) / 2.0, 0.0, 1.0))
    else:
        density = float(np.clip(stats["speech_ratio"], 0.0, 1.0)) * 0.6
    speech = stats["speech_ratio"]
    silence_penalty = float(np.clip(1.0 - stats["silence_ratio"] * 1.6, 0.0, 1.0))
    return float(0.55 * density + 0.25 * speech + 0.20 * silence_penalty)


def _energy_score(stats: Dict[str, float]) -> float:
    mean = stats["mean_energy"]
    peak = stats["peak_energy"]
    dynamics = stats["energy_dynamics"]
    # Loud with variation reads as passionate; flat loud is just noise.
    base = float(np.clip(mean / 0.55, 0.0, 1.0))
    variation = float(np.clip(dynamics / 0.12, 0.0, 1.0))
    peak_bonus = float(np.clip((peak - mean) / 0.3, 0.0, 1.0))
    return float(0.5 * base + 0.3 * variation + 0.2 * peak_bonus)


def _hook_score(text: str, segments: Sequence[Segment], start: float) -> float:
    """Does the first ~8 seconds earn attention?"""
    if not text:
        return 0.35
    opening = " ".join(
        s.text for s in segments if s.start < start + 8.0
    ) or text[:160]
    opening_l = opening.lower().strip()
    score = 0.35

    if opening_l.startswith(QUESTION_STARTS):
        score += 0.22
    for opener in HOOK_OPENERS:
        if opening_l.startswith(opener):
            score += 0.22
            break
    else:
        if any(opener in opening_l[:60] for opener in HOOK_OPENERS):
            score += 0.1

    if re.search(r"\b\d+([.,]\d+)?\s*(%|percent|x|million|thousand|billion|dollars|years|days)?\b", opening_l):
        score += 0.12  # concrete numbers are quotable
    if "?" in opening:
        score += 0.08
    # A very long first sentence means a slow wind-up.
    if len(opening.split()) > 42:
        score -= 0.12
    if len(opening.split()) < 5:
        score -= 0.08
    return float(np.clip(score, 0.0, 1.0))


def _emphasis_score(audio: AudioSignals, start: float, end: float) -> float:
    """Reward emphasis: peaks above the clip's own baseline, and spectrum swings."""
    if len(audio.energy) == 0:
        return 0.4
    i0 = max(0, int(start / audio.hop))
    i1 = min(len(audio.energy), int(end / audio.hop))
    if i1 <= i0 + 2:
        return 0.4
    energy = audio.energy[i0:i1]
    flux = audio.flux[i0:i1]
    baseline = float(np.median(energy)) + 1e-6
    peaks = float(np.mean(energy > baseline * 1.35))
    flux_level = float(np.clip(np.mean(flux) / 0.12, 0.0, 1.0))
    peak_level = float(np.clip(peaks / 0.25, 0.0, 1.0))
    return float(0.5 * peak_level + 0.5 * flux_level)


def _completeness_score(
    text: str,
    segments: Sequence[Segment],
    start: float,
    end: float,
    duration: float,
) -> float:
    """Does the clip start and finish a thought?"""
    if not text:
        return 0.5
    score = 0.5
    first_segment = next((s for s in segments if s.start >= start - 0.01), None)
    last_segment = None
    for seg in segments:
        if seg.end <= end + 0.01:
            last_segment = seg
    if first_segment is not None and first_segment.start - start <= 0.35:
        score += 0.15  # starts exactly on a sentence
    if last_segment is not None and end - last_segment.end <= 0.45:
        score += 0.15  # ends exactly on a sentence

    tail = text.rstrip(" .!?\"'").split()
    if tail:
        last_word = tail[-1].lower().strip(",;:")
        if last_word in DANGLING_ENDERS:
            score -= 0.18
    if text.rstrip().endswith((".", "!", "?", "。", "！", "？")):
        score += 0.1
    for marker in PAYOFF_MARKERS:
        if marker in text.lower():
            score += 0.12
            break
    if duration < 8:
        score -= 0.15
    return float(np.clip(score, 0.0, 1.0))


def _select_non_overlapping(
    candidates: List[ClipCandidate], config: CandidateConfig
) -> List[ClipCandidate]:
    """Greedy highest-score-first selection with a soft overlap penalty."""
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda c: c.score, reverse=True)
    selected: List[ClipCandidate] = []
    for candidate in ordered:
        if len(selected) >= config.max_clips:
            break
        if candidate.score < config.min_score:
            continue
        conflicting = False
        for chosen in selected:
            overlap = min(candidate.end, chosen.end) - max(candidate.start, chosen.start)
            if overlap <= 0:
                continue
            smaller = min(candidate.duration, chosen.duration) or 1.0
            ratio = overlap / smaller
            if ratio > 0.45:
                conflicting = True
                break
        if not conflicting:
            selected.append(candidate)
    selected.sort(key=lambda c: c.start)
    return selected


# --------------------------------------------------------------------------- #
# Human-readable output
# --------------------------------------------------------------------------- #
def _build_reasons(
    components: Dict[str, float], stats: Dict[str, float], duration: float, text: str
) -> List[str]:
    reasons: List[str] = []
    if components["hook"] >= 0.7:
        reasons.append("Strong opening hook")
    if components["completeness"] >= 0.75:
        reasons.append("Self-contained story with a clean ending")
    if components["pacing"] >= 0.7:
        reasons.append("Tight pacing, little dead air")
    if stats.get("speech_ratio", 0) >= 0.75:
        reasons.append("Dense speech throughout")
    if components["energy"] >= 0.7:
        reasons.append("Loud, high-energy delivery")
    if components["emphasis"] >= 0.7:
        reasons.append("Emphasis peaks on key lines")
    if components["length"] >= 0.8:
        reasons.append(f"Ideal short-form length ({duration:.0f}s)")
    if components["hook"] < 0.4:
        reasons.append("Weaker opening - consider trimming the first few seconds")
    if components["pacing"] < 0.4:
        reasons.append("Some dead air - tighten the cut")
    return reasons


def _hook_score_label(hook: float) -> str:
    if hook >= 0.75:
        return "very strong"
    if hook >= 0.55:
        return "strong"
    if hook >= 0.4:
        return "average"
    return "weak"


def _make_title_hook(
    text: str, segments: Sequence[Segment], start: float, end: float
) -> tuple[str, str]:
    """Derive a readable hook line and a working title without an LLM.

    Heuristic: use the first complete sentence in the clip, cleaned up. Titles
    are intentionally plain - they are a starting point the user can edit.
    """
    if not text:
        return "", f"Clip at {_mmss(start)}"

    sentences = re.split(r"(?<=[.!?。！？])\s+", text.strip())
    hook = sentences[0].strip() if sentences else text[:140]
    hr_units = (end - start) / 3600
    suffix = f" ({int(hr_units)}h)" if hr_units >= 1 else ""
    hook_text = hook[:240]

    title_source = hook
    # Drop lead-ins such as "So,", "And then," to make titles punchier.
    title_source = re.sub(r"^(so|and|but|well|okay|ok|um|uh)[,\s]+", "", title_source, flags=re.I)
    words = title_source.split()
    if len(words) > 12:
        title_source = " ".join(words[:12]).rstrip(",;:") + "…"
    title = title_source.replace('"', "").strip() or f"Clip at {_mmss(start)}"
    if not title[0].isupper():
        title = title[0].upper() + title[1:]
    return hook_text, f"{title}{suffix}"


def _mmss(seconds: float) -> str:
    minutes, secs = divmod(int(max(0, seconds)), 60)
    return f"{minutes:02d}:{secs:02d}"
