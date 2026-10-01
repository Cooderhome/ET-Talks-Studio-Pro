"""
app.py – Main Flask application entry point for ET-Talks Studio Pro.

Registers blueprints, initializes the DB, sets up logging, and serves the
single-page application. Handles file uploads and API endpoints for video
info, downloading, rendering, and history management.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    send_file,
    send_from_directory,
    stream_with_context,
)
from werkzeug.utils import secure_filename

import config
from utils.database import db
from utils.downloader import DownloadJob, download_progress_generator, fetch_video_info
from utils.gpu import get_gpu_info_dict
from utils.helpers import format_duration, new_job_id, safe_filename
from utils.renderer import RenderJob, register_job, render_progress_generator
from utils.thumbnails import delete_thumbnails, get_thumbnail_path
from utils.transcript import save_transcript_srt

# ---------------------------------------------------------------------------
# App initialization & Logging
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.config["SECRET_KEY"] = config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_BYTES

logging.basicConfig(
    level=logging.DEBUG if config.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(config.LOGS_DIR / "app.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# UI Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index() -> str:
    """Serve the main dashboard."""
    return render_template(
        "index.html",
        fonts=config.AVAILABLE_FONTS,
        default_font=config.DEFAULT_FONT,
        quality_profiles=config.QUALITY_PROFILES,
        default_quality=config.DEFAULT_QUALITY,
    )


# ---------------------------------------------------------------------------
# Video Info & Download API
# ---------------------------------------------------------------------------

@app.route("/api/info", methods=["POST"])
def api_info() -> Response:
    """Fetch video metadata (no download)."""
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided."}), 400

    try:
        info = fetch_video_info(url)
        return jsonify(info)
    except Exception as exc:
        logger.error("Failed to fetch info for %s: %s", url, exc)
        return jsonify({"error": str(exc)}), 400


@app.route("/api/download", methods=["POST"])
def api_download() -> Response:
    """Start a download job and return its ID."""
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided."}), 400

    if not hasattr(app, "download_jobs"):
        app.download_jobs = {}

    # Reuse an existing completed job for the same URL
    for existing in app.download_jobs.values():
        if existing.url == url and existing.status == "done" and existing.output_path:
            if Path(existing.output_path).exists():
                logger.info("Reusing job %s for %s", existing.job_id, url)
                return jsonify({
                    "job_id": existing.job_id,
                    "status": "done",
                    "output_path": str(existing.output_path),
                    "info": existing.info,
                })

    job = DownloadJob(url=url, job_id=new_job_id())
    app.download_jobs[job.job_id] = job

    return jsonify({"job_id": job.job_id, "status": "pending"})


@app.route("/api/stream/download/<job_id>")
def api_stream_download(job_id: str) -> Response:
    """SSE endpoint for download progress."""
    jobs = getattr(app, "download_jobs", {})
    job: DownloadJob | None = jobs.get(job_id)
    
    if not job:
        return jsonify({"error": "Job not found."}), 404

    return Response(
        stream_with_context(download_progress_generator(job)),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/preview/<job_id>")
def api_preview(job_id: str) -> Response:
    """Serve the downloaded video for phone preview."""
    jobs = getattr(app, "download_jobs", {})
    job: DownloadJob | None = jobs.get(job_id)
    if not job or not job.output_path or not Path(job.output_path).exists():
        return jsonify({"error": "Video not found."}), 404
        
    path = Path(job.output_path)
    return send_file(
        str(path),
        mimetype="video/mp4",
        as_attachment=False,
        conditional=True,  # Important for seeking in video tag
    )



# ---------------------------------------------------------------------------
# Downloads Library API
# ---------------------------------------------------------------------------

@app.route("/api/downloads")
def api_list_downloads() -> Response:
    """List all video files in the downloads directory."""
    files = []
    if config.DOWNLOADS_DIR.exists():
        for f in sorted(config.DOWNLOADS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if f.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov", ".avi"):
                size_mb = round(f.stat().st_size / (1024 * 1024), 1)
                files.append({
                    "filename": f.name,
                    "stem": f.stem,
                    "size_str": f"{size_mb} MB",
                })
    return jsonify({"downloads": files})


@app.route("/api/use-download", methods=["POST"])
def api_use_download() -> Response:
    """Load an existing downloaded file as a job without re-downloading."""
    data = request.get_json() or {}
    filename = data.get("filename", "").strip()
    if not filename:
        return jsonify({"error": "No filename provided."}), 400

    file_path = config.DOWNLOADS_DIR / filename
    if not file_path.exists():
        return jsonify({"error": "File not found."}), 404

    if not hasattr(app, "download_jobs"):
        app.download_jobs = {}

    # Reuse existing job for this exact file
    for existing in app.download_jobs.values():
        if existing.output_path and Path(existing.output_path) == file_path and existing.status == "done":
            return jsonify({"job_id": existing.job_id, "status": "done",
                            "output_path": str(file_path), "info": existing.info})

    # Create a synthetic completed job for a local file
    job = DownloadJob(url=f"local://{filename}", job_id=new_job_id())
    job.status = "done"
    job.output_path = file_path
    size_mb = round(file_path.stat().st_size / (1024 * 1024), 1)
    job.info = {
        "title": file_path.stem, "duration": 0, "duration_str": "—",
        "view_count": 0, "view_count_str": "—", "thumbnail": "",
        "width": 1920, "height": 1080, "fps": 30, "vcodec": "mp4",
        "filesize_approx_str": f"{size_mb} MB",
    }
    app.download_jobs[job.job_id] = job
    logger.info("Loaded local file %s as job %s", filename, job.job_id)

    return jsonify({"job_id": job.job_id, "status": "done",
                    "output_path": str(file_path), "info": job.info})


# ---------------------------------------------------------------------------
# Timeline Thumbnails API
# ---------------------------------------------------------------------------

@app.route("/api/thumbnails/<job_id>")
def api_thumbnails(job_id: str) -> Response:
    """Generate and return timeline thumbnails for a job."""
    jobs = getattr(app, "download_jobs", {})
    job: DownloadJob | None = jobs.get(job_id)
    if not job or not job.output_path:
        return jsonify({"error": "Job or video not found."}), 404

    from utils.thumbnails import generate_timeline_thumbnails, get_video_duration
    try:
        thumbs = generate_timeline_thumbnails(job.output_path, job_id)
        duration = (job.info or {}).get("duration") or get_video_duration(job.output_path)
        if not duration and len(thumbs) > 1:
            last_ts = thumbs[-1].get("timestamp") or 0
            duration = round(last_ts * len(thumbs) / (len(thumbs) - 1), 3)
        if duration:
            job.info["duration"] = duration
            job.info["duration_str"] = format_duration(duration)
        return jsonify({"thumbnails": thumbs, "duration": duration})
    except Exception as exc:
        logger.error("Thumbnail generation failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/thumbnail/<job_id>/<filename>")
def api_serve_thumbnail(job_id: str, filename: str) -> Response:
    """Serve a specific generated timeline thumbnail."""
    path = get_thumbnail_path(job_id, secure_filename(filename))
    if not path:
        return jsonify({"error": "Thumbnail not found."}), 404
    return send_file(str(path), mimetype="image/jpeg")


# ---------------------------------------------------------------------------
# File Uploads (Logo & Music)
# ---------------------------------------------------------------------------

@app.route("/api/upload", methods=["POST"])
def api_upload() -> Response:
    """Handle logo and background music uploads."""
    if "file" not in request.files:
        return jsonify({"error": "No file part."}), 400
        
    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"error": "No selected file."}), 400

    upload_type = request.form.get("type", "logo")
    ext = os.path.splitext(file.filename)[1].lower()
    
    if upload_type == "logo" and ext not in config.ALLOWED_LOGO_EXTENSIONS:
        return jsonify({"error": f"Invalid logo format. Allowed: {config.ALLOWED_LOGO_EXTENSIONS}"}), 400
    if upload_type == "music" and ext not in config.ALLOWED_AUDIO_EXTENSIONS:
        return jsonify({"error": f"Invalid audio format. Allowed: {config.ALLOWED_AUDIO_EXTENSIONS}"}), 400

    filename = secure_filename(file.filename)
    # Prefix with type and uuid to prevent collisions
    save_name = f"{upload_type}_{new_job_id()}_{filename}"
    save_path = config.UPLOADS_DIR / save_name
    
    try:
        file.save(str(save_path))
        # Store the path in session/app for the render job to pick up
        # We will just rely on the UI passing the filename or path. 
        # Actually, simpler: return the absolute path, the UI stores it, and sends it back in the render payload.
        # Wait, sending absolute paths to client is bad practice. We'll return the filename and let the backend construct it.
        return jsonify({
            "message": "Upload successful",
            "filename": save_name,
        })
    except Exception as exc:
        logger.error("Upload failed: %s", exc)
        return jsonify({"error": "Failed to save file."}), 500


@app.route("/api/transcript", methods=["POST"])
def api_transcript() -> Response:
    """Parse a copy-pasted YouTube transcript and save it as a .srt file.

    Accepts JSON: { "text": "<raw transcript text>", "job_id": "<download_job_id>" }
    Returns: { "subtitle_path": "<server-side path for render job>" }
    """
    data = request.get_json() or {}
    raw_text = data.get("text", "").strip()
    job_id   = data.get("job_id", new_job_id())

    if not raw_text:
        return jsonify({"error": "No transcript text provided."}), 400

    try:
        out_path = config.TEMP_DIR / f"{job_id}_transcript.srt"
        save_transcript_srt(raw_text, out_path)
        logger.info("Transcript saved: %s (%d chars)", out_path, len(raw_text))
        return jsonify({
            "message": "Transcript parsed successfully.",
            "subtitle_path": str(out_path),
            "line_count": raw_text.count("\n"),
        })
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.error("Transcript save failed: %s", exc)
        return jsonify({"error": "Failed to process transcript."}), 500


# ---------------------------------------------------------------------------
# Render API
# ---------------------------------------------------------------------------

@app.route("/api/render", methods=["POST"])
def api_render() -> Response:
    """Start a render job and return its ID."""
    data = request.get_json() or {}
    
    dl_job_id = data.get("download_job_id")
    if not dl_job_id:
        return jsonify({"error": "No download_job_id provided."}), 400
        
    jobs = getattr(app, "download_jobs", {})
    dl_job: DownloadJob | None = jobs.get(dl_job_id)
    if not dl_job or not dl_job.output_path:
        return jsonify({"error": "Valid download job not found."}), 400

    job = RenderJob(
        input_path=str(dl_job.output_path),
        source_url=dl_job.url,
        title=data.get("title", "Short"),
        quality=data.get("quality", config.DEFAULT_QUALITY),
        crop_mode=data.get("crop_mode", "blur_background"),
        start_time=float(data.get("start_time", 0)),
        end_time=float(data.get("end_time")) if data.get("end_time") else None,
        logo_opacity=float(data.get("logo_opacity", 1.0)),
        logo_position=data.get("logo_position", "top_right"),
        watermark_text=data.get("watermark_text", ""),
        music_volume=float(data.get("music_volume", 0.3)),
        original_volume=float(data.get("original_volume", 1.0)),
        text_overlays=data.get("text_overlays", []),
        burn_subtitles=bool(data.get("burn_subtitles", False)),
        color_template=data.get("color_template", "none"),
        subtitle_path=data.get("subtitle_path", ""),
    )

    register_job(job)
    
    # We simplified uploads in UI to just hold state, let's assume UI isn't sending filename yet,
    # or if it is, we need to map it. The spec didn't wire up the UI to pass the filename back to payload.
    # To fix this, if needed, we'd add fields. For now, empty strings handled safely by ffmpeg builder.
    
    return jsonify({"job_id": job.job_id, "status": "pending"})


@app.route("/api/stream/render/<job_id>")
def api_stream_render(job_id: str) -> Response:
    """SSE endpoint for render progress."""
    from utils.renderer import get_job
    job = get_job(job_id)
    
    if not job:
        return jsonify({"error": "Render job not found."}), 404

    return Response(
        stream_with_context(render_progress_generator(job)),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# GPU & History API
# ---------------------------------------------------------------------------

@app.route("/api/gpu-info")
def api_gpu_info() -> Response:
    """Return auto-detected GPU encoder details."""
    return jsonify(get_gpu_info_dict())


@app.route("/api/history")
def api_history() -> Response:
    """Return render history (optionally filtered)."""
    query = request.args.get("search", "").strip()
    try:
        if query:
            records = db.search_history(query)
        else:
            records = db.get_history()
        return jsonify({"history": records})
    except Exception as exc:
        logger.error("Failed to load history: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/history-thumb/<record_id>")
def api_history_thumb(record_id: str) -> Response:
    """Serve the cover thumbnail for a history record."""
    record = db.get_history_item(record_id)
    if not record or not record.get("thumbnail_path"):
        return jsonify({"error": "Thumbnail not found."}), 404
        
    path = Path(record["thumbnail_path"])
    if not path.exists():
        return jsonify({"error": "Thumbnail not found on disk."}), 404
        
    return send_file(str(path), mimetype="image/jpeg")


@app.route("/api/history/<record_id>", methods=["DELETE"])
def api_delete_history(record_id: str) -> Response:
    """Delete a history record and its associated files."""
    record = db.get_history_item(record_id)
    if not record:
        return jsonify({"error": "Record not found."}), 404
        
    try:
        # Delete output file
        if record.get("output_path"):
            out_p = Path(record["output_path"])
            if out_p.exists():
                out_p.unlink()
                
        # Delete thumbnail
        if record.get("thumbnail_path"):
            th_p = Path(record["thumbnail_path"])
            if th_p.exists():
                th_p.unlink()
                
        # Delete from DB
        db.delete_history(record_id)
        
        return jsonify({"message": "Deleted successfully."})
    except Exception as exc:
        logger.error("Delete failed for %s: %s", record_id, exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/download-output/<record_id>")
def api_download_output(record_id: str) -> Response:
    """Serve the final rendered Short as an attachment."""
    record = db.get_history_item(record_id)
    if not record or not record.get("output_path"):
        return jsonify({"error": "Record or file not found."}), 404
        
    path = Path(record["output_path"])
    if not path.exists():
        return jsonify({"error": "File no longer exists on disk."}), 404
        
    return send_file(
        str(path),
        mimetype="video/mp4",
        as_attachment=True,
        download_name=record.get("output_filename") or path.name,
    )


# ---------------------------------------------------------------------------
# Auto-Shorts (Batch Rendering) API
# ---------------------------------------------------------------------------

@app.route("/api/auto-shorts/extract", methods=["POST"])
def api_auto_shorts_extract() -> Response:
    """Extract highlights from a download job's transcript using Ollama."""
    data = request.get_json() or {}
    job_id = data.get("download_job_id")
    model = data.get("model", "llama3")
    
    if not job_id:
        return jsonify({"error": "No download_job_id provided."}), 400
        
    jobs = getattr(app, "download_jobs", {})
    from utils.downloader import DownloadJob
    dl_job: DownloadJob | None = jobs.get(job_id)
    
    if not dl_job or not dl_job.output_path:
        return jsonify({"error": "Download job or video not found."}), 404
        
    vid_path = Path(dl_job.output_path)
    
    # Priority 1: Check for manually pasted transcript (bypasses 429 error)
    import config
    pasted_sub = config.TEMP_DIR / f"{job_id}_transcript.srt"
    
    if pasted_sub.exists():
        sub_file = pasted_sub
    else:
        # Priority 2: Auto-downloaded subtitles
        candidates = [
            vid_path.with_name(f"{vid_path.stem}.am.vtt"),
            vid_path.with_name(f"{vid_path.stem}.en.vtt"),
            vid_path.with_name(f"{vid_path.stem}.am.srt"),
            vid_path.with_name(f"{vid_path.stem}.en.srt"),
        ]
        sub_file = next((c for c in candidates if c.exists()), None)
    
    if not sub_file:
        return jsonify({"error": "No subtitles found. YouTube may have blocked auto-download (Error 429). Please use the 'Paste YouTube Transcript' panel above, paste the transcript, click Parse, and then try Auto-Shorts again."}), 400
        
    from utils.ai_highlighter import read_transcript, get_highlights_from_ollama
    try:
        transcript = read_transcript(str(sub_file))
        highlights = get_highlights_from_ollama(transcript, model=model)
        return jsonify({"highlights": highlights})
    except Exception as exc:
        logger.error("Auto-Shorts extraction failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/auto-shorts/render", methods=["POST"])
def api_auto_shorts_render() -> Response:
    """Submit a batch of highlight jobs to be rendered sequentially."""
    data = request.get_json() or {}
    dl_job_id = data.get("download_job_id")
    highlights = data.get("highlights", [])
    
    if not dl_job_id or not highlights:
        return jsonify({"error": "Missing download_job_id or highlights."}), 400
        
    jobs = getattr(app, "download_jobs", {})
    from utils.downloader import DownloadJob
    dl_job: DownloadJob | None = jobs.get(dl_job_id)
    if not dl_job or not dl_job.output_path:
        return jsonify({"error": "Valid download job not found."}), 400

    def parse_timestamp(value) -> float:
        """Parse a timestamp into float seconds. Handles floats, ints, and M:SS / H:MM:SS strings."""
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip()
        parts = s.split(":")
        try:
            if len(parts) == 1:
                return float(parts[0])
            elif len(parts) == 2:
                return int(parts[0]) * 60 + float(parts[1])
            elif len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        except (ValueError, TypeError):
            pass
        return 0.0
        
    from utils.auto_shorts import BatchRenderJob, register_batch
    batch = BatchRenderJob()
    
    quality = data.get("quality", config.DEFAULT_QUALITY)
    crop_mode = data.get("crop_mode", "center_crop")
    burn_subtitles = bool(data.get("burn_subtitles", True))
    
    skipped = 0
    for i, hl in enumerate(highlights):
        title = hl.get("title", f"Short {i+1}")
        start_time = parse_timestamp(hl.get("start_time", 0))
        end_time   = parse_timestamp(hl.get("end_time", 60))
        
        duration = end_time - start_time
        if duration <= 1.0:
            logger.warning(f"Skipping highlight '{title}': invalid duration ({duration:.2f}s) — start={start_time}, end={end_time}")
            skipped += 1
            continue
        
        job = RenderJob(
            input_path=str(dl_job.output_path),
            source_url=dl_job.url,
            title=title,
            quality=quality,
            crop_mode=crop_mode,
            start_time=start_time,
            end_time=end_time,
            burn_subtitles=burn_subtitles
        )
        batch.jobs.append(job)
        
    if not batch.jobs:
        return jsonify({"error": f"All {skipped} highlights had invalid timestamps (0-second duration). The AI may have returned bad timestamps. Please try again."}), 400

    register_batch(batch)
    return jsonify({"batch_id": batch.batch_id, "status": "pending", "total": len(batch.jobs), "skipped": skipped})


@app.route("/api/stream/auto-shorts/<batch_id>")
def api_stream_auto_shorts(batch_id: str) -> Response:
    """SSE endpoint for batch render progress."""
    from utils.auto_shorts import get_batch, batch_progress_generator
    batch = get_batch(batch_id)
    if not batch:
        return jsonify({"error": "Batch job not found."}), 404
        
    return Response(
        stream_with_context(batch_progress_generator(batch)),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG, threaded=True)
