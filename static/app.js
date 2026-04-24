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
    setupBenchmarkBtn();
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
    if (tab === 'setup' && _connInfo) {
      renderSetup(_connInfo);
    }
    if (tab === 'benchmarks') {
      loadBenchmarks();
    }
    if (tab === 'analytics') {
      loadAnalytics();
    }
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
    if (_connInfo) renderSetup(_connInfo);
    loadConnectionInfo();
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
  // ── Model categories for filtering ────────────────────────────
  function getModelCategory(name) {
    var n = name.toLowerCase();
    if (/coder|codestral|deepseek.*v|qwen.*coder|starcoder|code/.test(n)) return 'Coding';
    if (/r1|reason|think|qwq|deep.*think|reasoner/.test(n)) return 'Reasoning';
    if (/vision|vl|mm|multimodal|gemma-4|llama-4/.test(n)) return 'Vision';
    if (/mini|nano|flash|1\.[0-9]b|3b|7b|8b|9b|1\.2b/.test(n)) return 'Fast';
    if (/70b|120b|405b|253b|397b|480b|maverick/.test(n)) return 'Large';
    return 'General';
  }

  function getModelSize(name) {
    var n = name.toLowerCase();
    var m = n.match(/(\d+(?:\.\d+)?)\s*b/);
    if (m) return parseFloat(m[1]);
    if (/120b|253b|405b/.test(n)) return 120;
    if (/70b|72b|80b/.test(n)) return 70;
    if (/30b|32b|35b|34b/.test(n)) return 30;
    if (/20b|24b|26b|27b/.test(n)) return 20;
    if (/12b|13b|14b/.test(n)) return 12;
    if (/8b|9b/.test(n)) return 8;
    if (/mini|nano|flash/.test(n)) return 3;
    return 50; // unknown = middle
  }

  var _modelsSortBy = localStorage.getItem('models_sort') || 'name';
  var _modelsFilter = localStorage.getItem('models_filter') || 'all';
  var _modelsSearch = '';

  function sortAndFilterModels(models) {
    var filtered = models;

    // Filter by search
    if (_modelsSearch) {
      var q = _modelsSearch.toLowerCase();
      filtered = filtered.filter(function(m) {
        return m.name.toLowerCase().indexOf(q) >= 0 ||
          m.providers.some(function(p) { return p.provider.toLowerCase().indexOf(q) >= 0 || p.model.toLowerCase().indexOf(q) >= 0; });
      });
    }

    // Filter by category
    if (_modelsFilter !== 'all') {
      filtered = filtered.filter(function(m) {
        return getModelCategory(m.name) === _modelsFilter;
      });
    }

    // Sort
    var sorted = filtered.slice();
    switch (_modelsSortBy) {
      case 'name':
        sorted.sort(function(a, b) { return a.name.localeCompare(b.name); });
        break;
      case 'provider':
        sorted.sort(function(a, b) {
          var pa = a.active_provider ? a.active_provider.provider : 'zzz';
          var pb = b.active_provider ? b.active_provider.provider : 'zzz';
          return pa.localeCompare(pb) || a.name.localeCompare(b.name);
        });
        break;
      case 'category':
        sorted.sort(function(a, b) {
          return getModelCategory(a.name).localeCompare(getModelCategory(b.name)) || a.name.localeCompare(b.name);
        });
        break;
      case 'size':
        sorted.sort(function(a, b) {
          return getModelSize(a.name) - getModelSize(b.name);
        });
        break;
      case 'fallbacks':
        sorted.sort(function(a, b) { return b.providers.length - a.providers.length; });
        break;
      case 'available':
        sorted.sort(function(a, b) {
          var aa = a.providers.filter(function(p) { return p.available; }).length;
          var bb = b.providers.filter(function(p) { return p.available; }).length;
          return bb - aa;
        });
        break;
    }
    return sorted;
  }

  function renderModels(models) {
    var tbody = document.getElementById('models-tbody');
    if (!tbody) return;

    // Render toolbar if not exists
    var toolbar = document.getElementById('models-toolbar');
    if (!toolbar) {
      var tableParent = tbody.closest('.tab-panel');
      if (!tableParent) return;
      toolbar = document.createElement('div');
      toolbar.id = 'models-toolbar';
      toolbar.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px';

      // Search
      var search = document.createElement('input');
      search.type = 'text';
      search.placeholder = 'Search models...';
      search.style.cssText = 'background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:6px 10px;border-radius:6px;font-size:13px;width:200px';
      search.addEventListener('input', function() {
        _modelsSearch = this.value;
        renderModels(cachedStatus ? cachedStatus.models || [] : []);
      });
      toolbar.appendChild(search);

      // Filter dropdown
      var filter = document.createElement('select');
      filter.style.cssText = 'background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:6px 10px;border-radius:6px;font-size:13px';
      ['all', 'General', 'Coding', 'Reasoning', 'Vision', 'Fast', 'Large'].forEach(function(cat) {
        var opt = document.createElement('option');
        opt.value = cat;
        opt.textContent = cat === 'all' ? 'All Categories' : cat;
        if (_modelsFilter === cat) opt.selected = true;
        filter.appendChild(opt);
      });
      filter.addEventListener('change', function() {
        _modelsFilter = this.value;
        localStorage.setItem('models_filter', _modelsFilter);
        renderModels(cachedStatus ? cachedStatus.models || [] : []);
      });
      toolbar.appendChild(filter);

      // Sort dropdown
      var sort = document.createElement('select');
      sort.style.cssText = 'background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:6px 10px;border-radius:6px;font-size:13px';
      [
        ['name', 'Name (A-Z)'],
        ['category', 'Category'],
        ['provider', 'Provider'],
        ['size', 'Size (small→large)'],
        ['fallbacks', 'Most Fallbacks'],
        ['available', 'Most Available']
      ].forEach(function(o) {
        var opt = document.createElement('option');
        opt.value = o[0];
        opt.textContent = 'Sort: ' + o[1];
        if (_modelsSortBy === o[0]) opt.selected = true;
        sort.appendChild(opt);
      });
      sort.addEventListener('change', function() {
        _modelsSortBy = this.value;
        localStorage.setItem('models_sort', _modelsSortBy);
        renderModels(cachedStatus ? cachedStatus.models || [] : []);
      });
      toolbar.appendChild(sort);

      // Model count
      var count = document.createElement('span');
      count.id = 'models-count';
      count.style.cssText = 'color:#8b949e;font-size:12px;margin-left:auto';
      toolbar.appendChild(count);

      // Insert before table
      var table = tbody.closest('table');
      if (table && table.parentNode) {
        table.parentNode.insertBefore(toolbar, table);
      }
    }

    // Sort and filter
    var sorted = sortAndFilterModels(models);
    clearEl(tbody);

    // Update count
    var countEl = document.getElementById('models-count');
    if (countEl) countEl.textContent = sorted.length + ' of ' + models.length + ' models';

    if (!sorted.length) {
      addEmptyRow(tbody, 5, 'No models match your filter');
      return;
    }

    sorted.forEach(function(m) {
      var tr = document.createElement('tr');

      // Category badge + model name
      var td1 = document.createElement('td');
      var dot = document.createElement('span');
      dot.className = 'status-dot ' + (m.active_provider ? 'ok' : 'off');
      td1.appendChild(dot);
      td1.appendChild(document.createTextNode(' '));
      var code = document.createElement('code');
      code.textContent = m.name;
      td1.appendChild(code);
      // Category tag
      var cat = getModelCategory(m.name);
      var catColors = { Coding: '#a371f7', Reasoning: '#f0883e', Vision: '#3fb950', Fast: '#58a6ff', Large: '#f85149', General: '#8b949e' };
      var catTag = document.createElement('span');
      catTag.style.cssText = 'display:inline-block;margin-left:6px;padding:1px 6px;border-radius:8px;font-size:10px;font-weight:600;color:' + (catColors[cat] || '#8b949e');
      catTag.textContent = cat;
      td1.appendChild(catTag);
      tr.appendChild(td1);

      // Active provider
      var td2 = document.createElement('td');
      td2.appendChild(makeTag(m.active_provider ? m.active_provider.provider : 'unavailable',
        m.active_provider ? 'tag-green' : 'tag-red'));
      tr.appendChild(td2);

      // Fallback providers
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

      // Fallback count
      var td4 = document.createElement('td');
      var avail = m.providers.filter(function(p) { return p.available; }).length;
      td4.textContent = avail + '/' + m.providers.length;
      td4.style.cssText = avail > 0 ? 'color:#3fb950;font-weight:600' : 'color:#f85149';
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

    // Health summary at top
    var validCount = 0;
    var totalCount = known.length;
    known.forEach(function(name) {
      var pi = provInfo[name] || {};
      if (pi.has_key) validCount++;
    });
    var summaryDiv = document.createElement('div');
    summaryDiv.className = 'provider-health-summary';
    summaryDiv.style.cssText = 'background:#161b22;border:1px solid #21262d;border-radius:8px;padding:12px 16px;margin-bottom:16px;display:flex;align-items:center;gap:12px';
    var sdot = document.createElement('span');
    sdot.className = 'status-dot ' + (validCount > 0 ? 'ok' : 'off');
    summaryDiv.appendChild(sdot);
    var stxt = document.createElement('span');
    stxt.style.cssText = 'font-size:14px;color:#c9d1d9';
    var sstrong = document.createElement('strong');
    sstrong.textContent = validCount + '/' + totalCount;
    stxt.appendChild(sstrong);
    stxt.appendChild(document.createTextNode(' providers have API keys configured'));
    summaryDiv.appendChild(stxt);
    grid.appendChild(summaryDiv);

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

      if (h.latency_ms) {
        var latLine = document.createElement('div');
        latLine.textContent = 'Latency: ' + Math.round(h.latency_ms) + 'ms';
        if (h.last_error) latLine.title = h.last_error;
        meta.appendChild(latLine);
      }

      if (info) {
        var rlLine = document.createElement('div');
        rlLine.textContent = 'RPM: ' + info.rpm_used + '/' + (info.rpm_limit > 0 ? info.rpm_limit : '\u221E') +
          '  \u00B7  RPD: ' + info.rpd_used + '/' + (info.rpd_limit > 0 ? info.rpd_limit : '\u221E');
        meta.appendChild(rlLine);
      }

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

  // ── Analytics Tab ──────────────────────────────────────────────
  async function loadAnalytics() {
    var summaryEl = document.getElementById('analytics-summary');
    if (!summaryEl) return;
    try {
      var resp = await fetch('/api/analytics');
      if (!resp.ok) return;
      var data = await resp.json();
      renderAnalytics(data);
    } catch(e) { console.error('loadAnalytics error:', e); }
  }

  function renderAnalytics(data) {
    var summary = data.summary || {};
    var savings = data.savings || {};
    var topModels = data.top_models || [];
    var providers = data.providers || [];
    var dailyHistory = data.daily_history || [];

    // Summary cards
    var summaryEl = document.getElementById('analytics-summary');
    if (summaryEl) {
      clearEl(summaryEl);
      var items = [
        { label: 'Total Requests', value: formatNumber(summary.total_requests || 0) },
        { label: 'Total Tokens', value: formatNumber(summary.total_tokens || 0) },
        { label: 'Est. Savings', value: '$' + (savings.all_time_usd || 0).toFixed(2), cls: 'savings' },
        { label: 'Today Requests', value: formatNumber(summary.today_requests || 0) },
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
        summaryEl.appendChild(card);
      });
    }

    // Top models bar chart
    var topModelsEl = document.getElementById('analytics-top-models');
    if (topModelsEl) {
      clearEl(topModelsEl);
      if (topModels.length) {
        var maxReq = topModels[0].requests || 1;
        topModels.forEach(function(m) {
          var row = document.createElement('div');
          row.style.cssText = 'display:flex;align-items:center;gap:8px;margin-bottom:6px';
          var name = document.createElement('span');
          name.style.cssText = 'width:120px;font-size:12px;color:#8b949e;overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
          name.textContent = m.model;
          name.title = m.model;
          row.appendChild(name);
          var bar = document.createElement('div');
          bar.style.cssText = 'flex:1;height:18px;background:#0d1117;border-radius:3px;overflow:hidden';
          var fill = document.createElement('div');
          fill.style.cssText = 'height:100%;background:#58a6ff;border-radius:3px;width:' + ((m.requests / maxReq) * 100) + '%';
          bar.appendChild(fill);
          row.appendChild(bar);
          var count = document.createElement('span');
          count.style.cssText = 'font-size:12px;color:#c9d1d9;min-width:40px;text-align:right';
          count.textContent = formatNumber(m.requests);
          row.appendChild(count);
          topModelsEl.appendChild(row);
        });
      } else {
        topModelsEl.appendChild(makeEmptyEl('No data yet'));
      }
    }

    // Provider success rates
    var provRatesEl = document.getElementById('analytics-provider-rates');
    if (provRatesEl) {
      clearEl(provRatesEl);
      if (providers.length) {
        providers.forEach(function(p) {
          var row = document.createElement('div');
          row.style.cssText = 'display:flex;align-items:center;gap:8px;margin-bottom:6px';
          var name = document.createElement('span');
          name.style.cssText = 'width:100px;font-size:12px;color:#8b949e;overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
          name.textContent = p.provider;
          name.title = p.provider;
          row.appendChild(name);
          var bar = document.createElement('div');
          bar.style.cssText = 'flex:1;height:18px;background:#0d1117;border-radius:3px;overflow:hidden';
          var fill = document.createElement('div');
          var rate = p.success_rate !== undefined ? p.success_rate : 0;
          var rateColor = rate >= 90 ? '#3fb950' : rate >= 50 ? '#d29922' : '#f85149';
          fill.style.cssText = 'height:100%;background:' + rateColor + ';border-radius:3px;width:' + rate + '%';
          bar.appendChild(fill);
          row.appendChild(bar);
          var lbl = document.createElement('span');
          lbl.style.cssText = 'font-size:12px;min-width:40px;text-align:right;color:' + rateColor;
          lbl.textContent = rate + '%';
          row.appendChild(lbl);
          provRatesEl.appendChild(row);
        });
      } else {
        provRatesEl.appendChild(makeEmptyEl('No data yet'));
      }
    }

    // Savings / daily chart
    var savingsEl = document.getElementById('analytics-savings');
    if (savingsEl) {
      clearEl(savingsEl);
      if (dailyHistory.length > 1) {
        var chart = document.createElement('div');
        chart.style.cssText = 'display:flex;align-items:flex-end;gap:2px;height:100px;padding:8px 0';
        var maxR = Math.max.apply(null, dailyHistory.map(function(d) { return d.requests || 0; })) || 1;
        dailyHistory.slice().reverse().forEach(function(d) {
          var pct = ((d.requests || 0) / maxR * 100);
          var bar = document.createElement('div');
          bar.style.cssText = 'flex:1;min-width:6px;background:linear-gradient(to top,#238636,#2ea043);border-radius:2px 2px 0 0;height:' + Math.max(pct, 2) + '%;cursor:pointer';
          bar.title = d.date + ': ' + (d.requests || 0) + ' requests';
          chart.appendChild(bar);
        });
        savingsEl.appendChild(chart);
      } else {
        savingsEl.appendChild(makeEmptyEl('Savings accumulate as you use the gateway'));
      }
    }
  }

  // ── Benchmarks Tab ────────────────────────────────────────────
  function setupBenchmarkBtn() {
    var btn = document.getElementById('run-benchmark-btn');
    if (!btn) return;
    btn.addEventListener('click', async function() {
      btn.disabled = true;
      btn.textContent = 'Running...';
      var statusEl = document.getElementById('benchmark-status');
      if (statusEl) {
        clearEl(statusEl);
        statusEl.appendChild(makeTag('Benchmarks running...', 'tag-yellow'));
      }
      try {
        var resp = await fetch('/api/benchmarks/run');
        if (resp.ok) {
          var result = await resp.json();
          if (statusEl) {
            clearEl(statusEl);
            statusEl.appendChild(makeTag('Benchmarks complete! ' + (result.results || []).length + ' models tested', 'tag-green'));
          }
          loadBenchmarks();
        } else {
          if (statusEl) {
            clearEl(statusEl);
            statusEl.appendChild(makeTag('Benchmark failed', 'tag-red'));
          }
        }
      } catch(e) {
        var statusEl2 = document.getElementById('benchmark-status');
        if (statusEl2) {
          clearEl(statusEl2);
          statusEl2.appendChild(makeTag('Error: ' + e.message, 'tag-red'));
        }
      }
      btn.disabled = false;
      btn.textContent = 'Run Benchmarks';
    });
  }

  async function loadBenchmarks() {
    var tbody = document.getElementById('benchmarks-tbody');
    if (!tbody) return;
    try {
      var resp = await fetch('/api/benchmarks');
      if (!resp.ok) return;
      var data = await resp.json();
      renderBenchmarks(data);
    } catch(e) { console.error('loadBenchmarks error:', e); }
  }

  function renderBenchmarks(data) {
    var tbody = document.getElementById('benchmarks-tbody');
    if (!tbody) return;
    clearEl(tbody);

    var results = data.results || [];
    if (!results.length) {
      addEmptyRow(tbody, 7, 'No benchmarks yet. Click "Run Benchmarks" to start.');
      return;
    }

    // Sort by latency (fastest first)
    var sorted = results.filter(function(r) { return r.success; })
      .sort(function(a, b) { return (a.latency_ms || 99999) - (b.latency_ms || 99999); });

    sorted.forEach(function(r, idx) {
      var tr = document.createElement('tr');

      // Rank with medal
      var rankTd = document.createElement('td');
      if (idx === 0) {
        var medal = document.createElement('span');
        medal.className = 'medal medal-gold';
        medal.textContent = '1';
        rankTd.appendChild(medal);
      } else if (idx === 1) {
        var medal = document.createElement('span');
        medal.className = 'medal medal-silver';
        medal.textContent = '2';
        rankTd.appendChild(medal);
      } else if (idx === 2) {
        var medal = document.createElement('span');
        medal.className = 'medal medal-bronze';
        medal.textContent = '3';
        rankTd.appendChild(medal);
      } else {
        rankTd.textContent = idx + 1;
      }
      tr.appendChild(rankTd);

      appendCodeCell(tr, r.model);
      appendCell(tr, r.provider || '-');
      appendCell(tr, r.latency_ms ? Math.round(r.latency_ms) + 'ms' : '-');
      appendCell(tr, r.ttft_ms !== undefined ? Math.round(r.ttft_ms) + 'ms' : '-');
      appendCell(tr, r.tokens_per_second ? r.tokens_per_second.toFixed(1) : '-');

      var statusTd = document.createElement('td');
      statusTd.appendChild(makeTag('OK', 'tag-green'));
      tr.appendChild(statusTd);

      tbody.appendChild(tr);
    });

    // Add failed entries
    results.filter(function(r) { return !r.success; }).forEach(function(r) {
      var tr = document.createElement('tr');
      var rankTd = document.createElement('td');
      rankTd.textContent = '-';
      tr.appendChild(rankTd);
      appendCodeCell(tr, r.model);
      appendCell(tr, r.provider || '-');
      appendCell(tr, '-');
      appendCell(tr, '-');
      appendCell(tr, '-');
      var statusTd = document.createElement('td');
      var tag = makeTag('FAIL', 'tag-red');
      if (r.error) tag.title = r.error;
      statusTd.appendChild(tag);
      tr.appendChild(statusTd);
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
          var result = await resp.json();
          document.getElementById('key-value').value = '';
          if (result.valid) {
            alert('Key added and validated successfully!');
          } else {
            alert('Key added but validation failed: ' + (result.validation_error || 'Could not verify'));
          }
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
      // Add validate-all button if not already there
      var keysTab = document.getElementById('tab-keys');
      if (keysTab && !document.getElementById('validate-all-btn')) {
        var btn = document.createElement('button');
        btn.id = 'validate-all-btn';
        btn.className = 'btn btn-primary';
        btn.style.cssText = 'margin-bottom:16px';
        btn.textContent = 'Validate All Provider Keys';
        btn.addEventListener('click', async function() {
          btn.disabled = true;
          btn.textContent = 'Validating...';
          try {
            var resp = await fetch('/api/keys/validate-all', { method: 'POST' });
            if (resp.ok) {
              var result = await resp.json();
              var s = result.summary || result;
              alert('Validation complete!\nValid: ' + (s.valid || 0) + '\nInvalid: ' + (s.invalid || 0) + '\nRate limited: ' + (s.rate_limited || 0) + '\nNo key: ' + (s.no_key || 0));
              loadKeys();
            }
          } catch(e) { alert('Error: ' + e.message); }
          btn.disabled = false;
          btn.textContent = 'Validate All Provider Keys';
        });
        keysTab.insertBefore(btn, keysTab.firstChild);
      }
    } catch(e) {}
  }

  function renderKeys(keys) {
    var container = document.getElementById('keys-list');
    if (!container) return;
    clearEl(container);
    var names = Object.keys(keys);
    if (!names.length) {
      container.appendChild(makeEmptyEl('No API keys stored. Add one above.'));
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

        var src = document.createElement('span');
        src.className = 'key-source';
        src.appendChild(makeTag(entry.source === 'env' ? '.env' : 'Runtime', entry.source === 'env' ? 'tag-blue' : 'tag-green'));
        row.appendChild(src);

        var st = document.createElement('span');
        st.className = 'key-status';
        var validLabel = 'Unknown';
        var validCls = 'tag-gray';
        if (entry.validated === true) { validLabel = 'Valid'; validCls = 'tag-green'; }
        else if (entry.validated === false) { validLabel = 'Invalid'; validCls = 'tag-red'; }
        st.appendChild(makeTag(validLabel, validCls));
        row.appendChild(st);

        var validateBtn = document.createElement('button');
        validateBtn.className = 'btn btn-sm btn-primary';
        validateBtn.textContent = 'Validate';
        validateBtn.dataset.provider = provider;
        validateBtn.dataset.index = entry.index;
        validateBtn.addEventListener('click', function() { validateKey(this.dataset.provider, parseInt(this.dataset.index)); });
        row.appendChild(validateBtn);

        if (entry.deletable) {
          var delBtn = document.createElement('button');
          delBtn.className = 'btn btn-sm btn-danger';
          delBtn.textContent = 'Remove';
          delBtn.dataset.provider = provider;
          delBtn.dataset.index = entry.index;
          delBtn.addEventListener('click', function() { deleteKey(this.dataset.provider, parseInt(this.dataset.index)); });
          row.appendChild(delBtn);
        }

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

  // ── Setup Tab ──────────────────────────────────────────────────
  var _connInfo = null;

  async function loadConnectionInfo() {
    try {
      var resp = await fetch('/api/connection-info');
      if (!resp.ok) return;
      _connInfo = await resp.json();
      renderSetup(_connInfo);
    } catch(e) { console.error('loadConnectionInfo error:', e); }
  }

  function renderSetup(info) {
    var container = document.getElementById('setup-content');
    if (!container) return;
    clearEl(container);

    // Connection info cards
    var infoGrid = document.createElement('div');
    infoGrid.className = 'setup-info-grid';

    var items = [
      { label: 'Base URL', value: info.base_url, id: 'setup-base-url', copy: true },
      { label: 'API Key (masked)', value: info.master_key_masked, id: 'setup-api-key-masked' },
      { label: 'Models Available', value: String(info.model_count || 0) },
      { label: 'Active Providers', value: String(info.provider_count || 0) },
    ];

    items.forEach(function(item) {
      var card = document.createElement('div');
      card.className = 'setup-info-card';
      var lbl = document.createElement('div');
      lbl.className = 'setup-label';
      lbl.textContent = item.label;
      card.appendChild(lbl);

      var row = document.createElement('div');
      row.className = 'setup-value-row';
      var val = document.createElement('code');
      if (item.id) val.id = item.id;
      val.textContent = item.value;
      row.appendChild(val);
      if (item.copy) {
        row.appendChild(makeCopyBtn(item.value));
      }
      card.appendChild(row);
      infoGrid.appendChild(card);
    });

    // API Key reveal toggle
    if (info.master_key) {
      var keyCard = document.createElement('div');
      keyCard.className = 'setup-info-card';
      var keyLabel = document.createElement('div');
      keyLabel.className = 'setup-label';
      keyLabel.textContent = 'API Key (click to reveal)';
      keyCard.appendChild(keyLabel);

      var keyRow = document.createElement('div');
      keyRow.className = 'setup-value-row';
      var keyInput = document.createElement('input');
      keyInput.type = 'password';
      keyInput.value = info.master_key;
      keyInput.readOnly = true;
      keyInput.className = 'setup-key-input';
      keyInput.id = 'setup-key-full';
      keyRow.appendChild(keyInput);

      var toggleBtn = document.createElement('button');
      toggleBtn.className = 'btn btn-sm';
      toggleBtn.textContent = 'Show';
      toggleBtn.addEventListener('click', function() {
        var inp = document.getElementById('setup-key-full');
        if (inp.type === 'password') { inp.type = 'text'; this.textContent = 'Hide'; }
        else { inp.type = 'password'; this.textContent = 'Show'; }
      });
      keyRow.appendChild(toggleBtn);

      var copyKey = makeCopyBtn(info.master_key);
      copyKey.textContent = 'Copy';
      keyRow.appendChild(copyKey);

      keyCard.appendChild(keyRow);
      infoGrid.appendChild(keyCard);
    }

    container.appendChild(infoGrid);

    // Smart Defaults section
    var defaultsTitle = document.createElement('h3');
    defaultsTitle.className = 'setup-section-title';
    defaultsTitle.textContent = 'Smart Default Models';
    container.appendChild(defaultsTitle);

    var defaultsDesc = document.createElement('p');
    defaultsDesc.style.cssText = 'color:#8b949e;font-size:13px;margin-bottom:12px';
    defaultsDesc.textContent = 'Recommended models by task type based on benchmarks and capabilities.';
    container.appendChild(defaultsDesc);

    var defaultsGrid = document.createElement('div');
    defaultsGrid.id = 'smart-defaults-grid';
    defaultsGrid.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px;margin-bottom:24px';
    defaultsGrid.appendChild(makeEmptyEl('Loading smart defaults...'));
    container.appendChild(defaultsGrid);

    loadSmartDefaults();

    // Export Config section
    var exportTitle = document.createElement('h3');
    exportTitle.className = 'setup-section-title';
    exportTitle.textContent = 'Export Config for Tools';
    container.appendChild(exportTitle);

    var exportRow = document.createElement('div');
    exportRow.style.cssText = 'display:flex;gap:8px;align-items:center;margin-bottom:16px';
    var exportSelect = document.createElement('select');
    exportSelect.id = 'export-tool-select';
    exportSelect.style.cssText = 'background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:8px 12px;border-radius:6px;font-size:13px;min-width:200px';
    var defaultOpt = document.createElement('option');
    defaultOpt.value = '';
    defaultOpt.textContent = 'Loading tools...';
    exportSelect.appendChild(defaultOpt);
    exportRow.appendChild(exportSelect);

    var exportBtn = document.createElement('button');
    exportBtn.className = 'btn btn-primary';
    exportBtn.textContent = 'Export';
    exportBtn.addEventListener('click', function() {
      var sel = document.getElementById('export-tool-select');
      if (sel && sel.value) loadExportConfig(sel.value);
    });
    exportRow.appendChild(exportBtn);
    container.appendChild(exportRow);

    var exportOutput = document.createElement('div');
    exportOutput.id = 'export-output';
    container.appendChild(exportOutput);

    loadExportTools();

    // Config snippets
    var snippetsTitle = document.createElement('h3');
    snippetsTitle.className = 'setup-section-title';
    snippetsTitle.textContent = 'Ready-to-Copy Configuration';
    container.appendChild(snippetsTitle);

    var defaultModel = (info.top_models && info.top_models[0]) || 'llama-3.3-70b';
    var apiKey = info.master_key || 'YOUR_KEY';
    var snippets = [
      {
        title: 'OpenAI Python',
        code: 'from openai import OpenAI\n\nclient = OpenAI(\n    api_key="' + apiKey + '",\n    base_url="' + info.base_url + '"\n)\n\nresponse = client.chat.completions.create(\n    model="' + defaultModel + '",\n    messages=[{"role": "user", "content": "Hello!"}]\n)\nprint(response.choices[0].message.content)'
      },
      {
        title: 'cURL',
        code: 'curl ' + info.base_url + '/chat/completions \\\n  -H "Authorization: Bearer ' + apiKey + '" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"model": "' + defaultModel + '", "messages": [{"role": "user", "content": "Hello!"}]}\''
      },
      {
        title: 'OpenClaw Config',
        code: JSON.stringify({ api_key: apiKey, base_url: info.base_url, default_model: defaultModel, models: info.top_models || [] }, null, 2)
      },
      {
        title: 'Hermes Config',
        code: JSON.stringify({ openai_api_key: apiKey, openai_base_url: info.base_url, model: defaultModel, available_models: info.top_models || [] }, null, 2)
      },
      {
        title: '.env File',
        code: 'OPENAI_API_KEY=' + apiKey + '\nOPENAI_BASE_URL=' + info.base_url + '\nDEFAULT_MODEL=' + defaultModel
      },
    ];

    snippets.forEach(function(snippet) {
      var wrap = document.createElement('div');
      wrap.className = 'snippet-card';

      var hdr = document.createElement('div');
      hdr.className = 'snippet-header';
      var title = document.createElement('span');
      title.textContent = snippet.title;
      hdr.appendChild(title);
      hdr.appendChild(makeCopyBtn(snippet.code));
      wrap.appendChild(hdr);

      var pre = document.createElement('pre');
      pre.className = 'snippet-code';
      var code = document.createElement('code');
      code.textContent = snippet.code;
      pre.appendChild(code);
      wrap.appendChild(pre);

      container.appendChild(wrap);
    });

    // Top models list
    if (info.top_models && info.top_models.length) {
      var modelsTitle = document.createElement('h3');
      modelsTitle.className = 'setup-section-title';
      modelsTitle.textContent = 'Recommended Models';
      container.appendChild(modelsTitle);

      var modelList = document.createElement('div');
      modelList.className = 'setup-model-list';
      info.top_models.forEach(function(m) {
        modelList.appendChild(makeTag(m, 'tag-blue'));
      });
      container.appendChild(modelList);
    }

    // Auto-update section
    var updateTitle = document.createElement('h3');
    updateTitle.className = 'setup-section-title';
    updateTitle.textContent = 'Keep Models Updated';
    container.appendChild(updateTitle);

    var updateInfo = document.createElement('p');
    updateInfo.style.cssText = 'color:#8b949e;font-size:13px;margin-bottom:12px;line-height:1.6';
    updateInfo.textContent = 'Providers add new free models regularly. Click the button below to re-scan all providers for new models.';
    container.appendChild(updateInfo);

    var updateBtn = document.createElement('button');
    updateBtn.className = 'btn btn-primary';
    updateBtn.textContent = 'Scan for New Models';
    updateBtn.style.marginRight = '8px';
    updateBtn.addEventListener('click', async function() {
      updateBtn.disabled = true;
      updateBtn.textContent = 'Scanning...';
      try {
        var resp = await fetch('/api/auto-update', {
          headers: { 'Authorization': 'Bearer ' + (info.master_key || '') }
        });
        if (resp.ok) {
          var result = await resp.json();
          if (result.new_models_discovered > 0) {
            alert('Found ' + result.new_models_discovered + ' new models! Total: ' + result.current_model_count);
          } else {
            alert('All up to date! ' + result.current_model_count + ' models available.');
          }
          loadStatus();
        } else {
          alert('Scan failed. Check server logs.');
        }
      } catch(e) {
        alert('Error: ' + e.message);
      }
      updateBtn.disabled = false;
      updateBtn.textContent = 'Scan for New Models';
    });
    container.appendChild(updateBtn);

    // Sync from awesome-free-llm-apis
    var syncInfo = document.createElement('p');
    syncInfo.style.cssText = 'color:#8b949e;font-size:13px;margin:16px 0 8px;line-height:1.6';
    syncInfo.textContent = 'Sync from awesome-free-llm-apis to get new providers and models.';
    container.appendChild(syncInfo);

    var syncBtn = document.createElement('button');
    syncBtn.className = 'btn btn-secondary';
    syncBtn.textContent = 'Sync Providers from Upstream';
    syncBtn.addEventListener('click', async function() {
      syncBtn.disabled = true;
      syncBtn.textContent = 'Syncing...';
      try {
        var resp = await fetch('/api/sync-providers', {
          method: 'POST',
          headers: { 'Authorization': 'Bearer ' + (info.master_key || '') }
        });
        if (resp.ok) {
          var result = await resp.json();
          alert('Synced! ' + result.new_models + ' new models from ' + result.providers + ' providers. Total: ' + result.total_models);
          loadStatus();
        } else {
          var err = await resp.text();
          alert('Sync failed: ' + err);
        }
      } catch(e) {
        alert('Error: ' + e.message);
      }
      syncBtn.disabled = false;
      syncBtn.textContent = 'Sync Providers from Upstream';
    });
    container.appendChild(syncBtn);

    // Provider Guides section
    if (info.providers && info.providers.length) {
      var provTitle = document.createElement('h3');
      provTitle.className = 'setup-section-title';
      provTitle.style.cssText = 'margin-top:24px';
      provTitle.textContent = '🔑 Provider Setup Guides';
      container.appendChild(provTitle);

      var provDesc = document.createElement('p');
      provDesc.style.cssText = 'color:#8b949e;font-size:13px;margin-bottom:12px;line-height:1.5';
      provDesc.textContent = 'Click any provider to see step-by-step instructions for getting a free API key.';
      container.appendChild(provDesc);

      var provGrid = document.createElement('div');
      provGrid.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px;margin-bottom:20px';

      info.providers.forEach(function(p) {
        var card = document.createElement('div');
        card.style.cssText = 'background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px;cursor:pointer;transition:border-color 0.2s';
        card.addEventListener('mouseenter', function() { this.style.borderColor = '#58a6ff'; });
        card.addEventListener('mouseleave', function() { this.style.borderColor = p.has_key ? '#238636' : '#30363d'; });
        if (p.has_key) card.style.borderColor = '#238636';

        // Header
        var hdr = document.createElement('div');
        hdr.style.cssText = 'display:flex;justify-content:space-between;align-items:center;margin-bottom:8px';
        var name = document.createElement('strong');
        name.style.cssText = 'color:#e6edf3;font-size:14px';
        name.textContent = p.name;
        hdr.appendChild(name);

        var status = document.createElement('span');
        if (p.has_key) {
          status.style.cssText = 'background:#0d1f0d;color:#3fb950;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600';
          status.textContent = '✓ Connected';
        } else {
          status.style.cssText = 'background:#1c1c1c;color:#8b949e;padding:2px 8px;border-radius:10px;font-size:11px';
          status.textContent = 'No key';
        }
        hdr.appendChild(status);
        card.appendChild(hdr);

        // Rate limit
        var rl = document.createElement('div');
        rl.style.cssText = 'color:#8b949e;font-size:12px;margin-bottom:6px';
        rl.textContent = '⚡ ' + p.rate_limit;
        card.appendChild(rl);

        // Env key
        var env = document.createElement('div');
        env.style.cssText = 'font-size:11px;color:#6e7681;margin-bottom:8px';
        env.textContent = p.env_key + '=...';
        card.appendChild(env);

        // Notes
        if (p.notes) {
          var note = document.createElement('div');
          note.style.cssText = 'color:#6e7681;font-size:11px;line-height:1.4;overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical';
          note.textContent = p.notes;
          card.appendChild(note);
        }

        // Get Key button
        var btnRow = document.createElement('div');
        btnRow.style.cssText = 'margin-top:10px;display:flex;gap:6px';
        var getKeyBtn = document.createElement('a');
        getKeyBtn.href = p.sign_in_url;
        getKeyBtn.target = '_blank';
        getKeyBtn.style.cssText = 'color:#58a6ff;font-size:12px;text-decoration:none;display:inline-flex;align-items:center;gap:4px';
        getKeyBtn.textContent = 'Get API Key →';
        btnRow.appendChild(getKeyBtn);

        // Instructions toggle
        var instrBtn = document.createElement('button');
        instrBtn.style.cssText = 'color:#8b949e;font-size:11px;background:none;border:none;cursor:pointer;padding:0';
        instrBtn.textContent = 'Instructions';
        instrBtn.addEventListener('click', function(e) {
          e.stopPropagation();
          loadProviderGuide(p.id, card);
        });
        btnRow.appendChild(instrBtn);
        card.appendChild(btnRow);

        provGrid.appendChild(card);
      });
      container.appendChild(provGrid);
    }
  }

  async function loadProviderGuide(providerId, cardEl) {
    // Toggle instructions panel
    var existing = cardEl.querySelector('.guide-panel');
    if (existing) { existing.remove(); return; }

    try {
      var resp = await fetch('/api/provider-guide/' + providerId);
      if (!resp.ok) return;
      var guide = await resp.json();

      var panel = document.createElement('div');
      panel.className = 'guide-panel';
      panel.style.cssText = 'margin-top:12px;padding-top:12px;border-top:1px solid #30363d';

      var steps = guide.instructions || [];
      steps.forEach(function(step, i) {
        var row = document.createElement('div');
        row.style.cssText = 'display:flex;gap:8px;margin-bottom:8px;font-size:12px;line-height:1.5';
        var num = document.createElement('span');
        num.style.cssText = 'color:#58a6ff;font-weight:700;min-width:18px';
        num.textContent = (i + 1) + '.';
        row.appendChild(num);
        var txt = document.createElement('span');
        txt.style.cssText = 'color:#c9d1d9';
        txt.textContent = step;
        row.appendChild(txt);
        panel.appendChild(row);
      });

      cardEl.appendChild(panel);
    } catch(e) {}
  }

  // ── Smart Defaults ──────────────────────────────────────────
  async function loadSmartDefaults() {
    var grid = document.getElementById('smart-defaults-grid');
    if (!grid) return;
    try {
      var resp = await fetch('/api/smart-default/all');
      if (!resp.ok) { grid.textContent = 'Could not load defaults'; return; }
      var data = await resp.json();
      clearEl(grid);
      var tasks = ['chat', 'code', 'reasoning', 'fast', 'creative', 'vision'];
      tasks.forEach(function(task) {
        var d = data[task] || {};
        var card = document.createElement('div');
        card.className = 'stat-card';
        card.style.cssText = 'background:#161b22;border:1px solid #21262d;border-radius:8px;padding:12px';
        var lbl = document.createElement('div');
        lbl.className = 'label';
        lbl.textContent = task.charAt(0).toUpperCase() + task.slice(1);
        card.appendChild(lbl);
        var val = document.createElement('div');
        val.style.cssText = 'font-size:14px;font-weight:600;color:#58a6ff;margin-top:4px';
        val.textContent = d.model || '-';
        card.appendChild(val);
        var reason = document.createElement('div');
        reason.style.cssText = 'font-size:11px;color:#8b949e;margin-top:2px';
        reason.textContent = d.reason || '';
        card.appendChild(reason);
        grid.appendChild(card);
      });
    } catch(e) {
      grid.textContent = 'Error loading defaults';
    }
  }

  // ── Export Config ───────────────────────────────────────────
  async function loadExportTools() {
    var select = document.getElementById('export-tool-select');
    if (!select) return;
    try {
      var resp = await fetch('/api/config/export/tools');
      if (!resp.ok) return;
      var data = await resp.json();
      var tools = data.tools || [];
      clearEl(select);
      var defOpt = document.createElement('option');
      defOpt.value = '';
      defOpt.textContent = 'Select a tool...';
      select.appendChild(defOpt);
      tools.forEach(function(t) {
        var opt = document.createElement('option');
        opt.value = (typeof t === 'object') ? t.id : t;
        opt.textContent = (typeof t === 'object') ? t.name : t;
        select.appendChild(opt);
      });
    } catch(e) {
      var select2 = document.getElementById('export-tool-select');
      if (select2) select2.textContent = 'Error loading tools';
    }
  }

  async function loadExportConfig(tool) {
    var output = document.getElementById('export-output');
    if (!output) return;
    clearEl(output);
    output.appendChild(makeEmptyEl('Loading config...'));
    try {
      var resp = await fetch('/api/config/export?tool=' + encodeURIComponent(tool));
      if (!resp.ok) { clearEl(output); output.appendChild(makeEmptyEl('Failed to load config')); return; }
      var data = await resp.json();
      clearEl(output);
      var wrap = document.createElement('div');
      wrap.className = 'snippet-card';
      var hdr = document.createElement('div');
      hdr.className = 'snippet-header';
      var title = document.createElement('span');
      title.textContent = tool + ' Configuration';
      hdr.appendChild(title);
      hdr.appendChild(makeCopyBtn(JSON.stringify(data, null, 2)));
      wrap.appendChild(hdr);
      var pre = document.createElement('pre');
      pre.className = 'snippet-code';
      var code = document.createElement('code');
      code.textContent = JSON.stringify(data, null, 2);
      pre.appendChild(code);
      wrap.appendChild(pre);
      output.appendChild(wrap);
    } catch(e) {
      clearEl(output);
      output.appendChild(makeEmptyEl('Error: ' + e.message));
    }
  }

  // ── DOM Helpers ──────────────────────────────────────────────
  function makeTag(text, cls) {
    var el = document.createElement('span');
    el.className = 'tag ' + cls;
    el.textContent = text;
    return el;
  }

  function makeEmptyEl(text) {
    var el = document.createElement('div');
    el.className = 'empty';
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

  function makeCopyBtn(text) {
    var btn = document.createElement('button');
    btn.className = 'btn btn-sm btn-copy';
    btn.textContent = 'Copy';
    btn.addEventListener('click', function() {
      navigator.clipboard.writeText(text).then(function() {
        btn.textContent = 'Copied!';
        setTimeout(function() { btn.textContent = 'Copy'; }, 1500);
      });
    });
    return btn;
  }

  function formatNumber(n) {
    if (n === undefined || n === null) return '0';
    return Number(n).toLocaleString();
  }

  window.switchTab = switchTab;
})();
