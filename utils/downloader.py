"""
utils/downloader.py – YouTube video downloader for ET-Talks Studio Pro.

Wraps yt-dlp to:
  1. Fetch video metadata without downloading (info extraction).
  2. Download the highest-quality video with real-time progress callbacks.
  3. Emit structured progress events suitable for SSE streaming.

All file operations use paths from config.py. Business logic is fully
separated from Flask routes.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Callable, Generator

import config
from utils.helpers import (
    format_bytes,
    format_duration,
    format_views,
    new_job_id,
    safe_filename,
)

logger = logging.getLogger(__name__)

# Progress callback type: receives a progress dict
ProgressCallback = Callable[[dict[str, Any]], None]


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------

def fetch_video_info(url: str) -> dict[str, Any]:
    """Extract video metadata from *url* without downloading.

    Returns a dict with keys: id, title, duration, view_count, thumbnail,
    width, height, fps, vcodec, acodec, filesize_approx, webpage_url.

    Raises:
        ValueError: if the URL is invalid or video is unavailable.
        RuntimeError: on yt-dlp internal errors.
    """
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        raise RuntimeError("yt-dlp is not installed. Run: pip install yt-dlp")

    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "noplaylist": True,
        "socket_timeout": config.YT_DLP_SOCKET_TIMEOUT,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        msg = str(exc)
        if "Private video" in msg:
            raise ValueError("This video is private and cannot be accessed.")
        if "Video unavailable" in msg or "not available" in msg.lower():
            raise ValueError("This video is unavailable or has been removed.")
        raise ValueError(f"Could not fetch video info: {msg}")
    except Exception as exc:
        raise RuntimeError(f"Unexpected error from yt-dlp: {exc}") from exc

    if not info:
        raise ValueError("No video information returned for this URL.")

    # Determine best format dimensions
    formats = info.get("formats") or []
    best = _pick_best_format(formats)

    return {
        "id": info.get("id", ""),
        "title": info.get("title", "Untitled"),
        "duration": info.get("duration", 0),
        "duration_str": format_duration(info.get("duration") or 0),
        "view_count": info.get("view_count", 0),
        "view_count_str": format_views(info.get("view_count") or 0),
        "thumbnail": info.get("thumbnail", ""),
        "webpage_url": info.get("webpage_url", url),
        "uploader": info.get("uploader", ""),
        "upload_date": info.get("upload_date", ""),
        # Best format details
        "width": best.get("width") or info.get("width") or 0,
        "height": best.get("height") or info.get("height") or 0,
        "fps": best.get("fps") or info.get("fps") or 0,
        "vcodec": (best.get("vcodec") or info.get("vcodec") or "unknown").split(".")[0],
        "acodec": (best.get("acodec") or info.get("acodec") or "unknown").split(".")[0],
        "filesize_approx": _estimate_filesize(info, best),
        "filesize_approx_str": format_bytes(_estimate_filesize(info, best)),
    }


def _pick_best_format(formats: list[dict]) -> dict:
    """Select the best video format from a formats list."""
    video_formats = [
        f for f in formats
        if f.get("vcodec") and f.get("vcodec") != "none"
        and f.get("height") is not None
    ]
    if not video_formats:
        return {}
    return max(video_formats, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))


def _estimate_filesize(info: dict, best_format: dict) -> int:
    """Estimate total file size in bytes."""
    if best_format.get("filesize"):
        return int(best_format["filesize"])
    if best_format.get("filesize_approx"):
        return int(best_format["filesize_approx"])
    if info.get("filesize_approx"):
        return int(info["filesize_approx"])
    # Rough estimate: 8 Mbps × duration
    duration = info.get("duration") or 0
    return int(duration * 8_000_000 / 8)


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

class DownloadJob:
    """Represents a single download operation with progress tracking."""

    def __init__(self, url: str, job_id: str | None = None) -> None:
        self.url = url
        self.job_id: str = job_id or new_job_id()
        self.status: str = "pending"   # pending | downloading | done | error
        self.progress: dict[str, Any] = {}
        self.output_path: Path | None = None
        self.info: dict[str, Any] = {}
        self._callbacks: list[ProgressCallback] = []

    def add_progress_callback(self, cb: ProgressCallback) -> None:
        self._callbacks.append(cb)

    def _emit(self, data: dict[str, Any]) -> None:
        for cb in self._callbacks:
            try:
                cb(data)
            except Exception:
                pass

    def run(self) -> Path:
        """Execute the download synchronously. Returns the output file path.

        Raises:
            ValueError: for invalid/unavailable videos.
            RuntimeError: for tool or network failures.
        """
        try:
            import yt_dlp  # type: ignore
        except ImportError:
            raise RuntimeError("yt-dlp is not installed.")

        self.status = "downloading"
        self._emit({"type": "status", "status": "downloading", "job_id": self.job_id})

        output_template = str(
            config.DOWNLOADS_DIR / f"%(id)s_{self.job_id}.%(ext)s"
        )

        def ydl_progress_hook(d: dict) -> None:
            if d["status"] == "downloading":
                payload: dict[str, Any] = {
                    "type": "progress",
                    "job_id": self.job_id,
                    "status": "downloading",
                    "downloaded_bytes": d.get("downloaded_bytes") or 0,
                    "total_bytes": d.get("total_bytes") or d.get("total_bytes_estimate") or 0,
                    "speed": d.get("speed") or 0,
                    "speed_str": format_bytes(d.get("speed") or 0) + "/s",
                    "eta": d.get("eta") or 0,
                    "percent": _calc_percent(d),
                    "filename": d.get("filename", ""),
                }
                self.progress = payload
                self._emit(payload)

            elif d["status"] == "finished":
                self.status = "processing"
                self._emit({"type": "status", "status": "processing", "job_id": self.job_id})

            elif d["status"] == "error":
                self.status = "error"
                self._emit({"type": "error", "status": "error", "job_id": self.job_id,
                            "message": "Download failed."})

        format_selector = config.YT_DLP_FORMAT
        if config.YT_DLP_PREFER_MP4:
            format_selector = (
                "bv*[ext=mp4][height<=2160]+ba[ext=m4a]/"
                "bv*[height<=2160]+ba/b[ext=mp4]/b"
            )

        # Video-only download options — subtitles fetched separately after this
        # to prevent a 429 rate-limit on subtitles from killing the whole download.
        ydl_opts: dict[str, Any] = {
            "format": format_selector,
            "outtmpl": output_template,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "retries": config.YT_DLP_RETRIES,
            "file_access_retries": 10,
            "nopart": True,
            "continuedl": False,
            "overwrites": True,
            "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
            "socket_timeout": config.YT_DLP_SOCKET_TIMEOUT,
            "progress_hooks": [ydl_progress_hook],
            "merge_output_format": "mp4",
            "postprocessors": [
                {
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": "mp4",
                }
            ],
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.url, download=True)
        except yt_dlp.utils.DownloadError as exc:
            self.status = "error"
            msg = str(exc)
            self._emit({"type": "error", "job_id": self.job_id, "message": msg})
            raise ValueError(f"Download failed: {msg}")
        except Exception as exc:
            self.status = "error"
            self._emit({"type": "error", "job_id": self.job_id, "message": str(exc)})
            raise RuntimeError(f"Download error: {exc}") from exc

        # Locate the downloaded file
        video_id = info.get("id", "")
        output_file = _find_downloaded_file(video_id, self.job_id)

        if not output_file:
            self.status = "error"
            raise RuntimeError("Downloaded file could not be located.")

        self.output_path = output_file
        self.info = _extract_final_info(info)

        # Try to fetch subtitles in a separate, non-fatal step.
        # YouTube 429 rate-limits or missing subtitles should never block the video.
        video_id = info.get("id", "")
        self._try_download_subtitles(video_id, output_file)

        self.status = "done"
        self._emit({
            "type": "done",
            "job_id": self.job_id,
            "status": "done",
            "output_path": str(output_file),
            "filename": output_file.name,
            "info": self.info,
        })
        logger.info("Download complete: %s -> %s", self.url, output_file)
        return output_file

    def _try_download_subtitles(self, video_id: str, output_file: Path) -> None:
        """Attempt to download Amharic/English subtitles as a non-fatal side-effect.

        If YouTube returns 429 (rate-limit) or subtitles don't exist, we silently
        skip — the video is already downloaded and rendering works without them.
        """
        try:
            import yt_dlp  # type: ignore
        except ImportError:
            return

        # Output to the same directory as the video, using the same stem
        sub_template = str(output_file.with_suffix(""))

        sub_opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["am", "en"],
            "subtitlesformat": "vtt/best",
            "skip_download": True,               # Don't re-download the video
            "outtmpl": sub_template + ".%(ext)s",
            "noplaylist": True,
            "socket_timeout": config.YT_DLP_SOCKET_TIMEOUT,
            "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
        }

        try:
            with yt_dlp.YoutubeDL(sub_opts) as ydl:
                ydl.download([self.url])
            logger.info("Subtitles downloaded for job %s", self.job_id)
        except Exception as exc:
            # 429, no subtitles available, or any other error — log and move on.
            logger.warning(
                "Subtitle download failed for job %s (non-fatal): %s",
                self.job_id, exc
            )
            self._emit({
                "type": "log",
                "job_id": self.job_id,
                "message": f"Subtitles unavailable (will render without them): {exc}",
            })


def _calc_percent(d: dict) -> float:
    downloaded = d.get("downloaded_bytes") or 0
    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
    if total > 0:
        return round(downloaded / total * 100, 1)
    percent_str = d.get("_percent_str", "0%")
    match = re.search(r"([\d.]+)", percent_str)
    return float(match.group(1)) if match else 0.0


def _find_downloaded_file(video_id: str, job_id: str = "") -> Path | None:
    """Locate the downloaded file by video ID and job ID in the downloads directory."""
    base_name = f"{video_id}_{job_id}" if job_id else video_id
    for ext in ("mp4", "mkv", "webm", "mov", "avi"):
        candidate = config.DOWNLOADS_DIR / f"{base_name}.{ext}"
        if candidate.exists():
            return candidate
    # Fallback: any file starting with base_name
    for f in config.DOWNLOADS_DIR.iterdir():
        if f.stem == base_name:
            return f
    return None


def _extract_final_info(info: dict) -> dict[str, Any]:
    """Extract a clean metadata dict from the yt-dlp info after download."""
    return {
        "id": info.get("id", ""),
        "title": info.get("title", ""),
        "duration": info.get("duration", 0),
        "duration_str": format_duration(info.get("duration") or 0),
        "view_count": info.get("view_count", 0),
        "view_count_str": format_views(info.get("view_count") or 0),
        "thumbnail": info.get("thumbnail", ""),
        "uploader": info.get("uploader", ""),
        "width": info.get("width") or 0,
        "height": info.get("height") or 0,
        "fps": info.get("fps") or 0,
        "vcodec": (info.get("vcodec") or "unknown").split(".")[0],
        "acodec": (info.get("acodec") or "unknown").split(".")[0],
        "webpage_url": info.get("webpage_url", ""),
    }


# ---------------------------------------------------------------------------
# SSE generator (used by Flask route)
# ---------------------------------------------------------------------------

def sse_event(data: dict[str, Any]) -> str:
    """Format a dict as an SSE data line."""
    import json
    return f"data: {json.dumps(data)}\n\n"


def download_progress_generator(job: DownloadJob) -> Generator[str, None, None]:
    """Yield SSE-formatted progress events from a DownloadJob.

    This generator is designed to be used inside a Flask streaming response.
    It collects events via a queue filled by the job's progress callback.
    """
    import queue
    import threading

    event_queue: queue.Queue[dict | None] = queue.Queue()

    def on_progress(data: dict) -> None:
        event_queue.put(data)

    job.add_progress_callback(on_progress)

    def run_job() -> None:
        try:
            job.run()
        except Exception as exc:
            event_queue.put({"type": "error", "message": str(exc)})
        finally:
            event_queue.put(None)  # Sentinel – always last

    thread = threading.Thread(target=run_job, daemon=True)
    thread.start()

    while True:
        try:
            event = event_queue.get(timeout=config.SSE_KEEPALIVE_INTERVAL)
        except queue.Empty:
            yield ": keepalive\n\n"
            continue

        if event is None:
            # Drain any remaining events (e.g. the 'done' event) before closing
            while not event_queue.empty():
                remaining = event_queue.get_nowait()
                if remaining is not None:
                    yield sse_event(remaining)
            break

        yield sse_event(event)
