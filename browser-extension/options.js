"use strict";

var DEFAULTS = {
  sendConsole: true,
  sendNetwork: true
};

var consoleBox = document.getElementById("sendConsole");
var networkBox = document.getElementById("sendNetwork");
var statusEl = document.getElementById("status");
var clearTimer = null;

function showSaved() {
  statusEl.textContent = "Saved.";
  if (clearTimer !== null) {
    clearTimeout(clearTimer);
  }
  clearTimer = setTimeout(function () {
    statusEl.textContent = "";
    clearTimer = null;
  }, 1500);
}

function saveOptions() {
  chrome.storage.local.set(
    {
      sendConsole: Boolean(consoleBox.checked),
      sendNetwork: Boolean(networkBox.checked)
    },
    showSaved
  );
}

chrome.storage.local.get(DEFAULTS, function (items) {
  consoleBox.checked = items.sendConsole !== false;
  networkBox.checked = items.sendNetwork !== false;
});

consoleBox.addEventListener("change", saveOptions);
networkBox.addEventListener("change", saveOptions);
