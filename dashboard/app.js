// Notewell dashboard. Plain JavaScript, no build step: the server serves this file as it is.
var TOKEN_STORAGE = 'mn_session_token';
var sessionToken = localStorage.getItem(TOKEN_STORAGE) || '';
var currentUser = null;
var templates = {};
var sessions = [], trashCount = 0, usedTags = [];
var search = '', tagFilter = null;
var view = 'home', activeId = null, activeData = null;
var ai = null;          // GET /settings/ai (admins only)
var rec = null;         // GET /recorder, or null when no recorder is attached
var devices = null;
var timers = [];
var SUGGESTED_TAGS = ['important', 'follow-up', 'client', 'internal', 'archived'];

function h(tag, props) {
  props = props || {};
  var children = Array.prototype.slice.call(arguments, 2);
  var el = document.createElement(tag);
  Object.keys(props).forEach(function(k) {
    var v = props[k];
    if (v == null || v === false) return;
    if (k.slice(0, 2) === 'on') el[k] = v;
    else if (k === 'class') el.className = v;
    else if (k === 'value') el.value = v;
    else if (k === 'checked') el.checked = v;
    else el.setAttribute(k, v === true ? '' : v);
  });
  children.flat().forEach(function(c) {
    if (c == null || c === false) return;
    el.appendChild(typeof c === 'string' || typeof c === 'number' ? document.createTextNode(String(c)) : c);
  });
  return el;
}

function $(id) { return document.getElementById(id); }


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
    return '<pre class="plain">' + escapeHtml(md) + '</pre>';
  }
  return DOMPurify.sanitize(marked.parse(md), {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['style', 'form', 'input', 'button', 'textarea', 'select',
                  'iframe', 'object', 'embed', 'img', 'svg', 'math', 'link', 'meta', 'base'],
    FORBID_ATTR: ['style', 'srcset'],
  });
}

function escapeHtml(s) {
  return (s || '').replace(/[<>&]/g, function(c) { return { '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c]; });
}

function markdownEl(md) {
  var d = h('div', { class: 'notes' });
  d.innerHTML = renderMarkdown(md);
  return d;
}

function toast(text) {
  var t = $('toast') || document.body.appendChild(h('div', { id: 'toast', class: 'toast' }));
  t.textContent = text;
  t.classList.add('show');
  clearTimeout(t._to);
  t._to = setTimeout(function() { t.classList.remove('show'); }, 2600);
}

function every(ms, fn) { timers.push(setInterval(fn, ms)); }
function stopTimers() { timers.forEach(clearInterval); timers = []; }

async function api(path, opts) {
  opts = opts || {};
  var headers = Object.assign({}, opts.headers || {}, { 'Authorization': 'Bearer ' + sessionToken });
  if (opts.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    opts = Object.assign({}, opts, { body: JSON.stringify(opts.json) });
  }
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

async function errorText(res) {
  var d = await res.json().catch(function() { return {}; });
  return detailText(d.detail) || ('error ' + res.status);
}

// --- dates and durations ------------------------------------------------------

function clock(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  var hh = Math.floor(sec / 3600), mm = Math.floor(sec / 60) % 60, ss = sec % 60;
  return (hh ? hh + ':' + String(mm).padStart(2, '0') : String(mm)) + ':' + String(ss).padStart(2, '0');
}
function minutes(sec) { var m = Math.round((sec || 0) / 60); return m < 1 ? '<1 min' : m + ' min'; }
function timeOf(ts) { return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); }
function dayGroup(ts) {
  var d = new Date(ts), today = new Date();
  var start = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  if (d.getTime() >= start) return 'Today';
  if (d.getTime() >= start - 864e5) return 'Yesterday';
  if (d.getTime() >= start - 6 * 864e5) return 'This week';
  return 'Earlier';
}
// The meeting list carries a title made from the notes; a single meeting does not.
function title(s) {
  var listed = sessions.filter(function(x) { return x.session_id === s.session_id; })[0];
  var t = s.title || (listed && listed.title);
  return t && t !== 'Untitled Meeting' ? t : (s.status === 'complete' ? 'Untitled meeting' : 'New meeting');
}
// The server stores "_Processing..._" as a placeholder until the AI has written something.
function written(text) { return text && text !== '_Processing..._' ? text : ''; }
function cap(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : ''; }

// --- sign in ---------------------------------------------------------------------

async function doLogin() {
  var email = $('login-email').value.trim();
  var pw = $('login-pw').value;
  var errEl = $('login-error');
  if (!email || !pw) return;
  try {
    var res = await fetch('/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email, password: pw })
    });
    if (!res.ok) { errEl.textContent = 'Wrong email or password.'; return; }
    var data = await res.json();
    sessionToken = data.token;
    currentUser = data;
    localStorage.setItem(TOKEN_STORAGE, sessionToken);
    start();
  } catch (e) { errEl.textContent = 'Could not sign in: ' + e.message; }
}

async function doLogout() {
  try { await api('/logout', { method: 'POST' }); } catch (e) {}
  localStorage.removeItem(TOKEN_STORAGE);
  sessionToken = '';
  currentUser = null;
  renderLogin();
}

function renderLogin() {
  stopTimers();
  if ($('login-email')) return;           // keep what is being typed
  var pw = h('input', { type: 'password', id: 'login-pw', placeholder: 'Password' });
  pw.addEventListener('keydown', function(e) { if (e.key === 'Enter') doLogin(); });
  var root = $('root');
  root.innerHTML = '';
  root.appendChild(h('div', { class: 'card gate' },
    h('div', { class: 'brand' }, h('span', { class: 'mark' }, 'N'), 'Notewell'),
    h('label', { class: 'field' }, h('span', {}, 'Email'),
      h('input', { type: 'email', id: 'login-email', placeholder: 'you@example.com' })),
    h('label', { class: 'field' }, h('span', {}, 'Password'), pw),
    h('div', { id: 'login-error', class: 'err' }),
    h('button', { class: 'btn primary', style: 'width:100%;justify-content:center', onclick: doLogin }, 'Sign in'),
    h('p', { class: 'hint' }, 'The administrator can sign in as admin@localhost with the server\'s key as the password.')));
}

// --- start-up ----------------------------------------------------------------------

// The Notewell app opens the window as /#token=...&view=settings so nobody signs in.
// The token is moved into storage and taken out of the address straight away.
function readAddress() {
  var p = new URLSearchParams(location.hash.slice(1));
  if (p.get('token')) {
    sessionToken = p.get('token');
    localStorage.setItem(TOKEN_STORAGE, sessionToken);
  }
  if (p.get('view')) view = p.get('view');
  if (location.hash) history.replaceState(null, '', location.pathname);
}

async function start() {
  if (!sessionToken) { renderLogin(); return; }
  $('root').innerHTML = '<div class="gate dim">Loading…</div>';
  try {
    var me = await api('/me');
    if (!me.ok) { renderLogin(); return; }
    currentUser = await me.json();
    var t = await api('/templates');
    if (t.ok) templates = (await t.json()).templates || {};
    if (isAdmin()) await Promise.all([loadAI(), loadRecorder()]);
    renderShell();
    loadSessions();
    every(10000, loadSessions);
    every(1000, function() { if (rec && (view === 'home' || rec.recording || rec.processing)) loadRecorder(true); });
    go(view === 'settings' || view === 'trash' ? view : 'home');
  } catch (e) { /* a 401 has already shown the sign-in screen */ }
}

function isAdmin() { return currentUser && currentUser.role === 'admin'; }

async function loadAI() {
  try { var r = await api('/settings/ai'); ai = r.ok ? await r.json() : null; } catch (e) { ai = null; }
}

async function loadRecorder(refresh) {
  try {
    var r = await api('/recorder');
    var d = r.ok ? await r.json() : null;
    var was = rec;
    rec = d && d.available ? d : null;
    if (!refresh) return;
    var shape = function(x) { return x ? [x.recording, x.processing, x.model_ready].join() : 'none'; };
    if (shape(was) !== shape(rec)) {
      renderRecordButton();
      if (view === 'home') renderHome();
      if (was && was.processing && rec && !rec.processing) loadSessions();
    } else {
      updateMeters();
    }
  } catch (e) {}
}

// --- the shell: sidebar + main -------------------------------------------------------

function renderShell() {
  var root = $('root');
  root.innerHTML = '';
  var searchBox = h('input', { type: 'search', id: 'search', placeholder: 'Search meetings', value: search });
  searchBox.addEventListener('input', function() {
    clearTimeout(searchBox._to);
    searchBox._to = setTimeout(function() { search = searchBox.value; loadSessions(); }, 300);
  });
  root.appendChild(h('div', { class: 'shell' },
    h('aside', { class: 'side' },
      h('div', { class: 'brand' }, h('span', { class: 'mark' }, 'N'), 'Notewell'),
      h('div', { id: 'recbtn-slot' }),
      h('nav', { class: 'nav', id: 'nav' }),
      h('div', { class: 'search' }, searchBox),
      h('div', { class: 'tagbar', id: 'tagbar' }),
      h('div', { class: 'list', id: 'list' }),
      h('div', { class: 'me' },
        h('b', {}, currentUser.name || currentUser.email), h('span', { class: 'spacer' }),
        h('button', { class: 'btn ghost small', onclick: doLogout }, 'Sign out'))),
    h('main', { class: 'main', id: 'main' })));
  renderRecordButton();
  renderNav();
  renderTagBar();
}

function renderRecordButton() {
  var slot = $('recbtn-slot');
  if (!slot) return;
  slot.innerHTML = '';
  if (!rec) return;
  var b = rec.recording
    ? h('button', { class: 'btn cta recording', onclick: stopRecording }, '■  Stop recording')
    : h('button', { class: 'btn primary cta', onclick: function() { go('home'); startRecording(); } }, '●  Record a meeting');
  slot.appendChild(h('div', { style: 'display:flex;flex-direction:column' }, b));
}

function renderNav() {
  var nav = $('nav');
  if (!nav) return;
  nav.innerHTML = '';
  var item = function(id, label, extra) {
    return h('button', { class: view === id ? 'on' : '', onclick: function() { go(id); } }, label, extra);
  };
  nav.appendChild(item('home', 'Home'));
  if (isAdmin()) nav.appendChild(item('settings', 'Settings', ai && !ai.ready ? h('span', { class: 'pill warn', style: 'margin-left:auto' }, 'set up') : null));
  nav.appendChild(item('trash', 'Trash', trashCount ? h('span', { class: 'count' }, trashCount) : null));
}

function renderTagBar() {
  var bar = $('tagbar');
  if (!bar) return;
  bar.innerHTML = '';
  // The suggested tags, then any other tag a meeting carries, so every tag can be filtered on.
  // Learned from the unfiltered list only, so they stay put while a filter is on.
  if (!search && !tagFilter) {
    usedTags = [];
    sessions.forEach(function(s) { (s.tags || []).forEach(function(t) { if (usedTags.indexOf(t) < 0) usedTags.push(t); }); });
  }
  var all = SUGGESTED_TAGS.slice();
  usedTags.concat(tagFilter ? [tagFilter] : []).forEach(function(t) { if (all.indexOf(t) < 0) all.push(t); });
  all.forEach(function(t) {
    bar.appendChild(h('span', {
      class: 'tag' + (tagFilter === t ? ' sel' : ' off'),
      onclick: function() { tagFilter = tagFilter === t ? null : t; renderTagBar(); loadSessions(); }
    }, t));
  });
}

async function loadSessions() {
  try {
    var q = new URLSearchParams();
    if (search) q.set('search', search);
    if (tagFilter) q.set('tag', tagFilter);
    var res = await api('/sessions' + (q.toString() ? '?' + q : ''));
    sessions = (await res.json()).sessions || [];
    var tr = await api('/trash');
    var trashed = tr.ok ? (await tr.json()).sessions || [] : [];
    var changed = trashed.length !== trashCount;
    trashCount = trashed.length;
    if (changed) renderNav();
    renderTagBar();
    renderList();
    if (view === 'home') renderRecent();
    if (view === 'trash') renderTrash(trashed);
  } catch (e) { if (e.message !== 'auth') console.error(e); }
}

function statusPill(s) {
  if (s.status === 'processing') return h('span', { class: 'pill warn' }, h('span', { class: 'spinner' }), ' writing notes');
  if (s.status === 'transcribing') return h('span', { class: 'pill warn' }, h('span', { class: 'spinner' }), ' transcribing');
  if (s.status === 'failed') return h('span', { class: 'pill bad' }, 'needs attention');
  return null;
}

function meetingRow(s, onclick, trashed) {
  return h('div', { class: 'item' + (s.session_id === activeId && view === 'meeting' ? ' on' : '') + (trashed ? ' trashed' : ''), onclick: onclick },
    h('div', { class: 't' }, title(s)),
    h('div', { class: 'm' },
      timeOf(s.timestamp), h('span', {}, '·'), minutes(s.duration_sec),
      s.meeting_type && s.meeting_type !== 'auto' ? h('span', {}, '· ' + cap(s.meeting_type)) : null,
      statusPill(s),
      (s.tags || []).map(function(t) { return h('span', { class: 'tag' }, t); })));
}

function renderList() {
  var list = $('list');
  if (!list) return;
  list.innerHTML = '';
  if (!sessions.length) {
    list.appendChild(h('div', { class: 'empty-list' },
      search || tagFilter ? 'Nothing matches.' : 'No meetings yet.' + (rec ? ' Press Record to start one.' : '')));
    return;
  }
  var last = null;
  sessions.forEach(function(s) {
    var g = dayGroup(s.timestamp);
    if (g !== last) { list.appendChild(h('div', { class: 'group' }, g)); last = g; }
    list.appendChild(meetingRow(s, function() { openMeeting(s.session_id); }));
  });
}

function go(v) {
  view = v;
  if (v !== 'meeting') { activeId = null; activeData = null; }
  renderNav();
  renderList();
  var main = $('main');
  main.innerHTML = '';
  main.scrollTop = 0;
  if (v === 'home') renderHome();
  else if (v === 'settings') renderSettings();
  else if (v === 'trash') { main.appendChild(h('div', { class: 'page narrow', id: 'trash-page' }, h('h1', {}, 'Trash'), h('p', { class: 'dim' }, 'Loading…'))); loadSessions(); }
}

// --- home: record, import, recent ----------------------------------------------------------

function renderHome() {
  var main = $('main');
  if (!main || view !== 'home') return;
  main.innerHTML = '';
  var page = h('div', { class: 'page' },
    h('h1', {}, greeting()),
    h('p', { class: 'dim', style: 'margin:0 0 20px' }, rec ? 'Record a call and Notewell writes the notes.' : 'Your meeting notes.'));
  if (isAdmin() && ai && !ai.ready) {
    page.appendChild(h('div', { class: 'banner warn' },
      h('div', { class: 'grow' }, h('b', {}, 'One step left: choose an AI. '),
        'Meetings are recorded and transcribed without one, but the notes need an AI to write them. There is a free option.'),
      h('button', { class: 'btn primary', onclick: function() { go('settings'); } }, 'Set up the AI')));
  }
  var left = h('div', {});
  if (rec) left.appendChild(recorderCard());
  left.appendChild(importCard());
  page.appendChild(h('div', { class: 'home-grid' }, left,
    h('div', { class: 'card recent' }, h('h2', {}, 'Recent meetings'), h('div', { id: 'recent' }))));
  main.appendChild(page);
  renderRecent();
}

function greeting() {
  var hr = new Date().getHours();
  var name = (currentUser.name || '').split(' ')[0];
  if (name.toLowerCase() === 'admin') name = '';
  return (hr < 12 ? 'Good morning' : hr < 18 ? 'Good afternoon' : 'Good evening') + (name ? ', ' + name : '');
}

function renderRecent() {
  var box = $('recent');
  if (!box) return;
  box.innerHTML = '';
  if (!sessions.length) { box.appendChild(h('p', { class: 'dim' }, 'Nothing yet. Your meetings appear here as soon as they are recorded.')); return; }
  sessions.slice(0, 8).forEach(function(s) {
    box.appendChild(meetingRow(s, function() { openMeeting(s.session_id); }));
  });
}

function recorderCard() {
  var card = h('div', { class: 'card recorder' + (rec.recording ? ' recording' : '') });
  if (rec.recording) {
    card.appendChild(h('div', { class: 'pill bad' }, '● Recording'));
    card.appendChild(h('button', { class: 'recbtn stop', title: 'Stop', onclick: stopRecording }, h('span', { class: 'sq' })));
    card.appendChild(h('div', { class: 'timer', id: 'rec-timer' }, clock(rec.seconds)));
    card.appendChild(h('div', { class: 'meters' },
      h('span', {}, 'Their audio'), h('div', { class: 'meter' }, h('i', { id: 'lvl-sys' })),
      h('span', {}, 'Your mic'), h('div', { class: 'meter' }, h('i', { id: 'lvl-mic' }))));
    card.appendChild(h('p', { class: 'consent' }, 'Both bars should move when people speak. If “Their audio” stays still, the call is playing on a different speaker: stop, pick it below, and start again.'));
  } else {
    card.appendChild(h('h2', {}, 'Record a meeting'));
    card.appendChild(h('p', { class: 'hint' }, 'Records what you hear and what you say. Works with Zoom, Teams, Meet and any other call app.'));
    card.appendChild(h('button', { class: 'recbtn', title: 'Start recording', onclick: startRecording }, h('span', { class: 'dot' })));
    card.appendChild(h('div', { class: 'timer dim' }, '0:00'));
    var opts = h('div', { class: 'rec-opts' },
      h('label', { class: 'field' }, h('span', {}, 'Listen to'), deviceSelect()),
      h('label', { class: 'field' }, h('span', {}, 'Language'), languageSelect()));
    card.appendChild(opts);
    card.appendChild(h('p', { class: 'consent' }, 'Tell people on the call that you are recording. In many places it is the law.'));
  }
  if (rec.processing) {
    card.appendChild(h('div', { class: 'banner info', style: 'margin:16px 0 0;justify-content:center' },
      h('span', { class: 'spinner' }), 'Turning the last meeting into text. It appears on the left when done.'));
  } else if (!rec.model_ready) {
    card.appendChild(h('div', { class: 'banner info', style: 'margin:16px 0 0;justify-content:center' },
      h('span', { class: 'spinner' }), 'Getting the speech model ready. The first time this downloads about 500 MB; you can record meanwhile.'));
  }
  updateMetersSoon();
  return card;
}

function updateMetersSoon() { setTimeout(updateMeters, 0); }

function updateMeters() {
  if (!rec) return;
  var t = $('rec-timer'); if (t) t.textContent = clock(rec.seconds);
  var pct = function(v) { return Math.min(100, Math.round(Math.sqrt(Math.max(0, v || 0)) * 100)) + '%'; };
  var s = $('lvl-sys'); if (s) s.style.width = pct(rec.level_system);
  var m = $('lvl-mic'); if (m) m.style.width = pct(rec.level_mic);
}

function deviceSelect() {
  var sel = h('select', { onchange: function() { saveRecorder({ audio_source: sel.value }); } });
  var fill = function() {
    sel.innerHTML = '';
    (devices || [{ id: rec.settings.audio_source, name: 'Loading…' }]).forEach(function(d) {
      sel.appendChild(h('option', { value: d.id }, d.name));
    });
    sel.value = rec.settings.audio_source;
  };
  fill();
  if (!devices) api('/recorder/devices').then(function(r) { return r.json(); })
    .then(function(d) { devices = d.devices || []; fill(); }).catch(function() {});
  return sel;
}

var LANGUAGES = [['', 'Detect automatically'], ['en', 'English'], ['de', 'German'], ['fr', 'French'], ['es', 'Spanish'],
  ['it', 'Italian'], ['nl', 'Dutch'], ['pt', 'Portuguese'], ['pl', 'Polish'], ['uk', 'Ukrainian'], ['ru', 'Russian'],
  ['tr', 'Turkish'], ['ar', 'Arabic'], ['hi', 'Hindi'], ['ta', 'Tamil'], ['zh', 'Chinese'], ['ja', 'Japanese'], ['ko', 'Korean']];

function languageSelect() {
  var sel = h('select', { onchange: function() { saveRecorder({ language: sel.value }); } },
    LANGUAGES.map(function(l) { return h('option', { value: l[0] }, l[1]); }));
  sel.value = rec.settings.language || '';
  if (sel.value !== (rec.settings.language || '')) {       // a code not in the list
    sel.appendChild(h('option', { value: rec.settings.language }, rec.settings.language));
    sel.value = rec.settings.language;
  }
  return sel;
}

async function saveRecorder(changes) {
  var r = await api('/recorder/settings', { method: 'PUT', json: changes });
  if (!r.ok) { toast('Not saved: ' + await errorText(r)); return; }
  await loadRecorder();
  toast('Saved');
}

async function startRecording() {
  var r = await api('/recorder/start', { method: 'POST' });
  if (!r.ok) { toast('Could not start: ' + await errorText(r)); return; }
  await loadRecorder(true);
}

async function stopRecording() {
  var r = await api('/recorder/stop', { method: 'POST' });
  if (!r.ok) { toast('Could not stop: ' + await errorText(r)); return; }
  await loadRecorder(true);
  toast('Stopped. The notes are being written.');
}

function importCard() {
  var input = h('input', { type: 'file', accept: 'audio/*,video/*,.mp3,.m4a,.wav,.ogg,.webm,.mp4', style: 'display:none',
    onchange: function() { if (input.files[0]) uploadAudio(input.files[0]); } });
  var drop = h('div', { class: 'drop', onclick: function() { input.click(); } },
    h('b', {}, 'Import a recording'), h('div', { class: 'hint' }, 'Drop an audio or video file here, or click to choose one.'));
  drop.addEventListener('dragover', function(e) { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', function() { drop.classList.remove('over'); });
  drop.addEventListener('drop', function(e) {
    e.preventDefault(); drop.classList.remove('over');
    if (e.dataTransfer.files[0]) uploadAudio(e.dataTransfer.files[0]);
  });
  return h('div', { class: 'card' }, drop, input, h('div', { id: 'upload-status', class: 'result' }));
}

async function uploadAudio(file) {
  var st = $('upload-status');
  st.className = 'result';
  st.textContent = 'Uploading ' + file.name + '…';
  var fd = new FormData();
  fd.append('file', file);
  try {
    var r = await api('/upload-audio?meeting_type=auto', { method: 'POST', body: fd });
    if (r.status === 501) { st.className = 'result bad'; st.textContent = 'This server cannot transcribe audio files. Import works in the Notewell app.'; return; }
    if (!r.ok) { st.className = 'result bad'; st.textContent = 'Not imported: ' + await errorText(r); return; }
    var d = await r.json();
    st.className = 'result ok';
    st.textContent = 'Imported. Transcribing now; it appears on the left.';
    loadSessions();
    if (d.session_id) openMeeting(d.session_id);
  } catch (e) { if (e.message !== 'auth') { st.className = 'result bad'; st.textContent = 'Upload failed: ' + e.message; } }
}

// --- a meeting -------------------------------------------------------------------------------

async function openMeeting(id, fromTrash) {
  view = 'meeting';
  activeId = id;
  renderNav();
  renderList();
  var r = await api(fromTrash ? '/trash/' + id : '/results/' + id);
  if (activeId !== id) return;
  var main = $('main');
  if (!r.ok) { main.innerHTML = ''; main.appendChild(h('div', { class: 'page' }, h('p', { class: 'dim' }, 'This meeting could not be loaded.'))); return; }
  activeData = await r.json();
  activeData.session_id = activeData.session_id || id;
  renderMeeting(fromTrash);
  // While the AI is writing, check every 3 seconds and redraw once it is done.
  if (!fromTrash && (activeData.status === 'processing' || activeData.status === 'transcribing')) {
    setTimeout(function() { if (activeId === id && view === 'meeting') openMeeting(id); }, 3000);
  }
}

function renderMeeting(fromTrash, editing) {
  var d = activeData, id = d.session_id;
  var main = $('main');
  var keepScroll = main.scrollTop;
  main.innerHTML = '';
  var page = h('div', { class: 'page' });
  var busy = d.status === 'processing' || d.status === 'transcribing';

  page.appendChild(h('div', { class: 'mhead' },
    h('div', { class: 'titles' },
      h('h1', {}, title(d)),
      h('div', { class: 'meta' },
        new Date(d.timestamp).toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }),
        h('span', {}, '·'), minutes(d.duration_sec),
        d.meeting_type ? h('span', {}, '· ' + cap(d.meeting_type) + ' meeting') : null,
        d.edited_at ? h('span', { class: 'pill' }, 'edited') : null),
      fromTrash ? null : tagEditor(d)),
    fromTrash ? h('div', { class: 'row' },
        h('button', { class: 'btn primary', onclick: function() { restore(id); } }, 'Restore'),
        h('button', { class: 'btn danger', onclick: function() { purge(id); } }, 'Delete for good'))
      : h('div', { class: 'row' },
        busy || editing ? null : h('button', { class: 'btn', onclick: function() { renderMeeting(false, true); } }, 'Edit notes'),
        h('button', { class: 'btn', onclick: copyNotes }, 'Copy'),
        downloadMenu(id),
        h('button', { class: 'btn danger', onclick: function() { trash(id); } }, 'Delete'))));

  if (fromTrash) page.appendChild(h('div', { class: 'banner warn' }, 'This meeting is in the trash. Restore it to edit, tag or download it.'));
  if (busy) page.appendChild(h('div', { class: 'banner info' }, h('span', { class: 'spinner' }),
    d.status === 'transcribing' ? 'Turning the recording into text…' : 'Writing the notes. This usually takes under a minute.'));
  if (d.status === 'failed') {
    var notSetUp = (d.error || '').indexOf('No AI is set up') === 0;
    page.appendChild(h('div', { class: 'banner bad' },
      h('div', { class: 'grow' }, h('b', {}, notSetUp ? 'No notes yet: no AI is set up. ' : 'The notes could not be written. '),
        notSetUp ? 'The transcript is safe. Set up an AI, then press “Write the notes” below.' : (d.error || '')),
      notSetUp && isAdmin() ? h('button', { class: 'btn primary', onclick: function() { go('settings'); } }, 'Set up the AI') : null));
  }
  if (!fromTrash && !busy && !editing && d.transcript) page.appendChild(rewritePanel(d));

  var notesPane = h('div', { class: 'pane' },
    h('div', { class: 'pane-h' }, 'Notes'),
    h('div', { class: 'pane-b' }, editing ? editBox(d) : notesBody(d)));
  var txPane = h('div', { class: 'pane tx' },
    h('div', { class: 'pane-h' }, 'Transcript', h('span', { class: 'spacer' }),
      d.transcript ? h('button', { class: 'btn ghost small', onclick: function() { copyText(d.transcript, 'Transcript copied'); } }, 'Copy') : null),
    h('div', { class: 'pane-b' }, transcriptBody(d.transcript)));
  page.appendChild(h('div', { class: 'split' }, notesPane, txPane));
  main.appendChild(page);
  main.scrollTop = keepScroll;
}

function notesBody(d) {
  var summary = written(d.summary), actions = written(d.actions);
  if (!summary && !actions) {
    return h('p', { class: 'dim', style: 'padding-top:12px' },
      d.status === 'complete' ? 'The AI wrote nothing for this meeting.'
        : d.status === 'failed' ? 'No notes yet.' : 'The notes appear here once they are written.');
  }
  var box = h('div', {});
  if (summary) box.appendChild(markdownEl(summary));
  if (actions) box.appendChild(markdownEl(withHeading(actions)));
  return box;
}

// The AI usually starts its action plan with its own heading; add one only when it did not.
function withHeading(actions) {
  return /^\s*#/.test(actions) ? actions : '## Action plan\n\n' + actions;
}

function transcriptBody(text) {
  if (!text) return h('p', { class: 'dim' }, 'No transcript yet.');
  var box = h('div', {});
  text.split('\n').forEach(function(line) {
    if (!line.trim()) return;
    var m = line.match(/^\[(\d+:\d\d(?::\d\d)?)\]\s*(?:\[([^\]]+)\]\s*)?(.*)$/);
    if (!m) { box.appendChild(h('div', { class: 'txline' }, line)); return; }
    box.appendChild(h('div', { class: 'txline' },
      h('span', { class: 'ts' }, m[1]),
      m[2] ? h('span', { class: 'who' + (m[2] === 'Me' ? ' mine' : '') }, m[2] + ':') : null,
      m[3]));
  });
  return box;
}

function editBox(d) {
  var s = h('textarea', { rows: 16, value: written(d.summary) });
  var a = h('textarea', { rows: 8, value: written(d.actions) });
  var err = h('div', { class: 'result bad' });
  var save = async function() {
    var r = await api('/results/' + d.session_id + '/notes', { method: 'PUT', json: { summary: s.value, actions: a.value } });
    if (r.status === 409) { err.textContent = 'The AI is still writing these notes. Try again in a minute.'; return; }
    if (!r.ok) { err.textContent = 'Not saved: ' + await errorText(r); return; }
    toast('Notes saved');
    openMeeting(d.session_id);
  };
  return h('div', { class: 'editbox', style: 'padding-top:14px' },
    h('label', { class: 'field' }, h('span', {}, 'Summary'), s),
    h('label', { class: 'field' }, h('span', {}, 'Action plan'), a),
    h('p', { class: 'hint' }, 'Plain text with # headings, - bullets and **bold**. Your edit is kept unless you press “Write the notes again”.'),
    err,
    h('div', { class: 'row' },
      h('button', { class: 'btn primary', onclick: save }, 'Save'),
      h('button', { class: 'btn', onclick: function() { renderMeeting(false, false); } }, 'Cancel')));
}

function rewritePanel(d) {
  var sel = h('select', {}, Object.keys(templates).map(function(k) {
    return h('option', { value: k, title: templates[k] }, cap(k) + ' meeting');
  }));
  sel.value = templates[d.meeting_type] ? d.meeting_type : 'generic';
  var ctx = h('textarea', { placeholder: 'Anything the AI should know: who was there, what the meeting was for, names it keeps getting wrong…', value: d.context || '' });
  var go_ = async function() {
    var r = await api('/results/' + d.session_id + '/reprocess?meeting_type=' + encodeURIComponent(sel.value),
      { method: 'POST', json: { context: ctx.value } });
    if (!r.ok) { toast('Could not start: ' + await errorText(r)); return; }
    toast('Writing the notes again…');
    loadSessions();
    setTimeout(function() { openMeeting(d.session_id); }, 800);
  };
  var label = written(d.summary) || written(d.actions) ? 'Write the notes again' : 'Write the notes';
  return h('div', { class: 'card', style: 'padding:14px 16px' },
    h('div', { class: 'hint', style: 'margin-bottom:8px;font-weight:600' }, label + ' — choose a style and add context if it helps'),
    h('div', { class: 'rewrite' }, sel, ctx, h('button', { class: 'btn primary', onclick: go_ }, label)),
    d.edited_at ? h('div', { class: 'hint' }, 'This replaces your edited notes.') : null);
}

function tagEditor(d) {
  var tags = (d.tags || []).slice();
  var box = h('div', { class: 'tags-edit' });
  var save = function() { d.tags = tags; draw();
    api('/results/' + d.session_id + '/tags', { method: 'PUT', json: { tags: tags } }).then(loadSessions); };
  var draw = function() {
    box.innerHTML = '';
    tags.forEach(function(t) {
      box.appendChild(h('span', { class: 'tag' }, t,
        h('span', { class: 'x', title: 'Remove', onclick: function() { tags = tags.filter(function(x) { return x !== t; }); save(); } }, ' ×')));
    });
    var inp = h('input', { type: 'text', placeholder: '+ tag' });
    inp.onkeydown = function(e) {
      var v = inp.value.trim().toLowerCase();
      if (e.key === 'Enter' && v && tags.indexOf(v) === -1) { tags.push(v); save(); }
    };
    box.appendChild(inp);
    SUGGESTED_TAGS.forEach(function(t) {
      if (tags.indexOf(t) === -1) box.appendChild(h('span', { class: 'tag off', onclick: function() { tags.push(t); save(); } }, '+ ' + t));
    });
  };
  draw();
  return box;
}

function downloadMenu(id) {
  var menu = h('div', { class: 'menu' });
  var list = h('div', { class: 'drop-menu', style: 'display:none' },
    h('button', { onclick: function() { download(id, 'md'); } }, 'Markdown (.md)'),
    h('button', { onclick: function() { download(id, 'txt'); } }, 'Plain text (.txt)'),
    h('button', { onclick: function() { download(id, 'pdf'); } }, 'PDF'));
  var btn = h('button', { class: 'btn', onclick: function(e) {
    e.stopPropagation();
    list.style.display = list.style.display === 'none' ? '' : 'none';
  } }, 'Download ▾');
  document.addEventListener('click', function() { list.style.display = 'none'; });
  menu.appendChild(btn);
  menu.appendChild(list);
  return menu;
}

function fileName(ext) {
  var base = (activeData ? title(activeData) : 'meeting').replace(/[^\w\- ]+/g, '').trim().slice(0, 60) || 'meeting';
  return base + '.' + ext;
}

function saveBlob(blob, name) {
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(function() { URL.revokeObjectURL(a.href); }, 1000);
}

async function download(id, format) {
  if (format === 'pdf') {
    var p = await api('/results/' + id + '/pdf');
    if (p.status === 501) { printNotes(); return; }
    if (!p.ok) { toast('Download failed: ' + await errorText(p)); return; }
    saveBlob(await p.blob(), fileName('pdf'));
    return;
  }
  var r = await api('/results/' + id + '/download');
  if (!r.ok) { toast('Download failed: ' + await errorText(r)); return; }
  var md = await r.text();
  if (format === 'txt') {
    md = md.replace(/^#{1,6}\s*/gm, '').replace(/\*{1,2}([^*]+)\*{1,2}/g, '$1').replace(/\|/g, ' ').replace(/---+/g, '---');
    saveBlob(new Blob([md], { type: 'text/plain' }), fileName('txt'));
  } else {
    saveBlob(new Blob([md], { type: 'text/markdown' }), fileName('md'));
  }
}

// Without the server's PDF engine (the Windows app has none), the print window makes the
// PDF: Windows and macOS both offer "Save as PDF" there. Only the notes are printed.
function printNotes() {
  var d = activeData;
  var area = h('div', { id: 'print-area' }, h('h1', {}, title(d)));
  var summary = written(d.summary), actions = written(d.actions);
  if (summary) area.appendChild(markdownEl(summary));
  if (actions) area.appendChild(markdownEl(withHeading(actions)));
  document.body.appendChild(area);
  document.body.classList.add('printing');
  var prevTitle = document.title;
  document.title = fileName('pdf').replace(/\.pdf$/, '');     // the suggested file name
  toast('Choose “Save as PDF” as the printer');
  setTimeout(function() {
    window.print();
    document.body.classList.remove('printing');
    area.remove();
    document.title = prevTitle;
  }, 50);
}

function copyText(text, done) {
  navigator.clipboard.writeText(text).then(function() { toast(done); }, function() { toast('Could not copy'); });
}

function copyNotes() {
  var d = activeData;
  var actions = written(d.actions);
  copyText('# ' + title(d) + '\n\n' + written(d.summary) + (actions ? '\n\n' + withHeading(actions) : ''), 'Notes copied');
}

async function trash(id) {
  if (!confirm('Move this meeting to the trash? You can restore it from Trash.')) return;
  var r = await api('/results/' + id, { method: 'DELETE' });
  if (!r.ok) { toast('Not deleted: ' + await errorText(r)); return; }
  toast('Moved to the trash');
  go('home');
  loadSessions();
}

async function restore(id) {
  var r = await api('/results/' + id + '/restore', { method: 'POST' });
  if (!r.ok) { toast('Not restored: ' + await errorText(r)); return; }
  toast('Restored');
  await loadSessions();
  openMeeting(id);
}

async function purge(id) {
  if (!confirm('Delete this meeting for good? This cannot be undone.')) return;
  var r = await api('/results/' + id + '/purge', { method: 'DELETE' });
  if (!r.ok) { toast('Not deleted: ' + await errorText(r)); return; }
  toast('Deleted for good');
  go('trash');
}

// --- trash -----------------------------------------------------------------------------------

function renderTrash(items) {
  var page = $('trash-page');
  if (!page || view !== 'trash') return;
  page.innerHTML = '';
  page.appendChild(h('h1', {}, 'Trash'));
  page.appendChild(h('p', { class: 'dim', style: 'margin:0 0 16px' }, 'Deleted meetings wait here until you restore them or delete them for good.'));
  if (!items.length) { page.appendChild(h('div', { class: 'card dim' }, 'The trash is empty.')); return; }
  var card = h('div', { class: 'card', style: 'padding:8px' });
  items.forEach(function(s) {
    card.appendChild(h('div', { class: 'row', style: 'padding:4px' },
      h('div', { style: 'flex:1;min-width:0' }, meetingRow(s, function() { openMeeting(s.session_id, true); }, true)),
      h('button', { class: 'btn small', onclick: function() { restore(s.session_id); } }, 'Restore'),
      h('button', { class: 'btn small danger', onclick: function() { purge(s.session_id); } }, 'Delete for good')));
  });
  page.appendChild(card);
}

// --- settings --------------------------------------------------------------------------------

function renderSettings() {
  var main = $('main');
  main.innerHTML = '';
  var page = h('div', { class: 'page narrow' }, h('h1', {}, 'Settings'), h('p', { class: 'dim', style: 'margin:0 0 20px' }, 'Changes take effect straight away.'));
  if (!isAdmin()) { page.appendChild(h('div', { class: 'card' }, 'Only the administrator can change settings.')); main.appendChild(page); return; }
  page.appendChild(aiCard());
  if (rec) page.appendChild(recordingCard());
  if (!rec) page.appendChild(peopleCard());
  main.appendChild(page);
}

function aiCard() {
  var card = h('div', { class: 'card' }, h('h2', {}, 'AI that writes the notes'));
  if (!ai) { card.appendChild(h('p', { class: 'dim' }, 'Could not read the AI settings.')); return card; }
  if (!ai.editable) {
    card.appendChild(h('p', { class: 'hint' }, 'Set by the server\'s configuration file, so it cannot be changed here.'));
    card.appendChild(h('p', {}, h('b', {}, ai.model || '?'), ' at ', h('code', {}, ai.base_url || '?')));
    return card;
  }
  if (!ai.ready) {
    card.appendChild(h('p', { class: 'hint' }, 'Notewell sends the transcript to an AI service that writes the notes. Pick one below. The free options work well; you only need a free key from their website.'));
    card.appendChild(h('div', { class: 'steps' },
      h('span', {}, h('b', {}, '1'), 'Choose a service'),
      h('span', {}, h('b', {}, '2'), 'Get a key from its website'),
      h('span', {}, h('b', {}, '3'), 'Paste it here and press Save')));
  } else {
    card.appendChild(h('div', { class: 'banner ok', style: 'margin:10px 0' }, '✓ Ready. Using ', h('b', {}, ai.model), ' (' + presetName(ai.provider) + ').'));
  }

  var chosen = ai.presets.filter(function(p) { return p.id === ai.provider; })[0] || ai.presets[0];
  var grid = h('div', { class: 'presets' });
  var fields = h('div', {});
  var draw = function() {
    grid.innerHTML = '';
    ai.presets.forEach(function(p) {
      grid.appendChild(h('button', { class: 'preset' + (p.id === chosen.id ? ' on' : ''), onclick: function() { chosen = p; draw(); } },
        h('b', {}, p.name),
        h('span', { class: 'hint' }, p.free ? (p.needs_key ? 'Free · needs a key' : 'Free · runs on this computer') : (p.id === 'custom' ? 'Any OpenAI-compatible service' : 'Paid · needs a key'))));
    });
    fields.innerHTML = '';
    var same = chosen.id === ai.provider;
    var url = h('input', { type: 'text', value: same ? ai.base_url : (chosen.base_url || ''), placeholder: 'https://…/v1' });
    var model = h('input', { type: 'text', value: same ? ai.model : (chosen.model || ''), placeholder: 'model name' });
    var keyBox = h('input', { type: 'password', autocomplete: 'off',
      placeholder: same && ai.key_hint ? 'Saved (ending ' + ai.key_hint + '). Leave blank to keep it.' : (chosen.needs_key ? 'Paste your key' : 'Not needed') });
    var result = h('div', { class: 'result' });
    if (chosen.note) fields.appendChild(h('p', { class: 'hint' }, chosen.note));
    if (chosen.key_url) fields.appendChild(h('p', {}, h('a', { href: chosen.key_url, target: '_blank', rel: 'noopener noreferrer' },
      (chosen.free ? 'Get a free key from ' : 'Get a key from ') + chosen.name.replace(/ \(.*\)$/, '') + ' ↗')));
    fields.appendChild(h('label', { class: 'field' }, h('span', {}, 'Key'), keyBox));
    var adv = h('details', { open: chosen.id === 'custom' || chosen.id === 'ollama' ? true : null },
      h('summary', { class: 'hint', style: 'cursor:pointer;margin-bottom:10px' }, 'Address and model'),
      h('div', { class: 'two' },
        h('label', { class: 'field' }, h('span', {}, 'Address'), url),
        h('label', { class: 'field' }, h('span', {}, 'Model'), model)));
    fields.appendChild(adv);
    var values = function() {
      var typed = keyBox.value.trim();
      return { provider: chosen.id, base_url: url.value.trim(), model: model.value.trim(), api_key: typed };
    };
    var save = async function() {
      result.className = 'result'; result.textContent = 'Saving…';
      var r = await api('/settings/ai', { method: 'PUT', json: values() });
      if (!r.ok) { result.className = 'result bad'; result.textContent = '✗ ' + await errorText(r); return; }
      await loadAI();
      renderNav();
      var ok = await testAI(result, values());
      if (ok) setTimeout(renderSettings, 1500);
    };
    fields.appendChild(h('div', { class: 'row' },
      h('button', { class: 'btn primary', onclick: save }, 'Save'),
      h('button', { class: 'btn', onclick: function() { testAI(result, values()); } }, 'Test without saving')));
    fields.appendChild(result);
  };
  draw();
  card.appendChild(grid);
  card.appendChild(fields);
  return card;
}

function presetName(id) {
  var p = (ai.presets || []).filter(function(x) { return x.id === id; })[0];
  return p ? p.name : (id || 'custom');
}

// Tries the values on screen, saved or not. A blank key means the saved one.
async function testAI(result, values) {
  result.className = 'result'; result.textContent = 'Asking the AI a test question…';
  var r = await api('/settings/ai/test', { method: 'POST', json: values });
  var d = await r.json().catch(function() { return {}; });
  if (r.ok && d.ok) { result.className = 'result ok'; result.textContent = '✓ It answered in ' + d.seconds + ' s. Notes will be written with it.'; return true; }
  result.className = 'result bad';
  result.textContent = '✗ ' + (d.error || detailText(d.detail) || ('error ' + r.status));
  return false;
}

// FastAPI sends a sentence for our own errors and a list for a malformed form.
function detailText(detail) {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail) && detail[0]) return (detail[0].loc || []).slice(-1)[0] + ': ' + detail[0].msg;
  return '';
}

function recordingCard() {
  var s = rec.settings;
  var model = h('select', { onchange: function() { saveRecorder({ whisper_model: model.value }); } },
    [['tiny', 'Tiny: fastest, rough'], ['base', 'Base: fast'], ['small', 'Small: good balance (recommended)'],
     ['medium', 'Medium: better, slower'], ['large-v3', 'Large: best, needs a fast PC'],
     ['distil-large-v3', 'Distil-large: near best, English only']].map(function(m) { return h('option', { value: m[0] }, m[1]); }));
  model.value = s.whisper_model;
  var auto = h('input', { type: 'checkbox', checked: !!s.auto_detect, onchange: function() { saveRecorder({ auto_detect: auto.checked }); } });
  var live = h('input', { type: 'checkbox', checked: !!s.live_transcript, onchange: function() { saveRecorder({ live_transcript: live.checked }); } });
  return h('div', { class: 'card' },
    h('h2', {}, 'Recording'),
    h('p', { class: 'hint' }, 'How Notewell listens and turns speech into text. All of this happens on this computer.'),
    h('div', { class: 'two', style: 'margin-top:12px' },
      h('label', { class: 'field' }, h('span', {}, 'Listen to'), deviceSelect()),
      h('label', { class: 'field' }, h('span', {}, 'Language'), languageSelect())),
    h('label', { class: 'field' }, h('span', {}, 'Speech-to-text quality'), model),
    h('p', { class: 'hint', style: 'margin-top:-6px' }, 'A bigger model downloads once (up to 3 GB) and takes longer after each meeting.'),
    h('label', { class: 'switch' }, auto, h('span', {}, h('b', {}, 'Start recording by itself'),
      h('div', { class: 'hint' }, 'When Zoom, Teams, Webex, Slack, Discord or a browser call starts. Stops when it ends.'))),
    h('label', { class: 'switch' }, live, h('span', {}, h('b', {}, 'Live transcript window'),
      h('div', { class: 'hint' }, 'Shows the words as they are spoken. Uses more of the computer while recording.'))));
}

// --- people (server installs) ----------------------------------------------------------------

// API keys are stored only as hashes, so a key can be shown exactly once: now.
function showKeyOnce(email, key) {
  window.prompt('API key for ' + email + '. Copy it now - it cannot be shown again.', key);
}

function peopleCard() {
  var card = h('div', { class: 'card' }, h('h2', {}, 'People'),
    h('p', { class: 'hint' }, 'Everyone who can sign in. Each person sees only their own meetings; the administrator sees all.'),
    h('div', { id: 'people' }, h('p', { class: 'dim' }, 'Loading…')));
  setTimeout(refreshPeople, 0);
  return card;
}

async function refreshPeople() {
  var box = $('people');
  if (!box) return;
  try {
    var res = await api('/admin/users');
    var users = (await res.json()).users || [];
    box.innerHTML = '';
    var table = h('table', { class: 'users' }, h('tr', {}, h('th', {}, 'Email'), h('th', {}, 'Name'), h('th', {}, 'Role'), h('th', {}, 'Recorder key'), h('th', {})));
    users.forEach(function(u) {
      table.appendChild(h('tr', {},
        h('td', {}, u.email), h('td', {}, u.name), h('td', {}, u.role),
        h('td', {}, u.has_api_key ? 'set ' : 'none ',
          h('button', { class: 'btn small', title: 'Create a new key. The old one stops working.', onclick: async function() {
            if (!confirm('Create a new key for ' + u.email + '? Their old key stops working immediately.')) return;
            var r = await api('/admin/users/' + encodeURIComponent(u.email) + '/rotate-key', { method: 'POST' });
            if (r.ok) { var d = await r.json(); showKeyOnce(u.email, d.api_key); }
          } }, 'New key')),
        h('td', {}, u.email === currentUser.email ? null : h('button', { class: 'btn small danger', onclick: async function() {
          if (!confirm('Remove ' + u.email + '? They can no longer sign in.')) return;
          await api('/admin/users/' + encodeURIComponent(u.email), { method: 'DELETE' });
          refreshPeople();
        } }, 'Remove'))));
    });
    box.appendChild(table);
    var e = h('input', { type: 'email', placeholder: 'Email' });
    var n = h('input', { type: 'text', placeholder: 'Name' });
    var p = h('input', { type: 'password', placeholder: 'Password (10+ characters)' });
    var err = h('div', { class: 'result bad' });
    box.appendChild(h('h3', { style: 'font-size:14px;margin:16px 0 8px' }, 'Add a person'));
    box.appendChild(h('div', { class: 'row' },
      h('div', { style: 'flex:1;min-width:150px' }, e), h('div', { style: 'flex:1;min-width:120px' }, n),
      h('div', { style: 'flex:1;min-width:150px' }, p),
      h('button', { class: 'btn primary', onclick: async function() {
        if (!e.value.trim() || !n.value.trim() || !p.value) return;
        var r = await api('/admin/users', { method: 'POST', json: { email: e.value.trim(), name: n.value.trim(), password: p.value } });
        if (!r.ok) { err.textContent = await errorText(r); return; }
        var made = await r.json();
        if (made.api_key) showKeyOnce(made.email, made.api_key);
        refreshPeople();
      } }, 'Add')));
    box.appendChild(err);
  } catch (ex) { if (ex.message !== 'auth') box.textContent = 'Could not load people: ' + ex.message; }
}

readAddress();
start();
