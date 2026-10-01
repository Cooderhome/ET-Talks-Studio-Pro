"""
utils/gpu.py – GPU hardware encoder detection for ET-Talks Studio Pro.

Probes available FFmpeg encoders and system utilities to determine the
best hardware-accelerated encoder. Falls back to libx264 (CPU) if none
is found.

Supported:
  - NVIDIA  → h264_nvenc  (via nvidia-smi + ffmpeg encoder probe)
  - Intel   → h264_qsv    (via wmic / lspci)
  - AMD     → h264_amf    (via wmic / lspci)
  - CPU     → libx264     (always available fallback)
"""

from __future__ import annotations

import logging
import platform
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache

import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class GPUInfo:
    """Information about the detected GPU and selected encoder."""
    vendor: str = "cpu"           # "nvidia" | "intel" | "amd" | "cpu"
    gpu_name: str = "CPU / Software"
    encoder: str = "libx264"
    hardware_acceleration: bool = False
    details: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], timeout: int = 5) -> str:
    """Run a subprocess and return stdout as a string, or '' on failure."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout
    except Exception:
        return ""


def _ffmpeg_supports_encoder(encoder: str) -> bool:
    """Return True if the system FFmpeg binary supports *encoder*."""
    out = _run([config.FFMPEG_BIN, "-hide_banner", "-encoders"])
    return encoder in out


def _probe_nvidia() -> GPUInfo | None:
    """Detect NVIDIA GPU via nvidia-smi."""
    out = _run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
    if not out.strip():
        return None
    gpu_name = out.strip().splitlines()[0].strip()
    if _ffmpeg_supports_encoder("h264_nvenc"):
        logger.info("NVIDIA GPU detected: %s -> h264_nvenc", gpu_name)
        return GPUInfo(
            vendor="nvidia",
            gpu_name=gpu_name,
            encoder="h264_nvenc",
            hardware_acceleration=True,
            details={"nvidia_smi": gpu_name},
        )
    logger.warning("NVIDIA GPU found but h264_nvenc not available in FFmpeg.")
    return None


def _probe_windows_gpu() -> str:
    """On Windows, use wmic to query GPU names."""
    return _run(["wmic", "path", "win32_VideoController", "get", "name"])


def _probe_linux_gpu() -> str:
    """On Linux, use lspci to list GPU devices."""
    return _run(["lspci"])


def _probe_amd_intel() -> GPUInfo | None:
    """Detect AMD or Intel GPU from system hardware information."""
    sys_platform = platform.system().lower()
    if sys_platform == "windows":
        gpu_info_str = _probe_windows_gpu().lower()
    else:
        gpu_info_str = _probe_linux_gpu().lower()

    if not gpu_info_str:
        return None

    # Check Intel
    if "intel" in gpu_info_str and _ffmpeg_supports_encoder("h264_qsv"):
        gpu_name = "Intel GPU"
        logger.info("Intel GPU detected -> h264_qsv")
        return GPUInfo(
            vendor="intel",
            gpu_name=gpu_name,
            encoder="h264_qsv",
            hardware_acceleration=True,
        )

    # Check AMD
    if ("amd" in gpu_info_str or "radeon" in gpu_info_str) and _ffmpeg_supports_encoder("h264_amf"):
        gpu_name = "AMD GPU"
        logger.info("AMD GPU detected -> h264_amf")
        return GPUInfo(
            vendor="amd",
            gpu_name=gpu_name,
            encoder="h264_amf",
            hardware_acceleration=True,
        )

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def detect_gpu() -> GPUInfo:
    """Detect the best available hardware encoder.

    Detection order: NVIDIA → Intel/AMD → CPU.
    Results are cached after the first call.
    """
    logger.info("Probing GPU hardware encoders...")

    nvidia = _probe_nvidia()
    if nvidia:
        return nvidia

    amd_intel = _probe_amd_intel()
    if amd_intel:
        return amd_intel

    logger.info("No compatible hardware encoder found, using libx264 (CPU)")
    return GPUInfo(
        vendor="cpu",
        gpu_name="CPU / Software Encoding",
        encoder="libx264",
        hardware_acceleration=False,
    )


def get_encoder(preferred: str | None = None) -> str:
    """Return the FFmpeg encoder string to use.

    If *preferred* is specified and supported, it is returned directly.
    Otherwise falls back to the auto-detected encoder.
    """
    if preferred and _ffmpeg_supports_encoder(preferred):
        return preferred
    gpu = detect_gpu()
    return gpu.encoder


def get_gpu_info_dict() -> dict:
    """Return GPU info as a serializable dict for the API/UI."""
    gpu = detect_gpu()
    return {
        "vendor": gpu.vendor,
        "gpu_name": gpu.gpu_name,
        "encoder": gpu.encoder,
        "hardware_acceleration": gpu.hardware_acceleration,
    }
