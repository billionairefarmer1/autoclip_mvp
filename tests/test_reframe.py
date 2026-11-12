"""Vertical reframing: crop-path planning and FFmpeg expression building."""
from __future__ import annotations

import pytest

from src.reframe.expressions import crop_expression
from src.reframe.tracker import (
    CropKeyframe,
    FaceSample,
    FaceTrack,
    plan_crop_path,
)


def _track(width: int, height: int, centres=None, face: float = 120) -> FaceTrack:
    samples = []
    for index, centre in enumerate(centres or []):
        samples.append(
            FaceSample(time=index * 0.5, cx=centre, cy=height / 2, width=face, height=face, confidence=1.0)
        )
    if not samples:
        samples = [FaceSample(0, width / 2, height / 2, 0, 0, 0.0)]
    return FaceTrack(samples=samples, source_width=width, source_height=height)


def test_center_crop_when_no_face_detected():
    keyframes, mode = plan_crop_path(_track(1920, 1080), 1080, 1920)
    assert mode == "crop"
    assert len(keyframes) == 1
    frame = keyframes[0]
    assert frame.width / frame.height == pytest.approx(1080 / 1920, rel=0.02)
    # Horizontally centred
    assert frame.x == pytest.approx((1920 - frame.width) / 2, abs=4)


def test_crop_follows_a_moving_face():
    keyframes, mode = plan_crop_path(_track(1280, 720, centres=[200, 400, 700, 1000, 1100]), 1080, 1920)
    assert mode == "crop"
    xs = [frame.x for frame in keyframes]
    assert xs == sorted(xs), "crop should travel with the subject"
    assert xs[-1] > xs[0] + 100


def test_crop_stays_inside_the_frame():
    keyframes, _ = plan_crop_path(_track(1280, 720, centres=[0, 1280, 5, 1275]), 1080, 1920)
    for frame in keyframes:
        assert 0 <= frame.x
        assert 0 <= frame.y
        assert frame.x + frame.width <= 1280 + 1
        assert frame.y + frame.height <= 720 + 1


def test_keyframe_times_are_distinct_and_ordered():
    keyframes, _ = plan_crop_path(_track(1920, 1080, centres=list(range(100, 1200, 100))), 1080, 1920)
    times = [frame.time for frame in keyframes]
    assert times == sorted(times)
    assert len(set(times)) == len(times)


def test_big_faces_zoom_in_more_than_small_faces():
    small, _ = plan_crop_path(_track(1920, 1080, [960] * 6, face=90), 1080, 1920)
    big, _ = plan_crop_path(_track(1920, 1080, [960] * 6, face=300), 1080, 1920)
    assert big[0].width > small[0].width


def test_very_wide_ultrawide_source_is_handled():
    keyframes, mode = plan_crop_path(_track(2560, 720), 1080, 1920)
    assert mode == "crop"
    assert keyframes[0].width / keyframes[0].height == pytest.approx(1080 / 1920, rel=0.02)


def test_portrait_source_fills_width():
    keyframes, mode = plan_crop_path(_track(1080, 1920), 1080, 1920)
    assert mode == "crop"
    assert keyframes[0].width == pytest.approx(1080, abs=2)


def test_invalid_source_reports_blur_mode():
    keyframes, mode = plan_crop_path(FaceTrack(samples=[], source_width=0, source_height=0), 1080, 1920)
    assert keyframes == []
    assert mode == "blur"


def test_even_dimensions_for_h264():
    keyframes, _ = plan_crop_path(_track(1279, 719, [640]), 1080, 1920)
    for frame in keyframes:
        assert int(frame.width) % 2 == 0
        assert int(frame.height) % 2 == 0


def test_static_crop_expression():
    frame = CropKeyframe(time=0, x=100, y=20, width=400, height=700)
    assert crop_expression([frame]) == "crop=400:700:100:20"


def test_moving_crop_expression_uses_time_variable():
    frames = [
        CropKeyframe(time=0, x=0, y=0, width=400, height=700),
        CropKeyframe(time=2, x=200, y=0, width=400, height=700),
    ]
    expression = crop_expression(frames)
    assert expression.startswith("crop=400:700:")
    assert "if(lt(t,2.000)" in expression
    assert "(0.0+(100.0000)*(t-0.000))" in expression


def test_empty_crop_expression_is_identity():
    assert crop_expression([]) == "crop=iw:ih:0:0"
