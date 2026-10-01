"""
utils/renderer.py – Render pipeline orchestrator for ET-Talks Studio Pro.

Responsibilities:
  1. Validate inputs (disk space, file existence, encoder availability).
  2. Build the FFmpeg command via ffmpeg.py.
  3. Execute FFmpeg in a subprocess, parsing progress from stderr.
  4. Emit SSE-compatible progress events (frame, fps, eta, percent).
  5. Save the result to the history database.
  6. Clean up temp files.

Business logic is fully separated from Flask routes.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Generator

import config
from utils.database import db
from utils.ffmpeg import FFmpegBuilder, TextOverlay
from utils.gpu import get_encoder
from utils.helpers import (
    check_disk_space,
    format_bytes,
    format_duration,
    new_job_id,
    safe_filename,
)
from utils.thumbnails import extract_cover_thumbnail

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[dict[str, Any]], None]


# ---------------------------------------------------------------------------
# Render job dataclass
# ---------------------------------------------------------------------------

@dataclass
class RenderJob:
    """Holds all parameters and live state for a single render operation."""

    job_id: str = field(default_factory=new_job_id)
    input_path: str = ""
    output_filename: str = ""
    source_url: str = ""
    title: str = ""
    quality: str = config.DEFAULT_QUALITY
    crop_mode: str = "blur_background"
    encoder: str = "libx264"
    start_time: float = 0.0
    end_time: float | None = None
    logo_path: str = ""
    logo_opacity: float = 1.0
    logo_position: str = "top_right"
    watermark_text: str = ""
    music_path: str = ""
    music_volume: float = 0.3
    original_volume: float = 1.0
    text_overlays: list[TextOverlay] = field(default_factory=list)
    burn_subtitles: bool = False
    color_template: str = "none"
    subtitle_path: str = ""   # set by /api/transcript (paste method) — takes priority

    # Runtime state (not user-set)
    status: str = "pending"          # pending|running|done|error|cancelled
    progress_percent: float = 0.0
    current_frame: int = 0
    total_frames: int = 0
    encoding_fps: float = 0.0
    eta_seconds: int = 0
    error_message: str = ""
    output_path: str = ""
    _callbacks: list[ProgressCallback] = field(default_factory=list, repr=False)
    _process: subprocess.Popen | None = field(default=None, repr=False)

    def add_callback(self, cb: ProgressCallback) -> None:
        self._callbacks.append(cb)

    def _emit(self, data: dict[str, Any]) -> None:
        for cb in self._callbacks:
            try:
                cb(data)
            except Exception:
                pass

    def cancel(self) -> None:
        """Attempt to terminate the running FFmpeg process."""
        if self._process and self._process.poll() is None:
            self._process.terminate()
            self.status = "cancelled"
            logger.info("Render job %s cancelled by user.", self.job_id)


# ---------------------------------------------------------------------------
# Job registry (in-memory, module-level)
# ---------------------------------------------------------------------------

_job_registry: dict[str, RenderJob] = {}
_registry_lock = threading.Lock()


def register_job(job: RenderJob) -> None:
    with _registry_lock:
        _job_registry[job.job_id] = job


def get_job(job_id: str) -> RenderJob | None:
    with _registry_lock:
        return _job_registry.get(job_id)


def get_all_jobs() -> list[RenderJob]:
    with _registry_lock:
        return list(_job_registry.values())


# ---------------------------------------------------------------------------
# Main renderer
# ---------------------------------------------------------------------------

class Renderer:
    """Orchestrates the complete render pipeline for a RenderJob."""

    def run(self, job: RenderJob) -> Path:
        """Execute the render synchronously.

        Returns the output file Path on success.
        Raises RuntimeError / ValueError on failure.
        """
        register_job(job)
        job.status = "running"
        job._emit({"type": "status", "status": "running", "job_id": job.job_id})

        try:
            self._validate(job)
            output_path = self._prepare_output_path(job)
            cmd = self._build_command(job, output_path)
            total_frames = self._estimate_total_frames(job)
            job.total_frames = total_frames

            self._execute(job, cmd, output_path)

            # Save to history
            self._record_history(job, output_path)

            job.status = "done"
            job.progress_percent = 100.0
            job._emit({
                "type": "done",
                "job_id": job.job_id,
                "status": "done",
                "output_path": str(output_path),
                "filename": output_path.name,
            })
            logger.info("Render complete: %s -> %s", job.job_id, output_path)
            return output_path

        except Exception as exc:
            job.status = "error"
            job.error_message = str(exc)
            job._emit({"type": "error", "job_id": job.job_id,
                       "status": "error", "message": str(exc)})
            logger.error("Render job %s failed: %s", job.job_id, exc)
            raise

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self, job: RenderJob) -> None:
        if not Path(job.input_path).exists():
            raise FileNotFoundError(f"Input file not found: {job.input_path}")

        ok, free = check_disk_space(config.OUTPUT_DIR, config.MIN_FREE_DISK_BYTES)
        if not ok:
            raise RuntimeError(
                f"Insufficient disk space. "
                f"Need {format_bytes(config.MIN_FREE_DISK_BYTES)}, "
                f"have {format_bytes(free)}."
            )

    # ------------------------------------------------------------------
    # Output path preparation
    # ------------------------------------------------------------------

    def _prepare_output_path(self, job: RenderJob) -> Path:
        if job.output_filename:
            filename = safe_filename(job.output_filename)
            if not filename.lower().endswith(".mp4"):
                filename += ".mp4"
        else:
            filename = f"{safe_filename(job.title or 'short')}_{job.job_id}.mp4"

        output_path = config.OUTPUT_DIR / filename

        # Avoid overwriting existing files
        counter = 1
        while output_path.exists():
            stem = output_path.stem.rstrip(f"_{counter-1}")
            output_path = config.OUTPUT_DIR / f"{stem}_{counter}.mp4"
            counter += 1

        job.output_path = str(output_path)
        return output_path

    # ------------------------------------------------------------------
    # Command building
    # ------------------------------------------------------------------

    def _build_command(self, job: RenderJob, output_path: Path) -> list[str]:
        encoder = get_encoder(job.encoder if job.encoder != "auto" else None)
        builder = FFmpegBuilder(
            input_path=job.input_path,
            output_path=output_path,
            encoder=encoder,
            quality=job.quality,
            crop_mode=job.crop_mode,
            start_time=job.start_time,
            end_time=job.end_time,
            logo_path=job.logo_path or None,
            logo_opacity=job.logo_opacity,
            logo_position=job.logo_position,
            watermark_text=job.watermark_text,
            music_path=job.music_path or None,
            music_volume=job.music_volume,
            original_volume=job.original_volume,
            text_overlays=job.text_overlays,
            subtitle_file=self._prepare_subtitles(job) if job.burn_subtitles else None,
            color_template=job.color_template,
        )
        job.encoder = encoder  # Update with resolved encoder
        return builder.build()

    def _prepare_subtitles(self, job: RenderJob) -> str | None:
        """Resolve the subtitle file to burn in, with priority:

        1. Manually pasted transcript (.srt) from /api/transcript  ← highest
        2. Auto-downloaded .vtt/.srt from yt-dlp (Amharic then English)
        3. None (subtitles silently skipped)

        If job.start_time > 0, timestamps are shifted so the trimmed clip
        starts at t=0 in the subtitle stream.
        """
        # Priority 1: manually pasted transcript
        if job.subtitle_path and Path(job.subtitle_path).exists():
            logger.info("Using pasted transcript: %s", job.subtitle_path)
            return self._shift_subtitle(job.subtitle_path, job.start_time, job.job_id)

        # Priority 2: auto-downloaded subtitle alongside the video
        vid_path = Path(job.input_path)
        candidates = [
            vid_path.with_name(f"{vid_path.stem}.am.vtt"),
            vid_path.with_name(f"{vid_path.stem}.en.vtt"),
            vid_path.with_name(f"{vid_path.stem}.am.srt"),
            vid_path.with_name(f"{vid_path.stem}.en.srt"),
        ]
        sub_file = next((c for c in candidates if c.exists()), None)
        if sub_file:
            logger.info("Using auto-downloaded subtitle: %s", sub_file)
            return self._shift_subtitle(str(sub_file), job.start_time, job.job_id)

        logger.warning("No subtitle file found for job %s — skipping subtitles.", job.job_id)
        return None

    def _shift_subtitle(self, sub_path: str, start_time: float, job_id: str) -> str:
        """Return the subtitle path as-is, or a timestamp-shifted copy if start_time > 0."""
        if start_time <= 0:
            return sub_path

        import re as _re
        src = Path(sub_path)
        out_file = config.TEMP_DIR / f"{job_id}_{src.name}"
        config.TEMP_DIR.mkdir(parents=True, exist_ok=True)

        _TS_PAT = _re.compile(
            r"(\d{1,2}:\d{2}:\d{2}[\.,]\d{3})"
        )

        def _parse(ts: str) -> float:
            ts = ts.replace(",", ".")
            p = ts.split(":")
            return int(p[0]) * 3600 + int(p[1]) * 60 + float(p[2])

        def _fmt(s: float) -> str:
            if s < 0:
                s = 0.0
            h = int(s // 3600)
            m = int((s % 3600) // 60)
            sec = s % 60
            return f"{h:02d}:{m:02d}:{int(sec):02d},{int(round((sec % 1) * 1000)):03d}"

        def _shift_line(line: str) -> str:
            def repl(m: re.Match) -> str:
                return _fmt(_parse(m.group(1)) - start_time)
            return _TS_PAT.sub(repl, line)

        with open(src, "r", encoding="utf-8") as fin, \
             open(out_file, "w", encoding="utf-8") as fout:
            for line in fin:
                fout.write(_shift_line(line))

        return str(out_file)

    # ------------------------------------------------------------------
    # FFmpeg execution with progress parsing
    # ------------------------------------------------------------------

    def _execute(self, job: RenderJob, cmd: list[str], output_path: Path) -> None:
        """Run FFmpeg and parse stderr for progress updates."""
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            raise RuntimeError(
                f"FFmpeg not found at '{config.FFMPEG_BIN}'. "
                "Please install FFmpeg and add it to your PATH."
            )

        job._process = process
        start_ts = time.monotonic()
        stderr_tail: list[str] = []

        for line in process.stderr:
            line = line.strip()
            if not line:
                continue
            stderr_tail.append(line)
            stderr_tail = stderr_tail[-20:]

            # Parse progress line: frame=  120 fps= 30 q=18.0 size=   512kB time=00:00:04.00 ...
            progress = _parse_ffmpeg_progress(line, job.total_frames, start_ts)
            if progress:
                job.current_frame = progress["frame"]
                job.encoding_fps = progress["fps"]
                job.eta_seconds = progress["eta"]
                job.progress_percent = progress["percent"]
                job._emit({
                    "type": "progress",
                    "job_id": job.job_id,
                    **progress,
                })
                continue

            # Log warnings and errors from FFmpeg
            if "error" in line.lower() or "warning" in line.lower():
                job._emit({"type": "log", "job_id": job.job_id, "message": line})

        process.wait()

        if process.returncode not in (0, None) and job.status != "cancelled":
            if stderr_tail:
                tail_text = "\n".join(stderr_tail)
                console_safe_tail = tail_text.encode(
                    "cp1252", errors="backslashreplace"
                ).decode("cp1252")
                logger.error(
                    "FFmpeg stderr tail for job %s:\n%s",
                    job.job_id,
                    console_safe_tail,
                )
            raise RuntimeError(
                f"FFmpeg exited with code {process.returncode}. "
                "Check the log for details."
            )

    def _estimate_total_frames(self, job: RenderJob) -> int:
        """Use ffprobe to estimate total frame count for progress display."""
        try:
            result = subprocess.run(
                [
                    config.FFPROBE_BIN,
                    "-v", "quiet",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=nb_frames,r_frame_rate,duration",
                    "-of", "json",
                    job.input_path,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            data = json.loads(result.stdout)
            stream = data.get("streams", [{}])[0]

            nb_frames = stream.get("nb_frames")
            if nb_frames and str(nb_frames).isdigit():
                total = int(nb_frames)
                if job.end_time and job.start_time is not None:
                    clip_duration = job.end_time - job.start_time
                    duration_str = stream.get("duration", "0")
                    full_duration = float(duration_str) if duration_str else 0
                    if full_duration > 0:
                        total = int(total * clip_duration / full_duration)
                return total

            # Fallback: fps × duration
            fps_str = stream.get("r_frame_rate", "25/1")
            num, den = (fps_str.split("/") + ["1"])[:2]
            fps = float(num) / float(den) if float(den) else 25.0
            duration = float(stream.get("duration") or 0)
            if job.end_time:
                duration = min(duration, job.end_time - job.start_time)
            return int(fps * duration)

        except Exception:
            return 0

    # ------------------------------------------------------------------
    # History recording
    # ------------------------------------------------------------------

    def _record_history(self, job: RenderJob, output_path: Path) -> None:
        """Save render result to SQLite and extract a cover thumbnail."""
        try:
            file_size = output_path.stat().st_size if output_path.exists() else 0

            # Extract cover from output video
            thumb_path = config.THUMBNAILS_DIR / f"{job.job_id}_cover.jpg"
            extract_cover_thumbnail(output_path, thumb_path, timestamp_seconds=1.0)

            duration = (job.end_time or 0) - job.start_time
            if duration <= 0:
                # Try to get from ffprobe
                duration = _probe_duration(str(output_path))

            db.add_history(
                record_id=job.job_id,
                title=job.title or output_path.stem,
                source_url=job.source_url,
                thumbnail_path=str(thumb_path) if thumb_path.exists() else "",
                output_filename=output_path.name,
                output_path=str(output_path),
                duration=duration,
                resolution=f"{config.OUTPUT_WIDTH}x{config.OUTPUT_HEIGHT}",
                quality=job.quality,
                crop_mode=job.crop_mode,
                encoder=job.encoder,
                file_size_bytes=file_size,
                render_settings={
                    "text_overlays": job.text_overlays,
                    "logo_position": job.logo_position,
                    "logo_opacity": job.logo_opacity,
                    "music_volume": job.music_volume,
                    "watermark_text": job.watermark_text,
                    "start_time": job.start_time,
                    "end_time": job.end_time,
                    "burn_subtitles": job.burn_subtitles,
                    "color_template": job.color_template,
                },
            )
        except Exception as exc:
            logger.error("Failed to record render history: %s", exc)


# ---------------------------------------------------------------------------
# FFmpeg stderr progress parser
# ---------------------------------------------------------------------------

_PROGRESS_RE = re.compile(
    r"frame=\s*(?P<frame>\d+)\s+"
    r"fps=\s*(?P<fps>[\d.]+)\s+"
    r".*?time=(?P<time>[\d:.]+)"
)


def _parse_ffmpeg_progress(
    line: str,
    total_frames: int,
    start_ts: float,
) -> dict[str, Any] | None:
    """Parse a FFmpeg progress line into a structured dict."""
    m = _PROGRESS_RE.search(line)
    if not m:
        return None

    frame = int(m.group("frame"))
    fps = float(m.group("fps")) or 1.0
    time_str = m.group("time")

    percent = (frame / total_frames * 100.0) if total_frames > 0 else 0.0
    percent = min(percent, 99.9)

    elapsed = time.monotonic() - start_ts
    eta = 0
    if fps > 0 and total_frames > 0 and frame > 0:
        remaining_frames = total_frames - frame
        eta = int(remaining_frames / fps)

    return {
        "frame": frame,
        "fps": round(fps, 1),
        "time": time_str,
        "percent": round(percent, 1),
        "elapsed": round(elapsed, 1),
        "eta": eta,
        "eta_str": format_duration(eta),
    }


def _probe_duration(path: str) -> float:
    """Use ffprobe to get duration of a video file."""
    try:
        result = subprocess.run(
            [
                config.FFPROBE_BIN,
                "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# SSE progress generator (used by Flask route)
# ---------------------------------------------------------------------------

def render_progress_generator(job: RenderJob) -> Generator[str, None, None]:
    """Yield SSE-formatted progress events, running the render in a thread."""
    import queue

    event_queue: queue.Queue[dict | None] = queue.Queue()

    def on_event(data: dict) -> None:
        event_queue.put(data)

    job.add_callback(on_event)
    renderer = Renderer()

    def run() -> None:
        try:
            renderer.run(job)
        except Exception as exc:
            event_queue.put({"type": "error", "message": str(exc)})
        finally:
            event_queue.put(None)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    while True:
        try:
            event = event_queue.get(timeout=config.SSE_KEEPALIVE_INTERVAL)
        except Exception:
            yield ": keepalive\n\n"
            continue

        if event is None:
            break

        yield f"data: {json.dumps(event)}\n\n"
