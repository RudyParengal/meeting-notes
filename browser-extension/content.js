(function () {
  "use strict";

  if (window.__notewellContentRelay) {
    return;
  }
  window.__notewellContentRelay = true;

  var SOURCE_TAG = "notewell-console-hook";
  var ALLOWED_LEVELS = { log: true, info: true, warn: true, error: true };

  window.addEventListener("message", function (event) {
    if (event.source !== window) {
      return;
    }
    var data = event.data;
    if (!data || typeof data !== "object" || data.source !== SOURCE_TAG) {
      return;
    }
    if (typeof data.level !== "string" || !ALLOWED_LEVELS[data.level]) {
      return;
    }
    if (typeof data.message !== "string" || !data.message) {
      return;
    }
    try {
      var maybePromise = chrome.runtime.sendMessage({
        type: "notewell-console",
        level: data.level,
        message: data.message
      });
      if (maybePromise && typeof maybePromise.catch === "function") {
        maybePromise.catch(function () {});
      }
    } catch (_) {
      // Extension reloaded or service worker asleep.
    }
  });
})();
