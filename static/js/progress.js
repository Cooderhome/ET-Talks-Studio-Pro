/**
 * progress.js – SSE client for download and render progress
 *
 * Connects to the Flask SSE endpoints and dispatches custom DOM events
 * so other modules (app.js, preview.js) can react without tight coupling.
 *
 * Exported events (dispatched on document):
 *   - et:download:progress  { detail: progressData }
 *   - et:download:done      { detail: doneData }
 *   - et:download:error     { detail: errorData }
 *   - et:render:progress    { detail: progressData }
 *   - et:render:done        { detail: doneData }
 *   - et:render:error       { detail: errorData }
 *   - et:log                { detail: { message, level } }
 */

'use strict';

/* ------------------------------------------------------------------ */
/* Helpers                                                              */
/* ------------------------------------------------------------------ */

function dispatch(name, detail) {
  document.dispatchEvent(new CustomEvent(name, { detail }));
}

function log(message, level = 'info') {
  dispatch('et:log', { message, level });
}

/* ------------------------------------------------------------------ */
/* SSE connection factory                                               */
/* ------------------------------------------------------------------ */

/**
 * Open an SSE stream to `url` and route incoming events.
 * Returns the EventSource so the caller can close it.
 *
 * @param {string}   url            - SSE endpoint URL
 * @param {string}   namespace      - 'download' | 'render'
 * @param {Function} onDone         - called when the stream signals completion
 * @returns {EventSource}
 */
function openProgressStream(url, namespace, onDone) {
  const es = new EventSource(url);

  es.onmessage = (evt) => {
    let data;
    try {
      data = JSON.parse(evt.data);
    } catch {
      return;
    }

    switch (data.type) {
      case 'progress':
        dispatch(`et:${namespace}:progress`, data);
        break;

      case 'status':
        log(`[${namespace.toUpperCase()}] ${data.status}`, 'info');
        break;

      case 'done':
        dispatch(`et:${namespace}:done`, data);
        log(`[${namespace.toUpperCase()}] Finished ✓`, 'success');
        es.close();
        onDone && onDone(data);
        break;

      case 'error':
        dispatch(`et:${namespace}:error`, data);
        log(`[${namespace.toUpperCase()} ERROR] ${data.message}`, 'error');
        es.close();
        break;

      case 'log':
        log(data.message, 'info');
        break;

      default:
        break;
    }
  };

  es.onerror = () => {
    log(`[${namespace.toUpperCase()}] Connection lost`, 'warning');
    es.close();
  };

  return es;
}

/* ------------------------------------------------------------------ */
/* Public API                                                           */
/* ------------------------------------------------------------------ */

/**
 * Start listening to a download SSE stream.
 * @param {string}   jobId
 * @param {Function} [onDone]
 * @returns {EventSource}
 */
function listenDownload(jobId, onDone) {
  const url = `/api/stream/download/${jobId}`;
  log(`Connecting to download stream: job ${jobId}`, 'info');
  return openProgressStream(url, 'download', onDone);
}

/**
 * Start listening to a render SSE stream.
 * @param {string}   jobId
 * @param {Function} [onDone]
 * @returns {EventSource}
 */
function listenRender(jobId, onDone) {
  const url = `/api/stream/render/${jobId}`;
  log(`Connecting to render stream: job ${jobId}`, 'info');
  return openProgressStream(url, 'render', onDone);
}

/**
 * Start listening to a batch render SSE stream for auto-shorts.
 * @param {string}   batchId
 * @param {Function} [onDone]
 * @returns {EventSource}
 */
function listenBatchRender(batchId, onDone) {
  const url = `/api/stream/auto-shorts/${batchId}`;
  log(`Connecting to batch render stream: ${batchId}`, 'info');
  const es = new EventSource(url);
  
  es.onmessage = (evt) => {
    let data;
    try { data = JSON.parse(evt.data); } catch { return; }
    
    if (data.type === 'batch_status') log(`[BATCH] Status: ${data.status} (${data.total_jobs} clips)`, 'info');
    else if (data.type === 'batch_progress') log(`[BATCH] Processing clip ${data.current_index + 1}/${data.total_jobs}: ${data.current_job_title}`, 'info');
    else if (data.type === 'job_event' && data.event.type === 'progress') {
        dispatch('et:render:progress', data.event); // Reuse single render progress bar
    }
    else if (data.type === 'batch_done') {
        log(`[BATCH] Completed successfully ✓`, 'success');
        dispatch('et:render:done', data);
        es.close();
        if (onDone) onDone(data);
    }
    else if (data.type === 'batch_error') {
        log(`[BATCH ERROR] ${data.message}`, 'error');
        dispatch('et:render:error', data);
        es.close();
    }
  };
  return es;
}

/* ------------------------------------------------------------------ */
/* Footer progress bar updater                                          */
/* ------------------------------------------------------------------ */

(function initFooterUpdater() {
  const bar       = document.getElementById('footer-progress-fill');
  const statusEl  = document.getElementById('footer-status');
  const speedEl   = document.getElementById('footer-speed');
  const etaEl     = document.getElementById('footer-eta');
  const frameEl   = document.getElementById('footer-frame');

  function updateFooter({ percent, speed_str, eta_str, frame, fps, status } = {}) {
    if (bar)      bar.style.width       = `${Math.min(percent ?? 0, 100)}%`;
    if (speedEl)  speedEl.textContent   = speed_str   ?? '—';
    if (etaEl)    etaEl.textContent     = eta_str     ?? '—';
    if (frameEl)  frameEl.textContent   = frame !== undefined
                                          ? `Frame ${frame}` + (fps ? ` · ${fps} fps` : '')
                                          : '';
    if (statusEl && status) statusEl.textContent = status;
  }

  document.addEventListener('et:download:progress', (e) => {
    const d = e.detail;
    if (statusEl) statusEl.textContent = `Downloading ${d.percent ?? 0}%`;
    updateFooter({
      percent:   d.percent,
      speed_str: d.speed_str,
      eta_str:   d.eta ? `${d.eta}s` : '—',
    });
  });

  document.addEventListener('et:render:progress', (e) => {
    const d = e.detail;
    if (statusEl) statusEl.textContent = `Rendering ${d.percent ?? 0}%`;
    updateFooter({
      percent:   d.percent,
      speed_str: d.fps ? `${d.fps} fps` : '—',
      eta_str:   d.eta_str,
      frame:     d.frame,
      fps:       d.fps,
    });
  });

  document.addEventListener('et:download:done', () => {
    if (statusEl) statusEl.textContent = 'Download complete';
    updateFooter({ percent: 100 });
    setTimeout(() => updateFooter({ percent: 0, status: 'Ready' }), 3000);
  });

  document.addEventListener('et:render:done', () => {
    if (statusEl) statusEl.textContent = 'Render complete ✓';
    updateFooter({ percent: 100 });
    setTimeout(() => updateFooter({ percent: 0, status: 'Ready' }), 5000);
  });

  document.addEventListener('et:download:error', (e) => {
    if (statusEl) statusEl.textContent = `Error: ${e.detail.message}`;
    updateFooter({ percent: 0 });
  });

  document.addEventListener('et:render:error', (e) => {
    if (statusEl) statusEl.textContent = `Render error: ${e.detail.message}`;
    updateFooter({ percent: 0 });
  });
})();

/* ------------------------------------------------------------------ */
/* Log panel                                                            */
/* ------------------------------------------------------------------ */

(function initLogPanel() {
  const logPanel    = document.getElementById('log-panel');
  const logToggle   = document.getElementById('log-toggle-btn');
  const logContent  = document.getElementById('log-content');
  if (!logPanel || !logContent) return;

  // Toggle
  if (logToggle) {
    logToggle.addEventListener('click', () => {
      logPanel.classList.toggle('open');
      logToggle.querySelector('i')?.classList.toggle('bi-chevron-up');
      logToggle.querySelector('i')?.classList.toggle('bi-chevron-down');
    });
  }

  document.addEventListener('et:log', (e) => {
    const { message, level = 'info' } = e.detail;
    const ts = new Date().toLocaleTimeString();
    const line = document.createElement('div');
    line.className = `log-line ${level}`;
    line.innerHTML = `<span class="ts">${ts}</span>${escapeHtml(message)}`;
    logContent.appendChild(line);
    logContent.scrollTop = logContent.scrollHeight;

    // Auto-show on errors
    if (level === 'error') logPanel.classList.add('open');
  });
})();

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

/* Export for other modules */
window.ETProgress = { listenDownload, listenRender, listenBatchRender, log };
