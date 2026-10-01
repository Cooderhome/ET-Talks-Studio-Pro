"""
utils/ffmpeg.py – FFmpeg command builder for ET-Talks Studio Pro.

Responsibilities:
  - Build FFmpeg filter graphs for vertical Shorts conversion (1080×1920).
  - Support crop modes: fit, blur_background, center_crop.
  - Compose text overlays with font, color, shadow, and animations.
  - Add logo/watermark with opacity control.
  - Mix background music.
  - Select GPU/CPU encoder.
  - Apply FastStart (moov atom at front) for web playback.

All methods return the constructed ffmpeg command as a list[str].
No subprocess calls are made here — that is the renderer's job.
"""

from __future__ import annotations

import logging
import shlex
from pathlib import Path
from typing import Any

import config
from utils.helpers import contains_amharic

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes / type aliases
# ---------------------------------------------------------------------------

TextOverlay = dict[str, Any]
"""
Keys:
  text        : str   – overlay text
  x           : str   – ffmpeg x expression (e.g. "(W-text_w)/2")
  y           : str   – ffmpeg y expression (e.g. "H*0.85")
  font_path   : str   – absolute path to TTF font file
  font_size   : int   – pixels
  font_color  : str   – hex color, e.g. "#FFFFFF"
  alpha       : float – 0.0–1.0
  shadow      : bool  – whether to add drop shadow
  shadow_color: str   – hex color for shadow
  bg_color    : str   – hex color for background box (empty = no box)
  bg_alpha    : float – background box opacity
  animation   : str   – one of config.TEXT_ANIMATIONS
  start_time  : float – seconds when text appears
  end_time    : float – seconds when text disappears (None = end of video)
"""


# ---------------------------------------------------------------------------
# FFmpeg builder
# ---------------------------------------------------------------------------

class FFmpegBuilder:
    """Builds FFmpeg command lists for vertical Shorts rendering."""

    def __init__(
        self,
        input_path: str | Path,
        output_path: str | Path,
        *,
        encoder: str = "libx264",
        quality: str = "high",
        crop_mode: str = "blur_background",
        start_time: float = 0.0,
        end_time: float | None = None,
        logo_path: str | Path | None = None,
        logo_opacity: float = 1.0,
        logo_position: str = "top_right",    # top_left|top_right|bottom_left|bottom_right|center
        watermark_text: str = "",
        music_path: str | Path | None = None,
        music_volume: float = 0.3,
        original_volume: float = 1.0,
        text_overlays: list[TextOverlay] | None = None,
        subtitle_file: str | None = None,
        color_template: str = "none",
    ) -> None:
        self.input_path = Path(input_path)
        self.output_path = Path(output_path)
        self.encoder = encoder
        self.quality_profile = config.QUALITY_PROFILES.get(quality, config.QUALITY_PROFILES["high"])
        self.crop_mode = crop_mode
        self.start_time = start_time
        self.end_time = end_time
        self.logo_path = Path(logo_path) if logo_path else None
        self.logo_opacity = logo_opacity
        self.logo_position = logo_position
        self.watermark_text = watermark_text
        self.music_path = Path(music_path) if music_path else None
        self.music_volume = music_volume
        self.original_volume = original_volume
        self.text_overlays = text_overlays or []
        self.subtitle_file = subtitle_file
        self.color_template = color_template

        # Output dimensions
        self.out_w = config.OUTPUT_WIDTH    # 1080
        self.out_h = config.OUTPUT_HEIGHT   # 1920

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self) -> list[str]:
        """Return the full FFmpeg command as a list of strings."""
        cmd: list[str] = [config.FFMPEG_BIN, "-hide_banner", "-y"]

        # Input trim
        if self.start_time > 0:
            cmd += ["-ss", str(self.start_time)]
        cmd += ["-i", str(self.input_path)]

        # Music input
        if self.music_path and self.music_path.exists():
            cmd += ["-i", str(self.music_path)]

        # Logo input
        if self.logo_path and self.logo_path.exists():
            cmd += ["-i", str(self.logo_path)]

        # Duration
        if self.end_time is not None:
            duration = self.end_time - self.start_time
            if duration > 0:
                cmd += ["-t", str(duration)]

        # Build filter graph
        filter_complex, audio_map = self._build_filter_complex()

        cmd += ["-filter_complex", filter_complex]
        cmd += ["-map", "[vout]"]
        cmd += ["-map", audio_map]

        # Video encoder settings
        cmd += self._encoder_args()

        # Audio
        cmd += [
            "-c:a", config.AUDIO_CODEC,
            "-b:a", config.AUDIO_BITRATE,
        ]

        # FastStart for web
        cmd += ["-movflags", "+faststart"]

        cmd.append(str(self.output_path))
        logger.debug("FFmpeg command: %s", shlex.join(cmd))
        return cmd

    # ------------------------------------------------------------------
    # Filter graph construction
    # ------------------------------------------------------------------

    def _build_filter_complex(self) -> tuple[str, str]:
        """Build the -filter_complex string and return (filter_str, audio_map)."""
        filters: list[str] = []
        stream_idx = 0       # Input stream counter (logo is added after music)
        video_in = f"[{stream_idx}:v]"
        audio_in = f"[{stream_idx}:a]"
        stream_idx += 1

        music_stream_idx: int | None = None
        logo_stream_idx: int | None = None

        if self.music_path and self.music_path.exists():
            music_stream_idx = stream_idx
            stream_idx += 1

        if self.logo_path and self.logo_path.exists():
            logo_stream_idx = stream_idx
            stream_idx += 1

        # Step 1: Crop to vertical
        crop_filter, last_v = self._crop_filter(video_in)
        filters.append(crop_filter)

        # Step 1.5: Color Template
        if self.color_template and self.color_template != "none":
            tpl_filter, last_v = self._color_template_filter(last_v)
            if tpl_filter:
                filters.append(tpl_filter)

        # Step 2: Text overlays
        if self.text_overlays:
            text_chain, last_v = self._text_filters(last_v)
            filters.append(text_chain)
            
        # Step 2.5: Subtitle burning
        if self.subtitle_file:
            sub_filter, last_v = self._subtitle_filter(last_v)
            filters.append(sub_filter)

        # Step 3: Logo overlay
        if logo_stream_idx is not None:
            logo_filter, last_v = self._logo_filter(
                last_v, f"[{logo_stream_idx}:v]"
            )
            filters.append(logo_filter)

        # Step 4: Watermark text (if no logo was used but watermark text set)
        if self.watermark_text and logo_stream_idx is None:
            wm_filter, last_v = self._watermark_text_filter(last_v)
            filters.append(wm_filter)

        filters.append(f"{last_v}null[vout]")

        # Audio mixing
        if music_stream_idx is not None:
            audio_filter = (
                f"[0:a]volume={self.original_volume}[oa];"
                f"[{music_stream_idx}:a]volume={self.music_volume}[ma];"
                f"[oa][ma]amix=inputs=2:duration=first:dropout_transition=2[aout]"
            )
            filters.append(audio_filter)
            audio_map = "[aout]"
        else:
            audio_map = "0:a"

        return ";".join(f for f in filters if f), audio_map

    def _crop_filter(self, video_in: str) -> tuple[str, str]:
        """Return filter string and output label for the chosen crop mode."""
        w, h = self.out_w, self.out_h

        if self.crop_mode == "center_crop":
            # Scale to fill height, then crop center strip
            return (
                f"{video_in}scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h}[vcrop]",
                "[vcrop]",
            )

        elif self.crop_mode == "fit":
            # Scale to fit width, pad with black bars
            return (
                f"{video_in}scale={w}:-2:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black[vfit]",
                "[vfit]",
            )

        else:  # blur_background (default)
            # Background: scaled + blurred to fill 1080×1920
            # Foreground: letterboxed content overlaid on top
            f = (
                f"{video_in}split[vbg][vfg];"
                f"[vbg]scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},boxblur=20:2[vblur];"
                f"[vfg]scale={w}:-2:force_original_aspect_ratio=decrease[vscaled];"
                f"[vblur][vscaled]overlay=(W-w)/2:(H-h)/2[vblended]"
            )
            return f, "[vblended]"

    def _color_template_filter(self, video_in: str) -> tuple[str, str]:
        templates = {
            "dark_reality": "eq=contrast=1.1:brightness=-0.08:saturation=0.75",
            "vibrant": "eq=saturation=1.4:contrast=1.1",
            "cinematic": "eq=contrast=1.15:gamma=0.9",
        }
        eq = templates.get(self.color_template)
        if not eq:
            return "", video_in
        return f"{video_in}{eq}[vtmode]", "[vtmode]"

    def _subtitle_filter(self, video_in: str) -> tuple[str, str]:
        safe_path = _escape_filter_value(_fontfile_for_filter(self.subtitle_file))
        fonts_dir = _escape_filter_value(_fontfile_for_filter(config.FONTS_DIR))
        # Ensure Amharic displays correctly by forcing Noto Sans Ethiopic
        style = "Fontname=Noto Sans Ethiopic,FontSize=24,PrimaryColour=&H00FFFF,OutlineColour=&H40000000,BorderStyle=3"
        f = f"{video_in}subtitles='{safe_path}':fontsdir='{fonts_dir}':force_style='{style}'[vsub]"
        return f, "[vsub]"

    def _text_filters(self, video_in: str) -> tuple[str, str]:
        """Build drawtext filters for all text overlays."""
        parts: list[str] = []
        last = video_in

        for i, overlay in enumerate(self.text_overlays):
            out_label = f"[vtxt{i}]"
            dt = self._drawtext(overlay)
            parts.append(f"{last}{dt}{out_label}")
            last = out_label

        return ";".join(parts), last

    def _drawtext(self, ov: TextOverlay) -> str:
        """Build a single drawtext filter string from an overlay dict."""
        text = str(ov.get("text", ""))
        font_path = ov.get("font_path", self._default_font_path(text))
        font_size = int(ov.get("font_size", 60))
        font_color = _hex_to_ffmpeg(ov.get("font_color", "#FFFFFF"), ov.get("alpha", 1.0))
        x = ov.get("x", "(w-text_w)/2")
        y = ov.get("y", "h*0.85")
        shadow = ov.get("shadow", True)
        bg_color = ov.get("bg_color", "")
        bg_alpha = float(ov.get("bg_alpha", 0.6))
        start = float(ov.get("start_time", 0))
        end = ov.get("end_time")
        animation = ov.get("animation", "none")

        # Time enable expression
        if end is not None:
            enable = f"between(t,{start},{end})"
        else:
            enable = f"gte(t,{start})"

        params: dict[str, str] = {
            "text": _escape_drawtext_text(text),
            "fontsize": str(font_size),
            "fontcolor": font_color,
            "x": _escape_filter_value(_animate_x(x, animation, start)),
            "y": _escape_filter_value(_animate_y(y, animation, start)),
            "enable": _escape_filter_value(enable),
        }

        if font_path:
            params["fontfile"] = _escape_filter_value(_fontfile_for_filter(font_path))

        if shadow:
            params["shadowcolor"] = "black@0.7"
            params["shadowx"] = "2"
            params["shadowy"] = "2"

        if bg_color:
            params["box"] = "1"
            params["boxcolor"] = _hex_to_ffmpeg(bg_color, bg_alpha)
            params["boxborderw"] = "12"

        # Fade animation adds alpha modulation
        alpha_expr = _animate_alpha(animation, start, end)
        if alpha_expr:
            params["alpha"] = alpha_expr

        param_str = ":".join(f"{k}={v}" for k, v in params.items())
        return f"drawtext={param_str}"

    def _logo_filter(self, video_in: str, logo_in: str) -> tuple[str, str]:
        """Overlay logo with opacity and positioning."""
        positions = {
            "top_left":     "10:10",
            "top_right":    f"W-w-10:10",
            "bottom_left":  f"10:H-h-10",
            "bottom_right": f"W-w-10:H-h-10",
            "center":       f"(W-w)/2:(H-h)/2",
        }
        pos = positions.get(self.logo_position, positions["top_right"])
        opacity = max(0.0, min(1.0, self.logo_opacity))

        f = (
            f"{logo_in}scale=200:-1,format=rgba,"
            f"colorchannelmixer=aa={opacity}[logo];"
            f"{video_in}[logo]overlay={pos}[vlogo]"
        )
        return f, "[vlogo]"

    def _watermark_text_filter(self, video_in: str) -> tuple[str, str]:
        """Add a lightweight text watermark."""
        text = _escape_drawtext_text(self.watermark_text)
        font_path = self._default_font_path(text)
        params = {
            "text": text,
            "fontsize": "28",
            "fontcolor": "white@0.4",
            "x": "W-text_w-20",
            "y": "H-text_h-20",
        }
        if font_path:
            params["fontfile"] = _escape_filter_value(_fontfile_for_filter(font_path))

        param_str = ":".join(f"{k}={v}" for k, v in params.items())
        f = f"{video_in}drawtext={param_str}[vwm]"
        return f, "[vwm]"

    # ------------------------------------------------------------------
    # Encoder arguments
    # ------------------------------------------------------------------

    def _encoder_args(self) -> list[str]:
        """Return encoder-specific -c:v and quality arguments."""
        crf = self.quality_profile["crf"]
        preset = self.quality_profile["preset"]

        if self.encoder == "h264_nvenc":
            return [
                "-c:v", "h264_nvenc",
                "-preset", "p4",      # NVENC equivalent of slow
                "-cq", str(crf),
                "-rc", "vbr",
                "-bf", "2",
                "-profile:v", "high",
            ]
        elif self.encoder == "h264_qsv":
            return [
                "-c:v", "h264_qsv",
                "-global_quality", str(crf),
                "-preset", "slower" if preset == "slow" else preset,
                "-profile:v", "high",
            ]
        elif self.encoder == "h264_amf":
            return [
                "-c:v", "h264_amf",
                "-quality", "quality",
                "-rc", "cqp",
                "-qp_i", str(crf),
                "-qp_p", str(crf),
            ]
        else:  # libx264
            return [
                "-c:v", "libx264",
                "-crf", str(crf),
                "-preset", preset,
                "-profile:v", "high",
                "-level", "4.1",
                "-pix_fmt", "yuv420p",
            ]

    # ------------------------------------------------------------------
    # Font helpers
    # ------------------------------------------------------------------

    def _default_font_path(self, text: str = "") -> str:
        """Return the appropriate font path, auto-detecting Amharic text."""
        if contains_amharic(text):
            font_name = config.AMHARIC_FONT
        else:
            font_name = config.DEFAULT_FONT

        for font_def in config.AVAILABLE_FONTS:
            if font_def["name"] == font_name:
                path = config.FONTS_DIR / font_def["file"]
                if path.exists():
                    return str(path)
                nested = _find_font_file(font_def["file"])
                if nested:
                    return str(nested)

        # Fallback to first available font
        for font_def in config.AVAILABLE_FONTS:
            path = config.FONTS_DIR / font_def["file"]
            if path.exists():
                return str(path)
            nested = _find_font_file(font_def["file"])
            if nested:
                return str(nested)

        # Last-resort system fonts on Windows.
        for path in (
            Path(r"C:\Windows\Fonts\segoeui.ttf"),
            Path(r"C:\Windows\Fonts\arial.ttf"),
        ):
            if path.exists():
                return str(path)

        logger.warning("No font files found in %s", config.FONTS_DIR)
        return ""


# ---------------------------------------------------------------------------
# Animation helpers
# ---------------------------------------------------------------------------

def _escape_filter_value(value: Any) -> str:
    """Escape characters that are special inside FFmpeg filter option values."""
    text = str(value)
    replacements = {
        "\\": r"\\",
        ":": r"\:",
        ",": r"\,",
        ";": r"\;",
        "[": r"\[",
        "]": r"\]",
        "'": r"\'",
    }
    return "".join(replacements.get(ch, ch) for ch in text)


def _escape_drawtext_text(value: Any) -> str:
    """Escape text for drawtext while leaving Unicode glyphs intact."""
    text = _escape_filter_value(value)
    return text.replace("%", r"\%")


def _fontfile_for_filter(font_path: str | Path) -> str:
    """Return a font path form that FFmpeg's filter parser accepts on Windows."""
    path = Path(font_path)
    try:
        path = path.resolve().relative_to(config.BASE_DIR.resolve())
    except ValueError:
        pass
    return path.as_posix()


def _find_font_file(filename: str) -> Path | None:
    """Find a configured font file even when font packs unpack into subfolders."""
    matches = sorted(config.FONTS_DIR.rglob(filename))
    for path in matches:
        if path.is_file():
            return path
    return None


def _animate_x(base_x: str, animation: str, start: float) -> str:
    if animation == "slide":
        return f"if(lt(t,{start}+0.5),w+10,{base_x}+(w+10)*exp(-8*(t-{start})))"
    return base_x


def _animate_y(base_y: str, animation: str, start: float) -> str:
    if animation == "bounce":
        return f"{base_y}+10*abs(sin(8*(t-{start})))*exp(-2*(t-{start}))"
    if animation == "zoom":
        return base_y
    return base_y


def _animate_alpha(animation: str, start: float, end: float | None) -> str:
    if animation == "fade":
        fade_in = f"min(1,(t-{start})/0.5)"
        if end is not None:
            fade_out = f"min(1,({end}-t)/0.5)"
            return f"min({fade_in},{fade_out})"
        return fade_in
    if animation == "pop":
        return f"min(1,(t-{start})/0.3)"
    if animation == "typewriter":
        # Typewriter effect via alpha (simplistic per-character)
        return f"min(1,(t-{start})/0.8)"
    return ""


def _hex_to_ffmpeg(hex_color: str, alpha: float = 1.0) -> str:
    """Convert CSS hex colors to FFmpeg's 0xRRGGBB@alpha format."""
    h = hex_color.strip().lstrip("#")
    if len(h) == 6:
        return f"0x{h}@{max(0.0, min(1.0, alpha)):.3f}"
    if len(h) == 8:
        rgb = h[:6]
        css_alpha = int(h[6:8], 16) / 255
        return f"0x{rgb}@{max(0.0, min(1.0, css_alpha * alpha)):.3f}"
    return hex_color
