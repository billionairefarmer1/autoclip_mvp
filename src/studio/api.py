"""
FastAPI router for the desktop clipping studio.

Mounted at ``/api/studio`` by ``backend_server.py``. Endpoints are deliberately
small and JSON-only, except for uploads and media streaming.

    GET    /api/studio/health            capability report (ffmpeg, ASR, fonts)
    GET    /api/studio/settings          read settings
    PUT    /api/studio/settings          update settings
    POST   /api/studio/settings/reset    restore defaults
    GET    /api/studio/jobs              list jobs
    POST   /api/studio/jobs              upload a video and create a job
    GET    /api/studio/jobs/{id}         job detail (status, clips)
    PATCH  /api/studio/jobs/{id}         rename
    DELETE /api/studio/jobs/{id}         delete job and rendered files
    POST   /api/studio/jobs/{id}/analyze start analysis (background)
    POST   /api/studio/jobs/{id}/render  render selected clips (background)
    POST   /api/studio/jobs/{id}/subtitle user-supplied .srt upload
    GET    /api/studio/jobs/{id}/source  stream the source video
    GET    /api/studio/jobs/{id}/clips/{clip_id}/video     stream a rendered clip
    GET    /api/studio/jobs/{id}/clips/{clip_id}/thumbnail clip thumbnail
    GET    /api/studio/jobs/{id}/clips/{clip_id}/download  download a clip
    GET    /api/studio/jobs/{id}/download-all              zip of all clips
    DELETE /api/studio/jobs/{id}/clips/{clip_id}/file      delete generated file
    GET    /api/studio/jobs/{id}/transcript                transcript as JSON/SRT
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from ..export.encoder import available_encoders
from ..captions.fonts import resolve_font
from ..media.ffmpeg import FFmpegNotFoundError, ffmpeg_version, get_ffmpeg, probe
from ..transcribe.engine import backend_status
from .jobs import STUDIO_ROOT, runner, store
from .settings import DEFAULTS, load_settings, reset_settings, update_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/studio", tags=["studio"])

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".ts", ".mpg", ".mpeg", ".wmv"}
MAX_UPLOAD_BYTES = 8 * 1024 ** 3  # 8 GB is plenty for long source files


class SettingsUpdate(BaseModel):
    values: Dict[str, Any] = Field(default_factory=dict)


class AnalyzeRequest(BaseModel):
    options: Dict[str, Any] = Field(default_factory=dict)


class RenderRequest(BaseModel):
    clip_ids: Optional[List[str]] = None
    options: Dict[str, Any] = Field(default_factory=dict)


class RenameRequest(BaseModel):
    name: str


# --------------------------------------------------------------------------- #
# Health / capabilities
# --------------------------------------------------------------------------- #
@router.get("/health")
async def health() -> Dict[str, Any]:
    """Report what this machine can do so the UI can coach the user."""
    try:
        ffmpeg = get_ffmpeg()
        version = ffmpeg_version()
        ffmpeg_ok = True
    except FFmpegNotFoundError as exc:
        return {
            "ok": False,
            "ffmpeg": {"available": False, "error": str(exc)},
            "transcription": backend_status(),
            "encoders": [],
            "fonts": resolve_font().to_dict(),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "ffmpeg": {"available": False, "error": str(exc)}}

    asr = backend_status()
    speech_ready = any(
        asr[name]["available"] for name in ("faster_whisper", "openai_whisper", "whisper_cpp")
    )
    return {
        "ok": True,
        "ffmpeg": {"available": ffmpeg_ok, "path": ffmpeg, "version": version},
        "transcription": asr,
        "speech_transcription_ready": speech_ready,
        "encoders": [enc.to_dict() for enc in available_encoders()],
        "fonts": resolve_font().to_dict(),
        "studio_root": str(STUDIO_ROOT),
    }


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
@router.get("/settings")
async def get_settings() -> Dict[str, Any]:
    return {"settings": load_settings(), "defaults": DEFAULTS}


@router.put("/settings")
async def put_settings(payload: SettingsUpdate) -> Dict[str, Any]:
    unknown = [key for key in payload.values if key not in DEFAULTS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown settings: {', '.join(unknown)}")
    updated = update_settings(payload.values)
    return {"settings": updated}


@router.post("/settings/reset")
async def post_reset_settings() -> Dict[str, Any]:
    return {"settings": reset_settings()}


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #
@router.get("/jobs")
async def list_jobs() -> Dict[str, Any]:
    return {"jobs": [job.to_dict() for job in store.list()]}


@router.post("/jobs")
async def create_job(
    video: UploadFile = File(...),
    name: str = Form(""),
    subtitle: Optional[UploadFile] = File(None),
) -> Dict[str, Any]:
    """Upload a video (streamed to disk) and create a job for it."""
    filename = video.filename or "video.mp4"
    suffix = Path(filename).suffix.lower()
    if suffix not in VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported video type '{suffix}'. Supported: {', '.join(sorted(VIDEO_EXTENSIONS))}",
        )

    job_name = (name or Path(filename).stem or "Untitled").strip()[:200]
    # Create the job's folder first so we can stream straight into it.
    import uuid

    job_id = uuid.uuid4().hex[:12]
    base = STUDIO_ROOT / job_id
    (base / "work").mkdir(parents=True, exist_ok=True)
    (base / "clips").mkdir(parents=True, exist_ok=True)
    source_path = base / f"source{suffix}"

    written = 0
    try:
        with open(source_path, "wb") as handle:
            while True:
                chunk = await video.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Video is larger than the 8 GB limit")
                handle.write(chunk)
    except HTTPException:
        shutil.rmtree(base, ignore_errors=True)
        raise
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(base, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}") from exc
    finally:
        await video.close()

    if written == 0:
        shutil.rmtree(base, ignore_errors=True)
        raise HTTPException(status_code=400, detail="The uploaded file was empty")

    # Validate that it is actually decodable video before creating a job.
    try:
        info = probe(source_path)
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(base, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"Could not read this video: {exc}") from exc
    if not info.has_video:
        shutil.rmtree(base, ignore_errors=True)
        raise HTTPException(status_code=400, detail="This file has no video stream")

    # Reuse the folder id we already created so the job and its media match.
    job = store.create(job_name, source_path, size=written, job_id=job_id)

    job = store.update(
        job_id,
        status="uploaded",
        duration=round(info.duration, 3),
        width=info.display_width or info.width,
        height=info.display_height or info.height,
        size=written,
        message="Ready to analyse",
    )

    # Optional subtitle sidecar (skips the need for Whisper).
    if subtitle is not None and subtitle.filename:
        srt_path = base / "work" / "submitted.srt"
        try:
            with open(srt_path, "wb") as handle:
                handle.write(await subtitle.read())
            store.update(job_id, options={**job.options, "srt_path": str(srt_path)})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not save subtitle file: %s", exc)
        finally:
            await subtitle.close()

    return {"job": store.get(job_id).to_dict()}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> Dict[str, Any]:
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    data = job.to_dict()
    data["is_running"] = runner.is_running(job_id)
    return {"job": data}


@router.patch("/jobs/{job_id}")
async def rename_job(job_id: str, payload: RenameRequest) -> Dict[str, Any]:
    job = runner.rename(job_id, payload.name)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job": job.to_dict()}


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str) -> Dict[str, Any]:
    if runner.is_running(job_id):
        raise HTTPException(status_code=409, detail="Wait for the current job to finish first")
    if not store.delete(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Processing
# --------------------------------------------------------------------------- #
@router.post("/jobs/{job_id}/analyze")
async def start_analysis(job_id: str, payload: AnalyzeRequest) -> Dict[str, Any]:
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if runner.is_running(job_id):
        raise HTTPException(status_code=409, detail="This job is already processing")
    if not Path(job.source).is_file():
        raise HTTPException(status_code=410, detail="The source video is missing from disk")
    try:
        runner.analyze(job_id, payload.options)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "job": store.get(job_id).to_dict()}


@router.post("/jobs/{job_id}/render")
async def start_render(job_id: str, payload: RenderRequest) -> Dict[str, Any]:
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if runner.is_running(job_id):
        raise HTTPException(status_code=409, detail="This job is already processing")
    analyzed_states = ("analyzed", "completed", "interrupted")
    if job.status not in analyzed_states:
        raise HTTPException(status_code=409, detail="Run analysis before rendering")
    if not job.clips:
        raise HTTPException(status_code=409, detail="No clip candidates yet - run analysis first")
    try:
        runner.render(job_id, payload.clip_ids, payload.options)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "job": store.get(job_id).to_dict()}


@router.post("/jobs/{job_id}/subtitle")
async def upload_subtitle(job_id: str, subtitle: UploadFile = File(...)) -> Dict[str, Any]:
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    paths = job.paths()
    paths["work"].mkdir(parents=True, exist_ok=True)
    target = paths["work"] / "submitted.srt"
    try:
        target.write_bytes(await subtitle.read())
    finally:
        await subtitle.close()
    # Accept the sidecar immediately: re-run analysis with it.
    store.update(job_id, options={**job.options, "srt_path": str(target)})
    return {"ok": True, "path": str(target)}


# --------------------------------------------------------------------------- #
# Media
# --------------------------------------------------------------------------- #
def _job_or_404(job_id: str):
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs/{job_id}/source")
async def stream_source(job_id: str):
    job = _job_or_404(job_id)
    path = Path(job.source)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Source video is missing")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.get("/jobs/{job_id}/clips/{clip_id}/video")
async def stream_clip(job_id: str, clip_id: str):
    job = _job_or_404(job_id)
    clip = next((c for c in job.clips if c.get("id") == clip_id), None)
    if not clip or not clip.get("file"):
        raise HTTPException(status_code=404, detail="Clip has not been rendered yet")
    path = Path(clip["file"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Clip file is missing")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.get("/jobs/{job_id}/clips/{clip_id}/thumbnail")
async def clip_thumbnail(job_id: str, clip_id: str):
    job = _job_or_404(job_id)
    clip = next((c for c in job.clips if c.get("id") == clip_id), None)
    if not clip or not clip.get("thumbnail"):
        raise HTTPException(status_code=404, detail="No thumbnail available")
    path = Path(clip["thumbnail"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail file is missing")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/jobs/{job_id}/clips/{clip_id}/download")
async def download_clip(job_id: str, clip_id: str):
    job = _job_or_404(job_id)
    clip = next((c for c in job.clips if c.get("id") == clip_id), None)
    if not clip or not clip.get("file"):
        raise HTTPException(status_code=404, detail="Clip has not been rendered yet")
    path = Path(clip["file"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Clip file is missing")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=path.name,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(path.name)}"},
    )


@router.delete("/jobs/{job_id}/clips/{clip_id}/file")
async def delete_clip_file(job_id: str, clip_id: str) -> Dict[str, Any]:
    _job_or_404(job_id)
    removed = runner.delete_clip_file(job_id, clip_id)
    return {"ok": True, "removed": removed, "job": store.get(job_id).to_dict()}


@router.get("/jobs/{job_id}/download-all")
async def download_all(job_id: str):
    job = _job_or_404(job_id)
    files = [Path(c["file"]) for c in job.clips if c.get("file") and Path(c["file"]).is_file()]
    if not files:
        raise HTTPException(status_code=404, detail="No rendered clips to download")
    tmp = Path(tempfile.mkstemp(suffix=".zip")[1])
    safe_name = "".join(ch for ch in job.name if ch not in '\\/:*?"<>|').strip() or "autoclip"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as archive:
        for file in files:
            archive.write(file, arcname=file.name)
    return FileResponse(
        tmp,
        media_type="application/zip",
        filename=f"{safe_name}-clips.zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(safe_name)}-clips.zip"},
    )


@router.get("/jobs/{job_id}/transcript")
async def get_transcript(job_id: str, format: str = Query("json", pattern="^(json|srt|txt)$")):
    job = _job_or_404(job_id)
    paths = job.paths()
    transcript_file = paths["work"] / "transcript.json"
    if not transcript_file.is_file():
        raise HTTPException(status_code=404, detail="No transcript available yet")
    data = json.loads(transcript_file.read_text(encoding="utf-8"))
    if format == "json":
        return JSONResponse(data)
    if format == "srt":
        srt_file = paths["work"] / "transcript.srt"
        if not srt_file.is_file():
            raise HTTPException(status_code=404, detail="No SRT available")
        return PlainTextResponse(srt_file.read_text(encoding="utf-8"), media_type="application/x-subrip")
    text = "\n".join(seg.get("text", "") for seg in data.get("segments", []))
    return PlainTextResponse(text)


@router.get("/jobs/{job_id}/clips/{clip_id}/srt")
async def get_clip_srt(job_id: str, clip_id: str):
    """Export just the caption cues for one clip (for platform uploads)."""
    job = _job_or_404(job_id)
    clip = next((c for c in job.clips if c.get("id") == clip_id), None)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    paths = job.paths()
    transcript_file = paths["work"] / "transcript.json"
    if not transcript_file.is_file():
        raise HTTPException(status_code=404, detail="No transcript available")
    from ..transcribe.models import Transcript
    from ..transcribe.srt import write_srt

    transcript = Transcript.from_dict(json.loads(transcript_file.read_text(encoding="utf-8")))
    segments = [
        type(seg)(start=seg.start - clip["start"], end=seg.end - clip["start"], text=seg.text)
        for seg in transcript.segments
        if seg.end > clip["start"] and seg.start < clip["end"]
    ]
    for seg in segments:
        seg.start = max(0.0, seg.start)
        seg.end = min(clip["end"] - clip["start"], seg.end)
    target = paths["work"] / f"{clip_id}.srt"
    write_srt(segments, target)
    return FileResponse(target, media_type="application/x-subrip", filename=f"{clip_id}.srt")


@router.get("/stats")
async def stats() -> Dict[str, Any]:
    jobs = store.list()
    completed = [j for j in jobs if j.status == "completed"]
    rendered = sum(
        1 for job in jobs for clip in job.clips if clip.get("status") == "done" and clip.get("file")
    )
    return {
        "jobs": len(jobs),
        "completed_jobs": len(completed),
        "rendered_clips": rendered,
        "source_seconds": round(sum(job.duration for job in jobs), 1),
        "clips_seconds": round(
            sum(clip.get("duration", 0) for job in jobs for clip in job.clips if clip.get("file")), 1
        ),
    }
