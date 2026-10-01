/**
 * timeline.js – Interactive timeline scrubber for ET-Talks Studio Pro
 *
 * Features:
 *  - Renders thumbnail strip from API
 *  - Drag handles for start and end trim points
 *  - Moving playhead that syncs with the phone video
 *  - Timestamp labels
 *  - Emits custom events: et:timeline:trim, et:timeline:seek
 */

'use strict';

class Timeline {
  /**
   * @param {object} opts
   * @param {string} opts.trackId        - id of .timeline-track element
   * @param {string} opts.startHandleId  - id of start drag handle
   * @param {string} opts.endHandleId    - id of end drag handle
   * @param {string} opts.playheadId     - id of playhead element
   * @param {string} opts.selectionId    - id of selection highlight div
   * @param {string} opts.thumbsId       - id of thumbnail container
   * @param {string} opts.timestampsId   - id of timestamps bar
   * @param {string} opts.trimStartId    - id of start time display span
   * @param {string} opts.trimEndId      - id of end time display span
   */
  constructor(opts) {
    this.track       = document.getElementById(opts.trackId       ?? 'timeline-track');
    this.handleStart = document.getElementById(opts.startHandleId ?? 'handle-start');
    this.handleEnd   = document.getElementById(opts.endHandleId   ?? 'handle-end');
    this.playhead    = document.getElementById(opts.playheadId    ?? 'timeline-playhead');
    this.selection   = document.getElementById(opts.selectionId   ?? 'timeline-selection');
    this.thumbsEl    = document.getElementById(opts.thumbsId      ?? 'timeline-thumbs');
    this.tsBar       = document.getElementById(opts.timestampsId  ?? 'timeline-timestamps');
    this.trimStartEl = document.getElementById(opts.trimStartId   ?? 'trim-start-display');
    this.trimEndEl   = document.getElementById(opts.trimEndId     ?? 'trim-end-display');

    this.duration     = 0;
    this.startRatio   = 0;      // 0..1
    this.endRatio     = 1;      // 0..1
    this.thumbnails   = [];

    this._dragging    = null;   // 'start' | 'end' | null
    this._dragOffset  = 0;

    if (!this.track) return;
    this._bindEvents();
  }

  /* ---------------------------------------------------------------- */
  /* Public API                                                         */
  /* ---------------------------------------------------------------- */

  /** Set the total video duration and reset handles. */
  setDuration(seconds) {
    this.duration   = seconds;
    this.startRatio = 0;
    this.endRatio   = 1;
    this._renderTimestamps();
    this._renderHandles();
    this._renderTrimInfo();
    this._dispatchTrim();
  }

  /** Load thumbnails from the API and render them into the strip. */
  async loadThumbnails(jobId) {
    if (!this.thumbsEl) return;
    this.thumbsEl.innerHTML = '';

    try {
      const res  = await fetch(`/api/thumbnails/${jobId}`);
      const data = await res.json();
      this.thumbnails = data.thumbnails || [];
      if (data.duration) {
        this.setDuration(data.duration);
      }
    } catch {
      this.thumbnails = [];
    }

    if (this.thumbnails.length === 0) {
      // Render placeholder strip
      for (let i = 0; i < 20; i++) {
        const ph = document.createElement('div');
        ph.className = 'timeline-thumb-placeholder';
        this.thumbsEl.appendChild(ph);
      }
      return;
    }

    this.thumbnails.forEach((thumb) => {
      const img = document.createElement('img');
      img.className = 'timeline-thumb-img';
      img.src = thumb.path;
      img.alt = thumb.timestamp_str ?? '';
      img.loading = 'lazy';
      img.draggable = false;
      this.thumbsEl.appendChild(img);
    });

    this._renderTimestamps();
  }

  /** Move the playhead to a given ratio (0..1). */
  setPlayheadRatio(ratio) {
    if (!this.playhead) return;
    const pct = Math.max(0, Math.min(1, ratio)) * 100;
    this.playhead.style.left = `${pct}%`;
  }

  /** Get current trim values in seconds. */
  getTrim() {
    return {
      start: +(this.startRatio * this.duration).toFixed(3),
      end:   +(this.endRatio   * this.duration).toFixed(3),
    };
  }

  /* ---------------------------------------------------------------- */
  /* Internal rendering                                                 */
  /* ---------------------------------------------------------------- */

  _renderHandles() {
    if (this.handleStart) this.handleStart.style.left = `${this.startRatio * 100}%`;
    if (this.handleEnd)   this.handleEnd.style.left   = `calc(${this.endRatio * 100}% - 8px)`;
    this._renderSelection();
  }

  _renderSelection() {
    if (!this.selection) return;
    const startPct = this.startRatio * 100;
    const width    = (this.endRatio - this.startRatio) * 100;
    this.selection.style.left  = `${startPct}%`;
    this.selection.style.width = `${width}%`;
  }

  _renderTimestamps() {
    if (!this.tsBar || this.duration === 0) return;
    this.tsBar.innerHTML = '';
    const count = 5;
    for (let i = 0; i <= count; i++) {
      const span = document.createElement('span');
      span.textContent = _fmtTime((i / count) * this.duration);
      this.tsBar.appendChild(span);
    }
  }

  _renderTrimInfo() {
    const trim = this.getTrim();
    if (this.trimStartEl) this.trimStartEl.textContent = _fmtTime(trim.start);
    if (this.trimEndEl)   this.trimEndEl.textContent   = _fmtTime(trim.end);
  }

  _dispatchTrim() {
    const trim = this.getTrim();
    document.dispatchEvent(new CustomEvent('et:timeline:trim', { detail: trim }));
    this._renderTrimInfo();
  }

  /* ---------------------------------------------------------------- */
  /* Event binding                                                      */
  /* ---------------------------------------------------------------- */

  _bindEvents() {
    // Handle drag
    [this.handleStart, this.handleEnd].forEach((handle) => {
      if (!handle) return;
      handle.addEventListener('mousedown', (e) => {
        e.preventDefault();
        this._dragging = handle === this.handleStart ? 'start' : 'end';
      });
    });

    document.addEventListener('mousemove', (e) => {
      if (!this._dragging) return;
      const rect  = this.track.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));

      if (this._dragging === 'start') {
        this.startRatio = Math.min(ratio, this.endRatio - 0.02);
      } else {
        this.endRatio = Math.max(ratio, this.startRatio + 0.02);
      }

      this._renderHandles();
      this._dispatchTrim();
    });

    document.addEventListener('mouseup', () => {
      this._dragging = null;
    });

    // Touch support
    [this.handleStart, this.handleEnd].forEach((handle) => {
      if (!handle) return;
      handle.addEventListener('touchstart', (e) => {
        e.preventDefault();
        this._dragging = handle === this.handleStart ? 'start' : 'end';
      }, { passive: false });
    });

    document.addEventListener('touchmove', (e) => {
      if (!this._dragging) return;
      const touch = e.touches[0];
      const rect  = this.track.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (touch.clientX - rect.left) / rect.width));

      if (this._dragging === 'start') {
        this.startRatio = Math.min(ratio, this.endRatio - 0.02);
      } else {
        this.endRatio = Math.max(ratio, this.startRatio + 0.02);
      }

      this._renderHandles();
      this._dispatchTrim();
    }, { passive: true });

    document.addEventListener('touchend', () => {
      this._dragging = null;
    });

    // Click to seek
    this.track.addEventListener('click', (e) => {
      if (this._dragging) return;
      const rect  = this.track.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      const time  = ratio * this.duration;
      document.dispatchEvent(new CustomEvent('et:timeline:seek', { detail: { time, ratio } }));
    });
  }
}

/* ------------------------------------------------------------------ */
/* Helpers                                                              */
/* ------------------------------------------------------------------ */

function _fmtTime(seconds) {
  const s = Math.floor(seconds);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2,'0')}:${String(r).padStart(2,'0')}`;
}

/* ------------------------------------------------------------------ */
/* Export                                                               */
/* ------------------------------------------------------------------ */

window.ETTimeline = Timeline;
