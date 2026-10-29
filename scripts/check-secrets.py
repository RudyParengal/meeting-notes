#!/usr/bin/env python3
"""Looks for things that must never be published: keys, tokens, private keys,
personal file paths, real IP addresses, and email addresses.

    python scripts/check-secrets.py                  # the files in the repo now
    python scripts/check-secrets.py --history        # every file in every commit, ever
    python scripts/check-secrets.py --staged         # only what you are about to commit (pre-commit hook)
    python scripts/check-secrets.py --deny acme --deny 203.0.113.9   # also flag your own private words

Exit code 1 if anything is found. It prints WHERE and WHAT KIND, never the secret
itself, so its output is safe to paste into a bug report.

Suppress a deliberate false positive on one line with:   # secret-scan: allow

Why --deny: no scanner can know that your company name, hostname or home folder
is private. Pass those words yourself (do not commit the list - the list would
be the leak).

Standard library only.
"""
import argparse
import math
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ALLOW_MARK = "secret-scan: allow"

# Provider credentials all have a recognisable shape.
KEY_PATTERNS = [
    ("OpenAI-style key",        r"\bsk-(?:proj-|svcacct-|live-|ant-)?[A-Za-z0-9_-]{20,}"),
    ("Google API key",          r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("NVIDIA key",              r"\bnvapi-[A-Za-z0-9_-]{20,}"),
    ("xAI key",                 r"\bxai-[A-Za-z0-9]{20,}"),
    ("GitHub token",            r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b"),
    ("GitHub fine-grained token", r"\bgithub_pat_[A-Za-z0-9_]{30,}"),
    ("AWS access key id",       r"\bAKIA[0-9A-Z]{16}\b"),
    ("Slack token",             r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    ("Slack webhook",           r"https://hooks\.slack\.com/services/[A-Za-z0-9/]{20,}"),
    ("Hugging Face token",      r"\bhf_[A-Za-z0-9]{30,}\b"),
    ("Private key block",       r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY"),
    ("JSON Web Token",          r"\beyJ[A-Za-z0-9_-]{15,}\.eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}"),
    ("Meeting Notes API key",   r"\bmn_[0-9a-f]{40,}\b"),
    ("Basic-auth URL",          r"[a-z]+://[^/\s:@]{2,}:[^/\s:@]{3,}@[A-Za-z0-9.-]+"),
]

# A value assigned to something called key/token/secret/password, that looks random.
ASSIGN = re.compile(r"""(?ix)
    (?:api[_-]?key|secret|token|passw(?:or)?d|credential|auth)\w*   # a name that suggests a secret
    ["']?\s*[:=]\s*["']?                                            # = or :
    ([A-Za-z0-9+/_\-.]{24,})                                        # a long value
""")
PLACEHOLDER = re.compile(r"(?i)(xxx|your[-_ ]|change[-_]?me|paste|example|placeholder|<[^>]+>|\.\.\.|test|dummy|fake|sample|secrets\.token|os\.environ|getenv|hash)")

# A Windows user folder, or /home/x or /Users/x at the START of a path. The lookbehind
# keeps a URL such as /admin/users/alice from being mistaken for a home folder.
PERSONAL_PATH = re.compile(
    r"(?:(?i:[A-Z]:\\+Users\\+)|(?<![\w.-])/home/|(?<![\w.-])/Users/)"
    r"(?!<|Public\b|Default\b|you\b|user\b|username\b|YourName\b|\$|%|\{|name\b|me\b)([A-Za-z0-9._-]{2,})")
IPV4 = re.compile(r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?![\d.])")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")

SAFE_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "example.test", "users.noreply.github.com",
                      "localhost", "invalid", "test")
SKIP_DIRS = {".git", "venv", "node_modules", "__pycache__", ".pytest_cache", "models", "data"}
SKIP_FILES = {"LICENSE"}                      # copyright line names the author, on purpose
SKIP_PREFIX = ("dashboard/vendor/",)          # third-party minified code: random-looking by nature
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".env", ".example",
                 ".ps1", ".vbs", ".bat", ".sh", ".html", ".js", ".css", ".service", ".conf", ""}


def entropy(s: str) -> float:
    if not s:
        return 0.0
    return -sum((s.count(c) / len(s)) * math.log2(s.count(c) / len(s)) for c in set(s))


def is_public_ipv4(ip: str) -> bool:
    try:
        a, b, c, d = (int(x) for x in ip.split("."))
    except ValueError:
        return False
    if any(x > 255 for x in (a, b, c, d)) or a == 0:
        return False
    if a in (10, 127) or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31) or (a == 169 and b == 254):
        return False
    if (a, b, c) in ((192, 0, 2), (198, 51, 100), (203, 0, 113)):      # documentation ranges
        return False
    if a >= 224:                                                        # multicast / reserved: not a host
        return False
    return True


def scan_text(path: str, text: str, deny: list[str]) -> list[tuple[int, str, str]]:
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARK in line:
            continue
        for kind, pat in KEY_PATTERNS:
            for m in re.finditer(pat, line):
                if not PLACEHOLDER.search(m.group(0)) or kind == "Private key block":
                    found.append((n, kind, "(value withheld)"))
        for m in ASSIGN.finditer(line):
            val = m.group(1)
            if not PLACEHOLDER.search(line) and entropy(val) >= 3.6 and not val.lower().startswith(("http", "www")):
                found.append((n, "high-entropy value assigned to a secret-looking name", "(value withheld)"))
        for m in PERSONAL_PATH.finditer(line):
            found.append((n, "a personal file path", f"user folder '{m.group(1)}'"))
        for m in IPV4.finditer(line):
            if is_public_ipv4(m.group(1)) and not re.search(r"(?i)(version|v\d|==|>=|<=|~=|pip|py-spy|[a-z]+-\d)", line[:m.start()][-12:]):
                found.append((n, "a public IP address", m.group(1)))
        for m in EMAIL.finditer(line):
            dom = m.group(1).lower()
            if not dom.endswith(SAFE_EMAIL_DOMAINS):
                found.append((n, "an email address", m.group(0)))
        low = line.lower()
        for word in deny:
            if word.lower() in low:
                found.append((n, "a denied word you supplied", f"'{word}'"))
    return found


def git(*args) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def wanted(rel: str) -> bool:
    p = Path(rel)
    if p.name in SKIP_FILES or any(part in SKIP_DIRS for part in p.parts) or rel.startswith(SKIP_PREFIX):
        return False
    return p.suffix.lower() in TEXT_SUFFIXES or p.name.startswith(".env")


def files_now(staged: bool) -> list[str]:
    if staged:
        return [f for f in git("diff", "--cached", "--name-only", "--diff-filter=ACM").splitlines() if f]
    tracked = git("ls-files").splitlines()
    untracked = git("ls-files", "--others", "--exclude-standard").splitlines()
    return [f for f in dict.fromkeys(tracked + untracked) if f]


def main() -> int:
    ap = argparse.ArgumentParser(description="Scan for secrets and personal data before publishing.")
    ap.add_argument("--history", action="store_true", help="also scan every earlier version of every file")
    ap.add_argument("--staged", action="store_true", help="only files staged for commit")
    ap.add_argument("--deny", action="append", default=[], metavar="WORD", help="a private word/host/name to flag (repeatable)")
    args = ap.parse_args()

    findings, scanned = [], 0
    for rel in files_now(args.staged):
        if not wanted(rel):
            continue
        p = REPO / rel
        if not p.is_file() or p.stat().st_size > 2_000_000:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        for n, kind, detail in scan_text(rel, text, args.deny):
            findings.append((rel, n, kind, detail, "working tree"))

    commits = 0
    if args.history and not args.staged:
        revs = git("rev-list", "--all").split()
        commits = len(revs)
        seen = set()
        for rev in revs:
            for rel in git("ls-tree", "-r", "--name-only", rev).splitlines():
                if not wanted(rel):
                    continue
                blob = git("rev-parse", f"{rev}:{rel}").strip()
                if (rel, blob) in seen:
                    continue
                seen.add((rel, blob))
                text = git("show", f"{rev}:{rel}")
                scanned += 1
                for n, kind, detail in scan_text(rel, text, args.deny):
                    findings.append((rel, n, kind, detail, f"commit {rev[:8]}"))
        # commit messages and author identities travel with the history too
        for rev in revs:
            msg = git("log", "-1", "--format=%an <%ae>%n%cn <%ce>%n%B", rev)
            for n, kind, detail in scan_text("(commit message)", msg, args.deny):
                if kind != "an email address":            # authorship emails are reviewed separately below
                    findings.append(("(commit message)", n, kind, detail, f"commit {rev[:8]}"))

    seen_out = set()
    for rel, n, kind, detail, where in sorted(findings):
        key = (rel, n, kind, detail)
        if key in seen_out:
            continue
        seen_out.add(key)
        print(f"  {rel}:{n}  {kind}  {detail}  [{where}]")

    if args.history:
        idents = {i for i in git("log", "--all", "--format=%ae%n%ce").split() if i}
        print(f"\nAuthor/committer addresses in this history ({len(idents)}): " + ", ".join(sorted(idents)) +
              "\n  (public forever once pushed - use a GitHub noreply address if you want it private)")

    print(f"\nscanned {scanned} file version(s)" + (f" across {commits} commit(s)" if commits else "") +
          f"; {len(seen_out)} finding(s)")
    if seen_out:
        print("\nFAILED. Fix every line above (or add '# secret-scan: allow' to a reviewed false positive).")
        return 1
    print("OK - nothing that looks like a secret or personal data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
