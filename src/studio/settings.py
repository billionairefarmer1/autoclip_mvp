"""Studio settings: clipping and export preferences persisted to disk.

Kept separate from the legacy ``data/settings.json`` (which holds LLM keys)
so the desktop clipping workflow can evolve without disturbing the older
AI-driven pipeline.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict

from ..config import PROJECT_ROOT

logger = logging.getLogger(__name__)

STUDIO_DIR = PROJECT_ROOT / "data" / "studio"
SETTINGS_FILE = STUDIO_DIR / "settings.json"

DEFAULTS: Dict[str, Any] = {
    # --- transcription -----------------------------------------------------
    "transcription_backend": "auto",     # auto | faster_whisper | openai_whisper | whisper_cpp | openai_api
    "whisper_model": "base",             # tiny | base | small | medium | large-v3
    "transcription_language": "",        # empty = auto-detect
    "whisper_device": "auto",
    "whisper_compute_type": "int8",
    "whisper_model_dir": "",
    "whisper_cpp_binary": "",
    "whisper_cpp_model": "",
    "transcription_api_key": "",
    "transcription_api_base_url": "https://api.openai.com/v1",
    "transcription_api_model": "whisper-1",
    # --- clip selection ----------------------------------------------------
    "min_duration": 12,
    "max_duration": 60,
    "ideal_duration": 28,
    "max_clips": 20,
    "min_score": 0.0,
    "snap_to_scenes": True,
    # --- export ------------------------------------------------------------
    "mode": "vertical",                  # vertical | horizontal | raw
    "width": 1080,
    "height": 1920,
    "fps": 30,
    "captions": True,
    "caption_font": "",                  # empty = auto-detected installed font
    "caption_font_size": 74,
    "caption_colour": "white",
    "caption_highlight_colour": "yellow",
    "caption_uppercase": False,
    "caption_karaoke": True,
    "caption_margin_v": 340,
    "auto_reframe": True,
    "blurred_background": True,
    "loudnorm": True,
    "prefer_hardware": True,
    "crf": 20,
    "encoder": "",
    # --- tooling -----------------------------------------------------------
    "ffmpeg_path": "",
    "ffprobe_path": "",
    "max_concurrent_jobs": 1,
}

_lock = threading.RLock()
_cache: Dict[str, Any] | None = None


def load_settings() -> Dict[str, Any]:
    """Return current studio settings merged over defaults."""
    global _cache
    with _lock:
        if _cache is None:
            settings = dict(DEFAULTS)
            if SETTINGS_FILE.is_file():
                try:
                    stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                    for key, value in stored.items():
                        if key in DEFAULTS:
                            settings[key] = value
                except (OSError, json.JSONDecodeError) as exc:
                    logger.warning("Could not read studio settings: %s", exc)
            _cache = settings
        return dict(_cache)


def update_settings(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a partial settings update and return the merged result."""
    global _cache
    with _lock:
        settings = load_settings()
        for key, value in updates.items():
            if key in DEFAULTS:
                settings[key] = value
        STUDIO_DIR.mkdir(parents=True, exist_ok=True)
        temp = SETTINGS_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(SETTINGS_FILE)
        _cache = settings
        return dict(settings)


def reset_settings() -> Dict[str, Any]:
    global _cache
    with _lock:
        _cache = None
        if SETTINGS_FILE.is_file():
            SETTINGS_FILE.unlink()
    return load_settings()


def invalidate_cache() -> None:
    """Force a re-read (used after the ffmpeg path changes)."""
    global _cache
    with _lock:
        _cache = None
