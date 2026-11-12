"""
Job store and worker for the desktop clipping workflow.

A *job* is one video the user dropped in. It owns:
  uploads/studio/<job_id>/source.mp4   original video
  uploads/studio/<job_id>/work/        analysis artefacts (transcript, scenes...)
  uploads/studio/<job_id>/clips/       rendered vertical clips + thumbnails

Jobs are persisted to ``data/studio/jobs.json`` so the UI can be closed and
reopened without losing work. Long operations run in a single background
worker thread to keep CPU-bound FFmpeg work from starving the web server.
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import PROJECT_ROOT
from ..pipeline.offline import (
    AnalyzeOptions,
    RenderOptions,
    analyze,
    load_analysis,
    render_clips,
)
from ..transcribe.models import Transcript
from ..utils.video_processor import VideoProcessor
from .settings import load_settings

logger = logging.getLogger(__name__)

STUDIO_ROOT = PROJECT_ROOT / "uploads" / "studio"
JOBS_FILE = PROJECT_ROOT / "data" / "studio" / "jobs.json"

_STATUS_ORDER = {
    "queued": 0,
    "analyzing": 1,
    "analyzed": 2,
    "rendering": 3,
    "completed": 4,
    "error": 5,
}


@dataclass
class Job:
    id: str
    name: str
    source: str
    status: str = "uploading"
    message: str = ""
    progress: float = 0.0
    created_at: str = ""
    updated_at: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    size: int = 0
    transcript_source: str = ""
    transcript_language: str = ""
    transcript_segments: int = 0
    scene_count: int = 0
    warnings: List[str] = field(default_factory=list)
    clips: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    options: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "status": self.status,
            "message": self.message,
            "progress": round(self.progress, 4),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "duration": self.duration,
            "width": self.width,
            "height": self.height,
            "size": self.size,
            "transcript_source": self.transcript_source,
            "transcript_language": self.transcript_language,
            "transcript_segments": self.transcript_segments,
            "scene_count": self.scene_count,
            "warnings": self.warnings,
            "clips": self.clips,
            "error": self.error,
            "options": self.options,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Job":
        job = cls(id=str(data["id"]), name=data.get("name") or "Untitled", source=data.get("source") or "")
        for key, value in data.items():
            if key in {"id", "name", "source"}:
                continue
            if hasattr(job, key):
                setattr(job, key, value)
        return job

    def paths(self) -> Dict[str, Path]:
        base = STUDIO_ROOT / self.id
        return {
            "base": base,
            "source": base / "source.mp4",
            "work": base / "work",
            "clips": base / "clips",
        }


class JobStore:
    """Thread-safe, file-backed collection of jobs."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: Dict[str, Job] = {}
        self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        if not JOBS_FILE.is_file():
            return
        try:
            data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
            for item in data.get("jobs", []):
                job = Job.from_dict(item)
                # A job that was mid-flight when the app closed is resumable:
                # mark it so the UI offers "Analyse again" instead of a spinner.
                if job.status in {"analyzing", "rendering", "queued"}:
                    job.status = "interrupted"
                    job.message = "App was closed during processing"
                self._jobs[job.id] = job
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            logger.warning("Could not load jobs: %s", exc)

    def _save(self) -> None:
        JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {"jobs": [job.to_dict() for job in self._jobs.values()]}
        temp = JOBS_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(JOBS_FILE)

    # -- CRUD --------------------------------------------------------------
    def create(self, name: str, source: Path, size: int = 0, job_id: Optional[str] = None) -> Job:
        with self._lock:
            job_id = job_id or uuid.uuid4().hex[:12]
            now = datetime.now().isoformat(timespec="seconds")
            job = Job(
                id=job_id,
                name=name,
                source=str(source),
                status="uploaded",
                created_at=now,
                updated_at=now,
                size=size,
                options={
                    "clip_settings": {
                        k: load_settings()[k]
                        for k in ("min_duration", "max_duration", "ideal_duration", "max_clips", "min_score")
                    },
                    "render": {
                        k: load_settings()[k]
                        for k in ("mode", "width", "height", "fps", "captions", "auto_reframe")
                    },
                },
            )
            self._jobs[job.id] = job
            self._save()
            return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> List[Job]:
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs

    def update(self, job_id: str, **updates: Any) -> Optional[Job]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            for key, value in updates.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            job.updated_at = datetime.now().isoformat(timespec="seconds")
            self._save()
            return job

    def delete(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.pop(job_id, None)
            if not job:
                return False
            self._save()
        base = job.paths()["base"]
        if base.exists():
            shutil.rmtree(base, ignore_errors=True)
        return True


store = JobStore()


# --------------------------------------------------------------------------- #
# Worker
# --------------------------------------------------------------------------- #
class JobRunner:
    """Runs analysis and rendering outside the request/response cycle."""

    def __init__(self) -> None:
        self._threads: Dict[str, threading.Thread] = {}
        self._lock = threading.RLock()

    def _start(self, job_id: str, target, *args) -> None:
        with self._lock:
            existing = self._threads.get(job_id)
            if existing and existing.is_alive():
                raise RuntimeError("This job is already running")
            thread = threading.Thread(target=target, args=(job_id, *args), daemon=True, name=f"job-{job_id}")
            self._threads[job_id] = thread
            thread.start()

    def is_running(self, job_id: str) -> bool:
        with self._lock:
            thread = self._threads.get(job_id)
            return bool(thread and thread.is_alive())

    # -- analysis ----------------------------------------------------------
    def analyze(self, job_id: str, options: Optional[Dict[str, Any]] = None) -> None:
        self._start(job_id, self._analyze_worker, options or {})

    def _analyze_worker(self, job_id: str, options: Dict[str, Any]) -> None:
        job = store.get(job_id)
        if not job:
            return
        paths = job.paths()
        store.update(job_id, status="analyzing", progress=0.0, message="Starting analysis", error=None)

        # Precedence: defaults < stored job options < this request.
        merged = {**load_settings(), **(job.options or {}), **(options or {})}

        def progress(fraction: float, message: str) -> None:
            store.update(job_id, progress=fraction, message=message)

        try:
            # A subtitle the user dropped in earlier lives next to the source.
            if not merged.get("srt_path"):
                candidate = paths["work"] / "submitted.srt"
                if candidate.is_file():
                    merged["srt_path"] = str(candidate)

            analyze_options = AnalyzeOptions.from_dict(merged)
            if merged.get("transcript_text"):
                analyze_options.transcript_text = merged["transcript_text"]
            if merged.get("srt_path"):
                analyze_options.srt_path = merged["srt_path"]
            if merged.get("language"):
                analyze_options.transcription.language = merged["language"]

            result = analyze(paths["source"], paths["work"], analyze_options, progress=progress)

            store.update(
                job_id,
                status="analyzed",
                progress=1.0,
                message=f"Found {len(result.clips)} clips",
                clips=[
                    {
                        "id": clip.id,
                        "start": clip.start,
                        "end": clip.end,
                        "duration": round(clip.duration, 2),
                        "score": round(clip.score, 4),
                        "breakdown": clip.breakdown,
                        "reasons": clip.reasons,
                        "title": clip.title,
                        "hook": clip.hook,
                        "hook_text": clip.hook_text,
                        "text": clip.text,
                        "words": clip.words,
                        "status": "candidate",
                    }
                    for clip in result.clips
                ],
                duration=result.duration,
                width=result.media.get("display_width") or result.media.get("width") or 0,
                height=result.media.get("display_height") or result.media.get("height") or 0,
                transcript_source=result.transcript_source,
                transcript_language=result.transcript_language,
                transcript_segments=result.transcript_segments,
                scene_count=result.scenes,
                warnings=result.warnings,
                options={
                    **job.options,
                    "last_analyze": merged,
                    "render": {**merged, **(job.options.get("render") or {})},
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Analysis failed for %s: %s\n%s", job_id, exc, traceback.format_exc())
            store.update(job_id, status="error", message="Analysis failed", error=str(exc))

    # -- rendering ---------------------------------------------------------
    def render(self, job_id: str, clip_ids: Optional[List[str]] = None, options: Optional[Dict[str, Any]] = None) -> None:
        self._start(job_id, self._render_worker, clip_ids or [], options or {})

    def _render_worker(self, job_id: str, clip_ids: List[str], options: Dict[str, Any]) -> None:
        job = store.get(job_id)
        if not job:
            return
        paths = job.paths()
        analysis = load_analysis(paths["work"])
        if not analysis:
            store.update(job_id, status="error", message="Nothing to render", error="Run analysis first")
            return

        clips = analysis.get("clips") or []
        if clip_ids:
            wanted = set(clip_ids)
            clips = [c for c in clips if c.get("id") in wanted]
        if not clips:
            store.update(job_id, status="error", message="No matching clips", error="No clips selected for rendering")
            return

        settings = load_settings()
        merged = {
            **settings,
            **(job.options.get("last_analyze") or {}),
            **(job.options.get("render") or {}),
            **(options or {}),
        }
        render_options = RenderOptions.from_dict(merged)

        transcript = None
        transcript_file = paths["work"] / "transcript.json"
        if transcript_file.is_file():
            try:
                transcript = Transcript.from_dict(json.loads(transcript_file.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                transcript = None

        store.update(
            job_id,
            status="rendering",
            progress=0.0,
            message=f"Rendering {len(clips)} clip(s)",
            error=None,
        )

        def progress(fraction: float, message: str) -> None:
            store.update(job_id, progress=fraction, message=message)

        try:
            rendered = render_clips(
                paths["source"],
                clips,
                paths["clips"],
                paths["work"],
                transcript=transcript,
                options=render_options,
                progress=progress,
            )
            by_id = {item["id"]: item for item in rendered}
            updated_clips = []
            for clip in job.clips:
                item = by_id.get(clip.get("id"))
                updated_clips.append({**clip, **item} if item else clip)
            # Include renders for clips that were not in the list (e.g. deleted).
            known = {c.get("id") for c in updated_clips}
            for item in rendered:
                if item["id"] not in known:
                    updated_clips.append(item)

            failures = [c for c in rendered if c.get("status") == "error"]
            if failures and len(failures) == len(rendered):
                store.update(
                    job_id,
                    status="error",
                    clips=updated_clips,
                    message="Rendering failed",
                    error=failures[0].get("error") or "Unknown render error",
                )
            else:
                store.update(
                    job_id,
                    status="completed",
                    progress=1.0,
                    clips=updated_clips,
                    message=f"Rendered {len(rendered) - len(failures)} clip(s)"
                    + (f", {len(failures)} failed" if failures else ""),
                    options={**job.options, "render": merged},
                )
        except Exception as exc:  # noqa: BLE001
            logger.error("Render failed for %s: %s\n%s", job_id, exc, traceback.format_exc())
            store.update(job_id, status="error", message="Rendering failed", error=str(exc))

    # -- maintenance -------------------------------------------------------
    def rename(self, job_id: str, name: str) -> Optional[Job]:
        return store.update(job_id, name=name.strip() or "Untitled")

    def delete_clip_file(self, job_id: str, clip_id: str) -> bool:
        job = store.get(job_id)
        if not job:
            return False
        removed = False
        clip = next((c for c in job.clips if c.get("id") == clip_id), None)
        if clip:
            for key in ("file", "thumbnail"):
                value = clip.get(key)
                if value:
                    path = Path(value)
                    if path.is_file():
                        path.unlink(missing_ok=True)
                        removed = True
            clip.update({"status": "candidate", "file": None, "thumbnail": None, "size": 0})
            store.update(job_id, clips=job.clips)
        return removed


runner = JobRunner()
