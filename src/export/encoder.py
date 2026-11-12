"""Encoder discovery: prefer hardware when asked, always have a software fallback."""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional

from ..media.ffmpeg import get_ffmpeg

logger = logging.getLogger(__name__)

# Ordered by usual quality/speed on a desktop PC.
HARDWARE_ENCODERS = [
    ("h264_nvenc", "NVIDIA NVENC", ["-preset", "p4", "-rc", "vbr", "-cq", "21", "-b:v", "0"]),
    ("h264_videotoolbox", "Apple VideoToolbox", ["-b:v", "8M"]),
    ("h264_qsv", "Intel Quick Sync", ["-global_quality", "21"]),
    ("h264_amf", "AMD AMF", ["-quality", "balanced", "-rc", "cqp", "-qp_i", "21", "-qp_p", "23"]),
]

SOFTWARE_ENCODER = ("libx264", "x264 (software)", ["-preset", "veryfast", "-crf", "20"])


@dataclass
class EncoderChoice:
    name: str
    label: str
    args: List[str]
    hardware: bool = False

    def to_dict(self) -> dict:
        return {"name": self.name, "label": self.label, "hardware": self.hardware}


@lru_cache(maxsize=1)
def _encoder_names() -> frozenset:
    try:
        result = subprocess.run(
            [get_ffmpeg(), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = result.stdout or ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not list encoders: %s", exc)
        return frozenset()

    names = set()
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            names.add(parts[1])
    return frozenset(names)


@lru_cache(maxsize=32)
def encoder_works(name: str) -> bool:
    """Actually try a one-frame encode.

    FFmpeg is often built with NVENC/QSV/AMF support on machines that have no
    compatible GPU, so listing encoders is not enough - we must test.
    """
    try:
        result = subprocess.run(
            [
                get_ffmpeg(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=256x256:d=0.1:r=10",
                "-frames:v",
                "2",
                "-c:v",
                name,
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=45,
        )
        return result.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def available_encoders(test_hardware: bool = True) -> List[EncoderChoice]:
    names = _encoder_names()
    choices: List[EncoderChoice] = []
    for name, label, args in HARDWARE_ENCODERS:
        if name in names and (not test_hardware or encoder_works(name)):
            choices.append(EncoderChoice(name=name, label=label, args=list(args), hardware=True))
    if SOFTWARE_ENCODER[0] in names or not names:
        choices.append(
            EncoderChoice(
                name=SOFTWARE_ENCODER[0],
                label=SOFTWARE_ENCODER[1],
                args=list(SOFTWARE_ENCODER[2]),
                hardware=False,
            )
        )
    else:
        # Fall back to whatever the build calls its H.264 encoder.
        for candidate in ("h264", "libopenh264", "mpeg4"):
            if candidate in names:
                choices.append(
                    EncoderChoice(name=candidate, label=candidate, args=[], hardware=False)
                )
                break
    return choices


def pick_encoder(prefer_hardware: bool = True, requested: Optional[str] = None) -> EncoderChoice:
    """Choose an encoder; never raises as long as *some* encoder exists."""
    choices = available_encoders()
    if not choices:
        return EncoderChoice(name="libx264", label="x264 (software)", args=list(SOFTWARE_ENCODER[2]))
    if requested:
        for choice in choices:
            if choice.name == requested:
                return choice
    if prefer_hardware:
        for choice in choices:
            if choice.hardware:
                return choice
    for choice in choices:
        if not choice.hardware:
            return choice
    return choices[-1]
