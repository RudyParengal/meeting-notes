// notewell-ui.js - the few lines of script the design system needs (chunk 07, boxes added 20 Nov 2026).
// nwToast('Saved', 'success' | 'error' | 'info'): a short message bottom right that goes away by itself.
// nwConfirm({title, body, ok, cancel, danger}) -> Promise<true|false>: a box with two named buttons.
// nwPrompt({title, body, label, value, type, ok, cancel, minLength, tooShort}) -> Promise<text|null>.
// nwAlert({title, body, ok}) -> Promise: a box with one button.
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
        else if (e.key === 'Enter' && withInput && document.activeElement === input) { e.preventDefault(); yes(); }
        else if (e.key === 'Tab') {   // keep the keyboard inside the box
          var all = d.querySelectorAll('button, input'), first = all[0], last = all[all.length - 1];
          if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
          else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
      }
      ok.onclick = yes;
      if (cancel) cancel.onclick = function () { close(no); };
      back.onclick = function (e) { if (e.target === back) close(no); };
      document.addEventListener('keydown', key, true);
      document.body.appendChild(back);
      (input || (o.danger && cancel) || ok).focus();
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
})();
