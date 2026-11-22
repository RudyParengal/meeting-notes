(function () {
  "use strict";

  // Idempotent guard so re-injection into an already-open tab never double-wraps.
  if (window.__notewellConsoleHookInstalled) {
    return;
  }
  window.__notewellConsoleHookInstalled = true;

  var MSG_MAX = 500;
  var SOURCE_TAG = "notewell-console-hook";
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
      var target =
        window.location &&
        window.location.origin &&
        window.location.origin !== "null"
          ? window.location.origin
          : "*";
      window.postMessage(
        { source: SOURCE_TAG, level: level, message: text },
        target
      );
    } catch (_) {
      // Never interfere with the page.
    }
  }

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
