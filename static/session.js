/* Session detail panel — event timeline viewer for individual sessions. */

const sessionPanel = {
  file: '',
  lastCount: 0,
  allEvents: [],
  pollTimer: null,
};

// DOM refs — cached once at parse time
const spEl = {
  panel: document.getElementById('session-panel'),
  overlay: document.getElementById('session-panel-overlay'),
  events: document.getElementById('sp-events'),
  name: document.getElementById('sp-name'),
  meta: document.getElementById('sp-meta'),
  summary: document.getElementById('sp-summary'),
  autoScroll: document.getElementById('sp-auto-scroll'),
  showThinking: document.getElementById('sp-show-thinking'),
  showToolResults: document.getElementById('sp-show-tool-results'),
  filterType: document.getElementById('sp-filter-type'),
};

function spFormatTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function spExtractContent(event) {
  const parts = [];
  const msg = event.message;

  if (!msg) {
    if (event.type === 'queue-operation') {
      parts.push(`<span class="sp-text">${esc(event.operation || '')}: ${esc(event.content || '')}</span>`);
    }
    if (event.type === 'progress' && event.data) {
      parts.push(`<span class="sp-text">[${esc(event.data.hookEvent || '')}] ${esc(event.data.command || '')}</span>`);
    }
    return parts.join('');
  }

  const content = msg.content;
  if (!content) return '';

  if (typeof content === 'string') {
    parts.push(`<div class="sp-text">${esc(content)}</div>`);
    return parts.join('');
  }

  if (Array.isArray(content)) {
    for (const block of content) {
      if (block.type === 'text' && block.text) {
        parts.push(`<div class="sp-text">${esc(block.text)}</div>`);
      }
      if (block.type === 'thinking' && block.thinking) {
        const id = 'spthink-' + Math.random().toString(36).slice(2);
        const expanded = spEl.showThinking.checked ? ' open' : '';
        parts.push(`<div class="sp-thinking">
          <span class="sp-thinking-toggle" onclick="document.getElementById('${id}').classList.toggle('open')">
            [thinking...]
          </span>
          <div class="sp-thinking-body${expanded}" id="${id}">${esc(block.thinking)}</div>
        </div>`);
      }
      if (block.type === 'tool_use') {
        let inputStr;
        try { inputStr = JSON.stringify(block.input, null, 2); } catch { inputStr = String(block.input); }
        parts.push(`<div class="sp-tool-call">
          <span class="sp-tool-name">${esc(block.name)}</span>
          <div class="sp-tool-input">${esc(inputStr)}</div>
        </div>`);
      }
      if (block.type === 'tool_result') {
        const isErr = block.is_error;
        let resultText = '';
        if (typeof block.content === 'string') {
          resultText = block.content;
        } else if (Array.isArray(block.content)) {
          resultText = block.content.map(c => c.text || c.tool_name || '').join('\n');
        }
        if (resultText) {
          const lines = resultText.split('\n');
          const truncated = lines.length > 30
            ? lines.slice(0, 30).join('\n') + `\n... (${lines.length - 30} more lines)`
            : resultText;
          parts.push(`<div class="sp-tool-result ${isErr ? 'sp-error' : ''}" data-sp-tool-result>
            ${esc(truncated)}
          </div>`);
        }
      }
    }
  }
  return parts.join('');
}

function spRenderEvent(event, idx) {
  const type = event.type || 'unknown';
  const ts = event.timestamp;
  const model = event.message?.model || '';
  const stopReason = event.message?.stop_reason || '';
  const usage = event.message?.usage;

  let usageStr = '';
  if (usage && usage.output_tokens) {
    usageStr = `out:${usage.output_tokens}`;
    if (usage.cache_read_input_tokens) usageStr += ` cache:${usage.cache_read_input_tokens}`;
  }

  const content = spExtractContent(event);
  if (!content) return '';

  return `<div class="sp-event sp-type-${esc(type)}" data-sp-type="${esc(type)}" data-sp-idx="${idx}">
    <div class="sp-event-header">
      <span class="sp-event-type">${esc(type)}</span>
      <span class="sp-timestamp">${spFormatTime(ts)}</span>
      ${model ? `<span class="sp-model">${esc(model)}</span>` : ''}
      ${stopReason ? `<span class="sp-model">[${esc(stopReason)}]</span>` : ''}
      ${usageStr ? `<span class="sp-usage">${esc(usageStr)}</span>` : ''}
    </div>
    <div class="sp-event-body">${content}</div>
  </div>`;
}

function spApplyFilters() {
  const filter = spEl.filterType.value;
  const showThinking = spEl.showThinking.checked;
  const showToolResults = spEl.showToolResults.checked;

  spEl.events.querySelectorAll('.sp-event').forEach(el => {
    const type = el.dataset.spType;
    let visible = true;
    if (filter === 'assistant' && type !== 'assistant') visible = false;
    if (filter === 'user' && type !== 'user') visible = false;
    if (filter === 'tools') {
      const hasTool = el.querySelector('.sp-tool-call') || el.querySelector('.sp-tool-result');
      if (!hasTool) visible = false;
    }
    el.classList.toggle('sp-hidden', !visible);
  });

  spEl.events.querySelectorAll('.sp-thinking').forEach(el => {
    el.style.display = showThinking ? '' : 'none';
    el.querySelector('.sp-thinking-body')?.classList.toggle('open', showThinking);
  });
  spEl.events.querySelectorAll('[data-sp-tool-result]').forEach(el => {
    el.style.display = showToolResults ? '' : 'none';
  });
}

function spUpdateSummary() {
  const counts = { assistant: 0, user: 0, tools: 0 };
  for (const e of sessionPanel.allEvents) {
    if (e.type === 'assistant') counts.assistant++;
    if (e.type === 'user') counts.user++;
    if (e.message?.content && Array.isArray(e.message.content)) {
      for (const b of e.message.content) {
        if (b.type === 'tool_use') counts.tools++;
      }
    }
  }
  spEl.summary.innerHTML =
    `<span class="sp-stat"><span class="sp-dot" style="background:var(--accent-cyan)"></span> Assistant: ${counts.assistant}</span>
     <span class="sp-stat"><span class="sp-dot" style="background:var(--accent-green)"></span> User: ${counts.user}</span>
     <span class="sp-stat"><span class="sp-dot" style="background:var(--accent-amber)"></span> Tool calls: ${counts.tools}</span>
     <span class="sp-stat">${sessionPanel.allEvents.length} total events</span>`;
}

async function spPoll() {
  if (!sessionPanel.file) return;
  try {
    const res = await fetch(`/api/session/events?file=${encodeURIComponent(sessionPanel.file)}&after=${sessionPanel.lastCount}`);
    if (!res.ok) return;
    const data = await res.json();
    if (data.events && data.events.length > 0) {
      for (const ev of data.events) {
        sessionPanel.allEvents.push(ev);
        const html = spRenderEvent(ev, sessionPanel.allEvents.length - 1);
        if (html) spEl.events.insertAdjacentHTML('beforeend', html);
      }
      sessionPanel.lastCount = data.total;
      spApplyFilters();
      spUpdateSummary();
      if (spEl.autoScroll.checked) {
        spEl.events.scrollTop = spEl.events.scrollHeight;
      }
    }
  } catch (e) { console.error('Session poll error:', e); }
}

function openSessionPanel(session) {
  sessionPanel.file = session.filepath;
  sessionPanel.lastCount = 0;
  sessionPanel.allEvents = [];
  spEl.events.innerHTML = '';
  spEl.autoScroll.checked = true;
  spEl.showThinking.checked = false;
  spEl.showToolResults.checked = true;
  spEl.filterType.value = 'all';

  spEl.name.textContent = session.title || session.session_id.slice(0, 12);
  spEl.meta.textContent =
    `${session.project || ''} · ${session.date || ''} · ${Object.keys(session.models).map(shortModelName).join(', ')}`;

  spEl.panel.classList.add('open');
  spEl.overlay.classList.add('open');

  if (sessionPanel.pollTimer) clearInterval(sessionPanel.pollTimer);
  spPoll();
  sessionPanel.pollTimer = setInterval(spPoll, 2000);
}

function closeSessionPanel() {
  if (sessionPanel.pollTimer) {
    clearInterval(sessionPanel.pollTimer);
    sessionPanel.pollTimer = null;
  }
  spEl.panel.classList.remove('open');
  spEl.overlay.classList.remove('open');
}

// Event listeners
document.getElementById('session-panel-close').addEventListener('click', closeSessionPanel);
spEl.overlay.addEventListener('click', closeSessionPanel);
spEl.filterType.addEventListener('change', spApplyFilters);
spEl.showThinking.addEventListener('change', spApplyFilters);
spEl.showToolResults.addEventListener('change', spApplyFilters);

document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && spEl.panel.classList.contains('open')) {
    closeSessionPanel();
  }
});
