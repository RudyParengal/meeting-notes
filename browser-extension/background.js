"use strict";

importScripts("clean_url.js");

var BRIDGE_PORTS = [47811, 47812, 47813, 47814, 47815];
var HELLO_INTERVAL_MS = 3000;
var FLUSH_INTERVAL_MS = 2000;
var HELLO_TIMEOUT_MS = 800;
var MAX_BATCH_LINES = 200;
var MAX_BATCH_BYTES = 200000;
var MAX_QUEUE_LINES = 1000;
var MAX_INFLIGHT = 500;
var TITLE_MAX = 300;
var MSG_MAX = 500;
var SLOW_REQUEST_MS = 2000;
var MAX_REQUEST_MS = 600000;
var REQ_RATE_LIMIT = 50;
var REQ_RATE_WINDOW_MS = 10000;
var ALLOWED_LEVELS = { log: true, info: true, warn: true, error: true };
var WEB_FILTER = { urls: ["http://*/*", "https://*/*"] };

var bridge = null;
var queue = [];
var activeTabId = null;
var lastPageKey = "";
var options = { sendConsole: true, sendNetwork: true };
var inFlight = new Map();
var reqWindowStart = 0;
var reqWindowCount = 0;
var reqDropNotified = false;
var probing = false;
var flushing = false;
var textEncoder = new TextEncoder();

function clipText(raw, maxLen) {
  if (raw === null || raw === undefined) return "";
  return String(raw).replace(/[\x00-\x1f\x7f]/g, " ").replace(/\s+/g, " ").trim().slice(0, maxLen);
}

function cleanMethod(raw) {
  var m = clipText(raw || "GET", 16).toUpperCase().replace(/[^A-Z]/g, "");
  return m || "GET";
}

function isBridgeUrl(rawUrl) {
  if (typeof rawUrl !== "string") return false;
  try {
    var u = new URL(rawUrl);
    return u.hostname === "127.0.0.1" && BRIDGE_PORTS.indexOf(Number(u.port)) !== -1;
  } catch (_) {
    return false;
  }
}

function resetRateLimit() {
  reqWindowStart = 0;
  reqWindowCount = 0;
  reqDropNotified = false;
}

// Chrome stops an idle extension worker after about 30 s and forgets everything in memory.
// The connection is kept in session storage (memory only, cleared when Chrome closes) so a
// woken worker carries on at once, and an alarm wakes the worker to look for a new recording.
function saveBridge() {
  chrome.storage.session.set({ bridge: bridge });
}

function disconnectBridge() {
  // Rule B6: no recording, nothing kept.
  bridge = null;
  chrome.storage.session.remove("bridge");
  queue = [];
  inFlight.clear();
  lastPageKey = "";
  resetRateLimit();
}

function enqueue(eventObj, capturedAt) {
  if (!bridge) return;
  if (queue.length >= MAX_QUEUE_LINES) queue.shift();
  queue.push({ event: eventObj, capturedAt: capturedAt });
}

function ensureHooks(tabId, rawUrl) {
  if (!bridge || !options.sendConsole || typeof tabId !== "number" || tabId < 0) return;
  if (!clean_url(rawUrl) || isBridgeUrl(rawUrl)) return;
  chrome.scripting
    .executeScript({ target: { tabId: tabId }, files: ["content.js"], world: "ISOLATED" })
    .catch(function () {});
  chrome.scripting
    .executeScript({ target: { tabId: tabId }, files: ["console-hook.js"], world: "MAIN" })
    .catch(function () {});
}

function recordPageFromTab(tab) {
  if (!bridge || !tab || tab.incognito || tab.id !== activeTabId) return;
  if (isBridgeUrl(tab.url)) return;
  var url = clean_url(tab.url);
  if (!url) return;
  var title = clipText(tab.title || "", TITLE_MAX);
  var key = url + "\n" + title;
  if (key === lastPageKey) return;
  lastPageKey = key;
  enqueue({ k: "page", url: url, title: title }, Date.now());
}

function setActiveTab(tab, emitPage) {
  if (!tab || tab.incognito) {
    activeTabId = null;
    return;
  }
  activeTabId = tab.id;
  if (emitPage && bridge) {
    ensureHooks(tab.id, tab.url);
    recordPageFromTab(tab);
  }
}

function refreshActiveTab(emitPage) {
  chrome.tabs.query({ active: true, lastFocusedWindow: true }, function (tabs) {
    if (chrome.runtime.lastError || !tabs || tabs.length === 0) {
      activeTabId = null;
      return;
    }
    setActiveTab(tabs[0], emitPage);
  });
}

async function askHello(port) {
  var ctrl = new AbortController();
  var timer = setTimeout(function () { ctrl.abort(); }, HELLO_TIMEOUT_MS);
  try {
    // POST, not GET: Chrome sends no Origin header on GET, and the app needs it.
    var resp = await fetch("http://127.0.0.1:" + port + "/hello", {
      method: "POST",
      mode: "cors",
      cache: "no-store",
      signal: ctrl.signal
    });
    if (!resp.ok) return null;
    var data = await resp.json();
    return data && typeof data.secret === "string" && data.secret.length > 0 ? data.secret : null;
  } catch (_) {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

async function probeBridge() {
  if (probing) return;
  probing = true;
  try {
    if (bridge) {
      var aliveSecret = await askHello(bridge.port);
      if (!aliveSecret) {
        disconnectBridge();
      } else if (aliveSecret !== bridge.secret) {
        // A new recording started on the same port.
        var samePort = bridge.port;
        disconnectBridge();
        bridge = { port: samePort, secret: aliveSecret };
        saveBridge();
        refreshActiveTab(true);
      } else {
        // Any extension call keeps the worker awake while a recording runs.
        chrome.runtime.getPlatformInfo(function () {});
      }
      return;
    }
    for (var i = 0; i < BRIDGE_PORTS.length; i += 1) {
      var port = BRIDGE_PORTS[i];
      var secret = await askHello(port);
      if (secret) {
        bridge = { port: port, secret: secret };
        saveBridge();
        lastPageKey = "";
        resetRateLimit();
        refreshActiveTab(true);
        return;
      }
    }
  } finally {
    probing = false;
  }
}

function buildBatch(now) {
  var lines = [];
  var count = Math.min(queue.length, MAX_BATCH_LINES);
  var totalBytes = 2;
  for (var i = 0; i < count; i += 1) {
    var item = queue[i];
    var age = Math.max(0, Math.min(86400000, Math.round(now - item.capturedAt)));
    var wire = Object.assign({}, item.event, { age_ms: age });
    var encodedLen = textEncoder.encode(JSON.stringify(wire)).length + (lines.length > 0 ? 1 : 0);
    if (lines.length > 0 && totalBytes + encodedLen > MAX_BATCH_BYTES) break;
    lines.push(wire);
    totalBytes += encodedLen;
  }
  return lines;
}

async function flushQueue() {
  if (!bridge || queue.length === 0 || flushing) return;
  flushing = true;
  var currentBridge = bridge;
  try {
    var batch = buildBatch(Date.now());
    if (batch.length === 0) return;
    var resp = await fetch("http://127.0.0.1:" + currentBridge.port + "/events", {
      method: "POST",
      mode: "cors",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        "X-Notewell-Bridge": currentBridge.secret
      },
      body: JSON.stringify(batch)
    });
    if (resp.ok || resp.status === 413) {
      queue.splice(0, batch.length);
    } else {
      disconnectBridge();
    }
  } catch (_) {
    disconnectBridge();
  } finally {
    flushing = false;
  }
}

function allowRequestEvent(now) {
  if (reqWindowStart === 0 || now - reqWindowStart >= REQ_RATE_WINDOW_MS) {
    reqWindowStart = now;
    reqWindowCount = 0;
    reqDropNotified = false;
  }
  if (reqWindowCount < REQ_RATE_LIMIT) {
    reqWindowCount += 1;
    return true;
  }
  if (!reqDropNotified) {
    reqDropNotified = true;
    enqueue({ k: "console", level: "warn", message: "requests dropped" }, now);
  }
  return false;
}

function finishRequest(details, statusCode) {
  var startTs = inFlight.get(details.requestId);
  inFlight.delete(details.requestId);
  if (!bridge || !options.sendNetwork) return;
  if (details.tabId < 0 || details.tabId !== activeTabId) return;
  if (isBridgeUrl(details.url)) return;
  var url = clean_url(details.url);
  if (!url) return;

  var ms = 0;
  if (typeof startTs === "number" && typeof details.timeStamp === "number") {
    ms = Math.round(details.timeStamp - startTs);
  }
  ms = Math.max(0, Math.min(MAX_REQUEST_MS, ms));
  var status = Number.isInteger(statusCode) ? Math.max(0, Math.min(599, statusCode)) : 0;
  if (status > 0 && status < 400 && ms < SLOW_REQUEST_MS) return;

  var now = Date.now();
  if (!allowRequestEvent(now)) return;
  enqueue({ k: "request", method: cleanMethod(details.method), url: url, status: status, ms: ms }, now);
}

chrome.tabs.onActivated.addListener(function (info) {
  chrome.tabs.get(info.tabId, function (tab) {
    if (chrome.runtime.lastError) {
      activeTabId = null;
      return;
    }
    setActiveTab(tab, true);
  });
});

chrome.tabs.onUpdated.addListener(function (tabId, changeInfo, tab) {
  if (tabId !== activeTabId || !tab || tab.incognito || !bridge) return;
  if (changeInfo.url || changeInfo.title || changeInfo.status === "complete") {
    if (changeInfo.status === "complete") ensureHooks(tabId, tab.url);
    recordPageFromTab(tab);
  }
});

chrome.tabs.onRemoved.addListener(function (tabId) {
  if (tabId === activeTabId) activeTabId = null;
});

chrome.windows.onFocusChanged.addListener(function (windowId) {
  if (windowId === chrome.windows.WINDOW_ID_NONE) return;
  chrome.tabs.query({ active: true, windowId: windowId }, function (tabs) {
    if (chrome.runtime.lastError || !tabs || tabs.length === 0) return;
    setActiveTab(tabs[0], true);
  });
});

chrome.runtime.onMessage.addListener(function (msg, sender) {
  if (!bridge || !options.sendConsole) return;
  if (!msg || msg.type !== "notewell-console") return;
  if (!sender || !sender.tab || sender.tab.incognito || sender.tab.id !== activeTabId) return;
  if (typeof msg.level !== "string" || !ALLOWED_LEVELS[msg.level]) return;
  var text = clipText(msg.message, MSG_MAX);
  if (!text) return;
  enqueue({ k: "console", level: msg.level, message: text }, Date.now());
});

chrome.webRequest.onBeforeRequest.addListener(function (details) {
  if (!bridge || !options.sendNetwork) return;
  if (details.tabId < 0 || details.tabId !== activeTabId || isBridgeUrl(details.url)) return;
  if (inFlight.size >= MAX_INFLIGHT) {
    for (var oldestKey of inFlight.keys()) {
      inFlight.delete(oldestKey);
      break;
    }
  }
  inFlight.set(details.requestId, details.timeStamp);
}, WEB_FILTER);

chrome.webRequest.onCompleted.addListener(function (details) {
  finishRequest(details, details.statusCode);
}, WEB_FILTER);

chrome.webRequest.onErrorOccurred.addListener(function (details) {
  finishRequest(details, 0);
}, WEB_FILTER);

function loadOptions() {
  chrome.storage.local.get({ sendConsole: true, sendNetwork: true }, function (items) {
    if (!chrome.runtime.lastError && items) {
      options.sendConsole = items.sendConsole !== false;
      options.sendNetwork = items.sendNetwork !== false;
    }
  });
}

chrome.storage.onChanged.addListener(function (_changes, area) {
  if (area === "local") loadOptions();
});

chrome.alarms.create("notewell-probe", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(function () { probeBridge(); });

loadOptions();
refreshActiveTab(false);
chrome.storage.session.get("bridge", function (items) {
  var saved = !chrome.runtime.lastError && items && items.bridge;
  if (!bridge && saved && typeof saved.secret === "string" && BRIDGE_PORTS.indexOf(saved.port) !== -1) {
    bridge = { port: saved.port, secret: saved.secret };
    refreshActiveTab(false);
  }
  probeBridge();
});
setInterval(probeBridge, HELLO_INTERVAL_MS);
setInterval(flushQueue, FLUSH_INTERVAL_MS);
