#!/usr/bin/env python3
"""Pull one knowledge slice - offline from this repository, or online from the public export.

    python tools/kb/kb_pull.py chips/s5pv210              # print guide + findings
    python tools/kb/kb_pull.py chips/s5pv210 --list       # one line per finding
    python tools/kb/kb_pull.py chips/s5pv210 --findings   # the raw findings.yaml
    python tools/kb/kb_pull.py chips/s5pv210 --out ./kb   # copy the slice there
    python tools/kb/kb_pull.py --slices                   # what slices exist

Offline mode (the default, and the only one that works today) reads the slice out
of this checkout. That is deliberate: a repair happens in a workshop, sometimes on
a bench with no network and a board that is the only interesting thing in the
room. The knowledge has to be on the laptop already.

Online mode (`--online`) reads the public export - see ONLINE_ENDPOINT below.

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

# The public export of xelth's knowledge base: the same findings that age into
# kb/**/findings.yaml (12-month embargo, or donated by their author), served
# unauthenticated as a JSON list in the findings.yaml shape. A slice maps to the
# anchor slug it is published under (the last path element, e.g. chips/s5pv210
# -> s5pv210, research/nand-retention -> nand-retention).
ONLINE_ENDPOINT = "https://xelth.com:3221/api/kb/public?anchor={anchor}&limit={limit}"
ONLINE_TIMEOUT = 20


def fetch_online(slice_name, limit=500):
    """Return (anchor, findings-list) from the public export, or raise OSError."""
    import json
    import urllib.request
    anchor = slice_name.rstrip("/").split("/")[-1]
    url = ONLINE_ENDPOINT.format(anchor=anchor, limit=int(limit))
    req = urllib.request.Request(url, headers={"User-Agent": "xelth-open kb_pull"})
    with urllib.request.urlopen(req, timeout=ONLINE_TIMEOUT) as r:
        data = json.loads(r.read().decode("utf-8"))
    if isinstance(data, dict):
        data = data.get("findings", [])
    return anchor, data


def dump_findings(items):
    """findings list -> YAML text (pyyaml if present, else a plain block dump)."""
    try:
        import yaml
        return yaml.safe_dump(items, sort_keys=False, allow_unicode=True, width=88)
    except ImportError:
        import json
        return json.dumps(items, indent=2, ensure_ascii=False)


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
                    help="fetch the slice's findings from the public export on the server")
    ap.add_argument("--limit", type=int, default=500, help="--online: max findings")
    a = ap.parse_args(argv)

    if a.slices or not a.slice:
        for s in find_slices():
            print(s)
        return 0

    if a.online:
        try:
            anchor, items = fetch_online(a.slice, a.limit)
        except Exception as e:  # network, HTTP, JSON - all the same to a workshop laptop
            print("online pull failed: %s" % e)
            print("  the slice still travels with this repository: `--out` a copy.")
            return 3
        if a.brief:
            print("public export, anchor=%s" % anchor)
            for f in items:
                print("  %-28s %-16s %-9s %-5s %s" % (
                    f.get("id", "?"), f.get("kind", "?"), f.get("status", "?"),
                    f.get("confidence", "?"), (f.get("text") or "").strip()[:96]))
            print("  %d finding(s)" % len(items))
            return 0
        text = dump_findings(items)
        if a.out:
            dest = os.path.join(a.out, a.slice.replace("/", os.sep))
            os.makedirs(dest, exist_ok=True)
            fn = os.path.join(dest, "findings.online.yaml")
            open(fn, "w", encoding="utf-8").write(text)
            print("wrote %s (%d finding(s))" % (fn, len(items)))
        else:
            sys.stdout.write(text)
        return 0

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
