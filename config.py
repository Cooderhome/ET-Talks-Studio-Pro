"""
config.py – Centralized configuration for ET-Talks Studio Pro.

All paths, quality profiles, encoder settings, and feature flags are
defined here. Import `config` in any module rather than hardcoding values.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Base paths
# ---------------------------------------------------------------------------

BASE_DIR: Path = Path(__file__).resolve().parent

DOWNLOADS_DIR: Path = BASE_DIR / "downloads"
OUTPUT_DIR: Path = BASE_DIR / "output"
UPLOADS_DIR: Path = BASE_DIR / "uploads"
TEMP_DIR: Path = BASE_DIR / "temp"
LOGS_DIR: Path = BASE_DIR / "logs"
THUMBNAILS_DIR: Path = BASE_DIR / "thumbnails"
FONTS_DIR: Path = BASE_DIR / "static" / "fonts"

DATABASE_PATH: Path = BASE_DIR / "history.db"

# Ensure all runtime directories exist
for _dir in (
    DOWNLOADS_DIR,
    OUTPUT_DIR,
    UPLOADS_DIR,
    TEMP_DIR,
    LOGS_DIR,
    THUMBNAILS_DIR,
):
    _dir.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------

SECRET_KEY: str = os.environ.get("ET_SECRET_KEY", "et-talks-studio-dev-key-change-in-prod")
DEBUG: bool = os.environ.get("FLASK_DEBUG", "0") == "1"
HOST: str = os.environ.get("ET_HOST", "127.0.0.1")
PORT: int = int(os.environ.get("ET_PORT", "5050"))

# ---------------------------------------------------------------------------
# Downloader (yt-dlp)
# ---------------------------------------------------------------------------

YT_DLP_FORMAT: str = "bv*[height<=2160]+ba/b"
YT_DLP_PREFER_MP4: bool = True
YT_DLP_RETRIES: int = 3
YT_DLP_SOCKET_TIMEOUT: int = 30

# ---------------------------------------------------------------------------
# FFmpeg
# ---------------------------------------------------------------------------

FFMPEG_BIN: str = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN: str = os.environ.get("FFPROBE_BIN", "ffprobe")

# Output dimensions
OUTPUT_WIDTH: int = 1080
OUTPUT_HEIGHT: int = 1920

# Audio codec
AUDIO_CODEC: str = "aac"
AUDIO_BITRATE: str = "192k"

# Crop modes available to the renderer
CROP_MODES: list[str] = ["fit", "blur_background", "center_crop"]

# Text animation types
TEXT_ANIMATIONS: list[str] = ["none", "fade", "slide", "pop", "zoom", "bounce", "typewriter"]

# ---------------------------------------------------------------------------
# Quality profiles  (name → {crf, preset})
# ---------------------------------------------------------------------------

QUALITY_PROFILES: dict[str, dict] = {
    "ultra":    {"crf": 16, "preset": "slow",   "label": "Ultra (CRF 16)"},
    "high":     {"crf": 18, "preset": "slow",   "label": "High (CRF 18)"},
    "balanced": {"crf": 20, "preset": "medium", "label": "Balanced (CRF 20)"},
    "fast":     {"crf": 23, "preset": "fast",   "label": "Fast (CRF 23)"},
}
DEFAULT_QUALITY: str = "high"

# ---------------------------------------------------------------------------
# GPU encoders
# ---------------------------------------------------------------------------

GPU_ENCODERS: dict[str, str] = {
    "nvidia": "h264_nvenc",
    "intel":  "h264_qsv",
    "amd":    "h264_amf",
    "cpu":    "libx264",
}

# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

AVAILABLE_FONTS: list[dict] = [
    {"name": "Noto Sans",          "file": "NotoSans-Regular.ttf",          "bold": "NotoSans-Bold.ttf"},
    {"name": "Noto Sans Ethiopic", "file": "NotoSansEthiopic-Regular.ttf",  "bold": "NotoSansEthiopic-Bold.ttf"},
    {"name": "Roboto",             "file": "Roboto-Regular.ttf",            "bold": "Roboto-Bold.ttf"},
    {"name": "Montserrat",         "file": "Montserrat-Regular.ttf",        "bold": "Montserrat-Bold.ttf"},
    {"name": "Oswald",             "file": "Oswald-Regular.ttf",            "bold": "Oswald-Bold.ttf"},
]

DEFAULT_FONT: str = "Noto Sans"
AMHARIC_FONT: str = "Noto Sans Ethiopic"

# ---------------------------------------------------------------------------
# Timeline thumbnails
# ---------------------------------------------------------------------------

TIMELINE_THUMB_COUNT: int = 20
TIMELINE_THUMB_WIDTH: int = 160
TIMELINE_THUMB_HEIGHT: int = 90

# ---------------------------------------------------------------------------
# Upload limits
# ---------------------------------------------------------------------------

MAX_UPLOAD_BYTES: int = 500 * 1024 * 1024  # 500 MB
ALLOWED_LOGO_EXTENSIONS: set[str] = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
ALLOWED_AUDIO_EXTENSIONS: set[str] = {".mp3", ".aac", ".wav", ".m4a", ".ogg", ".opus"}

# ---------------------------------------------------------------------------
# Minimum free disk space (bytes) required before a render starts
# ---------------------------------------------------------------------------

MIN_FREE_DISK_BYTES: int = 2 * 1024 * 1024 * 1024  # 2 GB

# ---------------------------------------------------------------------------
# SSE keep-alive interval (seconds)
# ---------------------------------------------------------------------------

SSE_KEEPALIVE_INTERVAL: float = 2.0

# ---------------------------------------------------------------------------
# Extension points (future features) – flags to enable when ready
# ---------------------------------------------------------------------------

FEATURE_WHISPER_SUBTITLES: bool = False
FEATURE_AI_TITLES: bool = False
FEATURE_AI_HASHTAGS: bool = False
FEATURE_THUMBNAIL_GEN: bool = False
FEATURE_SCENE_DETECTION: bool = False
FEATURE_FACE_TRACKING: bool = False
FEATURE_BATCH_RENDERING: bool = False
FEATURE_SOCIAL_PUBLISH: bool = False
