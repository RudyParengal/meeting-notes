(function () {
  "use strict";

  if (window.__notewellContentRelay) {
    return;
  }
  window.__notewellContentRelay = true;

  var HELLO_TAG = "notewell-console-hello";
  var OFFER_TAG = "notewell-console-offer";
  var ALLOWED_LEVELS = { log: true, info: true, warn: true, error: true };
  var MSG_MAX = 500;

  // Console lines arrive over a private MessageChannel that only console-hook.js
  // receives (it listens before any page script runs). A page cannot post a
  // line here with window.postMessage: nothing on window is trusted any more.
  function relay(event) {
    var data = event.data;
    if (!data || typeof data !== "object") {
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
        message: data.message.slice(0, MSG_MAX)
      });
      if (maybePromise && typeof maybePromise.catch === "function") {
        maybePromise.catch(function () {});
      }
    } catch (_) {
      // Extension reloaded or service worker asleep.
    }
  }

  var origin =
    window.location && window.location.origin && window.location.origin !== "null"
      ? window.location.origin
      : "*";

  function offerPort() {
    try {
      var channel = new MessageChannel();
      channel.port1.onmessage = relay;
      window.postMessage({ source: OFFER_TAG }, origin, [channel.port2]);
    } catch (_) {}
  }

  // Either script may load first, so offer a port now and answer the hook's
  // hello once. The hook keeps the first port it is given and ignores the rest.
  var answered = false;
  window.addEventListener("message", function (event) {
    if (answered || event.source !== window) {
      return;
    }
    var data = event.data;
    if (!data || typeof data !== "object" || data.source !== HELLO_TAG) {
      return;
    }
    answered = true;
    offerPort();
  });
  offerPort();
})();
