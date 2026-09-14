#!/usr/bin/env python3
"""Pull one knowledge slice - offline from this repository, or (later) online.

    python tools/kb/kb_pull.py chips/s5pv210              # print guide + findings
    python tools/kb/kb_pull.py chips/s5pv210 --list       # one line per finding
    python tools/kb/kb_pull.py chips/s5pv210 --findings   # the raw findings.yaml
    python tools/kb/kb_pull.py chips/s5pv210 --out ./kb   # copy the slice there
    python tools/kb/kb_pull.py --slices                   # what slices exist

Offline mode (the default, and the only one that works today) reads the slice out
of this checkout. That is deliberate: a repair happens in a workshop, sometimes on
a bench with no network and a board that is the only interesting thing in the
room. The knowledge has to be on the laptop already.

Online mode (`--online`) is a documented stub - see ONLINE_ENDPOINT below.

Read the slice BEFORE touching a tool that names it. Most of the traps are already
written down; the rest is what you are about to discover, and that one belongs in
`proposals/`.
"""
import argparse
import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
KB = os.path.join(ROOT, "kb")

# The free tier of xelth's knowledge base will serve the same slices over HTTP so
# that a tool can pull a slice it does not have. NOT YET AVAILABLE - the export
# bot (server -> findings.yaml) and the public endpoint are still to be built, and
# until they are, --online says so instead of pretending.
ONLINE_ENDPOINT = "https://xelth.com:3221/kb/{slice}"


def find_slices():
    out = []
    for base, dirs, names in os.walk(KB):
        dirs[:] = sorted(d for d in dirs if d not in (".git", "evidence"))
        if "findings.yaml" in names or "README.md" in names:
            rel = os.path.relpath(base, KB).replace(os.sep, "/")
            if rel != ".":
                out.append(rel)
    return sorted(out)


def resolve(slug):
    slug = slug.strip("/").replace("\\", "/")
    if slug.startswith("kb/"):
        slug = slug[3:]
    path = os.path.join(KB, slug.replace("/", os.sep))
    if not os.path.isdir(path):
        sys.stderr.write("no such slice: %s\n\nslices in this repository:\n  %s\n"
                         % (slug, "\n  ".join(find_slices())))
        sys.exit(1)
    return slug, path


def iter_findings(path):
    """Yield (id, kind, status, confidence, first line of text) per finding.

    Uses pyyaml when it is there and falls back to a line scan when it is not, so
    that --list works on a bare workshop laptop with nothing installed.
    """
    fy = os.path.join(path, "findings.yaml")
    if not os.path.exists(fy):
        return
    try:
        import yaml
        for f in (yaml.safe_load(open(fy, encoding="utf-8")) or []):
            text = " ".join(str(f.get("text", "")).split())
            yield (f.get("id") or "(no id)", f.get("kind"), f.get("status"),
                   f.get("confidence"), text)
        return
    except ImportError:
        pass
    cur = {}
    for line in open(fy, encoding="utf-8"):
        s = line.strip()
        if s.startswith("- id:"):
            if cur:
                yield (cur.get("id", "(no id)"), cur.get("kind"), cur.get("status"),
                       cur.get("confidence"), cur.get("text", ""))
            cur = {"id": s.split(":", 1)[1].strip()}
        elif ":" in s and not s.startswith("#"):
            k, v = s.split(":", 1)
            k = k.lstrip("- ").strip()
            if k in ("kind", "status", "confidence"):
                cur[k] = v.strip()
            elif k == "text":
                cur["text"] = ""
        elif cur.get("text") == "" and s and not s.startswith(("evidence", "author")):
            cur["text"] = s
    if cur:
        yield (cur.get("id", "(no id)"), cur.get("kind"), cur.get("status"),
               cur.get("confidence"), cur.get("text", ""))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("slice", nargs="?", help="e.g. chips/s5pv210, research/nand-retention")
    ap.add_argument("--slices", action="store_true", help="list the slices and exit")
    ap.add_argument("--list", action="store_true", dest="brief",
                    help="one line per finding instead of the whole slice")
    ap.add_argument("--findings", action="store_true", help="print findings.yaml only")
    ap.add_argument("--guide", action="store_true", help="print README.md only")
    ap.add_argument("--out", metavar="DIR", help="copy the slice into DIR/<slice>")
    ap.add_argument("--online", action="store_true",
                    help="fetch from the public endpoint (not yet available)")
    a = ap.parse_args(argv)

    if a.slices or not a.slice:
        for s in find_slices():
            print(s)
        return 0

    if a.online:
        print("online mode is not yet available.")
        print("  endpoint (planned): " + ONLINE_ENDPOINT.format(slice=a.slice))
        print("  the server -> findings.yaml export bot and the public free-tier")
        print("  endpoint are still to be built. Until then a slice travels with")
        print("  this repository: clone it, or `--out` a copy next to your project.")
        return 3

    slug, path = resolve(a.slice)

    if a.out:
        dest = os.path.join(a.out, slug.replace("/", os.sep))
        if os.path.exists(dest):
            shutil.rmtree(dest)
        shutil.copytree(path, dest)
        print("copied kb/%s -> %s" % (slug, dest))
        return 0

    if a.brief:
        print("kb/%s" % slug)
        n = 0
        for fid, kind, status, conf, text in iter_findings(path):
            n += 1
            print("  %-28s %-16s %-9s %-5s %s"
                  % (fid, kind or "?", status or "?", conf or "?",
                     (text[:96] + "...") if len(text) > 99 else text))
        print("  %d finding(s)" % n)
        return 0

    readme = os.path.join(path, "README.md")
    fy = os.path.join(path, "findings.yaml")
    if not a.findings and os.path.exists(readme):
        sys.stdout.write(open(readme, encoding="utf-8").read())
    if not a.guide and os.path.exists(fy):
        print("\n\n" + "=" * 78)
        print("== kb/%s/findings.yaml" % slug)
        print("=" * 78 + "\n")
        sys.stdout.write(open(fy, encoding="utf-8").read())
    ev = os.path.join(path, "evidence")
    if os.path.isdir(ev) and not (a.findings or a.guide):
        print("\nevidence/ in this slice:")
        for n in sorted(os.listdir(ev)):
            print("  kb/%s/evidence/%s" % (slug, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
