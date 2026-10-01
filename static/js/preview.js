/**
 * preview.js – Live phone mockup preview for ET-Talks Studio Pro
 *
 * Renders a live preview on the canvas overlay that mirrors
 * the exact text/logo overlays FFmpeg will burn into the final video.
 *
 * Also:
 *  - Loads the downloaded video into the phone screen
 *  - Drives the playback controls
 *  - Syncs the timeline playhead
 *  - Responds to et:timeline:seek events
 */

'use strict';

class PhonePreview {
  /**
   * @param {object} opts
   * @param {string} opts.videoId    - id of <video> element
   * @param {string} opts.canvasId   - id of <canvas> element
   * @param {string} opts.playBtnId  - id of play/pause button
   * @param {string} opts.progressId - id of phone progress bar element
   * @param {string} opts.timeLblId  - id of time label
   */
  constructor(opts = {}) {
    this.video     = document.getElementById(opts.videoId    ?? 'phone-video');
    this.canvas    = document.getElementById(opts.canvasId   ?? 'phone-canvas');
    this.playBtn   = document.getElementById(opts.playBtnId  ?? 'phone-play-btn');
    this.progress  = document.getElementById(opts.progressId ?? 'phone-progress-fill');
    this.timeLabel = document.getElementById(opts.timeLblId  ?? 'phone-time-label');
    this.placeholder = document.getElementById('phone-placeholder');

    this.ctx       = this.canvas ? this.canvas.getContext('2d') : null;
    this._rafId    = null;
    this._overlays = {};   // { title, titleColor, titleSize, subtitle, subtitleColor, subtitleSize }

    this._bindEvents();
  }

  /* ---------------------------------------------------------------- */
  /* Public API                                                         */
  /* ---------------------------------------------------------------- */

  /** Load a video URL into the phone preview. */
  loadVideo(src) {
    if (!this.video) return;
    this.video.src = src;
    this.video.load();
    this.video.muted = true;

    if (this.placeholder) this.placeholder.style.display = 'none';
    this.video.style.display = 'block';

    // Sync canvas size once metadata is ready
    this.video.addEventListener('loadedmetadata', () => {
      this._syncCanvasSize();
      this._startRender();
    }, { once: true });
  }

  /** Unload / clear the preview. */
  clear() {
    if (this.video) {
      this.video.pause();
      this.video.src = '';
      this.video.style.display = 'none';
    }
    if (this.placeholder) this.placeholder.style.display = 'flex';
    this._stopRender();
    if (this.ctx) this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
  }

  /** Update overlay settings and redraw. */
  setOverlays(overlayData) {
    this._overlays = { ...overlayData };
    // Trigger immediate redraw even when paused
    if (!this.video?.paused) return;
    this._drawFrame();
  }

  /* ---------------------------------------------------------------- */
  /* Rendering loop                                                     */
  /* ---------------------------------------------------------------- */

  _startRender() {
    this._stopRender();
    const loop = () => {
      this._drawFrame();
      this._updateProgress();
      this._rafId = requestAnimationFrame(loop);
    };
    this._rafId = requestAnimationFrame(loop);
  }

  _stopRender() {
    if (this._rafId) cancelAnimationFrame(this._rafId);
    this._rafId = null;
  }

  _syncCanvasSize() {
    if (!this.canvas || !this.video) return;
    const screen = this.canvas.parentElement;
    this.canvas.width  = screen?.offsetWidth  ?? 1080;
    this.canvas.height = screen?.offsetHeight ?? 1920;
  }

  _drawFrame() {
    const ctx = this.ctx;
    if (!ctx || !this.canvas) return;

    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    this._drawOverlays(ctx);
  }

  _drawOverlays(ctx) {
    const W = this.canvas.width;
    const H = this.canvas.height;
    const ov = this._overlays;

    // Title overlay
    if (ov.title) {
      const size = this._relSize(ov.titleSize ?? 60, H);
      ctx.save();
      ctx.font = `700 ${size}px Inter, sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'bottom';

      const x = W / 2;
      const y = H * 0.82;

      // Text background box
      if (ov.titleBg) {
        const metrics = ctx.measureText(ov.title);
        const pad = 12;
        const boxH = size + pad * 2;
        const boxW = metrics.width + pad * 2 * 3;
        ctx.globalAlpha = 0.6;
        ctx.fillStyle = ov.titleBgColor ?? '#000000';
        ctx.roundRect(x - boxW / 2, y - size - pad, boxW, boxH, 8);
        ctx.fill();
        ctx.globalAlpha = 1;
      }

      // Shadow
      if (ov.titleShadow !== false) {
        ctx.shadowColor = 'rgba(0,0,0,0.8)';
        ctx.shadowBlur  = 8;
        ctx.shadowOffsetX = 2;
        ctx.shadowOffsetY = 2;
      }

      ctx.fillStyle = ov.titleColor ?? '#FFFFFF';
      this._wrapText(ctx, ov.title, x, y, W * 0.88, size * 1.3);
      ctx.restore();
    }

    // Subtitle overlay
    if (ov.subtitle) {
      const size = this._relSize(ov.subtitleSize ?? 36, H);
      ctx.save();
      ctx.font = `400 ${size}px Inter, sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'bottom';
      ctx.fillStyle = ov.subtitleColor ?? '#E5E5E5';
      ctx.shadowColor   = 'rgba(0,0,0,0.7)';
      ctx.shadowBlur    = 6;
      ctx.shadowOffsetX = 1;
      ctx.shadowOffsetY = 1;
      this._wrapText(ctx, ov.subtitle, W / 2, H * 0.92, W * 0.85, size * 1.25);
      ctx.restore();
    }
  }

  /** Scale font size proportionally to the canvas height. */
  _relSize(basePx, canvasH) {
    // basePx is sized for 1920px height
    return Math.round(basePx * canvasH / 1920);
  }

  /** Draw wrapped text, splitting on newlines and word boundaries. */
  _wrapText(ctx, text, x, y, maxWidth, lineH) {
    const words = text.split(' ');
    let line = '';
    const lines = [];

    for (const word of words) {
      const test = line ? `${line} ${word}` : word;
      if (ctx.measureText(test).width > maxWidth && line) {
        lines.push(line);
        line = word;
      } else {
        line = test;
      }
    }
    if (line) lines.push(line);

    // Draw bottom-up
    for (let i = lines.length - 1; i >= 0; i--) {
      ctx.fillText(lines[i], x, y - (lines.length - 1 - i) * lineH);
    }
  }

  /* ---------------------------------------------------------------- */
  /* Playback controls                                                  */
  /* ---------------------------------------------------------------- */

  _updateProgress() {
    if (!this.video || !this.video.duration) return;
    const ratio = this.video.currentTime / this.video.duration;

    if (this.progress) {
      this.progress.style.width = `${ratio * 100}%`;
    }

    if (this.timeLabel) {
      this.timeLabel.textContent = `${_fmtTime(this.video.currentTime)} / ${_fmtTime(this.video.duration)}`;
    }

    // Sync timeline playhead
    document.dispatchEvent(new CustomEvent('et:preview:timeupdate', {
      detail: { ratio, time: this.video.currentTime }
    }));
  }

  /* ---------------------------------------------------------------- */
  /* Event binding                                                      */
  /* ---------------------------------------------------------------- */

  _bindEvents() {
    // Play/pause button
    if (this.playBtn) {
      this.playBtn.addEventListener('click', () => this._togglePlay());
    }

    // Click on phone progress bar
    const progressTrack = document.getElementById('phone-progress');
    if (progressTrack) {
      progressTrack.addEventListener('click', (e) => {
        if (!this.video?.duration) return;
        const rect  = progressTrack.getBoundingClientRect();
        const ratio = (e.clientX - rect.left) / rect.width;
        this.video.currentTime = ratio * this.video.duration;
      });
    }

    // Sync play/pause icon
    if (this.video) {
      this.video.addEventListener('play',  () => this._updatePlayIcon(true));
      this.video.addEventListener('pause', () => this._updatePlayIcon(false));
      this.video.addEventListener('ended', () => {
        this._updatePlayIcon(false);
        this.video.currentTime = 0;
      });
    }

    // Timeline seek → move video
    document.addEventListener('et:timeline:seek', (e) => {
      if (!this.video?.duration) return;
      this.video.currentTime = e.detail.time;
    });

    // Resize observer to keep canvas dimensions in sync
    if (this.canvas) {
      const ro = new ResizeObserver(() => this._syncCanvasSize());
      ro.observe(this.canvas.parentElement ?? this.canvas);
    }
  }

  _togglePlay() {
    if (!this.video) return;
    this.video.paused ? this.video.play() : this.video.pause();
  }

  _updatePlayIcon(playing) {
    if (!this.playBtn) return;
    const icon = this.playBtn.querySelector('i');
    if (!icon) return;
    icon.className = playing ? 'bi bi-pause-fill' : 'bi bi-play-fill';
  }
}

/* ------------------------------------------------------------------ */
/* Helpers                                                              */
/* ------------------------------------------------------------------ */

function _fmtTime(seconds) {
  const s = Math.floor(seconds);
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2,'0')}:${String(s % 60).padStart(2,'0')}`;
}

/* ------------------------------------------------------------------ */
/* Export                                                               */
/* ------------------------------------------------------------------ */

window.ETPreview = PhonePreview;
