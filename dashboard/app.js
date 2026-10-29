var TOKEN_STORAGE = 'mn_session_token';
var sessionToken = localStorage.getItem(TOKEN_STORAGE) || '';
var currentUser = null;
var sessions = [], activeId = null, activeData = null;
var tagFilter = null;
var templates = {};
var viewMode = 'normal'; // 'normal' or 'trash'
var trashCount = 0;

function h(tag, props) {
  props = props || {};
  var children = Array.prototype.slice.call(arguments, 2);
  var el = document.createElement(tag);
  Object.keys(props).forEach(function(k) {
    var v = props[k];
    if (k === 'onclick') el.onclick = v;
    else if (k === 'oninput') el.oninput = v;
    else if (k === 'onchange') el.onchange = v;
    else if (k === 'onkeydown') el.onkeydown = v;
    else if (k === 'class') el.className = v;
    else el.setAttribute(k, v);
  });
  children.flat().forEach(function(c) {
    if (c == null) return;
    el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  });
  return el;
}


// ---------------------------------------------------------------------------
// Rendering AI-written text safely.
//
// A note is built by an AI summarising what people SAID on a call, so it is
// attacker-influenced: anyone on the call can speak words that make the model
// emit markup. Rendered raw, that is script running in the dashboard with the
// login token in reach. Everything goes through DOMPurify first, images are
// dropped entirely (a remote image is a tracking beacon), and links open in a
// new tab without handing over the page.
// ---------------------------------------------------------------------------
if (window.DOMPurify) {
  DOMPurify.addHook('afterSanitizeAttributes', function (node) {
    if (node.tagName === 'A') {
      node.setAttribute('rel', 'noopener noreferrer');
      node.setAttribute('target', '_blank');
    }
  });
}

function renderMarkdown(md) {
  if (!window.marked || !window.DOMPurify) {
    // Fail closed: if the sanitiser did not load, show plain text, never raw HTML.
    return '<pre class="transcript">' + escapeHtml(md) + '</pre>';
  }
  return DOMPurify.sanitize(marked.parse(md), {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['style', 'form', 'input', 'button', 'textarea', 'select',
                  'iframe', 'object', 'embed', 'img', 'svg', 'math', 'link', 'meta', 'base'],
    FORBID_ATTR: ['style', 'srcset'],
  });
}

function getTagClass(tag) {
  var map = { important: 'important', 'follow-up': 'follow-up', client: 'client', internal: 'internal', archived: 'archived' };
  return map[tag] || 'default-tag';
}

async function api(path, opts) {
  opts = opts || {};
  var headers = Object.assign({}, opts.headers || {}, { 'Authorization': 'Bearer ' + sessionToken });
  var res = await fetch(path, Object.assign({}, opts, { headers: headers }));
  if (res.status === 401) {
    localStorage.removeItem(TOKEN_STORAGE);
    sessionToken = '';
    currentUser = null;
    renderLogin();
    throw new Error('auth');
  }
  return res;
}

async function doLogin() {
  var email = document.getElementById('login-email').value.trim();
  var pw = document.getElementById('login-pw').value;
  var errEl = document.getElementById('login-error');
  if (!email || !pw) return;
  try {
    var res = await fetch('/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email, password: pw })
    });
    if (!res.ok) { errEl.textContent = 'Invalid email or password'; return; }
    var data = await res.json();
    sessionToken = data.token;
    currentUser = data;
    localStorage.setItem(TOKEN_STORAGE, sessionToken);
    initAndRender();
  } catch (e) { errEl.textContent = 'Login failed: ' + e.message; }
}

async function doLogout() {
  try { await api('/logout', { method: 'POST' }); } catch(e) {}
  localStorage.removeItem(TOKEN_STORAGE);
  sessionToken = '';
  currentUser = null;
  renderLogin();
}

async function fetchMe() {
  try {
    var res = await api('/me');
    if (res.ok) { currentUser = await res.json(); }
  } catch(e) {}
}

async function fetchTemplates() {
  try {
    var res = await api('/templates');
    if (res.ok) { var d = await res.json(); templates = d.templates || {}; }
  } catch(e) {}
}

function downloadFile(sessionId, format) {
  fetch('/results/' + sessionId + '/download', {
    headers: { 'Authorization': 'Bearer ' + sessionToken }
  }).then(function(res) { return res.text(); }).then(function(md) {
    var content = md;
    var filename = 'meeting-' + sessionId.substring(0,8) + '.md';
    var mime = 'text/markdown';
    if (format === 'txt') {
      content = md.replace(/^#{1,6}\s*/gm, '').replace(/\*{1,2}([^*]+)\*{1,2}/g, '$1').replace(/\|/g, ' ').replace(/---+/g, '---');
      filename = 'meeting-' + sessionId.substring(0,8) + '.txt';
      mime = 'text/plain';
    }
    var blob = new Blob([content], { type: mime });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
  });
}

async function loadSessions(search) {
  try {
    var json;
    if (viewMode === 'trash') {
      var res = await api('/trash');
      json = await res.json();
    } else {
      var q = search ? '?search=' + encodeURIComponent(search) : '';
      if (tagFilter) { q += (q ? '&' : '?') + 'tag=' + encodeURIComponent(tagFilter); }
      var res = await api('/sessions' + q);
      json = await res.json();
    }
    sessions = json.sessions || [];
    renderSidebar();
  } catch (e) { console.error(e); }
  // Always refresh trash count in background
  refreshTrashCount();
}

async function refreshTrashCount() {
  try {
    var res = await api('/trash');
    if (res.ok) {
      var d = await res.json();
      trashCount = (d.sessions || []).length;
      updateTrashButton();
    }
  } catch(e) {}
}

function updateTrashButton() {
  var btn = document.getElementById('trash-toggle-btn');
  if (!btn) return;
  btn.innerHTML = '';
  var icon = document.createElement('span');
  icon.textContent = viewMode === 'trash' ? 'Back' : 'Trash';
  btn.appendChild(icon);
  if (trashCount > 0) {
    var cnt = document.createElement('span');
    cnt.className = 'trash-count';
    cnt.textContent = trashCount;
    btn.appendChild(cnt);
  }
  btn.className = 'trash-btn' + (viewMode === 'trash' ? ' active' : '');
}

function toggleTrashView() {
  viewMode = viewMode === 'trash' ? 'normal' : 'trash';
  activeId = null;
  activeData = null;
  var contentEl = document.getElementById('content');
  if (contentEl) {
    contentEl.innerHTML = '';
    var empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = viewMode === 'trash'
      ? 'Trash: select an item to restore or permanently delete.'
      : 'Select a meeting to view';
    contentEl.appendChild(empty);
  }
  // Toggle visibility of tag filter bar and search relevance for trash view
  var tagBar = document.getElementById('tag-filter-bar');
  if (tagBar) tagBar.style.display = viewMode === 'trash' ? 'none' : '';
  var searchInp = document.getElementById('search-input');
  if (searchInp) searchInp.style.display = viewMode === 'trash' ? 'none' : '';
  updateTrashButton();
  loadSessions();
}

async function deleteSession(sessionId) {
  if (!confirm('Delete this meeting?')) return;
  try {
    var res = await api('/results/' + sessionId, { method: 'DELETE' });
    if (!res.ok) {
      var err = await res.json().catch(function() { return {}; });
      alert('Delete failed: ' + (err.detail || res.status));
      return;
    }
    if (activeId === sessionId) {
      activeId = null;
      activeData = null;
      var contentEl = document.getElementById('content');
      if (contentEl) contentEl.innerHTML = '<div class="empty">Select a meeting to view</div>';
    }
    loadSessions(document.getElementById('search-input') ? document.getElementById('search-input').value : '');
  } catch(e) { alert('Delete failed: ' + e.message); }
}

async function restoreSession(sessionId) {
  try {
    var res = await api('/results/' + sessionId + '/restore', { method: 'POST' });
    if (!res.ok) {
      var err = await res.json().catch(function() { return {}; });
      alert('Restore failed: ' + (err.detail || res.status));
      return;
    }
    activeId = null;
    activeData = null;
    var contentEl = document.getElementById('content');
    if (contentEl) contentEl.innerHTML = '<div class="empty">Restored. Switch back to the main view to see it.</div>';
    loadSessions();
  } catch(e) { alert('Restore failed: ' + e.message); }
}

async function purgeSession(sessionId) {
  if (!confirm('Permanently delete this meeting? This cannot be undone.')) return;
  try {
    var res = await api('/results/' + sessionId + '/purge', { method: 'DELETE' });
    if (!res.ok) {
      var err = await res.json().catch(function() { return {}; });
      alert('Purge failed: ' + (err.detail || res.status));
      return;
    }
    activeId = null;
    activeData = null;
    var contentEl = document.getElementById('content');
    if (contentEl) contentEl.innerHTML = '<div class="empty">Permanently deleted.</div>';
    loadSessions();
  } catch(e) { alert('Purge failed: ' + e.message); }
}

function escapeHtml(s) {
  return (s || '').replace(/[<>&]/g, function(c) { return { '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c]; });
}

function copyToClipboard(text) {
  navigator.clipboard.writeText(text).then(function() {
    // brief visual feedback could go here
  });
}

async function reprocessSession(sessionId, meetingType) {
  try {
    await api('/results/' + sessionId + '/reprocess?meeting_type=' + encodeURIComponent(meetingType), { method: 'POST' });
    setTimeout(function() { loadSession(sessionId); }, 1000);
    loadSessions(document.getElementById('search-input') ? document.getElementById('search-input').value : '');
  } catch(e) { console.error(e); }
}

async function updateTags(sessionId, tags) {
  try {
    await api('/results/' + sessionId + '/tags', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + sessionToken },
      body: JSON.stringify({ tags: tags })
    });
    if (activeData) activeData.tags = tags;
    loadSessions(document.getElementById('search-input') ? document.getElementById('search-input').value : '');
  } catch(e) { console.error(e); }
}

async function loadSession(id, fromTrash) {
  activeId = id;
  var endpoint = fromTrash ? '/trash/' + id : '/results/' + id;
  var res = await api(endpoint);
  if (!res.ok) {
    var contentEl0 = document.getElementById('content');
    if (contentEl0) contentEl0.innerHTML = '<div class="empty">Failed to load session.</div>';
    return;
  }
  var data = await res.json();
  activeData = data;
  var summary = data.summary || '_no summary_';
  var actions = data.actions || '_no actions_';
  var header = '**Type:** ' + data.meeting_type + ' | **Duration:** ' + Math.round(data.duration_sec/60) + ' min | **Time:** ' + new Date(data.timestamp).toLocaleString();
  var md = header + '\n\n---\n\n' + summary + '\n\n---\n\n# Action Plan\n\n' + actions;
  var contentEl = document.getElementById('content');
  contentEl.innerHTML = '';

  // -- Trash view: show banner + Restore/Purge actions; skip tags/download/re-analyze --
  if (fromTrash) {
    var banner = document.createElement('div');
    banner.className = 'trash-banner';
    banner.textContent = 'This meeting is in the trash. Restore it to edit, tag, or download.';
    contentEl.appendChild(banner);
    var actionsDiv = document.createElement('div');
    actionsDiv.className = 'trash-actions';
    var btnRestore = document.createElement('button');
    btnRestore.className = 'btn-restore';
    btnRestore.textContent = 'Restore';
    btnRestore.onclick = function() { restoreSession(id); };
    var btnPurge = document.createElement('button');
    btnPurge.className = 'btn-purge';
    btnPurge.textContent = 'Delete permanently';
    btnPurge.onclick = function() { purgeSession(id); };
    actionsDiv.appendChild(btnRestore);
    actionsDiv.appendChild(btnPurge);
    contentEl.appendChild(actionsDiv);
    contentEl.insertAdjacentHTML('beforeend', renderMarkdown(md) +
      '<h2 id="full-transcript">Full transcript</h2><p class="dim" style="margin-top:-6px">Everything that was said, verbatim. Included in the downloaded file.</p><pre class="transcript">' + escapeHtml(data.transcript || '') + '</pre>');
    renderSidebar();
    return;
  }

  // Tags editor
  var currentTags = data.tags || [];
  var tagsEditorDiv = document.createElement('div');
  tagsEditorDiv.className = 'tags-editor';

  function renderTagsEditor() {
    tagsEditorDiv.innerHTML = '';
    currentTags.forEach(function(tag) {
      var pill = document.createElement('span');
      pill.className = 'tag-pill ' + getTagClass(tag);
      pill.textContent = tag;
      var rm = document.createElement('span');
      rm.className = 'remove-tag';
      rm.textContent = '\u00d7';
      rm.onclick = function() {
        currentTags = currentTags.filter(function(t) { return t !== tag; });
        updateTags(id, currentTags);
        renderTagsEditor();
      };
      pill.appendChild(rm);
      tagsEditorDiv.appendChild(pill);
    });
    var inp = document.createElement('input');
    inp.type = 'text';
    inp.placeholder = 'Add tag...';
    inp.onkeydown = function(e) {
      if (e.key === 'Enter' && inp.value.trim()) {
        var newTag = inp.value.trim().toLowerCase();
        if (currentTags.indexOf(newTag) === -1) {
          currentTags.push(newTag);
          updateTags(id, currentTags);
        }
        inp.value = '';
        renderTagsEditor();
      }
    };
    tagsEditorDiv.appendChild(inp);
    // Suggestions
    var sugDiv = document.createElement('div');
    sugDiv.className = 'tag-suggestions';
    var suggestions = ['important', 'follow-up', 'client', 'internal', 'archived'];
    suggestions.forEach(function(sug) {
      if (currentTags.indexOf(sug) === -1) {
        var pill = document.createElement('span');
        pill.className = 'tag-pill ' + getTagClass(sug);
        pill.textContent = '+' + sug;
        pill.onclick = function() {
          currentTags.push(sug);
          updateTags(id, currentTags);
          renderTagsEditor();
        };
        sugDiv.appendChild(pill);
      }
    });
    tagsEditorDiv.appendChild(sugDiv);
  }
  renderTagsEditor();
  contentEl.appendChild(tagsEditorDiv);

  // Download + re-analyze bar
  var barDiv = document.createElement('div');
  barDiv.className = 'reanalyze-bar';
  var btnMd = document.createElement('button');
  btnMd.textContent = 'Download MD';
  btnMd.style.cssText = 'padding:6px 14px;background:#238636;color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600;';
  btnMd.onclick = function() { downloadFile(id, 'md'); };
  var btnTxt = document.createElement('button');
  btnTxt.textContent = 'Download TXT';
  btnTxt.style.cssText = 'padding:6px 14px;background:#30363d;color:#e6edf3;border:1px solid #484f58;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600;';
  btnTxt.onclick = function() { downloadFile(id, 'txt'); };
  barDiv.appendChild(btnMd);
  barDiv.appendChild(btnTxt);

  // Re-analyze dropdown
  if (data.status === 'complete') {
    var sel = document.createElement('select');
    sel.style.marginLeft = '12px';
    var defaultOpt = document.createElement('option');
    defaultOpt.value = '';
    defaultOpt.textContent = 'Re-analyze as...';
    sel.appendChild(defaultOpt);
    Object.keys(templates).forEach(function(key) {
      var opt = document.createElement('option');
      opt.value = key;
      opt.textContent = key.charAt(0).toUpperCase() + key.slice(1);
      if (key === data.meeting_type) opt.disabled = true;
      sel.appendChild(opt);
    });
    sel.onchange = function() {
      if (sel.value) {
        reprocessSession(id, sel.value);
        sel.value = '';
      }
    };
    barDiv.appendChild(sel);
  }
  contentEl.appendChild(barDiv);

  contentEl.insertAdjacentHTML('beforeend', renderMarkdown(md) +
    '<h2 id="full-transcript">Full transcript</h2><p class="dim" style="margin-top:-6px">Everything that was said, verbatim. Included in the downloaded file.</p><pre class="transcript">' + escapeHtml(data.transcript) + '</pre>');
  renderSidebar();
}

function renderSidebar() {
  var list = document.getElementById('session-list');
  if (!list) return;
  list.innerHTML = '';
  if (!sessions.length) {
    var msg = viewMode === 'trash'
      ? 'Trash is empty.'
      : 'No meetings yet. Start one from the Windows recorder.';
    list.appendChild(h('div', { class: 'empty' }, msg));
    return;
  }
  var isTrash = viewMode === 'trash';
  sessions.forEach(function(s) {
    var active = s.session_id === activeId ? ' active' : '';
    var processing = s.status === 'processing';
    var failed = s.status === 'failed';
    var badge = processing ? h('span', { class: 'badge processing' }, 'processing')
              : failed ? h('span', { class: 'badge failed' }, 'failed')
              : h('span', { class: 'badge' }, s.meeting_type || '\u2014');
    var titleEl = processing
      ? h('div', { class: 'title' }, h('span', { class: 'spinner' }), (s.title || '(new meeting)'))
      : h('div', { class: 'title' }, s.title || '(untitled)');
    // Tag pills
    var tagEls = (s.tags || []).map(function(tag) {
      var pill = document.createElement('span');
      pill.className = 'tag-pill ' + getTagClass(tag);
      pill.textContent = tag;
      pill.onclick = function(e) {
        e.stopPropagation();
        if (isTrash) return; // no tag filter in trash view
        tagFilter = tagFilter === tag ? null : tag;
        loadSessions(document.getElementById('search-input') ? document.getElementById('search-input').value : '');
        renderTagFilterBar();
      };
      return pill;
    });
    var metaChildren = [
      document.createTextNode(new Date(s.timestamp).toLocaleDateString() + ' ' + new Date(s.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })),
      badge,
      document.createTextNode(Math.round(s.duration_sec / 60) + 'm')
    ];
    tagEls.forEach(function(te) { metaChildren.push(te); });
    var metaDiv = document.createElement('div');
    metaDiv.className = 'meta';
    metaChildren.forEach(function(c) { metaDiv.appendChild(c); });
    var item = document.createElement('div');
    item.className = 'session' + active + (isTrash ? ' trashed' : '');
    item.onclick = function() { loadSession(s.session_id, isTrash); };
    item.appendChild(titleEl);
    item.appendChild(metaDiv);
    // Delete button (only in normal view)
    if (!isTrash) {
      var del = document.createElement('button');
      del.className = 'del-btn';
      del.title = 'Delete meeting';
      del.textContent = '\u{1F5D1}';
      del.onclick = function(e) { e.stopPropagation(); deleteSession(s.session_id); };
      item.appendChild(del);
    }
    list.appendChild(item);
  });
}

function renderTagFilterBar() {
  var bar = document.getElementById('tag-filter-bar');
  if (!bar) return;
  bar.innerHTML = '';
  var label = document.createElement('span');
  label.style.cssText = 'font-size:11px;color:var(--muted);margin-right:4px;';
  label.textContent = 'Filter:';
  bar.appendChild(label);
  var allTags = ['important', 'follow-up', 'client', 'internal', 'archived'];
  allTags.forEach(function(tag) {
    var pill = document.createElement('span');
    pill.className = 'tag-pill ' + getTagClass(tag) + (tagFilter === tag ? ' active-filter' : '');
    pill.textContent = tag;
    pill.onclick = function() {
      tagFilter = tagFilter === tag ? null : tag;
      loadSessions(document.getElementById('search-input') ? document.getElementById('search-input').value : '');
      renderTagFilterBar();
    };
    bar.appendChild(pill);
  });
  if (tagFilter) {
    var clear = document.createElement('span');
    clear.style.cssText = 'font-size:11px;color:var(--accent);cursor:pointer;margin-left:8px;';
    clear.textContent = 'clear';
    clear.onclick = function() {
      tagFilter = null;
      loadSessions(document.getElementById('search-input') ? document.getElementById('search-input').value : '');
      renderTagFilterBar();
    };
    bar.appendChild(clear);
  }
}

// --- Admin panel ---
async function showAdminPanel() {
  var overlay = document.createElement('div');
  overlay.className = 'admin-overlay';
  overlay.onclick = function(e) { if (e.target === overlay) overlay.remove(); };
  var panel = document.createElement('div');
  panel.className = 'admin-panel';
  panel.innerHTML = '<h2>User Management</h2><div id="admin-content">Loading...</div>';
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  await refreshAdminUsers();
}

// API keys are stored only as hashes, so a key can be shown exactly once: now.
function showKeyOnce(email, key) {
  window.prompt('API key for ' + email + '. Copy it now - it cannot be shown again.', key);
}

async function refreshAdminUsers() {
  var container = document.getElementById('admin-content');
  if (!container) return;
  try {
    var res = await api('/admin/users');
    var data = await res.json();
    var users = data.users || [];
    container.innerHTML = '';
    var table = document.createElement('table');
    var thead = '<tr><th>Email</th><th>Name</th><th>Role</th><th>API key</th><th></th></tr>';
    table.innerHTML = thead;
    users.forEach(function(u) {
      var tr = document.createElement('tr');
      var tdEmail = document.createElement('td');
      tdEmail.textContent = u.email;
      var tdName = document.createElement('td');
      tdName.textContent = u.name;
      var tdRole = document.createElement('td');
      tdRole.textContent = u.role;
      var tdKey = document.createElement('td');
      tdKey.appendChild(document.createTextNode(u.has_api_key ? 'set ' : 'none '));
      var newKeyBtn = document.createElement('button');
      newKeyBtn.className = 'copy-btn';
      newKeyBtn.textContent = 'New key';
      newKeyBtn.title = 'Create a new API key. The old one stops working.';
      newKeyBtn.onclick = async function() {
        if (!confirm('Create a new API key for ' + u.email + '? Their old key stops working immediately.')) return;
        var r = await api('/admin/users/' + encodeURIComponent(u.email) + '/rotate-key', { method: 'POST' });
        if (r.ok) { var d = await r.json(); showKeyOnce(u.email, d.api_key); }
      };
      tdKey.appendChild(newKeyBtn);
      var tdActions = document.createElement('td');
      if (u.email !== currentUser.email) {
        var delBtn = document.createElement('button');
        delBtn.className = 'btn-danger btn-small';
        delBtn.textContent = 'Delete';
        delBtn.onclick = async function() {
          if (!confirm('Delete user ' + u.email + '?')) return;
          await api('/admin/users/' + encodeURIComponent(u.email), { method: 'DELETE' });
          await refreshAdminUsers();
        };
        tdActions.appendChild(delBtn);
      }
      tr.appendChild(tdEmail);
      tr.appendChild(tdName);
      tr.appendChild(tdRole);
      tr.appendChild(tdKey);
      tr.appendChild(tdActions);
      table.appendChild(tr);
    });
    container.appendChild(table);
    // Add user form
    var formDiv = document.createElement('div');
    formDiv.style.cssText = 'margin-top:16px;padding-top:16px;border-top:1px solid var(--border);';
    formDiv.innerHTML = '<h3 style="margin-top:0">Add User</h3>';
    var row = document.createElement('div');
    row.style.cssText = 'display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:8px;align-items:end;';
    var emailInp = document.createElement('input');
    emailInp.placeholder = 'Email';
    emailInp.id = 'add-user-email';
    var nameInp = document.createElement('input');
    nameInp.placeholder = 'Name';
    nameInp.id = 'add-user-name';
    var pwInp = document.createElement('input');
    pwInp.placeholder = 'Password (10+ characters)';
    pwInp.type = 'password';
    pwInp.id = 'add-user-pw';
    var addBtn = document.createElement('button');
    addBtn.className = 'btn-primary';
    addBtn.textContent = 'Add';
    addBtn.onclick = async function() {
      var e = document.getElementById('add-user-email').value.trim();
      var n = document.getElementById('add-user-name').value.trim();
      var p = document.getElementById('add-user-pw').value;
      if (!e || !n || !p) return;
      var errDiv = document.getElementById('add-user-error');
      try {
        var res = await api('/admin/users', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + sessionToken },
          body: JSON.stringify({ email: e, name: n, password: p })
        });
        if (!res.ok) { var errData = await res.json(); errDiv.textContent = errData.detail || 'Failed'; return; }
        errDiv.textContent = '';
        var made = await res.json();
        if (made.api_key) showKeyOnce(made.email, made.api_key);
        await refreshAdminUsers();
      } catch(ex) { errDiv.textContent = ex.message; }
    };
    row.appendChild(emailInp);
    row.appendChild(nameInp);
    row.appendChild(pwInp);
    row.appendChild(addBtn);
    formDiv.appendChild(row);
    var errDiv = document.createElement('div');
    errDiv.id = 'add-user-error';
    errDiv.style.cssText = 'color:#f85149;font-size:13px;margin-top:8px;';
    formDiv.appendChild(errDiv);
    container.appendChild(formDiv);
  } catch(e) { container.textContent = 'Failed to load users: ' + e.message; }
}

function renderLogin() {
  // Idempotent: skip redraw if login form already present (preserves typing)
  if (document.getElementById('login-email')) return;
  var root = document.getElementById('root');
  root.innerHTML = '';
  var emailInput = h('input', { type: 'email', id: 'login-email', placeholder: 'admin@localhost  (or your email)' });
  var pwInput = h('input', { type: 'password', id: 'login-pw', placeholder: 'Password  (admin: your API key)' });
  pwInput.addEventListener('keydown', function(e) { if (e.key === 'Enter') doLogin(); });
  var errMsg = h('div', { id: 'login-error', style: 'color:#f85149;font-size:13px;min-height:20px;margin-bottom:4px' });
  var btn = h('button', { onclick: doLogin }, 'Sign In');
  root.appendChild(h('div', { class: 'auth-gate' },
    h('h2', {}, 'Meeting Notes'),
    h('p', {}, 'Sign in to view your meeting notes:'),
    emailInput, pwInput, errMsg, btn
  ));
}

async function initAndRender() {
  if (!sessionToken) { renderLogin(); return; }
  var root = document.getElementById('root');
  root.innerHTML = '<div class="empty">Loading...</div>';
  try {
    await fetchMe();
    if (!currentUser) { renderLogin(); return; }
    await fetchTemplates();
    renderApp();
  } catch(e) {
    // 401 from api() already called renderLogin; nothing else to do
  }
}

function renderApp() {
  var root = document.getElementById('root');
  root.innerHTML = '';
  var searchInput = h('input', { type: 'search', placeholder: 'Search notes...', id: 'search-input' });

  // Topbar with user info
  var userInfoDiv = document.createElement('div');
  userInfoDiv.className = 'user-info';
  var nameSpan = document.createElement('span');
  nameSpan.className = 'user-name';
  nameSpan.textContent = currentUser.name || currentUser.email;
  userInfoDiv.appendChild(nameSpan);
  var btnGroup = document.createElement('div');
  btnGroup.style.cssText = 'display:flex;gap:6px;';
  if (currentUser.role === 'admin') {
    var adminBtn = document.createElement('button');
    adminBtn.textContent = 'Admin';
    adminBtn.style.cssText = 'padding:3px 10px;background:#8957e5;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:11px;font-weight:600;';
    adminBtn.onclick = showAdminPanel;
    btnGroup.appendChild(adminBtn);
  }
  var trashBtn = document.createElement('button');
  trashBtn.id = 'trash-toggle-btn';
  trashBtn.className = 'trash-btn' + (viewMode === 'trash' ? ' active' : '');
  trashBtn.textContent = viewMode === 'trash' ? 'Back' : 'Trash';
  trashBtn.onclick = toggleTrashView;
  btnGroup.appendChild(trashBtn);
  var logoutBtn = document.createElement('button');
  logoutBtn.textContent = 'Logout';
  logoutBtn.style.cssText = 'padding:3px 10px;background:transparent;color:var(--muted);border:1px solid var(--border);border-radius:4px;cursor:pointer;font-size:11px;';
  logoutBtn.onclick = doLogout;
  btnGroup.appendChild(logoutBtn);
  userInfoDiv.appendChild(btnGroup);

  var topbarDiv = document.createElement('div');
  topbarDiv.className = 'topbar';
  topbarDiv.appendChild(userInfoDiv);
  var titleRow = document.createElement('div');
  titleRow.style.cssText = 'display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;';
  titleRow.appendChild(h('h1', { style: 'margin:0' }, 'Meeting Notes'));
  topbarDiv.appendChild(titleRow);
  topbarDiv.appendChild(searchInput);

  var tagFilterBar = document.createElement('div');
  tagFilterBar.className = 'tag-filter-bar';
  tagFilterBar.id = 'tag-filter-bar';

  var sidebarDiv = document.createElement('div');
  sidebarDiv.className = 'sidebar';
  sidebarDiv.appendChild(topbarDiv);
  sidebarDiv.appendChild(tagFilterBar);
  sidebarDiv.appendChild(h('div', { id: 'session-list' }));

  var appDiv = document.createElement('div');
  appDiv.className = 'app';
  appDiv.appendChild(sidebarDiv);
  appDiv.appendChild(h('div', { class: 'content', id: 'content' }, h('div', { class: 'empty' }, 'Select a meeting to view')));

  root.appendChild(appDiv);

  searchInput.addEventListener('input', function(e) {
    clearTimeout(window._searchTO);
    window._searchTO = setTimeout(function() { loadSessions(e.target.value); }, 300);
  });

  renderTagFilterBar();
  loadSessions();
  setInterval(function() {
    var q = document.getElementById('search-input');
    loadSessions(q ? q.value : '');
  }, 10000);
}

initAndRender();
