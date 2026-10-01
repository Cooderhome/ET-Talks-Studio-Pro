# ET-Talks Studio Pro

A professional Flask-based web application that downloads YouTube videos and converts them into highly engaging vertical Shorts (1080×1920) optimized for YouTube Shorts, TikTok, Instagram Reels, and Facebook Reels.

## Features
- **Modern UI**: Dark-themed, glassmorphism dashboard with an interactive phone mockup for live previews.
- **Auto GPU Detection**: Automatically utilizes hardware acceleration (`h264_nvenc`, `h264_qsv`, `h264_amf`) for faster rendering if available.
- **Advanced Editing**: Three crop modes (fit, blur background, center crop), custom text overlays with animations (fade, pop, typewriter, etc.), logo/watermarks, and background music mixing.
- **Smart Downloading**: Uses `yt-dlp` to fetch the best format available, preferring MP4.
- **Timeline Scrubber**: Drag handles to accurately trim your video clip with visual timeline thumbnails.
- **SSE Real-time Progress**: View exact rendering frames, FPS, ETA, and download speeds in real-time.

## Prerequisites

1. **Python 3.11+**
2. **FFmpeg** installed and added to your system `PATH`.
   - Windows: Download from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) or install via `winget install ffmpeg`.
   - Mac: `brew install ffmpeg`
   - Linux: `sudo apt install ffmpeg`

## Installation

1. Create a virtual environment and install dependencies:
```bash
python -m venv env
source env/bin/activate  # On Windows: .\env\Scripts\activate
pip install -r requirements.txt
```

2. Download your preferred fonts (e.g., *Noto Sans*, *Roboto*) into `static/fonts/` for text overlays. (Fallback mechanisms exist, but adding local TTF files is required for the best experience).

## Running the Application

```bash
python app.py
```
Or with Flask directly:
```bash
flask --app app run --debug
```

Access the dashboard at `http://127.0.0.1:5000/`.

## Project Structure
- `app.py`: Main Flask application.
- `config.py`: Centralized configuration.
- `utils/`: Core business logic decoupled from Flask.
  - `downloader.py`: `yt-dlp` integration.
  - `ffmpeg.py`: FFmpeg filter graph and command generation.
  - `renderer.py`: Execution and progress tracking for FFmpeg.
  - `gpu.py`: Auto-detection of NVENC/QSV/AMF.
- `static/`: UI assets (JS, CSS).
- `templates/`: Jinja2 templates.
