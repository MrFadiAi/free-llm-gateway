/* Free LLM Gateway — Dashboard Application */
(function() {
  'use strict';

  var REFRESH_MS = 10000;
  var refreshTimer = null;
  var currentTab = 'models';
  var cachedStatus = null;

  // ── Init ─────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', function() {
    setupNavigation();
    setupMobileMenu();
    setupKeyForm();
    loadStatus();
    startAutoRefresh();
  });

  // ── Navigation ───────────────────────────────────────────────
  function setupNavigation() {
    document.querySelectorAll('.nav-item').forEach(function(item) {
      item.addEventListener('click', function() {
        switchTab(this.dataset.tab);
      });
    });
  }

  function switchTab(tab) {
    currentTab = tab;
    document.querySelectorAll('.nav-item').forEach(function(el) {
      el.classList.toggle('active', el.dataset.tab === tab);
    });
    document.querySelectorAll('.tab-panel').forEach(function(el) {
      el.classList.toggle('active', el.id === 'tab-' + tab);
    });
  }

  // ── Mobile ───────────────────────────────────────────────────
  function setupMobileMenu() {
    var hamburger = document.getElementById('hamburger');
    var sidebar = document.getElementById('sidebar');
    var overlay = document.getElementById('overlay');
    if (hamburger) {
      hamburger.addEventListener('click', function() {
        sidebar.classList.toggle('open');
        overlay.classList.toggle('open');
      });
    }
    if (overlay) {
      overlay.addEventListener('click', function() {
        sidebar.classList.remove('open');
        overlay.classList.remove('open');
      });
    }
  }

  // ── Auto-refresh ─────────────────────────────────────────────
  function startAutoRefresh() {
    refreshTimer = setInterval(loadStatus, REFRESH_MS);
  }

  async function loadStatus() {
    try {
      var resp = await fetch('/api/status');
      if (!resp.ok) return;
      cachedStatus = await resp.json();
      renderAll(cachedStatus);
      updateLiveDot(true);
    } catch(e) {
      updateLiveDot(false);
    }
  }

  function updateLiveDot(ok) {
    var dot = document.getElementById('live-dot');
    if (dot) dot.style.background = ok ? '#3fb950' : '#f85149';
  }

  // ── Render all ───────────────────────────────────────────────
  function renderAll(data) {
    renderStats(data);
    renderModels(data.models || []);
    renderProviders(data);
    renderUsage(data);
    renderCacheQueue(data);
    renderLogs(data.logs || []);
    loadKeys();
  }

  function renderStats(data) {
    var models = (data.models || []).length;
    var providers = 0;
    (data.models || []).forEach(function(m) { if (m.active_provider) providers++; });
    setText('stat-models', models);
    setText('stat-providers', providers);

    var usage = data.usage || {};
    var today = usage.today || {};
    setText('stat-requests', today.requests || 0);
    setText('stat-tokens', (today.total_tokens || 0).toLocaleString() + ' tokens');

    var savings = (usage.estimated_savings || {}).today_usd || 0;
    setText('stat-savings', '$' + savings.toFixed(4));

    var cache = data.cache || {};
    var hitRate = cache.hit_rate || 0;
    setText('stat-cache-rate', (hitRate * 100).toFixed(1) + '%');
  }

  // ── Models Tab ───────────────────────────────────────────────
  function renderModels(models) {
    var tbody = document.getElementById('models-tbody');
    if (!tbody) return;
    clearEl(tbody);
    if (!models.length) {
      addEmptyRow(tbody, 4, 'No models configured');
      return;
    }
    models.forEach(function(m) {
      var tr = document.createElement('tr');

      var td1 = document.createElement('td');
      var dot = document.createElement('span');
      dot.className = 'status-dot ' + (m.active_provider ? 'ok' : 'off');
      td1.appendChild(dot);
      td1.appendChild(document.createTextNode(' '));
      var code = document.createElement('code');
      code.textContent = m.name;
      td1.appendChild(code);
      tr.appendChild(td1);

      var td2 = document.createElement('td');
      td2.appendChild(makeTag(m.active_provider ? m.active_provider.provider : 'unavailable',
        m.active_provider ? 'tag-green' : 'tag-red'));
      tr.appendChild(td2);

      var td3 = document.createElement('td');
      var wrap = document.createElement('div');
      wrap.className = 'providers-cell';
      m.providers.forEach(function(p) {
        var cls, label;
        if (p.available) { cls = 'tag-blue'; label = p.provider; }
        else if (p.rate_limited) { cls = 'tag-yellow'; label = p.provider + ' (limited)'; }
        else if (!p.has_key) { cls = 'tag-gray'; label = p.provider; }
        else { cls = 'tag-gray'; label = p.provider; }
        var tag = makeTag(label, cls);
        tag.title = p.model;
        wrap.appendChild(tag);
      });
      td3.appendChild(wrap);
      tr.appendChild(td3);

      var td4 = document.createElement('td');
      td4.textContent = m.providers.length;
      tr.appendChild(td4);

      tbody.appendChild(tr);
    });
  }

  // ── Providers Tab ────────────────────────────────────────────
  function renderProviders(data) {
    var grid = document.getElementById('provider-grid');
    if (!grid) return;
    clearEl(grid);
    var known = ['openrouter','github','groq','cerebras','cloudflare',
      'huggingface','nvidia','siliconflow','cohere','google_gemini',
      'mistral','kilo','llm7','ollama'];
    var rl = data.rate_limits || {};
    var health = data.health || {};
    var provInfo = data.providers || {};

    known.forEach(function(name) {
      var info = rl[name];
      var h = health[name] || {};
      var pi = provInfo[name] || {};
      var limited = info ? info.limited : false;
      var hasAct = info && (info.rpm_used > 0 || info.rpd_used > 0);
      var isUp = h.status === 'up';
      var isDown = h.status === 'down';

      var statusLabel, statusTag;
      if (limited) { statusLabel = 'Rate Limited'; statusTag = 'tag-yellow'; }
      else if (isDown) { statusLabel = 'Down'; statusTag = 'tag-red'; }
      else if (isUp) { statusLabel = 'Healthy'; statusTag = 'tag-green'; }
      else if (hasAct) { statusLabel = 'Active'; statusTag = 'tag-green'; }
      else { statusLabel = 'Idle'; statusTag = 'tag-gray'; }

      var card = document.createElement('div');
      card.className = 'provider-card';

      var header = document.createElement('div');
      header.className = 'card-header';
      var h3 = document.createElement('h3');
      h3.textContent = name;
      header.appendChild(h3);
      header.appendChild(makeTag(statusLabel, statusTag));
      card.appendChild(header);

      var meta = document.createElement('div');
      meta.className = 'card-meta';

      // Health info
      if (h.latency_ms) {
        var latLine = document.createElement('div');
        latLine.textContent = 'Latency: ' + Math.round(h.latency_ms) + 'ms';
        if (h.last_error) latLine.title = h.last_error;
        meta.appendChild(latLine);
      }

      // Rate limits
      if (info) {
        var rlLine = document.createElement('div');
        rlLine.textContent = 'RPM: ' + info.rpm_used + '/' + (info.rpm_limit > 0 ? info.rpm_limit : '\u221E') +
          '  \u00B7  RPD: ' + info.rpd_used + '/' + (info.rpd_limit > 0 ? info.rpd_limit : '\u221E');
        meta.appendChild(rlLine);
      }

      // Key info
      var keyLine = document.createElement('div');
      var totalKeys = pi.total_keys || 0;
      var hasKey = pi.has_key || false;
      if (hasKey) {
        keyLine.textContent = 'Keys: ' + totalKeys + ' configured';
        if (totalKeys > 1) keyLine.textContent += ' (active #' + (pi.active_key_index + 1) + ')';
      } else {
        keyLine.textContent = 'No API key set';
        keyLine.style.color = '#f85149';
      }
      meta.appendChild(keyLine);

      if (!hasKey && !hasAct && h.status !== 'up') {
        var noData = document.createElement('div');
        noData.textContent = 'Not configured';
        noData.style.color = '#484f58';
        meta.appendChild(noData);
      }

      card.appendChild(meta);
      grid.appendChild(card);
    });
  }

  // ── Usage Tab ────────────────────────────────────────────────
  function renderUsage(data) {
    var usage = data.usage || {};
    var today = usage.today || {};
    var week = usage.week || {};
    var allTime = usage.all_time || {};
    var savings = usage.estimated_savings || {};

    var summary = document.getElementById('usage-summary');
    if (summary) {
      clearEl(summary);
      var items = [
        { label: 'Today Requests', value: today.requests || 0 },
        { label: 'Today Tokens', value: (today.total_tokens || 0).toLocaleString() },
        { label: 'Week Tokens', value: (week.total_tokens || 0).toLocaleString() },
        { label: 'All-Time Tokens', value: (allTime.total_tokens || 0).toLocaleString() },
        { label: 'Savings Today', value: '$' + (savings.today_usd || 0).toFixed(4), cls: 'savings' },
        { label: 'Savings All-Time', value: '$' + (savings.all_time_usd || 0).toFixed(4), cls: 'savings' },
      ];
      items.forEach(function(item) {
        var card = document.createElement('div');
        card.className = 'stat-card';
        var lbl = document.createElement('div');
        lbl.className = 'label';
        lbl.textContent = item.label;
        card.appendChild(lbl);
        var val = document.createElement('div');
        val.className = 'value' + (item.cls ? ' ' + item.cls : '');
        val.style.fontSize = '20px';
        val.textContent = item.value;
        card.appendChild(val);
        summary.appendChild(card);
      });
    }

    var modelTable = document.getElementById('usage-models-table');
    if (modelTable) {
      clearEl(modelTable);
      var byModel = today.by_model || {};
      var modelNames = Object.keys(byModel);
      if (modelNames.length) {
        var table = document.createElement('table');
        table.style.marginTop = '16px';

        var thead = document.createElement('thead');
        var headRow = document.createElement('tr');
        ['Model','Requests','Prompt Tokens','Completion Tokens','Total Tokens'].forEach(function(h) {
          var th = document.createElement('th');
          th.textContent = h;
          headRow.appendChild(th);
        });
        thead.appendChild(headRow);
        table.appendChild(thead);

        var tbody = document.createElement('tbody');
        modelNames.forEach(function(name) {
          var d = byModel[name];
          var tr = document.createElement('tr');
          appendCodeCell(tr, name);
          appendCell(tr, d.requests || 0);
          appendCell(tr, (d.prompt_tokens || 0).toLocaleString());
          appendCell(tr, (d.completion_tokens || 0).toLocaleString());
          appendCell(tr, (d.total_tokens || 0).toLocaleString());
          tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        modelTable.appendChild(table);
      }
    }

    var container = document.getElementById('usage-chart');
    if (!container) return;
    clearEl(container);
    var logs = data.logs || [];
    if (!logs.length) {
      var empty = document.createElement('div');
      empty.className = 'empty';
      empty.textContent = 'No usage data yet';
      container.appendChild(empty);
      return;
    }

    var byProv = {};
    logs.forEach(function(l) {
      if (!byProv[l.provider]) byProv[l.provider] = { ok: 0, fail: 0 };
      if (l.success) byProv[l.provider].ok++; else byProv[l.provider].fail++;
    });
    var maxVal = 1;
    Object.values(byProv).forEach(function(v) { var t = v.ok + v.fail; if (t > maxVal) maxVal = t; });
    var colors = ['#3fb950','#58a6ff','#d29922','#f85149','#bc8cff','#79c0ff','#56d364','#e3b341'];

    var title = document.createElement('h3');
    title.textContent = 'Requests by Provider';
    title.style.cssText = 'font-size:14px;color:#f0f6fc;margin-bottom:16px';
    container.appendChild(title);

    container.appendChild(makeBarChart(Object.keys(byProv).map(function(name, i) {
      var v = byProv[name];
      return { label: name, value: v.ok + v.fail, color: colors[i % colors.length] };
    }), maxVal));

    var latTitle = document.createElement('h3');
    latTitle.textContent = 'Recent Latency (ms)';
    latTitle.style.cssText = 'font-size:14px;color:#f0f6fc;margin:24px 0 16px';
    container.appendChild(latTitle);

    var recent = logs.slice(0, 30).reverse();
    var maxLat = 1;
    recent.forEach(function(l) { if (l.latency_ms > maxLat) maxLat = l.latency_ms; });
    container.appendChild(makeBarChart(recent.map(function(l) {
      return { value: Math.round(l.latency_ms), color: l.success ? '#3fb950' : '#f85149' };
    }), maxLat));
  }

  // ── Cache & Queue Tab ────────────────────────────────────────
  function renderCacheQueue(data) {
    var cache = data.cache || {};
    var queue = data.queue || {};

    var cacheSection = document.getElementById('cache-section');
    if (cacheSection) {
      clearEl(cacheSection);
      var title = document.createElement('h3');
      title.textContent = 'Response Cache';
      title.style.cssText = 'font-size:16px;color:#f0f6fc;margin-bottom:12px';
      cacheSection.appendChild(title);

      var miniStats = document.createElement('div');
      miniStats.className = 'mini-stats';
      [
        { label: 'Size', value: cache.size + '/' + cache.max_size },
        { label: 'Hits', value: cache.hits || 0 },
        { label: 'Misses', value: cache.misses || 0 },
        { label: 'Hit Rate', value: ((cache.hit_rate || 0) * 100).toFixed(1) + '%' },
        { label: 'TTL', value: (cache.ttl_seconds || 1800) + 's' },
      ].forEach(function(item) {
        var card = document.createElement('div');
        card.className = 'stat-card';
        var lbl = document.createElement('div');
        lbl.className = 'label';
        lbl.textContent = item.label;
        card.appendChild(lbl);
        var val = document.createElement('div');
        val.className = 'value';
        val.style.fontSize = '20px';
        val.textContent = item.value;
        card.appendChild(val);
        miniStats.appendChild(card);
      });
      cacheSection.appendChild(miniStats);

      var btnWrap = document.createElement('div');
      btnWrap.style.cssText = 'margin-top:12px';
      var clearBtn = document.createElement('button');
      clearBtn.className = 'btn btn-danger';
      clearBtn.textContent = 'Clear Cache';
      clearBtn.addEventListener('click', async function() {
        if (!confirm('Clear all cached responses?')) return;
        try {
          var resp = await fetch('/api/cache', { method: 'DELETE' });
          if (resp.ok) { var r = await resp.json(); alert('Cleared ' + r.cleared + ' entries'); loadStatus(); }
        } catch(e) { alert('Error: ' + e.message); }
      });
      btnWrap.appendChild(clearBtn);
      cacheSection.appendChild(btnWrap);
    }

    var queueSection = document.getElementById('queue-section');
    if (queueSection) {
      clearEl(queueSection);
      var qTitle = document.createElement('h3');
      qTitle.textContent = 'Request Queue';
      qTitle.style.cssText = 'font-size:16px;color:#f0f6fc;margin:24px 0 12px';
      queueSection.appendChild(qTitle);

      var qStats = document.createElement('div');
      qStats.className = 'mini-stats';
      [
        { label: 'Queue Depth', value: queue.queue_depth || 0 },
        { label: 'Total Queued', value: queue.total_queued || 0 },
        { label: 'Completed', value: queue.total_completed || 0 },
        { label: 'Failed', value: queue.total_failed || 0 },
        { label: 'Workers', value: queue.workers || 0 },
        { label: 'Max Wait', value: (queue.max_wait_seconds || 120) + 's' },
      ].forEach(function(item) {
        var card = document.createElement('div');
        card.className = 'stat-card';
        var lbl = document.createElement('div');
        lbl.className = 'label';
        lbl.textContent = item.label;
        card.appendChild(lbl);
        var val = document.createElement('div');
        val.className = 'value';
        val.style.fontSize = '20px';
        val.textContent = item.value;
        card.appendChild(val);
        qStats.appendChild(card);
      });
      queueSection.appendChild(qStats);
    }
  }

  function makeBarChart(items, maxVal) {
    var wrap = document.createElement('div');
    wrap.className = 'bar-chart';
    items.forEach(function(item) {
      var col = document.createElement('div');
      col.className = 'bar-col';
      var valEl = document.createElement('div');
      valEl.className = 'bar-value';
      valEl.textContent = item.value;
      col.appendChild(valEl);
      var bar = document.createElement('div');
      bar.className = 'bar';
      bar.style.height = Math.round((item.value / maxVal) * 100) + '%';
      bar.style.background = item.color || '#58a6ff';
      col.appendChild(bar);
      if (item.label) {
        var lbl = document.createElement('div');
        lbl.className = 'bar-label';
        lbl.textContent = item.label;
        col.appendChild(lbl);
      }
      wrap.appendChild(col);
    });
    return wrap;
  }

  // ── Logs Tab ─────────────────────────────────────────────────
  function renderLogs(logs) {
    var tbody = document.getElementById('logs-tbody');
    if (!tbody) return;
    clearEl(tbody);
    if (!logs.length) {
      addEmptyRow(tbody, 7, 'No requests yet');
      return;
    }
    logs.forEach(function(l) {
      var tr = document.createElement('tr');
      appendCell(tr, l.time_str);
      appendCodeCell(tr, l.model);
      appendCell(tr, l.provider);
      appendCodeCell(tr, l.provider_model);
      appendCell(tr, Math.round(l.latency_ms) + 'ms');
      appendCell(tr, l.tokens ? (l.tokens.total_tokens || '-').toLocaleString() : '-');
      var statusEl = document.createElement('span');
      statusEl.className = l.success ? 'log-ok' : 'log-fail';
      statusEl.textContent = l.success ? 'OK' : 'FAIL';
      if (!l.success && l.error) statusEl.title = l.error;
      var td = document.createElement('td');
      td.appendChild(statusEl);
      tr.appendChild(td);
      tbody.appendChild(tr);
    });
  }

  // ── Keys Tab ─────────────────────────────────────────────────
  function setupKeyForm() {
    var form = document.getElementById('key-form');
    if (!form) return;
    form.addEventListener('submit', async function(e) {
      e.preventDefault();
      var provider = document.getElementById('key-provider').value;
      var key = document.getElementById('key-value').value.trim();
      if (!provider || !key) return;
      try {
        var resp = await fetch('/api/keys', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ provider: provider, key: key })
        });
        if (resp.ok) {
          document.getElementById('key-value').value = '';
          loadKeys();
        } else {
          var result = await resp.json();
          alert(result.detail || 'Failed to add key');
        }
      } catch(err) { alert('Error: ' + err.message); }
    });
  }

  async function loadKeys() {
    try {
      var resp = await fetch('/api/keys');
      if (!resp.ok) return;
      var data = await resp.json();
      renderKeys(data.keys || {});
    } catch(e) {}
  }

  function renderKeys(keys) {
    var container = document.getElementById('keys-list');
    if (!container) return;
    clearEl(container);
    var names = Object.keys(keys);
    if (!names.length) {
      var empty = document.createElement('div');
      empty.className = 'empty';
      empty.textContent = 'No API keys stored. Add one above.';
      container.appendChild(empty);
      return;
    }
    names.forEach(function(provider) {
      var entries = keys[provider];
      var group = document.createElement('div');
      group.className = 'key-group';

      var hdr = document.createElement('div');
      hdr.className = 'key-group-header';
      var h3 = document.createElement('h3');
      h3.textContent = provider + ' ';
      h3.appendChild(makeTag(entries.length + ' key(s)', 'tag-blue'));
      hdr.appendChild(h3);
      group.appendChild(hdr);

      entries.forEach(function(entry) {
        var row = document.createElement('div');
        row.className = 'key-row';

        var val = document.createElement('span');
        val.className = 'key-val';
        val.textContent = entry.key_masked;
        row.appendChild(val);

        var st = document.createElement('span');
        st.className = 'key-status';
        st.appendChild(makeTag(entry.validated ? 'Validated' : 'Unknown', entry.validated ? 'tag-green' : 'tag-gray'));
        row.appendChild(st);

        var validateBtn = document.createElement('button');
        validateBtn.className = 'btn btn-sm btn-primary';
        validateBtn.textContent = 'Validate';
        validateBtn.dataset.provider = provider;
        validateBtn.dataset.index = entry.index;
        validateBtn.addEventListener('click', function() { validateKey(this.dataset.provider, parseInt(this.dataset.index)); });
        row.appendChild(validateBtn);

        var delBtn = document.createElement('button');
        delBtn.className = 'btn btn-sm btn-danger';
        delBtn.textContent = 'Remove';
        delBtn.dataset.provider = provider;
        delBtn.dataset.index = entry.index;
        delBtn.addEventListener('click', function() { deleteKey(this.dataset.provider, parseInt(this.dataset.index)); });
        row.appendChild(delBtn);

        group.appendChild(row);
      });
      container.appendChild(group);
    });
  }

  async function deleteKey(provider, index) {
    if (!confirm('Remove this key?')) return;
    try {
      await fetch('/api/keys/' + encodeURIComponent(provider) + '/' + index, { method: 'DELETE' });
      loadKeys();
    } catch(e) { alert('Error: ' + e.message); }
  }

  async function validateKey(provider, index) {
    try {
      var resp = await fetch('/api/keys/' + encodeURIComponent(provider) + '/' + index + '/validate', { method: 'POST' });
      var result = await resp.json();
      alert(result.valid ? 'Key is valid!' : 'Validation failed: ' + (result.error || 'Unknown error'));
      loadKeys();
    } catch(e) { alert('Error: ' + e.message); }
  }

  // ── DOM Helpers ──────────────────────────────────────────────
  function makeTag(text, cls) {
    var el = document.createElement('span');
    el.className = 'tag ' + cls;
    el.textContent = text;
    return el;
  }

  function setText(id, val) {
    var el = document.getElementById(id);
    if (el) el.textContent = val;
  }

  function clearEl(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
  }

  function appendCell(tr, text) {
    var td = document.createElement('td');
    td.textContent = text;
    tr.appendChild(td);
  }

  function appendCodeCell(tr, text) {
    var td = document.createElement('td');
    var code = document.createElement('code');
    code.textContent = text;
    td.appendChild(code);
    tr.appendChild(td);
  }

  function addEmptyRow(tbody, colspan, text) {
    var tr = document.createElement('tr');
    var td = document.createElement('td');
    td.colSpan = colspan;
    td.className = 'empty';
    td.textContent = text;
    tr.appendChild(td);
    tbody.appendChild(tr);
  }

  window.switchTab = switchTab;
})();
