"""Static privacy, security and contract tests for app/browser-extension.

    python app/tests/extension/test_extension_static.py

No browser required. Runs node against clean_url.js and clean_url.test.txt
when node is already installed.
"""
import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit

APP_ROOT = Path(__file__).resolve().parents[2]
EXT_DIR = APP_ROOT / "browser-extension"
CASES_FILE = Path(__file__).resolve().parent / "clean_url.test.txt"

REQUIRED_FILES = [
    "manifest.json",
    "background.js",
    "clean_url.js",
    "console-hook.js",
    "content.js",
    "options.html",
    "options.js",
    "README.md",
]

_passed, _failed = 0, []


def check(name, fn):
    global _passed
    try:
        fn()
        _passed += 1
        print(f"  PASS  {name}")
    except Exception as e:
        _failed.append((name, e))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")


def js_files():
    return sorted(EXT_DIR.glob("*.js"))


def derive_chrome_id(pub_b64):
    der = base64.b64decode(pub_b64, validate=True)
    assert len(der) > 100 and der[0] == 0x30, "public key is not DER SPKI"
    digest = hashlib.sha256(der).digest()[:16]
    return "".join(chr(ord("a") + (b >> 4)) + chr(ord("a") + (b & 0xF)) for b in digest)


def clean_url_py(u):
    """Python reference for clean_url(u) matching clean_url.js and B4/B3 rules."""
    if not isinstance(u, str) or not u:
        return ""
    stripped = u.strip()
    try:
        parts = urlsplit(stripped)
    except ValueError:
        return ""
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname:
        return ""
    host = parts.hostname.lower()
    try:
        port = parts.port
    except ValueError:
        return ""
    default_port = 80 if scheme == "http" else 443
    netloc = f"{host}:{port}" if port is not None and port != default_port else host
    path = parts.path or "/"
    base = re.sub(r"[\x00-\x1f\x7f]", "", f"{scheme}://{netloc}{path}")
    if len(base) >= 500:
        return base[:500]
    if not parts.query:
        return base
    keys = []
    for k, _v in parse_qsl(parts.query, keep_blank_values=True):
        ck = re.sub(r"[\x00-\x1f\x7f]", "", k)
        if ck:
            keys.append(quote(ck, safe="-_.!~*'()") + "=*")
    if not keys:
        return base
    out = base
    for i, part in enumerate(keys):
        sep = "?" if i == 0 else "&"
        nxt = sep + part
        if len(out) + len(nxt) > 500:
            break
        out += nxt
    return out


def load_cases():
    cases = []
    for raw in CASES_FILE.read_text(encoding="utf-8").splitlines():
        if not raw or raw.startswith("#"):
            continue
        inp, exp = raw.split("\t", 1)
        cases.append((inp, exp))
    assert len(cases) >= 15, f"too few test cases: {len(cases)}"
    return cases


# ------------------------------------------------------------ files and limits
print("\nFiles and limits")


def t_required_files_and_caps():
    for name in REQUIRED_FILES:
        p = EXT_DIR / name
        assert p.is_file(), f"missing {p}"
        text = p.read_text(encoding="utf-8")
        lines = text.splitlines()
        cap = 200 if name.endswith(".md") else 400
        assert len(lines) < cap, f"{name} has {len(lines)} lines (cap {cap})"
        assert "\u2014" not in text, f"em dash found in {name}"
        pem_marker = "PRIVATE" + " KEY"
        assert pem_marker not in text, f"private key marker found in {name}"


check("required files exist, stay under line caps, no em dashes, no private key", t_required_files_and_caps)


# -------------------------------------------------------------------- manifest
print("\nManifest")


def t_manifest():
    m = json.loads((EXT_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert m["manifest_version"] == 3, m.get("manifest_version")
    assert m["name"] == "Notewell recorder helper", m.get("name")
    assert m["incognito"] == "not_allowed", m.get("incognito")
    assert set(m["permissions"]) == {"tabs", "webRequest", "scripting", "storage"}, m["permissions"]
    assert "<all_urls>" in m["host_permissions"], m["host_permissions"]
    assert "http://127.0.0.1/*" in m["host_permissions"], m["host_permissions"]
    assert m["background"]["service_worker"] == "background.js"
    ext_id = derive_chrome_id(m["key"])
    assert re.fullmatch(r"[a-p]{32}", ext_id), ext_id
    worlds = {cs.get("world") for cs in m.get("content_scripts", [])}
    assert worlds == {"ISOLATED", "MAIN"}, worlds
    assert m["options_ui"]["page"] == "options.html"


check("manifest.json is valid MV3 with fixed key, incognito off and exact permissions", t_manifest)


# -------------------------------------------------------- privacy and security
print("\nPrivacy and security static scan")


def t_no_forbidden_apis_or_tokens():
    # Forbidden tokens across all JS files.
    always_forbidden = [
        r"requestBody",
        r"cookies",
        r"document\.cookie",
        r"innerText",
        r"\bvalue\b",
        r"\.value\b",
        r"clipboard",
        r"requestHeaders",
        r"responseHeaders",
        r"extraHeaders",
        r"onBeforeSendHeaders",
        r"onHeadersReceived",
        r"onSendHeaders",
        r"getResponseHeader",
        r"getAllResponseHeaders",
        r"\.headers\b",
        r"\beval\s*\(",
        r"new\s+Function\s*\(",
        r"XMLHttpRequest",
        r"WebSocket",
        r"sendBeacon",
    ]
    for path in js_files():
        text = path.read_text(encoding="utf-8")
        for pat in always_forbidden:
            m = re.search(pat, text, re.I)
            assert m is None, f"forbidden pattern {pat!r} matched in {path.name}: {m.group(0)!r}"

        # "headers" is forbidden everywhere except the single POST /events fetch init in background.js.
        hdr_hits = list(re.finditer(r"headers", text, re.I))
        if path.name != "background.js":
            assert not hdr_hits, f"'headers' found in {path.name}"
        else:
            assert len(hdr_hits) == 1, f"expected 1 outbound fetch headers block in background.js, found {len(hdr_hits)}"
            line_no = text[: hdr_hits[0].start()].count("\n") + 1
            snippet = "\n".join(text.splitlines()[max(0, line_no - 6) : line_no + 5])
            assert "http://127.0.0.1:" in snippet and "X-Notewell-Bridge" in snippet, snippet


def t_fetch_only_localhost_127():
    for path in js_files():
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"\bfetch\s*\(([^,\n)]+)", text):
            arg = m.group(1).strip()
            assert arg.startswith('"http://127.0.0.1:') or arg.startswith("'http://127.0.0.1:"), (
                f"{path.name} fetch target not 127.0.0.1: {arg}"
            )
        for m in re.finditer(r"https?://[^\s\"'`]+", text):
            url = m.group(0)
            allowed = (
                url.startswith("http://127.0.0.1:")
                or url in ("http://*/*", "https://*/*")
            )
            assert allowed, f"unexpected URL literal in {path.name}: {url}"


def t_options_html_no_inline_script():
    html = (EXT_DIR / "options.html").read_text(encoding="utf-8")
    assert re.search(r"\bon[a-z]+\s*=", html, re.I) is None, "inline event handler in options.html"
    scripts = re.findall(r"<script([^>]*)>(.*?)</script>", html, re.I | re.S)
    assert len(scripts) == 1 and 'src="options.js"' in scripts[0][0] and scripts[0][1].strip() == "", scripts
    assert "Send console messages" in html
    assert "Send network requests" in html


check("no forbidden privacy/security tokens or header reads in JS files", t_no_forbidden_apis_or_tokens)
check("all fetch() calls and URL literals target 127.0.0.1 only", t_fetch_only_localhost_127)
check("options.html has both switches and no inline script or handlers", t_options_html_no_inline_script)


# ----------------------------------------------------------- protocol contract
print("\nBridge protocol and hook contract")


def t_protocol_and_hooks():
    bg = (EXT_DIR / "background.js").read_text(encoding="utf-8")
    for token in ("47811", "47815", "/hello", "/events", "X-Notewell-Bridge", "age_ms",
                  '"page"', '"console"', '"request"', "requests dropped", "2000", "400", "50", "10000"):
        assert token in bg, f"missing {token!r} in background.js"
    hook = (EXT_DIR / "console-hook.js").read_text(encoding="utf-8")
    for token in ("console.error", "console.warn", "window.onerror", "unhandledrejection"):
        assert token in hook, f"missing {token!r} in console-hook.js"
    readme = (EXT_DIR / "README.md").read_text(encoding="utf-8")
    for n in range(1, 6):
        assert re.search(rf"(?m)^{n}\.\s+", readme), f"README missing numbered step {n}"


check("background.js, console-hook.js and README.md match B1/B2 contract", t_protocol_and_hooks)


# ------------------------------------------------------------------- clean_url
print("\nclean_url cases")


def assert_b3_url_invariants(cleaned):
    if not cleaned:
        return
    assert len(cleaned) <= 500, len(cleaned)
    assert "#" not in cleaned, cleaned
    assert not re.search(r"[\x00-\x1f\x7f]", cleaned), repr(cleaned)
    parts = urlsplit(cleaned)
    assert parts.scheme in ("http", "https"), cleaned
    assert "@" not in parts.netloc, cleaned
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        assert v == "*", f"query param {k!r} not '*': {cleaned}"


def t_clean_url_python_cases():
    for inp, exp in load_cases():
        got = clean_url_py(inp)
        assert got == exp, f"{inp!r} -> {got!r} != {exp!r}"
        assert_b3_url_invariants(got)
    long_url = "https://example.com/" + ("a" * 460) + "?first=1&second=2&very_long_third_param=3"
    got_long = clean_url_py(long_url)
    assert_b3_url_invariants(got_long)
    assert got_long.endswith("?first=*&second=*"), got_long


def t_clean_url_and_syntax_in_node():
    node = shutil.which("node")
    if not node:
        print("  NOTE  node is not installed; skipping node JS checks")
        return
    for path in js_files():
        r = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=15)
        assert r.returncode == 0, f"node --check failed for {path.name}: {r.stderr}"

    script = r"""
const fs = require("fs");
const { clean_url } = require(process.argv[1]);
const lines = fs.readFileSync(process.argv[2], "utf8").split(/\r?\n/);
let count = 0;
for (const raw of lines) {
  if (!raw || raw.startsWith("#")) continue;
  const idx = raw.indexOf("\t");
  const inp = raw.slice(0, idx);
  const exp = raw.slice(idx + 1);
  const got = clean_url(inp);
  if (got !== exp) {
    console.error("MISMATCH:", JSON.stringify(inp), "got", JSON.stringify(got), "expected", JSON.stringify(exp));
    process.exit(1);
  }
  count += 1;
}
const longUrl = "https://example.com/" + "a".repeat(460) + "?first=1&second=2&very_long_third_param=3";
const gotLong = clean_url(longUrl);
if (gotLong.length > 500 || !gotLong.endsWith("?first=*&second=*")) {
  console.error("LONG MISMATCH:", gotLong.length, gotLong);
  process.exit(1);
}
console.log("node checked " + count + " cases + long-url truncation OK");
"""
    r = subprocess.run(
        [node, "-e", script, str(EXT_DIR / "clean_url.js"), str(CASES_FILE)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert r.returncode == 0, f"node clean_url test failed: {r.stdout}\n{r.stderr}"


check("clean_url Python reference matches clean_url.test.txt and B3 invariants", t_clean_url_python_cases)
check("node syntax check and clean_url.js execution match clean_url.test.txt", t_clean_url_and_syntax_in_node)

print(f"\n{_passed} passed, {len(_failed)} failed")
sys.exit(1 if _failed else 0)
