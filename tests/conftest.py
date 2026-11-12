"""Shared fixtures: a synthetic video with audio so media tests run anywhere."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _ffmpeg() -> str:
    from src.media.ffmpeg import get_ffmpeg

    return get_ffmpeg()


@pytest.fixture(scope="session")
def demo_video(tmp_path_factory) -> Path:
    """A 24 second 640x360 clip with a moving box and speech-like audio."""
    target = tmp_path_factory.mktemp("media") / "demo.mp4"
    cmd = [
        _ffmpeg(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=0x101820:s=640x360:r=25:d=24,"
        "drawbox=x='mod(t*40\\,640)':y=80:w=200:h=200:color=0x2b5876@0.9:t=fill,"
        "format=yuv420p",
        "-f",
        "lavfi",
        "-i",
        "aevalsrc=0.25*sin(2*PI*200*t)*(lt(mod(t\\,5)\\,3))"
        "|0.25*sin(2*PI*260*t)*(lt(mod(t\\,5)\\,3)):s=44100:d=24",
        "-t",
        "24",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(target),
    ]
    subprocess.run(cmd, check=True)
    return target


@pytest.fixture(scope="session")
def demo_srt(tmp_path_factory) -> Path:
    """Subtitles covering the demo video's timeline."""
    target = tmp_path_factory.mktemp("media") / "demo.srt"
    lines = [
        "The biggest mistake I made was hiring too fast.",
        "Most people think growth solves everything but it hides problems.",
        "We went from zero users to two million in eighteen months.",
        "Here is the exact playbook we used, step by step.",
        "Nobody tells you this but retention is the only metric that matters.",
        "So we deleted half the features and the product got better.",
    ]
    blocks = []
    cursor = 0.5
    for index, text in enumerate(lines, start=1):
        start = cursor
        end = cursor + 3.5

        def code(value: float) -> str:
            minutes, secs = divmod(value, 60)
            return f"00:{int(minutes):02d}:{secs:06.3f}".replace(".", ",")

        blocks.append(f"{index}\n{code(start)} --> {code(end)}\n{text}\n")
        cursor = end + 0.3
    target.write_text("\n".join(blocks), encoding="utf-8")
    return target
