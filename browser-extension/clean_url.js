"use strict";

// Keep only scheme, host and path for http/https URLs.
// Replace every query param with *, drop the # fragment and credentials,
// strip control chars, and cap safely at 500 chars without cutting a param.
var URL_MAX_LEN = 500;

function stripControlChars(s) {
  return String(s).replace(/[\x00-\x1f\x7f]/g, "");
}

function clean_url(u) {
  if (typeof u !== "string" || !u) {
    return "";
  }
  var parsed;
  try {
    parsed = new URL(u);
  } catch (_) {
    return "";
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return "";
  }
  // Using protocol + host + pathname drops any userinfo and fragment.
  var base = stripControlChars(parsed.protocol + "//" + parsed.host + parsed.pathname);
  if (!base) {
    return "";
  }
  if (base.length >= URL_MAX_LEN) {
    return base.slice(0, URL_MAX_LEN);
  }

  var keys = [];
  parsed.searchParams.forEach(function (_v, k) {
    var cleanKey = stripControlChars(k);
    if (cleanKey.length > 0) {
      keys.push(encodeURIComponent(cleanKey) + "=*");
    }
  });
  if (keys.length === 0) {
    return base;
  }

  var out = base;
  for (var i = 0; i < keys.length; i += 1) {
    var sep = i === 0 ? "?" : "&";
    var next = sep + keys[i];
    if (out.length + next.length > URL_MAX_LEN) {
      break;
    }
    out += next;
  }
  return out;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { clean_url: clean_url };
}
