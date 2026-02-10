const state = {
  selectedProjects: new Set(),
  datePreset: 'today',
  customStart: '',
  customEnd: '',
  sortColumn: 'date',
  sortDirection: 'desc',
  projectsData: {},
  statsData: null,
  chart: null,
};

const CHART_COLORS = ['#00ff41', '#e63946', '#ffa500', '#00d4ff', '#ff00ff', '#ffff00', '#7b68ee', '#ff6b6b'];

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function formatNumber(n) {
  if (n == null) return '-';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return n.toLocaleString();
}

function formatCost(n) {
  if (n == null) return '-';
  return '$' + n.toFixed(2);
}

function computeDateRange(preset) {
  const today = new Date();
  const fmt = d => d.toISOString().slice(0, 10);

  switch (preset) {
    case 'today': return { start: fmt(today), end: fmt(today) };
    case 'yesterday': {
      const y = new Date(today);
      y.setDate(y.getDate() - 1);
      return { start: fmt(y), end: fmt(y) };
    }
    case '7d': {
      const d = new Date(today);
      d.setDate(d.getDate() - 6);
      return { start: fmt(d), end: fmt(today) };
    }
    case '30d': {
      const d = new Date(today);
      d.setDate(d.getDate() - 29);
      return { start: fmt(d), end: fmt(today) };
    }
    case 'custom':
      return { start: state.customStart, end: state.customEnd };
    default:
      return { start: '', end: '' };
  }
}

async function fetchProjects() {
  const res = await fetch('/api/projects');
  state.projectsData = await res.json();
  const list = document.getElementById('project-list');
  const entries = Object.values(state.projectsData).sort((a, b) => a.name.localeCompare(b.name));

  document.getElementById('project-count').textContent = entries.length;
  state.selectedProjects = new Set();

  list.innerHTML = entries.map(p => `
    <label class="project-item" data-folder="${esc(p.folder)}">
      <input type="checkbox">
      <span class="name" title="${esc(p.folder)}">${esc(p.name)}</span>
      <span class="count">${p.sessions}</span>
    </label>
  `).join('');

  list.querySelectorAll('.project-item').forEach(item => {
    const cb = item.querySelector('input');
    const folder = item.dataset.folder;
    cb.addEventListener('change', () => {
      if (cb.checked) {
        state.selectedProjects.add(folder);
        item.classList.add('active');
      } else {
        state.selectedProjects.delete(folder);
        item.classList.remove('active');
      }
      fetchStats();
    });
  });

  // Restore claude toggle from previous session
  if (localStorage.getItem('lain-hide-claude') === '1') {
    applyClaudeToggle(true);
  }
}

async function fetchStats() {
  const { start, end } = computeDateRange(state.datePreset);
  const projects = [...state.selectedProjects].join(',');
  const params = new URLSearchParams();
  if (projects) params.set('projects', projects);
  if (start) params.set('start', start);
  if (end) params.set('end', end);

  const res = await fetch('/api/stats?' + params);
  state.statsData = await res.json();

  renderCards(state.statsData);
  renderChart(state.statsData);
  renderTable(state.statsData.sessions_list);
}

function renderCards(stats) {
  document.getElementById('stat-calls').textContent = formatNumber(stats.api_calls);
  document.getElementById('stat-sessions').textContent = formatNumber(stats.sessions);
  document.getElementById('stat-input').textContent = formatNumber(stats.input_tokens);
  document.getElementById('stat-output').textContent = formatNumber(stats.output_tokens);
  document.getElementById('stat-cost').textContent = formatCost(stats.cost);
}

function renderChart(stats) {
  const models = Object.entries(stats.models).sort((a, b) => b[1] - a[1]);
  const total = models.reduce((s, [, c]) => s + c, 0);
  const canvas = document.getElementById('model-chart');
  const legend = document.getElementById('chart-legend');

  if (state.chart) state.chart.destroy();

  if (!models.length) {
    canvas.style.display = 'none';
    legend.innerHTML = '<div class="loading">No data</div>';
    return;
  }
  canvas.style.display = 'block';

  state.chart = new Chart(canvas, {
    type: 'doughnut',
    data: {
      labels: models.map(([m]) => m),
      datasets: [{
        data: models.map(([, c]) => c),
        backgroundColor: models.map((_, i) => CHART_COLORS[i % CHART_COLORS.length]),
        borderWidth: 0,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      cutout: '65%',
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: () => getComputedStyle(document.documentElement).getPropertyValue('--tooltip-bg').trim(),
          titleColor: () => getComputedStyle(document.documentElement).getPropertyValue('--text-primary').trim(),
          bodyColor: () => getComputedStyle(document.documentElement).getPropertyValue('--text-primary').trim(),
          titleFont: { family: 'Share Tech Mono' },
          bodyFont: { family: 'Share Tech Mono' },
        },
      },
    },
  });

  legend.innerHTML = models.map(([m, c], i) => {
    const pct = total ? (c / total * 100).toFixed(1) : 0;
    const color = CHART_COLORS[i % CHART_COLORS.length];
    return `<div class="legend-item">
      <span class="legend-color" style="background:${color}"></span>
      <span class="legend-name">${esc(m)}</span>
      <span class="legend-count">${formatNumber(c)}</span>
      <span class="legend-pct">${pct}%</span>
    </div>`;
  }).join('');
}

function renderTable(sessions) {
  const sorted = [...sessions].sort((a, b) => {
    const col = state.sortColumn;
    let va = a[col], vb = b[col];
    if (col === 'models') {
      va = Object.keys(a.models).join(',');
      vb = Object.keys(b.models).join(',');
    }
    if (va == null) va = '';
    if (vb == null) vb = '';
    const cmp = typeof va === 'number' ? va - vb : String(va).localeCompare(String(vb));
    return state.sortDirection === 'asc' ? cmp : -cmp;
  });

  const tbody = document.getElementById('sessions-body');
  tbody.innerHTML = sorted.map(s => `<tr>
    <td>${esc(s.date || '-')}</td>
    <td>${esc(s.project || '-')}</td>
    <td class="title-cell" title="${esc(s.title || s.session_id)}">${esc(s.title || s.session_id.slice(0, 8))}</td>
    <td class="models-cell">${Object.keys(s.models).map(m => esc(m.split('-').slice(1, 3).join('-'))).join(', ')}</td>
    <td class="num">${formatNumber(s.api_calls)}</td>
    <td class="num">${formatNumber(s.input_tokens)}</td>
    <td class="num">${formatNumber(s.output_tokens)}</td>
    <td class="num">${formatNumber(s.cache_read_tokens)}</td>
    <td class="num">${formatNumber(s.cache_create_tokens)}</td>
    <td class="num cost-cell">${formatCost(s.cost)}</td>
  </tr>`).join('');

  // Update sort arrows
  document.querySelectorAll('#sessions-table th').forEach(th => {
    const arrow = th.querySelector('.sort-arrow');
    if (th.dataset.col === state.sortColumn) {
      arrow.textContent = state.sortDirection === 'asc' ? ' \u25b2' : ' \u25bc';
    } else {
      arrow.textContent = '';
    }
  });
}

// Event listeners — select/deselect all buttons (user requested split)
function setAllProjects(selected) {
  document.querySelectorAll('.project-item').forEach(item => {
    if (item.classList.contains('hidden')) return;
    const cb = item.querySelector('input');
    cb.checked = selected;
    if (selected) {
      state.selectedProjects.add(item.dataset.folder);
      item.classList.add('active');
    } else {
      state.selectedProjects.delete(item.dataset.folder);
      item.classList.remove('active');
    }
  });
  fetchStats();
}

function updateProjectCount() {
  const visible = document.querySelectorAll('.project-item:not(.hidden)').length;
  document.getElementById('project-count').textContent = visible;
}

function applyClaudeToggle(active) {
  const btn = document.getElementById('hide-claude');
  btn.classList.toggle('active', active);
  document.querySelectorAll('.project-item').forEach(item => {
    const folder = item.dataset.folder;
    if (folder.startsWith('-home-') && folder.includes('--claude')) {
      item.classList.toggle('hidden', active);
      if (active) {
        // Deselect hidden items
        const cb = item.querySelector('input');
        cb.checked = false;
        state.selectedProjects.delete(folder);
        item.classList.remove('active');
      }
    }
  });
  updateProjectCount();
}

document.getElementById('hide-claude').addEventListener('click', () => {
  const btn = document.getElementById('hide-claude');
  const nowActive = !btn.classList.contains('active');
  applyClaudeToggle(nowActive);
  if (nowActive) {
    localStorage.setItem('lain-hide-claude', '1');
  } else {
    localStorage.removeItem('lain-hide-claude');
  }
  fetchStats();
});

document.getElementById('select-all').addEventListener('click', () => setAllProjects(true));
document.getElementById('deselect-all').addEventListener('click', () => setAllProjects(false));

document.querySelectorAll('.preset-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.preset-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.datePreset = btn.dataset.preset;
    const custom = document.getElementById('date-custom');
    custom.classList.toggle('visible', state.datePreset === 'custom');
    if (state.datePreset !== 'custom') fetchStats();
  });
});

document.getElementById('apply-dates').addEventListener('click', () => {
  state.customStart = document.getElementById('date-start').value;
  state.customEnd = document.getElementById('date-end').value;
  fetchStats();
});

document.querySelectorAll('#sessions-table th').forEach(th => {
  th.addEventListener('click', () => {
    const col = th.dataset.col;
    if (state.sortColumn === col) {
      state.sortDirection = state.sortDirection === 'asc' ? 'desc' : 'asc';
    } else {
      state.sortColumn = col;
      state.sortDirection = 'desc';
    }
    if (state.statsData) renderTable(state.statsData.sessions_list);
  });
});

document.getElementById('sidebar-toggle').addEventListener('click', () => {
  document.getElementById('sidebar').classList.toggle('open');
});

// Project search — respects claude toggle
document.getElementById('project-search').addEventListener('input', (e) => {
  const query = e.target.value.toLowerCase();
  const claudeHidden = document.getElementById('hide-claude').classList.contains('active');
  document.querySelectorAll('.project-item').forEach(item => {
    const name = item.querySelector('.name').textContent.toLowerCase();
    const folder = item.dataset.folder.toLowerCase();
    const matchesSearch = !query || name.includes(query) || folder.includes(query);
    const isClaude = folder.startsWith('-home-') && folder.includes('--claude');
    item.classList.toggle('hidden', !matchesSearch || (claudeHidden && isClaude));
  });
  updateProjectCount();
});

// Theme toggle — init handled by inline script in <head> to prevent flash
document.getElementById('theme-toggle').addEventListener('click', () => {
  const root = document.documentElement;
  const next = root.getAttribute('data-theme') === 'light' ? '' : 'light';
  if (next) {
    root.setAttribute('data-theme', next);
    localStorage.setItem('lain-theme', next);
  } else {
    root.removeAttribute('data-theme');
    localStorage.removeItem('lain-theme');
  }
  // Re-render chart so tooltip colors pick up new vars
  if (state.statsData) renderChart(state.statsData);
});

// Sidebar resize
(() => {
  const handle = document.getElementById('sidebar-resize');
  const sidebar = document.getElementById('sidebar');
  const main = document.querySelector('.main');
  let dragging = false;

  handle.addEventListener('mousedown', (e) => {
    e.preventDefault();
    dragging = true;
    handle.classList.add('dragging');
    document.body.classList.add('sidebar-resizing');
  });

  document.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const width = Math.max(200, Math.min(600, e.clientX));
    sidebar.style.width = width + 'px';
    main.style.marginLeft = width + 'px';
  });

  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove('dragging');
    document.body.classList.remove('sidebar-resizing');
  });
})();

// Init
fetchProjects().then(() => fetchStats());
