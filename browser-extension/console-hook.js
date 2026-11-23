(function () {
  "use strict";

  // Idempotent guard so re-injection into an already-open tab never double-wraps.
  if (window.__notewellConsoleHookInstalled) {
    return;
  }
  window.__notewellConsoleHookInstalled = true;

  var MSG_MAX = 500;
  var HELLO_TAG = "notewell-console-hello";
  var OFFER_TAG = "notewell-console-offer";
  var port = null;
  var pending = [];
  var PENDING_MAX = 50;
  var origPostMessage = window.postMessage;
  var lastErrMsg = "";
  var lastErrAt = 0;

  function cleanText(s) {
    return String(s)
      .replace(/[\x00-\x1f\x7f]/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, MSG_MAX);
  }

  function formatPart(item) {
    if (item === null) {
      return "null";
    }
    if (item === undefined) {
      return "undefined";
    }
    var t = typeof item;
    if (t === "string") {
      return item;
    }
    if (t === "number" || t === "boolean" || t === "bigint") {
      return String(item);
    }
    if (t === "symbol") {
      return item.toString();
    }
    if (t === "function") {
      return "[Function]";
    }
    // Never inspect DOM nodes so form fields or page text are never serialized.
    if (typeof Node !== "undefined" && item instanceof Node) {
      return "[Node]";
    }
    if (item instanceof Error) {
      var name = item.name ? String(item.name) : "Error";
      var msg = item.message ? String(item.message) : "";
      return msg ? name + ": " + msg : name;
    }
    try {
      var json = JSON.stringify(item);
      if (typeof json === "string") {
        return json;
      }
    } catch (_) {
      // Circular or non-serializable object.
    }
    try {
      return String(item);
    } catch (_) {
      return "[Object]";
    }
  }

  function emit(level, message) {
    try {
      var text = cleanText(message);
      if (!text) {
        return;
      }
      if (port) {
        port.postMessage({ level: level, message: text });
      } else if (pending.length < PENDING_MAX) {
        pending.push({ level: level, message: text });
      }
    } catch (_) {
      // Never interfere with the page.
    }
  }

  // Lines go to content.js over a private channel, so a page cannot forge them
  // with window.postMessage. Take the first port offered, before page scripts run.
  function onOffer(ev) {
    var d = ev.data;
    if (port || ev.source !== window || !d || d.source !== OFFER_TAG || !ev.ports || !ev.ports[0]) {
      return;
    }
    port = ev.ports[0];
    window.removeEventListener("message", onOffer, true);
    ev.stopImmediatePropagation();
    while (pending.length) {
      port.postMessage(pending.shift());
    }
  }
  window.addEventListener("message", onOffer, true);
  try {
    origPostMessage.call(window, { source: HELLO_TAG }, "*");
  } catch (_) {}

  function emitUncaught(msg, err) {
    var text = err instanceof Error ? formatPart(err) : formatPart(msg);
    var cleaned = cleanText(text);
    if (!cleaned) {
      return;
    }
    var now = Date.now();
    if (cleaned === lastErrMsg && now - lastErrAt < 50) {
      return;
    }
    lastErrMsg = cleaned;
    lastErrAt = now;
    emit("error", cleaned);
  }

  function formatArgs(args) {
    var parts = [];
    for (var i = 0; i < args.length; i += 1) {
      parts.push(formatPart(args[i]));
    }
    return parts.join(" ");
  }

  var origError = console.error;
  console.error = function () {
    try {
      emit("error", formatArgs(arguments));
    } catch (_) {}
    return origError.apply(this, arguments);
  };

  var origWarn = console.warn;
  console.warn = function () {
    try {
      emit("warn", formatArgs(arguments));
    } catch (_) {}
    return origWarn.apply(this, arguments);
  };

  var origOnError = window.onerror;
  window.onerror = function (message, source, lineno, colno, error) {
    try {
      emitUncaught(message, error);
    } catch (_) {}
    if (typeof origOnError === "function") {
      return origOnError.apply(this, arguments);
    }
    return false;
  };

  // Fallback listener in case the page overwrites window.onerror later.
  window.addEventListener("error", function (ev) {
    try {
      if (!ev || ev.target !== window) {
        return;
      }
      emitUncaught(ev.message, ev.error);
    } catch (_) {}
  });

  window.addEventListener("unhandledrejection", function (ev) {
    try {
      var reason = ev && ev.reason !== undefined ? ev.reason : "Unhandled promise rejection";
      emit("error", formatPart(reason));
    } catch (_) {}
  });
})();
