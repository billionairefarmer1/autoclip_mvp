#!/usr/bin/env python3
"""
Create a synthetic demo video so you can try AutoClip without hunting for
footage.

    python scripts/make_demo_video.py --out demo/demo.mp4 --seconds 90

The result is a 16:9 "talking head" style clip: a moving subject, a quiet room
tone, and bursts of speech-like audio. It also writes ``demo/demo.srt`` with
speech-like lines and correct timings, so you can compare:

* pipeline with captions/subtitles  (``--srt demo/demo.srt``)
* pipeline with audio-only analysis  (no srt)

Note: the faces are synthetic, so face tracking falls back to a centred crop -
exactly the path a real webcam/podcast video without a detectable face takes.
"""
from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.media.ffmpeg import get_ffmpeg  # noqa: E402

LINES = [
    "The biggest mistake I made in my first year was hiring too fast.",
    "Most people think growth solves everything, but growth hides broken products.",
    "We went from zero users to two million in eighteen months.",
    "Here is the exact playbook we used, step by step.",
    "First, we talked to thirty customers every single week.",
    "Nobody tells you this, but retention is the only metric that matters early on.",
    "What if your growth was just hiding a leaky bucket the whole time?",
    "So we deleted half the features and the product got better overnight.",
    "That is why we only shipped what people asked for twice.",
    "The lesson is simple: charge more, sell to fewer people, and listen harder.",
    "I learned this the expensive way, and it cost us about four hundred thousand dollars.",
    "If you take one thing away, make it this: speed of learning beats speed of shipping.",
]


def _timecode(seconds: float) -> str:
    hours, rem = divmod(max(0.0, seconds), 3600)
    minutes, secs = divmod(rem, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{int(secs):02d},000"


def build_srt(seconds: float, out_path: Path) -> Path:
    blocks = []
    index = 1
    cursor = 1.0
    for _ in range(math.ceil(seconds / 6.5) + 1):
        for line in LINES:
            chunks = [line]
            # Break long lines into two cues so captions stay readable.
            if len(line) > 62:
                words = line.split()
                mid = len(words) // 2
                chunks = [" ".join(words[:mid]), " ".join(words[mid:])]
            for chunk in chunks:
                start = cursor
                end = min(seconds, cursor + max(1.8, len(chunk) / 15))
                if start >= seconds:
                    return out_path
                blocks.append(f"{index}\n{_timecode(start)} --> {_timecode(end)}\n{chunk}\n")
                index += 1
                cursor = end + 0.35
        if cursor >= seconds:
            break
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(blocks), encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a demo video for AutoClip")
    parser.add_argument("--out", default="demo/demo.mp4", help="output video path")
    parser.add_argument("--seconds", type=float, default=90.0, help="length in seconds")
    parser.add_argument("--size", default="1280x720", help="frame size")
    args = parser.parse_args()

    ffmpeg = get_ffmpeg()
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # A slow-moving "subject" over a graded background, plus speech-like audio
    # bursts with pauses between them.
    video_filter = (
        "color=c=0x101820:s={size}:r=30,"
        "drawbox=x='mod(t*90\\,{w})':y=140:w=420:h=520:color=0x2b5876@0.95:t=fill,"
        "drawbox=x='mod(t*90\\,{w})+80':y=600:w=260:h=44:color=0x4facfe@0.8:t=fill,"
        "drawtext=text='AutoClip demo':x=(w-tw)/2:y=h-84:fontsize=42:fontcolor=white@0.75,"
        "format=yuv420p"
    )
    width, height = args.size.split("x")
    video_filter = video_filter.format(size=args.size, w=width)

    # Speech-like bursts: harmonics at a speech-ish rate, silent gaps between.
    audio_src = (
        "aevalsrc="
        "0.22*sin(2*PI*170*t)*(lt(mod(t\\,6.5)\\,4.2))"
        "+0.10*sin(2*PI*340*t)*(lt(mod(t\\,6.5)\\,4.2))"
        "|0.22*sin(2*PI*190*t)*(lt(mod(t\\,6.5)\\,4.2))"
        "+0.10*sin(2*PI*380*t)*(lt(mod(t\\,6.5)\\,4.2))"
        f":s=44100:d={args.seconds}"
    )

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        video_filter,
        "-f",
        "lavfi",
        "-i",
        audio_src,
        "-t",
        str(args.seconds),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(out_path),
    ]
    print(f"Rendering demo video -> {out_path}")
    subprocess.run(cmd, check=True)

    srt_path = build_srt(args.seconds, out_path.with_suffix(".srt"))
    print(f"Wrote subtitle file -> {srt_path}")
    print(f"Size: {out_path.stat().st_size / 1_048_576:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
