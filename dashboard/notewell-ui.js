// notewell-ui.js - the few lines of script the design system needs (chunk 07, boxes added 20 Nov 2026).
// nwToast('Saved', 'success' | 'error' | 'info'): a short message bottom right that goes away by itself.
// nwConfirm({title, body, ok, cancel, danger}) -> Promise<true|false>: a box with two named buttons.
// nwPrompt({title, body, label, value, type, ok, cancel, minLength, tooShort}) -> Promise<text|null>.
// nwAlert({title, body, ok}) -> Promise: a box with one button.
// nwShareBox({title, url, on, passwordSet, expires, save}) -> Promise<true|false>: the one Share box (link, password, end date).
// nwErrorBanner(text, onRetry) and nwEmpty(title, sentence, buttonText, onClick) return an element to put in the page.
// Screen readers hear toasts (role=status, or role=alert for errors); boxes are role=dialog and keep the keyboard inside.
(function () {
  function region() {
    var r = document.querySelector('.toast-region');
    if (!r) { r = document.createElement('div'); r.className = 'toast-region'; document.body.appendChild(r); }
    return r;
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  window.nwToast = function (text, kind) {
    var t = el('div', 'toast toast-' + (kind || 'info'), text);
    t.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    region().appendChild(t);
    setTimeout(function () { t.remove(); }, kind === 'error' ? 7000 : 3500);
  };

  var seq = 0;
  // One box at a time. Resolves with the OK value, or with the cancel value (Escape, Cancel, click outside).
  function box(o, withInput) {
    return new Promise(function (resolve) {
      var back = el('div', 'dialog-backdrop'), d = el('div', 'dialog'), id = 'nw-dlg-' + (++seq);
      d.setAttribute('role', o.danger ? 'alertdialog' : 'dialog');
      d.setAttribute('aria-modal', 'true');
      d.setAttribute('aria-labelledby', id);
      var head = el('div', 'dialog-header'), title = el('h2', 'dialog-title', o.title || 'Notewell');
      title.id = id; head.appendChild(title); d.appendChild(head);
      var body = el('div', 'dialog-body');
      if (o.body) body.appendChild(el('p', 'muted', o.body));
      var input = null, err = null;
      if (withInput) {
        var f = el('div', 'field');
        var lab = el('label', 'label', o.label || '');
        input = el('input', 'input');
        input.type = o.type || 'text';
        input.id = id + '-in'; lab.htmlFor = input.id;
        input.value = o.value || '';
        input.autocomplete = o.type === 'password' ? 'new-password' : 'off';
        err = el('div', 'hint'); err.setAttribute('role', 'alert');
        if (o.label) f.appendChild(lab);
        f.appendChild(input); f.appendChild(err); body.appendChild(f);
      }
      // A box with its own fields: fill(body) adds them and returns what gets the keyboard first;
      // submit() returns '' (or a promise of '') when done, or the reason it failed, shown in the box.
      var firstFocus = o.fill ? o.fill(body) : null;
      if (o.submit) { err = el('div', 'hint'); err.setAttribute('role', 'alert'); body.appendChild(err); }
      d.appendChild(body);
      var foot = el('div', 'dialog-footer');
      var cancel = o.cancel === false ? null : el('button', 'btn btn-secondary', o.cancel || 'Cancel');
      var ok = el('button', 'btn ' + (o.danger ? 'btn-danger-solid' : 'btn-primary'), o.ok || 'OK');
      if (cancel) { cancel.type = 'button'; foot.appendChild(cancel); }
      ok.type = 'button'; foot.appendChild(ok); d.appendChild(foot);
      back.appendChild(d);
      var before = document.activeElement;
      function close(v) {
        document.removeEventListener('keydown', key, true);
        back.remove();
        if (before && before.focus) { try { before.focus(); } catch (e) {} }
        resolve(v);
      }
      var no = withInput ? null : false;
      function yes() {
        if (o.submit) {
          ok.disabled = true; err.textContent = '';
          return Promise.resolve(o.submit()).then(function (why) {
            ok.disabled = false;
            if (why) err.textContent = why; else close(true);
          });
        }
        if (!withInput) return close(true);
        var v = input.value;
        if (o.minLength && v.length < o.minLength) {
          err.textContent = o.tooShort || ('At least ' + o.minLength + ' characters, please.');
          input.setAttribute('aria-invalid', 'true'); input.focus(); return;
        }
        close(v);
      }
      function key(e) {
        if (e.key === 'Escape') { e.preventDefault(); close(no); }
        else if (e.key === 'Enter' && ((withInput && document.activeElement === input) ||
                 (o.submit && /^(text|password|datetime-local)$/.test(document.activeElement.type)))) { e.preventDefault(); yes(); }
        else if (e.key === 'Tab') {   // keep the keyboard inside the box
          var all = d.querySelectorAll('button, input, a[href]'), first = all[0], last = all[all.length - 1];
          if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
          else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
      }
      ok.onclick = yes;
      if (cancel) cancel.onclick = function () { close(no); };
      back.onclick = function (e) { if (e.target === back) close(no); };
      document.addEventListener('keydown', key, true);
      document.body.appendChild(back);
      (input || firstFocus || (o.danger && cancel) || ok).focus();
      if (input) input.select();
    });
  }
  window.nwConfirm = function (o) { return box(o || {}, false); };
  window.nwPrompt = function (o) { return box(o || {}, true); };
  window.nwAlert = function (o) {
    if (typeof o === 'string') o = { body: o };
    o = o || {}; o.cancel = false;
    return box(o, false);
  };

  // The one Share box (20 Nov 2026), used by the website and the phone page: the link (copy, watch),
  // link on or off, password, end date, and one Save. save(changes) sends only what changed -
  // {share_on, password ('' removes it), expires_at (ISO or null)} - and resolves '' or the reason it failed.
  function localInput(sec) { var d = new Date(sec * 1000); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16); }
  function field(labelText, control, hintText) {
    var f = el('div', 'field'), lab = el('label', 'label', labelText);
    control.id = 'nw-f-' + (++seq); lab.htmlFor = control.id;
    f.appendChild(lab); f.appendChild(control);
    if (hintText) f.appendChild(el('div', 'hint', hintText));
    return f;
  }
  function check(text, on) {
    var l = el('label', 'check'), c = el('input');
    c.type = 'checkbox'; c.checked = !!on;
    l.appendChild(c); l.appendChild(document.createTextNode(text));
    return { label: l, box: c };
  }
  window.nwShareBox = function (o) {
    var on, pw, nopw = null, end;
    function fill(body) {
      var copy = null;
      if (o.url) {
        var link = el('input', 'input'); link.readOnly = true; link.value = o.url;
        copy = el('button', 'btn btn-secondary btn-sm', 'Copy'); copy.type = 'button';
        copy.onclick = function () {
          link.select();
          (navigator.clipboard ? navigator.clipboard.writeText(o.url) : Promise.reject()).then(function () {
            copy.textContent = 'Copied'; setTimeout(function () { copy.textContent = 'Copy'; }, 1500);
          }, function () { nwToast('Press Ctrl+C to copy the link.', 'info'); });
        };
        var watch = el('a', 'btn btn-secondary btn-sm', 'Watch');
        watch.href = o.url; watch.target = '_blank'; watch.rel = 'noopener';
        var lf = field('Link', link), row = el('div', 'row share-link');
        lf.insertBefore(row, link); row.appendChild(link); row.appendChild(copy); row.appendChild(watch);
        body.appendChild(lf);
      }
      on = check('Link on: anyone with the link can watch', o.on);
      var of = el('div', 'field'); of.appendChild(on.label);
      of.appendChild(el('div', 'hint', 'Switch it off and nobody can watch, even with the link.'));
      body.appendChild(of);
      pw = el('input', 'input'); pw.type = 'password'; pw.autocomplete = 'new-password';
      var pf = field('Password', pw, o.passwordSet
        ? 'A password is set. Type a new one to change it, or leave this empty to keep it.'
        : 'Optional, at least 8 characters. People type it before they can watch.');
      if (o.passwordSet) {
        nopw = check('Remove the password', false);
        nopw.box.onchange = function () { pw.disabled = nopw.box.checked; };
        pf.appendChild(nopw.label);
      }
      body.appendChild(pf);
      end = el('input', 'input'); end.type = 'datetime-local';
      if (o.expires) end.value = localInput(o.expires);
      body.appendChild(field('Link stops working on', end, 'Optional. Leave it empty and the link works until you switch it off.'));
      return copy || on.box;
    }
    function submit() {
      var ch = {};
      if (on.box.checked !== !!o.on) ch.share_on = on.box.checked;
      pw.removeAttribute('aria-invalid');
      var typed = pw.value;
      if (nopw && nopw.box.checked) ch.password = '';
      else if (typed) {
        if (typed.length < 8) { pw.setAttribute('aria-invalid', 'true'); pw.focus(); return 'The password needs at least 8 characters.'; }
        ch.password = typed;
      }
      if (end.value !== (o.expires ? localInput(o.expires) : '')) ch.expires_at = end.value ? new Date(end.value).toISOString() : null;
      return Object.keys(ch).length ? o.save(ch) : '';
    }
    return box({ title: o.title ? 'Share "' + o.title + '"' : 'Share', ok: 'Save', fill: fill, submit: submit }, false);
  };

  window.nwErrorBanner = function (text, onRetry) {
    var b = el('div', 'banner banner-error');
    b.setAttribute('role', 'alert');
    b.appendChild(el('span', 'spacer', text || 'Could not load. Check the connection and try again.'));
    if (onRetry) {
      var r = el('button', 'btn btn-sm', 'Try again');
      r.type = 'button'; r.onclick = onRetry; b.appendChild(r);
    }
    return b;
  };
  window.nwEmpty = function (title, sentence, buttonText, onClick) {
    var e = el('div', 'empty-state');
    e.appendChild(el('strong', null, title));
    if (sentence) e.appendChild(el('span', null, sentence));
    if (buttonText && onClick) {
      var b = el('button', 'btn btn-sm', buttonText);
      b.type = 'button'; b.onclick = onClick; e.appendChild(b);
    }
    return e;
  };

  // Colours: '' follows the computer, 'light' or 'dark' sticks (kept in this browser only).
  function applyTheme(v) {
    if (v === 'light' || v === 'dark') document.documentElement.dataset.theme = v;
    else delete document.documentElement.dataset.theme;
  }
  var savedTheme = '';
  try { savedTheme = localStorage.getItem('nw-theme') || ''; } catch (e) {}
  applyTheme(savedTheme);
  window.nwGetTheme = function () { return savedTheme; };
  window.nwSetTheme = function (v) {
    savedTheme = (v === 'light' || v === 'dark') ? v : '';
    applyTheme(savedTheme);
    try { if (savedTheme) localStorage.setItem('nw-theme', savedTheme); else localStorage.removeItem('nw-theme'); } catch (e) {}
  };
})();
