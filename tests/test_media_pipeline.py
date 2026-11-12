"""End-to-end media tests: probing, audio signals, and actual rendering.

These exercise FFmpeg for real, so they are the tests that would catch a broken
filter graph or an unreadable output file.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.analysis.audio_signals import extract_audio_signals
from src.analysis.scenes import detect_scenes
from src.export.encoder import available_encoders, pick_encoder
from src.export.renderer import RenderSpec, make_thumbnail, render_clip
from src.media.ffmpeg import get_ffmpeg, probe
from src.pipeline.offline import AnalyzeOptions, RenderOptions, analyze, render_clips


def test_ffmpeg_is_available():
    path = get_ffmpeg()
    assert Path(path).is_file()


def test_probe_reads_real_metadata(demo_video):
    info = probe(demo_video)
    assert info.has_video
    assert info.has_audio
    assert info.width == 640
    assert info.height == 360
    assert info.duration == pytest.approx(24.0, abs=0.5)
    assert info.fps > 20


def test_probe_rejects_missing_file(tmp_path):
    with pytest.raises(Exception):
        probe(tmp_path / "nope.mp4")


def test_audio_signals_shape_and_ranges(demo_video):
    signals = extract_audio_signals(demo_video)
    assert len(signals.energy) > 100
    assert 0.0 <= float(signals.energy.min()) and float(signals.energy.max()) <= 1.0
    assert signals.voiced.dtype == bool
    window = signals.window(2.0, 8.0)
    assert 0.0 <= window["mean_energy"] <= 1.0
    assert 0.0 <= window["speech_ratio"] <= 1.0


def test_audio_signals_window_outside_duration_is_safe(demo_video):
    signals = extract_audio_signals(demo_video)
    stats = signals.window(10_000, 10_010)
    assert stats["mean_energy"] == 0.0


def test_scene_detection_runs(demo_video):
    scenes = detect_scenes(demo_video)
    assert scenes.duration == pytest.approx(0.0, abs=1e-6) or scenes.duration >= 0
    assert isinstance(scenes.cuts, list)


def test_encoder_detection_returns_usable_choice():
    choices = available_encoders()
    assert choices
    assert all(choice.name for choice in choices)
    chosen = pick_encoder()
    assert chosen.name in {c.name for c in choices}


@pytest.mark.parametrize("mode", ["vertical", "horizontal", "raw"])
def test_render_each_mode(demo_video, tmp_path, mode):
    output = tmp_path / f"out_{mode}.mp4"
    result = render_clip(
        RenderSpec(
            source=str(demo_video),
            output=str(output),
            start=2.0,
            end=8.0,
            mode=mode,
            prefer_hardware=False,
        )
    )
    assert output.is_file() and output.stat().st_size > 1000
    assert result.duration == pytest.approx(6.0, abs=0.6)
    if mode == "vertical":
        assert (result.width, result.height) == (1080, 1920)
    if mode == "horizontal":
        assert (result.width, result.height) == (1920, 1080)
    if mode == "raw":
        # Raw keeps the source resolution, trimmed to an exact cut.
        assert (result.width, result.height) == (640, 360)
    # The output must be decodable media, not a truncated file.
    info = probe(output)
    assert info.has_video
    assert info.duration == pytest.approx(6.0, abs=0.6)


def test_render_with_captions_and_keyframes(demo_video, tmp_path):
    from src.captions.ass import CaptionStyle, write_ass
    from src.reframe.tracker import CropKeyframe
    from src.transcribe.models import Segment

    captions = write_ass(
        [Segment(start=0.0, end=3.0, text="Burned in caption"), Segment(start=3.2, end=5.5, text="Second caption")],
        2.0,
        8.0,
        tmp_path / "cap.ass",
        CaptionStyle(),
    )
    assert captions is not None

    keyframes = [
        CropKeyframe(time=0, x=100, y=0, width=404, height=720),
        CropKeyframe(time=4, x=500, y=0, width=404, height=720),
    ]
    output = tmp_path / "captioned.mp4"
    result = render_clip(
        RenderSpec(
            source=str(demo_video),
            output=str(output),
            start=2.0,
            end=8.0,
            mode="vertical",
            crop_keyframes=keyframes,
            captions_path=str(captions),
            caption_style=CaptionStyle(),
            prefer_hardware=False,
        )
    )
    assert result.captions is True
    assert result.width == 1080 and result.height == 1920


def test_render_blur_fallback(demo_video, tmp_path):
    output = tmp_path / "blur.mp4"
    result = render_clip(
        RenderSpec(
            source=str(demo_video),
            output=str(output),
            start=1.0,
            end=5.0,
            mode="vertical",
            crop_mode="blur",
            prefer_hardware=False,
        )
    )
    assert output.is_file()
    assert result.width == 1080


def test_render_clamps_out_of_frame_keyframes(demo_video, tmp_path):
    """A stale/oversized crop path must never ask ffmpeg for pixels outside the frame."""
    from src.reframe.tracker import CropKeyframe

    output = tmp_path / "clamped.mp4"
    result = render_clip(
        RenderSpec(
            source=str(demo_video),  # 640x360 fixture
            output=str(output),
            start=1.0,
            end=4.0,
            mode="vertical",
            crop_mode="crop",
            crop_keyframes=[
                CropKeyframe(time=0, x=9999.0, y=-500.0, width=360.0, height=640.0),
                CropKeyframe(time=3, x=-80.0, y=20.0, width=4000.0, height=4000.0),
            ],
            prefer_hardware=False,
        )
    )
    assert output.is_file()
    assert output.stat().st_size > 0
    assert (result.width, result.height) == (1080, 1920)


def test_render_blur_ignores_crop_keyframes(demo_video, tmp_path):
    from src.reframe.tracker import CropKeyframe

    output = tmp_path / "blur-kf.mp4"
    render_clip(
        RenderSpec(
            source=str(demo_video),
            output=str(output),
            start=1.0,
            end=4.0,
            mode="vertical",
            crop_mode="blur",
            crop_keyframes=[CropKeyframe(time=0, x=50.0, y=0.0, width=200.0, height=360.0)],
            prefer_hardware=False,
        )
    )
    assert output.is_file() and output.stat().st_size > 0


def test_render_rejects_bad_ranges(demo_video, tmp_path):
    with pytest.raises(Exception):
        render_clip(
            RenderSpec(source=str(demo_video), output=str(tmp_path / "x.mp4"), start=10, end=5)
        )


def test_thumbnail_generation(demo_video, tmp_path):
    thumb = make_thumbnail(demo_video, tmp_path / "thumb.jpg", at=3.0)
    assert thumb is not None
    assert Path(thumb).stat().st_size > 500


def test_full_offline_analyze_and_render(demo_video, demo_srt, tmp_path):
    work = tmp_path / "work"
    analysis = analyze(
        demo_video,
        work,
        AnalyzeOptions(
            srt_path=str(demo_srt),
            candidates=__import__(
                "src.analysis.candidates", fromlist=["CandidateConfig"]
            ).CandidateConfig(min_duration=8, max_duration=20, ideal_duration=12, max_clips=3),
        ),
    )
    assert analysis.transcript_source == "user_srt"
    assert analysis.transcript_segments > 0
    assert analysis.clips
    assert (work / "analysis.json").is_file()
    assert (work / "transcript.srt").is_file()

    saved = json.loads((work / "analysis.json").read_text(encoding="utf-8"))
    assert len(saved["clips"]) == len(analysis.clips)

    renders = render_clips(
        demo_video,
        analysis.clips[:1],
        tmp_path / "clips",
        work,
        options=RenderOptions(mode="vertical", captions=True, prefer_hardware=False),
    )
    assert renders[0]["status"] == "done", renders[0].get("error")
    assert renders[0]["captions"] is True
    assert Path(renders[0]["file"]).is_file()
    assert (tmp_path / "clips" / "clips.json").is_file()


def test_analyze_without_transcript_still_produces_output(demo_video, tmp_path):
    analysis = analyze(
        demo_video,
        tmp_path / "work",
        AnalyzeOptions(
            transcription=__import__(
                "src.transcribe.engine", fromlist=["TranscriptionOptions"]
            ).TranscriptionOptions(backend="none_installed"),
        ),
    )
    assert analysis.transcript_source in ("none", "")
    assert analysis.warnings
    # Must not raise even with no speech engine at all.
    assert isinstance(analysis.clips, list)
