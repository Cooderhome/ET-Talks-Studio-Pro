"""
utils/helpers.py – General-purpose helper utilities for ET-Talks Studio Pro.

Provides:
  - Human-readable file size formatting
  - Safe filename generation
  - Disk space checking
  - Amharic character detection
  - Unique job ID generation
  - Duration formatting
"""

from __future__ import annotations

import re
import shutil
import unicodedata
import uuid
from pathlib import Path


# ---------------------------------------------------------------------------
# File size
# ---------------------------------------------------------------------------

def format_bytes(num_bytes: int | float) -> str:
    """Return a human-readable file size string (e.g. '1.23 GB')."""
    if num_bytes < 0:
        return "Unknown"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} PB"


def parse_bytes(size_str: str) -> int:
    """Parse a human-readable size string to bytes (e.g. '1.5 GB' → 1610612736)."""
    units = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    match = re.match(r"([\d.]+)\s*([A-Za-z]+)?", size_str.strip())
    if not match:
        return 0
    value = float(match.group(1))
    unit = (match.group(2) or "B").upper()
    return int(value * units.get(unit, 1))


# ---------------------------------------------------------------------------
# Duration
# ---------------------------------------------------------------------------

def format_duration(seconds: float | int) -> str:
    """Format seconds into HH:MM:SS or MM:SS string."""
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_duration_ms(milliseconds: int) -> str:
    """Format milliseconds into MM:SS.mmm string for timeline display."""
    total_seconds = milliseconds // 1000
    ms = milliseconds % 1000
    minutes, secs = divmod(total_seconds, 60)
    return f"{minutes:02d}:{secs:02d}.{ms:03d}"


# ---------------------------------------------------------------------------
# Safe filenames
# ---------------------------------------------------------------------------

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTI_SPACE = re.compile(r'\s+')


def safe_filename(name: str, max_length: int = 100) -> str:
    """Sanitize a string for use as a filename.

    Removes path separators and control characters, collapses whitespace,
    and truncates to *max_length* characters.
    """
    # Normalize unicode (NFC)
    name = unicodedata.normalize("NFC", name)
    name = _UNSAFE_CHARS.sub("", name)
    name = _MULTI_SPACE.sub(" ", name).strip()
    name = name[:max_length].strip()
    return name or "untitled"


# ---------------------------------------------------------------------------
# Unique IDs
# ---------------------------------------------------------------------------

def new_job_id() -> str:
    """Return a short unique job identifier (8 hex chars)."""
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Disk space
# ---------------------------------------------------------------------------

def free_disk_bytes(path: str | Path) -> int:
    """Return free disk space in bytes for the volume containing *path*."""
    usage = shutil.disk_usage(str(path))
    return usage.free


def check_disk_space(path: str | Path, required_bytes: int) -> tuple[bool, int]:
    """Check whether *path*'s volume has at least *required_bytes* free.

    Returns:
        (ok, free_bytes) where *ok* is True if sufficient space exists.
    """
    free = free_disk_bytes(path)
    return free >= required_bytes, free


# ---------------------------------------------------------------------------
# Amharic detection
# ---------------------------------------------------------------------------

# Unicode range for Ethiopic script (U+1200–U+137F and extended blocks)
_ETHIOPIC_RANGE = re.compile(r"[\u1200-\u137F\u1380-\u139F\u2D80-\u2DDF\uAB01-\uAB2F]")


def contains_amharic(text: str) -> bool:
    """Return True if *text* contains any Ethiopic/Amharic characters."""
    return bool(_ETHIOPIC_RANGE.search(text))


def detect_font_for_text(text: str, default_font: str, amharic_font: str) -> str:
    """Return the appropriate font name based on text content.

    Automatically selects the Amharic/Ethiopic font when Amharic characters
    are detected; otherwise returns the default font.
    """
    return amharic_font if contains_amharic(text) else default_font


# ---------------------------------------------------------------------------
# Number formatting
# ---------------------------------------------------------------------------

def format_views(count: int) -> str:
    """Format a view count into a human-readable string (e.g. 1.2M)."""
    if count >= 1_000_000_000:
        return f"{count / 1_000_000_000:.1f}B"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


# ---------------------------------------------------------------------------
# Path utilities
# ---------------------------------------------------------------------------

def ensure_dir(path: str | Path) -> Path:
    """Create directory (and parents) if it does not exist. Returns Path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p
