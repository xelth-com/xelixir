#!/usr/bin/env python3
"""Scan the repository for anything that must never be published.

The rules live in `.denylist` at the repository root, one Python regular
expression per line (see that file for the format). Matching is
case-insensitive. A rule prefixed with `path:` is matched against the file path
instead of the file's content.

    python tools/kb/denylist.py            # scan every tracked text file
    python tools/kb/denylist.py path ...   # scan only these paths

Exit status 0 = clean, 1 = at least one hit, 2 = the scanner could not run.

Why this exists: this repository publishes techniques and facts, which is what
reverse engineering for interoperability is allowed to produce. It does not
redistribute anybody's product. The deny-list is where that line is written down
so that neither a tired human nor an eager agent has to remember it.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RULES_FILE = os.path.join(ROOT, ".denylist")

# Files that necessarily contain the patterns themselves.
SELF = {".denylist", "tools/kb/denylist.py"}

BINARY_SNIFF = 8192


def load_rules():
    content, paths = [], []
    if not os.path.exists(RULES_FILE):
        sys.stderr.write("no .denylist at %s\n" % RULES_FILE)
        sys.exit(2)
    with open(RULES_FILE, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            label = ""
            if "##" in line:
                line, label = line.split("##", 1)
                label = label.strip()
            pat = line.strip()
            if not pat:
                continue
            target = content
            if pat.startswith("path:"):
                pat, target = pat[5:], paths
            try:
                rx = re.compile(pat, re.IGNORECASE)
            except re.error as exc:
                sys.stderr.write(".denylist:%d: bad regex %r (%s)\n" % (lineno, pat, exc))
                sys.exit(2)
            target.append((rx, pat, label, lineno))
    return content, paths


def tracked_files():
    try:
        out = subprocess.check_output(["git", "ls-files"], cwd=ROOT)
        files = [p for p in out.decode("utf-8", "replace").splitlines() if p]
        if files:
            return files
    except (OSError, subprocess.CalledProcessError):
        pass
    # not a git checkout (or nothing committed yet) — walk the tree
    files = []
    for base, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".venv", "venv")]
        for n in names:
            files.append(os.path.relpath(os.path.join(base, n), ROOT).replace(os.sep, "/"))
    return files


def is_text(path):
    try:
        with open(path, "rb") as fh:
            return b"\0" not in fh.read(BINARY_SNIFF)
    except OSError:
        return False


def main(argv):
    content_rules, path_rules = load_rules()
    files = argv[1:] or tracked_files()
    hits = 0
    scanned = 0

    for rel in files:
        rel = rel.replace(os.sep, "/")
        full = os.path.join(ROOT, rel)
        if not os.path.isfile(full):
            continue

        for rx, pat, label, lineno in path_rules:
            if rx.search(rel):
                hits += 1
                print("%s: FORBIDDEN PATH  [/%s/]%s" % (rel, pat, "  — " + label if label else ""))

        if rel in SELF or not is_text(full):
            continue
        scanned += 1
        with open(full, encoding="utf-8", errors="replace") as fh:
            for n, line in enumerate(fh, 1):
                for rx, pat, label, lineno in content_rules:
                    m = rx.search(line)
                    if m:
                        hits += 1
                        print("%s:%d: %r matches /%s/%s"
                              % (rel, n, m.group(0), pat, "  — " + label if label else ""))

    if hits:
        print("\nDENY-LIST: %d hit(s) in %d file(s) scanned — nothing may be committed "
              "until every one is gone." % (hits, scanned))
        print("Describe the CLASS, not the product. See CONTRIBUTING.md section 4.")
        return 1
    print("deny-list clean: %d rule(s), %d file(s) scanned, 0 hits"
          % (len(content_rules) + len(path_rules), scanned))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
