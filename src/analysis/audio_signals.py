"""
Frame-level audio analysis used for clip scoring and for speech-free operation.

The whole file is decoded once to a temporary mono PCM stream, then processed
with vectorised numpy operations, so an hour of audio takes a few seconds and
a few hundred MB of temporary disk - not memory.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import numpy as np

from ..media.ffmpeg import get_ffmpeg, probe

logger = logging.getLogger(__name__)

DEFAULT_SAMPLE_RATE = 8000
DEFAULT_HOP_SECONDS = 0.05  # 50 ms resolution


@dataclass
class AudioSignals:
    """Per-hop audio features. Every array has ``len(energy)`` elements."""

    hop: float
    sample_rate: int
    duration: float
    rms_db: np.ndarray
    energy: np.ndarray  # 0..1 normalised RMS
    voiced: np.ndarray  # bool, likely speech
    zcr: np.ndarray  # zero-crossing rate
    centroid: np.ndarray  # spectral centroid normalised 0..1
    flux: np.ndarray  # spectral change (onset strength)

    def window(self, start: float, end: float) -> Dict[str, float]:
        """Aggregate features over ``[start, end]`` seconds."""
        if len(self.energy) == 0:
            return {
                "mean_energy": 0.0,
                "peak_energy": 0.0,
                "energy_dynamics": 0.0,
                "speech_ratio": 0.0,
                "silence_ratio": 1.0,
                "mean_flux": 0.0,
            }
        i0 = max(0, min(len(self.energy) - 1, int(start / self.hop)))
        i1 = min(len(self.energy), max(i0 + 1, int(end / self.hop)))
        energy = self.energy[i0:i1]
        voiced = self.voiced[i0:i1]
        flux = self.flux[i0:i1]
        if energy.size == 0:
            return {
                "mean_energy": 0.0,
                "peak_energy": 0.0,
                "energy_dynamics": 0.0,
                "speech_ratio": 0.0,
                "silence_ratio": 1.0,
                "mean_flux": 0.0,
            }
        return {
            "mean_energy": float(np.mean(energy)),
            "peak_energy": float(np.percentile(energy, 95)),
            "energy_dynamics": float(np.std(energy)),
            "speech_ratio": float(np.mean(voiced)) if len(voiced) else 0.0,
            "silence_ratio": float(1.0 - np.mean(voiced)) if len(voiced) else 1.0,
            "mean_flux": float(np.mean(flux)) if len(flux) else 0.0,
        }

    def silence_regions(self, min_duration: float = 0.8) -> list[tuple[float, float]]:
        """Contiguous non-speech spans, useful for finding clean cut points."""
        regions: list[tuple[float, float]] = []
        if len(self.voiced) == 0:
            return regions
        in_silence = False
        start = 0.0
        for index, is_voiced in enumerate(self.voiced):
            if not is_voiced and not in_silence:
                in_silence = True
                start = index * self.hop
            elif is_voiced and in_silence:
                in_silence = False
                if index * self.hop - start >= min_duration:
                    regions.append((start, index * self.hop))
        if in_silence and len(self.voiced) * self.hop - start >= min_duration:
            regions.append((start, len(self.voiced) * self.hop))
        return regions


def _decode_pcm(video_path: Path, sample_rate: int, tmpdir: str) -> Path:
    pcm_path = Path(tmpdir) / "audio.f32"
    args = [
        get_ffmpeg(),
        "-nostdin",
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
        "-f",
        "f32le",
        "-y",
        str(pcm_path),
    ]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Audio decode failed: {result.stderr[-400:]}")
    return pcm_path


def extract_audio_signals(
    video_path: str | Path,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    hop_seconds: float = DEFAULT_HOP_SECONDS,
    max_seconds: Optional[float] = None,
) -> AudioSignals:
    """Decode the audio track and compute hop-level features."""
    video_path = Path(video_path)
    media = probe(video_path)
    if not media.has_audio:
        empty = np.zeros(0, dtype=np.float32)
        return AudioSignals(
            hop=hop_seconds,
            sample_rate=sample_rate,
            duration=media.duration,
            rms_db=empty,
            energy=empty,
            voiced=np.zeros(0, dtype=bool),
            zcr=empty,
            centroid=empty,
            flux=empty,
        )

    with tempfile.TemporaryDirectory(prefix="autoclip-audio-") as tmpdir:
        pcm_path = _decode_pcm(video_path, sample_rate, tmpdir)
        if max_seconds:
            max_samples = int(max_seconds * sample_rate)
            samples = np.fromfile(pcm_path, dtype=np.float32, count=max_samples)
        else:
            samples = np.fromfile(pcm_path, dtype=np.float32)

    if samples.size == 0:
        empty = np.zeros(0, dtype=np.float32)
        return AudioSignals(
            hop=hop_seconds,
            sample_rate=sample_rate,
            duration=media.duration,
            rms_db=empty,
            energy=empty,
            voiced=np.zeros(0, dtype=bool),
            zcr=empty,
            centroid=empty,
            flux=empty,
        )

    hop_samples = max(1, int(round(hop_seconds * sample_rate)))
    n_hops = samples.size // hop_samples
    if n_hops == 0:
        samples = np.pad(samples, (0, hop_samples - samples.size))
        n_hops = 1
    frames = samples[: n_hops * hop_samples].reshape(n_hops, hop_samples)

    # --- energy -----------------------------------------------------------
    rms = np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1) + 1e-12)
    rms_db = 20.0 * np.log10(rms + 1e-9)
    # Map -60..0 dBFS to 0..1 for easy thresholds.
    energy = np.clip((rms_db + 60.0) / 60.0, 0.0, 1.0).astype(np.float32)

    # --- zero crossing rate (speech vs noise/music heuristic) -------------
    signs = np.signbit(frames)
    zcr = np.mean(signs[:, 1:] != signs[:, :-1], axis=1).astype(np.float32)

    # --- spectral centroid + flux ---------------------------------------
    window = np.hanning(hop_samples).astype(np.float32)
    spectrum = np.abs(np.fft.rfft(frames * window, axis=1)).astype(np.float32)
    freqs = np.fft.rfftfreq(hop_samples, d=1.0 / sample_rate).astype(np.float32)
    magnitudes = spectrum.sum(axis=1) + 1e-9
    centroid_raw = (spectrum * freqs).sum(axis=1) / magnitudes
    centroid = np.clip(centroid_raw / (sample_rate / 2), 0.0, 1.0).astype(np.float32)
    # Normalised spectrum diff = onset/spectral flux.
    normalised = spectrum / magnitudes[:, None]
    flux = np.zeros(n_hops, dtype=np.float32)
    if n_hops > 1:
        flux[1:] = np.abs(np.diff(normalised, axis=0)).sum(axis=1)

    # --- speech presence --------------------------------------------------
    voiced = _estimate_voiced(energy, zcr, media.duration, hop_seconds)

    return AudioSignals(
        hop=hop_seconds,
        sample_rate=sample_rate,
        duration=media.duration or (n_hops * hop_seconds),
        rms_db=rms_db.astype(np.float32),
        energy=energy,
        voiced=voiced,
        zcr=zcr,
        centroid=centroid,
        flux=flux,
    )


def _estimate_voiced(
    energy: np.ndarray, zcr: np.ndarray, duration: float, hop: float
) -> np.ndarray:
    """Heuristic speech activity detection.

    Speech sits above a noise floor that adapts to the recording, and has
    moderate zero-crossing rate (not silent, not hissy). We smooth the result
    over ~200 ms to avoid flicker, then fill short gaps inside speech runs.
    """
    if energy.size == 0:
        return np.zeros(0, dtype=bool)

    # Adaptive noise floor: 20th percentile of a rolling window is a robust
    # estimate of the room tone.
    window = max(5, int(2.0 / hop))
    kernel = np.ones(window, dtype=np.float32) / window
    smoothed = np.convolve(energy, kernel, mode="same")
    floor = float(np.percentile(energy, 20))
    loud = float(np.percentile(energy, 90))
    threshold = floor + max(0.06, 0.28 * (loud - floor))

    speech = smoothed > threshold
    # Zero crossing sanity: very low ZCR is rumble, very high is hiss.
    speech &= zcr > 0.005
    speech &= zcr < 0.35

    # Fill gaps shorter than 350 ms (word/breath gaps).
    gap = max(1, int(0.35 / hop))
    speech = _fill_short_gaps(speech, gap)
    # Remove blips shorter than 150 ms.
    speech = _remove_short_runs(speech, max(1, int(0.15 / hop)))
    return speech.astype(bool)


def _fill_short_gaps(mask: np.ndarray, max_gap: int) -> np.ndarray:
    if not mask.any():
        return mask
    out = mask.copy()
    indices = np.flatnonzero(mask)
    for left, right in zip(indices[:-1], indices[1:]):
        if 0 < right - left <= max_gap:
            out[left:right] = True
    return out


def _remove_short_runs(mask: np.ndarray, min_run: int) -> np.ndarray:
    if not mask.any():
        return mask
    out = mask.copy()
    padded = np.concatenate(([False], mask, [False])).astype(np.int8)
    diffs = np.diff(padded)
    starts = np.flatnonzero(diffs == 1)
    ends = np.flatnonzero(diffs == -1)
    for start, end in zip(starts, ends):
        if end - start < min_run:
            out[start:end] = False
    return out
