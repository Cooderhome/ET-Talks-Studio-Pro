/**
 * app.js – Main application controller for ET-Talks Studio Pro
 *
 * Orchestrates:
 *  - URL fetch (video info without download)
 *  - Download trigger + SSE stream
 *  - Form state management (title, subtitle, fonts, colors, etc.)
 *  - Live preview updates (delegates to ETPreview)
 *  - Timeline init (delegates to ETTimeline)
 *  - Render trigger + SSE stream
 *  - History panel (load, search, delete, download)
 *  - File upload handlers (logo, music)
 *  - Theme / safe-zone toggles
 */

'use strict';

/* ================================================================== */
/* State                                                                */
/* ================================================================== */

const state = {
  videoInfo:     null,     // metadata from /api/info
  downloadJobId: null,
  renderJobId:   null,
  downloadPath:  null,     // server path of downloaded file
  trimStart:     0,
  trimEnd:       null,
  logoUploaded:  false,
  musicUploaded: false,
  currentDownloadES: null,
  currentRenderES:   null,
  subtitlePath:  null,     // server-side .srt path from /api/transcript
};

/* ================================================================== */
/* DOM refs                                                             */
/* ================================================================== */

const $ = (id) => document.getElementById(id);

const urlInput        = $('url-input');
const btnFetchInfo    = $('btn-fetch-info');
const btnDownloadUrl  = $('btn-download-url');
const videoInfoCard   = $('video-info-card');
const btnRender       = $('btn-render');
const btnAutoShorts   = $('btn-auto-shorts');
const historyList     = $('history-list');
const historySearch   = $('history-search');
const safeZoneToggle  = $('safe-zone-toggle');
const centerPanel     = $('panel-center');

/* ================================================================== */
/* Initialization                                                       */
/* ================================================================== */

document.addEventListener('DOMContentLoaded', () => {
  initTimeline();
  initPreview();
  initFormHandlers();
  initUploadHandlers();
  initTranscriptPanel();
  loadHistory();
  loadDownloads();
  initTabBar();
  initThemeToggle();
  initSafeZoneToggle();
  initGPUBadge();
  updatePreviewOverlays();   // render with default empty state
});

/* ================================================================== */
/* Timeline                                                             */
/* ================================================================== */

let timeline;

function initTimeline() {
  timeline = new ETTimeline({
    trackId:       'timeline-track',
    startHandleId: 'handle-start',
    endHandleId:   'handle-end',
    playheadId:    'timeline-playhead',
    selectionId:   'timeline-selection',
    thumbsId:      'timeline-thumbs',
    timestampsId:  'timeline-timestamps',
    trimStartId:   'trim-start-display',
    trimEndId:     'trim-end-display',
  });

  document.addEventListener('et:timeline:trim', (e) => {
    state.trimStart = e.detail.start;
    state.trimEnd   = e.detail.end;
  });

  document.addEventListener('et:preview:timeupdate', (e) => {
    if (timeline) timeline.setPlayheadRatio(e.detail.ratio);
  });
}

/* ================================================================== */
/* Phone preview                                                        */
/* ================================================================== */

let preview;

function initPreview() {
  preview = new ETPreview({
    videoId:   'phone-video',
    canvasId:  'phone-canvas',
    playBtnId: 'phone-play-btn',
    progressId:'phone-progress-fill',
    timeLblId: 'phone-time-label',
  });
}

/* ================================================================== */
/* Fetch video info (no download)                                       */
/* ================================================================== */

if (btnFetchInfo) {
  btnFetchInfo.addEventListener('click', fetchVideoInfo);
}

urlInput?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') fetchVideoInfo();
});

async function fetchVideoInfo() {
  const url = urlInput?.value.trim();
  if (!url) return showToast('Please enter a YouTube URL', 'error');

  setLoadingState(btnFetchInfo, true, 'Fetching…');

  try {
    const res  = await fetch('/api/info', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error ?? 'Failed to fetch info');

    state.videoInfo = data;
    renderVideoInfoCard(data);
    ETProgress.log(`Fetched: ${data.title}`, 'success');
  } catch (err) {
    showToast(err.message, 'error');
    ETProgress.log(err.message, 'error');
  } finally {
    setLoadingState(btnFetchInfo, false, '<i class="bi bi-search"></i>');
  }
}

function renderVideoInfoCard(info) {
  if (!videoInfoCard) return;

  $('vi-thumb')?.setAttribute('src', info.thumbnail ?? '');
  setTextContent('vi-title',    info.title);
  setTextContent('vi-duration', info.duration_str);
  setTextContent('vi-views',    info.view_count_str);
  setTextContent('vi-res',      `${info.width}×${info.height}`);
  setTextContent('vi-fps',      `${info.fps} fps`);
  setTextContent('vi-codec',    info.vcodec);
  setTextContent('vi-size',     info.filesize_approx_str);

  videoInfoCard.classList.add('visible');

  // Pre-fill title field with video title
  const titleInput = $('overlay-title');
  if (titleInput && !titleInput.value) {
    titleInput.value = info.title.substring(0, 80);
    updatePreviewOverlays();
  }
}

/* ================================================================== */
/* Download                                                             */
/* ================================================================== */

if (btnDownloadUrl) {
  btnDownloadUrl.addEventListener('click', startDownload);
}

async function startDownload() {
  const url = urlInput?.value.trim();
  if (!url) return showToast('Please enter a YouTube URL', 'error');

  // Close any existing SSE connection
  state.currentDownloadES?.close();

  setLoadingState(btnDownloadUrl, true, 'Starting…');
  setLoadingState(btnRender, true);

  try {
    const res  = await fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error ?? 'Download failed');

    state.downloadJobId = data.job_id;

    // If the server returned a cached/already-done job, skip the SSE stream
    if (data.status === 'done') {
      ETProgress.log(`Using cached download. Job: ${data.job_id}`, 'success');
      setLoadingState(btnDownloadUrl, false, '<i class="bi bi-check-circle-fill"></i> Downloaded');
      await onDownloadDone(data);
      return;
    }

    ETProgress.log(`Download started. Job: ${data.job_id}`, 'info');
    state.currentDownloadES = ETProgress.listenDownload(data.job_id, onDownloadDone);
    setLoadingState(btnDownloadUrl, true, 'Downloading…');

  } catch (err) {
    showToast(err.message, 'error');
    ETProgress.log(err.message, 'error');
    setLoadingState(btnDownloadUrl, false, '<i class="bi bi-download"></i> Download');
    setLoadingState(btnRender, false);
    if (btnAutoShorts) setLoadingState(btnAutoShorts, false);
  }
}

async function onDownloadDone(data) {
  state.downloadPath = data.output_path;
  setLoadingState(btnDownloadUrl, false, '<i class="bi bi-check-circle-fill"></i> Downloaded');

  // Load into phone preview
  if (preview && data.job_id) {
    preview.loadVideo(`/api/preview/${data.job_id}`);
  }

  // Load timeline thumbnails — wrapped so errors never block the render button
  if (timeline && data.job_id) {
    try {
      await timeline.loadThumbnails(data.job_id);
    } catch (err) {
      ETProgress.log(`Timeline thumbnails failed: ${err.message}`, 'warning');
    }
    if (data.info?.duration) {
      timeline.setDuration(data.info.duration);
      state.trimEnd = data.info.duration;
    }
  }

  // Populate video info card if not already done
  if (!state.videoInfo && data.info) {
    state.videoInfo = data.info;
    renderVideoInfoCard(data.info);
  }

  // Always enable render button regardless of what happened above
  setLoadingState(btnRender, false);
  if (btnAutoShorts) setLoadingState(btnAutoShorts, false);
  ETProgress.log('Video ready for rendering.', 'success');
  showToast('Download complete! \u2713', 'success');
}

/* ================================================================== */
/* Form handlers                                                        */
/* ================================================================== */

function initFormHandlers() {
  // Live preview update on any form field change
  const liveFields = [
    'overlay-title', 'overlay-subtitle',
    'title-color', 'subtitle-color',
    'title-font-size', 'subtitle-font-size',
    'title-shadow', 'title-bg',
  ];

  liveFields.forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener('input',  updatePreviewOverlays);
    el.addEventListener('change', updatePreviewOverlays);
  });

  // Crop mode buttons
  document.querySelectorAll('.crop-mode-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.crop-mode-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
    });
  });

  // Animation buttons
  document.querySelectorAll('.anim-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const group = btn.closest('[data-anim-group]');
      if (group) {
        group.querySelectorAll('.anim-btn').forEach((b) => b.classList.remove('active'));
      }
      btn.classList.add('active');
    });
  });

  // Range → display value
  document.querySelectorAll('.range-dark').forEach((range) => {
    const display = range.nextElementSibling;
    if (display?.classList.contains('range-value')) {
      range.addEventListener('input', () => {
        display.textContent = range.value;
      });
    }
  });

  // Render button
  if (btnRender) {
    btnRender.addEventListener('click', startRender);
  }
  
  // Auto-Shorts button
  if (btnAutoShorts) {
    btnAutoShorts.addEventListener('click', startAutoShorts);
  }
}

function updatePreviewOverlays() {
  if (!preview) return;
  preview.setOverlays({
    title:          $('overlay-title')?.value    ?? '',
    titleColor:     $('title-color')?.value      ?? '#ffffff',
    titleSize:      parseInt($('title-font-size')?.value ?? 60),
    titleShadow:    $('title-shadow')?.checked   !== false,
    titleBg:        $('title-bg')?.checked       ?? false,
    titleBgColor:   '#000000',
    subtitle:       $('overlay-subtitle')?.value ?? '',
    subtitleColor:  $('subtitle-color')?.value   ?? '#e5e5e5',
    subtitleSize:   parseInt($('subtitle-font-size')?.value ?? 36),
  });
}

/* ================================================================== */
/* Upload handlers                                                      */
/* ================================================================== */

function initUploadHandlers() {
  initFileUpload('logo-upload',  'logo-filename',  'logo');
  initFileUpload('music-upload', 'music-filename', 'music');
}

function initFileUpload(inputId, displayId, type) {
  const input   = $(inputId);
  const display = $(displayId);
  if (!input) return;

  input.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);
    formData.append('type', type);

    try {
      const res  = await fetch('/api/upload', { method: 'POST', body: formData });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? 'Upload failed');

      if (display) {
        display.textContent = file.name;
        display.style.display = 'block';
      }
      ETProgress.log(`${type} uploaded: ${file.name}`, 'success');
    } catch (err) {
      showToast(err.message, 'error');
    }
  });
}

/* ================================================================== */
/* Render                                                               */
/* ================================================================== */

async function startRender() {
  if (!state.downloadJobId) {
    return showToast('Please download a video first', 'error');
  }

  state.currentRenderES?.close();

  const payload = buildRenderPayload();

  setLoadingState(btnRender, true, '<span class="spinner"></span> Rendering…');

  try {
    const res  = await fetch('/api/render', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error ?? 'Render failed');

    state.renderJobId = data.job_id;
    ETProgress.log(`Render started. Job: ${data.job_id}`, 'info');

    state.currentRenderES = ETProgress.listenRender(data.job_id, onRenderDone);

  } catch (err) {
    showToast(err.message, 'error');
    ETProgress.log(err.message, 'error');
    setLoadingState(btnRender, false, '<i class="bi bi-film"></i> Render');
  }
}

function buildRenderPayload() {
  const getActive = (selector) =>
    document.querySelector(`${selector}.active`)?.dataset.value ?? '';

  const titleText    = $('overlay-title')?.value.trim()    ?? '';
  const subtitleText = $('overlay-subtitle')?.value.trim() ?? '';

  const overlays = [];
  if (titleText) {
    overlays.push({
      text:       titleText,
      x:          '(w-text_w)/2',
      y:          'h*0.80',
      font_size:  parseInt($('title-font-size')?.value ?? 60),
      font_color: $('title-color')?.value ?? '#FFFFFF',
      shadow:     $('title-shadow')?.checked !== false,
      bg_color:   $('title-bg')?.checked ? '#000000' : '',
      bg_alpha:   0.6,
      animation:  document.querySelector('[data-anim-group="title"] .anim-btn.active')?.dataset.anim ?? 'none',
      start_time: 0,
      end_time:   null,
    });
  }
  if (subtitleText) {
    overlays.push({
      text:       subtitleText,
      x:          '(w-text_w)/2',
      y:          'h*0.90',
      font_size:  parseInt($('subtitle-font-size')?.value ?? 36),
      font_color: $('subtitle-color')?.value ?? '#E5E5E5',
      shadow:     true,
      animation:  document.querySelector('[data-anim-group="subtitle"] .anim-btn.active')?.dataset.anim ?? 'none',
      start_time: 0,
      end_time:   null,
    });
  }

  return {
    download_job_id:  state.downloadJobId,
    title:            titleText || state.videoInfo?.title || '',
    quality:          $('quality-select')?.value       ?? 'high',
    crop_mode:        document.querySelector('.crop-mode-btn.active')?.dataset.mode ?? 'blur_background',
    start_time:       state.trimStart,
    end_time:         state.trimEnd,
    logo_opacity:     parseFloat($('logo-opacity')?.value ?? 1),
    logo_position:    $('logo-position')?.value ?? 'top_right',
    watermark_text:   $('watermark-text')?.value ?? '',
    music_volume:     parseFloat($('music-volume')?.value ?? 0.3),
    original_volume:  parseFloat($('original-volume')?.value ?? 1.0),
    text_overlays:    overlays,
    burn_subtitles:   !!state.subtitlePath,
    color_template:   $('color-template')?.value ?? 'none',
    subtitle_path:    state.subtitlePath ?? '',
  };
}

function onRenderDone(data) {
  setLoadingState(btnRender, false, '<i class="bi bi-film"></i> Render');
  showToast('Render complete! ✓', 'success');
  ETProgress.log(`Output: ${data.filename}`, 'success');
  loadHistory();  // refresh history panel
}

/* ================================================================== */
/* Auto-Shorts                                                          */
/* ================================================================== */

async function startAutoShorts() {
  if (!state.downloadJobId) {
    return showToast('Please download a video first', 'error');
  }

  state.currentRenderES?.close();
  setLoadingState(btnAutoShorts, true, '<span class="spinner"></span> Extracting...');
  
  try {
    // 1. Extract highlights
    const model = $('ollama-model')?.value || 'llama3';
    ETProgress.log(`Requesting highlights from Ollama (${model})...`, 'info');
    
    const extractRes = await fetch('/api/auto-shorts/extract', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ download_job_id: state.downloadJobId, model })
    });
    const extractData = await extractRes.json();
    if (!extractRes.ok) throw new Error(extractData.error ?? 'Extraction failed');
    
    if (!extractData.highlights || extractData.highlights.length === 0) {
        throw new Error('Ollama returned no highlights.');
    }
    
    ETProgress.log(`Ollama found ${extractData.highlights.length} highlights. Starting batch render...`, 'success');
    
    // 2. Start batch render
    setLoadingState(btnAutoShorts, true, '<span class="spinner"></span> Rendering...');
    const payload = buildRenderPayload();
    payload.highlights = extractData.highlights; // inject highlights
    
    const renderRes = await fetch('/api/auto-shorts/render', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const renderData = await renderRes.json();
    if (!renderRes.ok) throw new Error(renderData.error ?? 'Batch render failed');
    
    // 3. Listen to batch progress
    state.currentRenderES = ETProgress.listenBatchRender(renderData.batch_id, () => {
        setLoadingState(btnAutoShorts, false, '<i class="bi bi-magic"></i> Auto-Generate Shorts');
        showToast('Auto-Shorts batch complete! ✓', 'success');
        loadHistory();
    });
    
  } catch (err) {
    showToast(err.message, 'error');
    ETProgress.log(err.message, 'error');
    setLoadingState(btnAutoShorts, false, '<i class="bi bi-magic"></i> Auto-Generate Shorts');
  }
}

/* ================================================================== */
/* Transcript Paste Panel                                              */
/* ================================================================== */

function initTranscriptPanel() {
  const btnToggle = $('btn-toggle-transcript');
  const tBody     = $('transcript-body');
  const tChevron  = $('transcript-chevron');
  const btnParse  = $('btn-parse-transcript');
  const btnClear  = $('btn-clear-transcript');
  const textarea  = $('transcript-text');
  const panel     = $('transcript-panel');
  const badge     = $('transcript-status-badge');
  const errorDiv  = $('transcript-error');

  if (btnToggle && tBody) {
    btnToggle.addEventListener('click', () => {
      const isExpanded = btnToggle.getAttribute('aria-expanded') === 'true';
      btnToggle.setAttribute('aria-expanded', !isExpanded);
      tBody.style.display = isExpanded ? 'none' : 'block';
      if (tChevron) {
        tChevron.className = isExpanded ? 'bi bi-chevron-down' : 'bi bi-chevron-up';
      }
    });
  }

  if (!btnParse || !textarea) return;

  btnParse.addEventListener('click', async () => {
    const text = textarea.value.trim();
    if (!text) {
      showTranscriptError('Please paste your transcript text first.');
      return;
    }

    setLoadingState(btnParse, true, '<span class="spinner"></span> Parsing…');
    hideTranscriptError();

    try {
      const res  = await fetch('/api/transcript', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          text,
          job_id: state.downloadJobId ?? 'manual',
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? 'Parse failed');

      state.subtitlePath = data.subtitle_path;
      panel?.classList.add('has-subs');
      if (badge) { badge.textContent = '✓ Ready'; badge.style.display = ''; }
      ETProgress.log(`Transcript parsed: ${data.line_count + 1} lines → subtitle ready.`, 'success');
      showToast('Transcript parsed! Subtitles ready.', 'success');
    } catch (err) {
      showTranscriptError(err.message);
      ETProgress.log(err.message, 'error');
    } finally {
      setLoadingState(btnParse, false, '<i class="bi bi-check2-circle"></i> Parse Transcript');
    }
  });

  btnClear?.addEventListener('click', () => {
    textarea.value = '';
    state.subtitlePath = null;
    panel?.classList.remove('has-subs');
    if (badge) badge.style.display = 'none';
    hideTranscriptError();
  });

  function showTranscriptError(msg) {
    if (!errorDiv) return;
    errorDiv.textContent = msg;
    errorDiv.style.display = 'block';
  }
  function hideTranscriptError() {
    if (errorDiv) errorDiv.style.display = 'none';
  }
}

/* ================================================================== */
/* History panel                                                        */
/* ================================================================== */

async function loadHistory(query = '') {
  if (!historyList) return;

  const url = query
    ? `/api/history?search=${encodeURIComponent(query)}`
    : '/api/history';

  try {
    const res  = await fetch(url);
    const data = await res.json();
    renderHistory(data.history ?? []);
  } catch {
    // Silent fail — history is non-critical
  }
}

function renderHistory(items) {
  if (!historyList) return;
  historyList.innerHTML = '';

  if (items.length === 0) {
    historyList.innerHTML = `
      <div class="empty-state">
        <div class="empty-state-icon"><i class="bi bi-camera-video"></i></div>
        <div class="empty-state-text">No renders yet.<br>Create your first Short!</div>
      </div>`;
    return;
  }

  items.forEach((item) => {
    const card = document.createElement('div');
    card.className = 'history-card animate-fade-in';
    card.dataset.id = item.id;

    const thumb = item.thumbnail_path
      ? `<img class="history-thumb" src="/api/history-thumb/${item.id}" alt="${escHtml(item.title)}" loading="lazy">`
      : `<div class="history-thumb-placeholder"><i class="bi bi-film"></i></div>`;

    const date = item.created_at
      ? new Date(item.created_at + 'Z').toLocaleDateString()
      : '';

    card.innerHTML = `
      ${thumb}
      <div class="history-body">
        <div class="history-title" title="${escHtml(item.title)}">${escHtml(item.title)}</div>
        <div class="history-meta">
          <span class="badge badge-muted">${escHtml(item.resolution)}</span>
          <span class="badge badge-muted">${escHtml(item.quality)}</span>
          <span class="text-muted text-xs">${date}</span>
        </div>
      </div>
      <div class="history-actions">
        <button class="btn-icon btn-blue" data-action="download" data-id="${item.id}" title="Download" data-tooltip="Download">
          <i class="bi bi-download"></i>
        </button>
        <button class="btn-icon" data-action="rerender" data-id="${item.id}" title="Re-render" data-tooltip="Re-render">
          <i class="bi bi-arrow-repeat"></i>
        </button>
        <button class="btn-icon btn-danger" data-action="delete" data-id="${item.id}" title="Delete" data-tooltip="Delete">
          <i class="bi bi-trash3"></i>
        </button>
      </div>`;

    historyList.appendChild(card);
  });

  // Delegate events
  historyList.removeEventListener('click', historyClickHandler);
  historyList.addEventListener('click', historyClickHandler);
}

function historyClickHandler(e) {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  const { action, id } = btn.dataset;

  if (action === 'download') {
    window.location.href = `/api/download-output/${id}`;
  } else if (action === 'delete') {
    deleteHistoryItem(id);
  } else if (action === 'rerender') {
    showToast('Re-render: configure settings and click Render.', 'info');
  }
}

async function deleteHistoryItem(id) {
  if (!confirm('Delete this render? This cannot be undone.')) return;

  try {
    const res = await fetch(`/api/history/${id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('Delete failed');
    const card = historyList.querySelector(`[data-id="${id}"]`);
    card?.remove();
    ETProgress.log(`Deleted render: ${id}`, 'info');
  } catch (err) {
    showToast(err.message, 'error');
  }
}

// History search
historySearch?.addEventListener('input', debounce(() => {
  loadHistory(historySearch.value.trim());
}, 300));

/* ================================================================== */
/* Downloads Library                                                    */
/* ================================================================== */

const downloadsList = $('downloads-list');
const downloadsSearch = $('downloads-search');
let _allDownloads = [];

async function loadDownloads() {
  if (!downloadsList) return;
  try {
    const res  = await fetch('/api/downloads');
    const data = await res.json();
    _allDownloads = data.downloads ?? [];
    renderDownloads(_allDownloads);
  } catch {
    // Silent — non-critical
  }
}

function renderDownloads(items) {
  if (!downloadsList) return;
  downloadsList.innerHTML = '';

  if (items.length === 0) {
    downloadsList.innerHTML = `
      <div class="empty-state">
        <div class="empty-state-icon"><i class="bi bi-folder2-open"></i></div>
        <div class="empty-state-text">No downloads yet.<br>Download a video first.</div>
      </div>`;
    return;
  }

  items.forEach((item) => {
    const card = document.createElement('div');
    card.className = 'dl-card';
    card.innerHTML = `
      <div class="dl-card-icon"><i class="bi bi-file-earmark-play"></i></div>
      <div class="dl-card-body">
        <div class="dl-card-name" title="${escHtml(item.stem)}">${escHtml(item.stem)}</div>
        <div class="dl-card-size">${escHtml(item.size_str)}</div>
      </div>
      <button class="btn-primary dl-card-use" data-filename="${escHtml(item.filename)}">
        Use
      </button>`;
    downloadsList.appendChild(card);
  });

  downloadsList.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-filename]');
    if (!btn) return;
    await useDownloadedFile(btn.dataset.filename, btn);
  });
}

async function useDownloadedFile(filename, btn) {
  const origText = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '…';

  try {
    const res  = await fetch('/api/use-download', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ filename }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error ?? 'Failed');

    state.downloadJobId = data.job_id;
    await onDownloadDone(data);
    showToast(`Loaded: ${filename}`, 'success');

    // Switch to Downloads tab so user sees it's loaded
    btn.innerHTML = '<i class="bi bi-check-circle-fill"></i>';
  } catch (err) {
    showToast(err.message, 'error');
    btn.disabled = false;
    btn.innerHTML = origText;
  }
}

downloadsSearch?.addEventListener('input', debounce(() => {
  const q = downloadsSearch.value.trim().toLowerCase();
  renderDownloads(q ? _allDownloads.filter(f => f.stem.toLowerCase().includes(q)) : _allDownloads);
}, 200));

/* ================================================================== */
/* Tab bar (Downloads / Renders)                                        */
/* ================================================================== */

function initTabBar() {
  document.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tabId = btn.dataset.tab;
      document.querySelectorAll('.tab-btn').forEach((b) => {
        b.classList.toggle('active', b === btn);
        b.setAttribute('aria-selected', b === btn ? 'true' : 'false');
      });
      document.querySelectorAll('.tab-panel').forEach((panel) => {
        panel.classList.toggle('hidden', panel.id !== `tab-panel-${tabId}`);
      });
      // Refresh content on switch
      if (tabId === 'history') loadHistory();
      if (tabId === 'downloads') loadDownloads();
    });
  });

  // Refresh button refreshes whichever tab is active
  $('btn-refresh-history')?.addEventListener('click', () => {
    loadHistory();
    loadDownloads();
  });
}

/* ================================================================== */
/* Theme toggle                                                         */
/* ================================================================== */

function initThemeToggle() {
  const toggle = $('theme-toggle');
  if (!toggle) return;
  toggle.addEventListener('click', () => {
    document.body.classList.toggle('theme-light');
    toggle.classList.toggle('active');
  });
}

/* ================================================================== */
/* Safe zone toggle                                                     */
/* ================================================================== */

function initSafeZoneToggle() {
  const btn = safeZoneToggle;
  if (!btn) return;
  btn.addEventListener('click', () => {
    const screen = document.querySelector('.phone-screen');
    screen?.closest('.phone-wrap')?.classList.toggle('safe-zones-hidden');
    btn.classList.toggle('active');
  });
}

/* ================================================================== */
/* GPU badge                                                            */
/* ================================================================== */

async function initGPUBadge() {
  const badge = $('gpu-badge');
  if (!badge) return;
  try {
    const res  = await fetch('/api/gpu-info');
    const data = await res.json();
    badge.textContent = data.gpu_name ?? 'CPU';
    badge.title       = `Encoder: ${data.encoder}`;
  } catch {
    badge.textContent = 'CPU';
  }
}

/* ================================================================== */
/* Utility functions                                                    */
/* ================================================================== */

function setLoadingState(btn, loading, label) {
  if (!btn) return;
  btn.disabled = loading;
  if (label !== undefined) btn.innerHTML = label;
}

function setTextContent(id, text) {
  const el = $(id);
  if (el) el.textContent = text ?? '—';
}

function showToast(message, type = 'info') {
  const container = $('toast-container');
  if (!container) return;

  const icons = { success: 'bi-check-circle-fill', error: 'bi-x-circle-fill', info: 'bi-info-circle-fill' };
  const toast = document.createElement('div');
  toast.className = `alert-dark alert-${type === 'error' ? 'error' : type === 'success' ? 'success' : 'info'} animate-fade-in`;
  toast.style.cssText = 'min-width:240px;max-width:360px;pointer-events:auto;';
  toast.innerHTML = `<i class="bi ${icons[type] ?? icons.info}"></i> ${escHtml(message)}`;

  container.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}

function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}
