/* Analyze panel — SSE streaming of workflow analysis results. */

const azEl = {
  panel: document.getElementById('analyze-panel'),
  content: document.getElementById('analyze-content'),
  meta: document.getElementById('analyze-meta'),
  btn: document.getElementById('analyze-btn'),
  overlay: document.getElementById('session-panel-overlay'),
};

let azAbort = null;
let azRawMd = '';
let azRenderPending = 0;

function azRender(immediate) {
  if (immediate) {
    cancelAnimationFrame(azRenderPending);
    azRenderPending = 0;
    const html = typeof marked !== 'undefined' ? marked.parse(azRawMd) : esc(azRawMd);
    azEl.content.innerHTML = typeof DOMPurify !== 'undefined'
      ? DOMPurify.sanitize(html)
      : esc(azRawMd);
    azEl.content.scrollTop = azEl.content.scrollHeight;
    return;
  }
  if (azRenderPending) return;
  azRenderPending = requestAnimationFrame(() => {
    azRenderPending = 0;
    azRender(true);
  });
}

function openAnalyzePanel(filepaths) {
  if (typeof closeSessionPanel === 'function') closeSessionPanel();

  azRawMd = '';
  azEl.content.innerHTML = '';
  azEl.meta.textContent = `${filepaths.length} session${filepaths.length > 1 ? 's' : ''}`;
  azEl.panel.classList.add('open');
  azEl.overlay.classList.add('open');
  azEl.btn.disabled = true;
  azEl.btn.textContent = 'Analyzing...';
  azEl.btn.classList.add('analyzing');

  azAbort = new AbortController();

  fetch('/api/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filepaths }),
    signal: azAbort.signal,
  }).then(res => {
    if (!res.ok) {
      return res.json().then(e => { throw new Error(e.detail || 'Request failed'); });
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    function processChunk() {
      return reader.read().then(({ done, value }) => {
        if (done) {
          azRender(true);
          azFinish();
          return;
        }
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();

        let eventType = '';
        for (const line of lines) {
          if (line.startsWith('event: ')) {
            eventType = line.slice(7).trim();
          } else if (line.startsWith('data: ')) {
            const data = line.slice(6);
            if (eventType === 'token') {
              try { azRawMd += JSON.parse(data); } catch {}
            } else if (eventType === 'error') {
              try {
                const err = JSON.parse(data);
                azRawMd += `\n\n**Error:** ${err.message}`;
              } catch {}
            } else if (eventType === 'done') {
              azRender(true);
              azFinish();
            }
            eventType = '';
          }
        }
        azRender();
        return processChunk();
      });
    }
    return processChunk();
  }).catch(err => {
    if (err.name !== 'AbortError') {
      azRawMd += `\n\n**Error:** ${err.message}`;
      azRender(true);
    }
    azFinish();
  });
}

function azFinish() {
  azEl.btn.classList.remove('analyzing');
  updateAnalyzeButton();
}

function closeAnalyzePanel() {
  azEl.panel.classList.remove('open');
  azEl.overlay.classList.remove('open');
  if (azAbort) {
    azAbort.abort();
    azAbort = null;
  }
  azFinish();
}

// Wire up button
azEl.btn.addEventListener('click', () => {
  const filepaths = [];
  for (const idx of state.selectedSessions) {
    const s = state.sortedSessions[idx];
    if (s) filepaths.push(s.filepath);
  }
  if (filepaths.length > 0) openAnalyzePanel(filepaths);
});

// Close button (overlay + Escape handled by session.js's single listener)
document.getElementById('analyze-panel-close').addEventListener('click', closeAnalyzePanel);
