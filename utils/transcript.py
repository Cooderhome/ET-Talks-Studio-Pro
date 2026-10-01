"""
utils/transcript.py – YouTube transcript text → SRT converter for ET-Talks Studio Pro.

When YouTube's auto-subtitle download is rate-limited (HTTP 429), the user can
manually open the YouTube transcript panel (⋮ → Show Transcript), copy all the
text, and paste it here.

Supported input formats
-----------------------
YouTube transcript copy-paste produces lines like:

  Format A (separate lines):
      0:00
      ሰላም ወዳጆቼ
      0:03
      እንኳን ደህና መጡ

  Format B (inline timestamp):
      0:00 ሰላም ወዳጆቼ
      0:03 እንኳን ደህና መጡ

  Format C (HH:MM:SS):
      0:00:01 ሰላም
      0:00:05 ወዳጆቼ

This module converts any of these into a standard .srt file that FFmpeg can burn
directly into the video via the `subtitles=` filter.
"""

from __future__ import annotations

import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Matches timestamps like: 0:05  /  1:23  /  0:00:01  /  1:23:45
_TS_RE = re.compile(
    r"^(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d+))?\s*(.*)$"
)


# ---------------------------------------------------------------------------
# Core parsing
# ---------------------------------------------------------------------------

def _parse_timestamp(ts_str: str) -> float:
    """Convert a timestamp string (H:MM:SS, M:SS, or M:SS.ms) to seconds."""
    ts_str = ts_str.strip().replace(",", ".")
    parts = ts_str.split(":")
    try:
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        elif len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
    except ValueError:
        pass
    return 0.0


def _seconds_to_srt_time(seconds: float) -> str:
    """Format seconds as SRT timestamp: HH:MM:SS,mmm"""
    if seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    whole = int(s)
    ms = int(round((s - whole) * 1000))
    return f"{h:02d}:{m:02d}:{whole:02d},{ms:03d}"


def _parse_lines(raw_text: str) -> list[tuple[float, str]]:
    """
    Parse raw transcript text into a list of (start_seconds, text) tuples.

    Handles both inline (timestamp + text on same line) and split
    (timestamp on one line, text on next) formats automatically.
    """
    entries: list[tuple[float, str]] = []

    # Normalize line endings
    lines = raw_text.replace("\r\n", "\n").replace("\r", "\n").splitlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Skip blank lines
        if not line:
            i += 1
            continue

        # Try to match a timestamp (with optional inline text after it)
        # Pattern: optional HH: then MM:SS
        ts_match = re.match(
            r"^(\d+:\d{2}(?::\d{2})?(?:[.,]\d+)?)\s*(.*)?$", line
        )
        if ts_match:
            ts_str = ts_match.group(1)
            inline_text = (ts_match.group(2) or "").strip()
            start = _parse_timestamp(ts_str)

            if inline_text:
                # Format B/C: timestamp + text on same line
                entries.append((start, inline_text))
                i += 1
            else:
                # Format A: text is on the next line(s)
                text_parts = []
                i += 1
                while i < len(lines):
                    next_line = lines[i].strip()
                    if not next_line:
                        i += 1
                        break
                    # Stop if we hit another timestamp line
                    if re.match(r"^\d+:\d{2}", next_line):
                        break
                    text_parts.append(next_line)
                    i += 1
                if text_parts:
                    entries.append((start, " ".join(text_parts)))
        else:
            # Plain text line with no timestamp — attach to previous entry if any
            if entries:
                prev_start, prev_text = entries[-1]
                entries[-1] = (prev_start, prev_text + " " + line)
            i += 1

    return entries


def _entries_to_srt(entries: list[tuple[float, str]]) -> str:
    """Convert (start, text) pairs into SRT-formatted string."""
    if not entries:
        return ""

    lines: list[str] = []
    for idx, (start, text) in enumerate(entries):
        # End time = start of next subtitle (or start + 3s for last one)
        if idx + 1 < len(entries):
            end = entries[idx + 1][0] - 0.05  # small gap
            if end <= start:
                end = start + 2.0
        else:
            end = start + 3.0

        lines.append(str(idx + 1))
        lines.append(f"{_seconds_to_srt_time(start)} --> {_seconds_to_srt_time(end)}")
        lines.append(text)
        lines.append("")  # blank line between entries

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def youtube_transcript_to_srt(raw_text: str) -> str:
    """
    Parse a raw YouTube transcript copy-paste and return SRT-formatted text.

    Args:
        raw_text: Raw text from YouTube's transcript panel (copy-pasted).

    Returns:
        SRT-formatted subtitle string, ready to write to a .srt file.

    Raises:
        ValueError: if no valid timestamp+text entries could be parsed.
    """
    raw_text = raw_text.strip()
    if not raw_text:
        raise ValueError("Transcript text is empty.")

    entries = _parse_lines(raw_text)
    if not entries:
        raise ValueError(
            "No timestamp entries found. "
            "Make sure you copied the transcript with timestamps enabled. "
            "(In YouTube, click 'Toggle timestamps' in the transcript panel.)"
        )

    return _entries_to_srt(entries)


def save_transcript_srt(raw_text: str, output_path: Path) -> Path:
    """
    Parse raw YouTube transcript text and save it as an .srt file.

    Args:
        raw_text: Raw transcript text from YouTube.
        output_path: Destination .srt file path.

    Returns:
        The path to the written .srt file.

    Raises:
        ValueError: if parsing fails.
    """
    srt_content = youtube_transcript_to_srt(raw_text)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(srt_content, encoding="utf-8")
    return output_path
