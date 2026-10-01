"""
utils/thumbnails.py – Timeline thumbnail generator for ET-Talks Studio Pro.

Uses OpenCV to extract evenly-spaced frames from a video file and save
them as JPEG thumbnails. These thumbnails are served to the frontend for
the timeline scrubber UI.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

import config
from utils.helpers import ensure_dir

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Thumbnail generation
# ---------------------------------------------------------------------------

def generate_timeline_thumbnails(
    video_path: str | Path,
    job_id: str,
    count: int = config.TIMELINE_THUMB_COUNT,
    width: int = config.TIMELINE_THUMB_WIDTH,
    height: int = config.TIMELINE_THUMB_HEIGHT,
) -> list[dict[str, Any]]:
    """Extract *count* evenly-spaced frames from *video_path* as JPEG files.

    Thumbnails are saved to:
        config.THUMBNAILS_DIR / job_id / thumb_NNNN.jpg

    Returns:
        A list of dicts:
        [{"index": 0, "timestamp": 0.0, "path": "/thumbnails/job_id/thumb_0000.jpg"}, ...]

    Raises:
        FileNotFoundError: if *video_path* does not exist.
        RuntimeError: if OpenCV cannot open the video.
    """
    try:
        import cv2  # type: ignore
    except ImportError:
        raise RuntimeError(
            "opencv-python is not installed. Run: pip install opencv-python"
        )

    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    out_dir = ensure_dir(config.THUMBNAILS_DIR / job_id)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV could not open video: {video_path}")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps_val = cap.get(cv2.CAP_PROP_FPS) or 25.0
    duration = total_frames / fps_val

    # Clamp count to available frames
    count = min(count, total_frames)
    if count <= 0:
        cap.release()
        return []

    interval = total_frames / count
    thumbnails: list[dict[str, Any]] = []

    for i in range(count):
        frame_idx = int(i * interval)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            continue

        # Resize to desired thumbnail dimensions
        thumb = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)

        thumb_name = f"thumb_{i:04d}.jpg"
        thumb_path = out_dir / thumb_name
        cv2.imwrite(str(thumb_path), thumb, [cv2.IMWRITE_JPEG_QUALITY, 75])

        timestamp = frame_idx / fps_val
        thumbnails.append({
            "index": i,
            "timestamp": round(timestamp, 3),
            "timestamp_str": _fmt_ts(timestamp),
            "path": f"/api/thumbnail/{job_id}/{thumb_name}",
        })

    cap.release()
    logger.info(
        "Generated %d timeline thumbnails for job %s (duration %.1fs)",
        len(thumbnails), job_id, duration,
    )
    return thumbnails


def get_video_duration(video_path: str | Path) -> float:
    """Return the duration of a video file in seconds using OpenCV."""
    try:
        import cv2  # type: ignore
    except ImportError:
        return 0.0

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return 0.0

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps_val = cap.get(cv2.CAP_PROP_FPS) or 0.0
    cap.release()

    if total_frames <= 0 or fps_val <= 0:
        return 0.0
    return round(total_frames / fps_val, 3)


def _fmt_ts(seconds: float) -> str:
    """Format seconds as MM:SS for display on the timeline."""
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def delete_thumbnails(job_id: str) -> None:
    """Remove all thumbnails for *job_id* from disk."""
    thumb_dir = config.THUMBNAILS_DIR / job_id
    if thumb_dir.exists():
        shutil.rmtree(str(thumb_dir))
        logger.info("Deleted thumbnails for job: %s", job_id)


def get_thumbnail_path(job_id: str, filename: str) -> Path | None:
    """Return the absolute path for a specific thumbnail file, or None."""
    path = config.THUMBNAILS_DIR / job_id / filename
    return path if path.exists() else None


# ---------------------------------------------------------------------------
# Video cover thumbnail (used for history cards)
# ---------------------------------------------------------------------------

def extract_cover_thumbnail(
    video_path: str | Path,
    output_path: str | Path,
    timestamp_seconds: float = 1.0,
    size: tuple[int, int] = (320, 180),
) -> bool:
    """Extract a single frame at *timestamp_seconds* and save as JPEG.

    Used to create the history thumbnail shown in the right panel.

    Returns True on success, False on failure.
    """
    try:
        import cv2  # type: ignore

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return False

        fps_val = cap.get(cv2.CAP_PROP_FPS) or 25.0
        target_frame = int(timestamp_seconds * fps_val)
        cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
        ret, frame = cap.read()
        cap.release()

        if not ret:
            return False

        thumb = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(output_path), thumb, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return True

    except Exception as exc:
        logger.error("Cover thumbnail extraction failed: %s", exc)
        return False
