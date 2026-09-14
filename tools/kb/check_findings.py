#!/usr/bin/env python3
"""Schema validator for `kb/**/findings.yaml`, `proposals/*.yaml` and `agents/*.md`.

    python tools/kb/check_findings.py            # everything
    python tools/kb/check_findings.py kb/chips/s5pv210/findings.yaml

Exit status 0 = valid, 1 = at least one problem, 2 = the validator could not run.
Requires `pyyaml` (`pip install pyyaml`).

FINDING (one list entry in a findings.yaml / proposals/*.yaml):

    id:          kb_finding:<slug>   optional - absent for a proposal, assigned on ingest
    kind:        protocol | re_fact | observation | hypothesis |
                 repair_heuristic | repair_outcome | negative
    anchors:     {chip: s5pv210, concept: irom_uart_boot}   at least one anchor
    confidence:  high | med | low
    status:      verified | proposed | refuted
    text:        the finding in English, self-contained
    evidence:    {kind: re_function|capture|observation, path: "...", summary: "..."}
    author:      xelth | <github handle>
    date:        YYYY-MM-DD
    supersedes:  kb_finding:<slug>   optional, or a list of them

AGENT REGISTRY ENTRY (`agents/<name>.md`, YAML front matter between --- lines):

    name, repo, maintainer, base, targets[], kb_slices[], server, status

    kb_slices must name slices that exist under kb/, and the body of the file
    must link every slice it claims to pull from.
"""
import os
import re
import sys

try:
    import yaml
except ImportError:
    sys.stderr.write("check_findings.py needs pyyaml:  pip install pyyaml\n")
    sys.exit(2)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

KINDS = {"protocol", "re_fact", "observation", "hypothesis",
         "repair_heuristic", "repair_outcome", "negative"}
CONFIDENCE = {"high", "med", "low"}
STATUS = {"verified", "proposed", "refuted"}
EVIDENCE_KINDS = {"re_function", "capture", "observation", "measurement", "bench_log"}

ID_RE = re.compile(r"^kb_finding:[a-z0-9]{6,40}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

AGENT_REQUIRED = ["name", "repo", "maintainer", "base", "targets", "kb_slices",
                  "server", "status"]
AGENT_STATUS = {"experimental", "proven"}
SERVER_RE = re.compile(r"^(none|xelixir-plugin:[a-z0-9_\-]+)$")


class Problems(object):
    def __init__(self):
        self.n = 0

    def __call__(self, where, msg):
        self.n += 1
        print("%s: %s" % (where, msg))


def slice_exists(slug):
    return os.path.isdir(os.path.join(ROOT, "kb", slug.replace("/", os.sep)))


def check_finding(f, where, bad, proposal):
    if not isinstance(f, dict):
        bad(where, "entry is not a mapping")
        return

    unknown = set(f) - {"id", "kind", "anchors", "confidence", "status", "text",
                        "evidence", "author", "date", "supersedes", "tags"}
    if unknown:
        bad(where, "unknown key(s): %s" % ", ".join(sorted(unknown)))

    fid = f.get("id")
    if fid is None:
        if not proposal:
            bad(where, "a finding in kb/ must carry its server id, or the literal "
                       "'pending' if it originated here and has not been ingested "
                       "yet (only proposals/ may omit the key entirely)")
    elif str(fid) == "pending":
        pass  # originated in this repository; an id is assigned on ingest
    elif not ID_RE.match(str(fid)):
        bad(where, "id %r is not kb_finding:<slug> (or 'pending')" % fid)

    if f.get("kind") not in KINDS:
        bad(where, "kind %r not one of %s" % (f.get("kind"), sorted(KINDS)))
    if f.get("confidence") not in CONFIDENCE:
        bad(where, "confidence %r not one of %s" % (f.get("confidence"), sorted(CONFIDENCE)))
    if f.get("status") not in STATUS:
        bad(where, "status %r not one of %s" % (f.get("status"), sorted(STATUS)))

    anchors = f.get("anchors")
    if not isinstance(anchors, dict) or not anchors:
        bad(where, "anchors must be a non-empty mapping, e.g. {chip: s5pv210}")
    else:
        for k, v in anchors.items():
            if not re.match(r"^[a-z][a-z0-9_]*$", str(k)):
                bad(where, "anchor key %r must be lower_snake_case" % k)
            if not str(v).strip():
                bad(where, "anchor %r has an empty value" % k)

    text = f.get("text")
    if not isinstance(text, str) or len(text.strip()) < 40:
        bad(where, "text must be a self-contained English sentence or more "
                   "(at least 40 characters)")

    author = f.get("author")
    if not isinstance(author, str) or not author.strip():
        bad(where, "author is required (xelth, or a github handle)")

    date = f.get("date")
    if not DATE_RE.match(str(date)):
        bad(where, "date %r is not YYYY-MM-DD" % date)

    sup = f.get("supersedes")
    if sup is not None:
        for s in (sup if isinstance(sup, list) else [sup]):
            if not ID_RE.match(str(s)):
                bad(where, "supersedes %r is not kb_finding:<slug>" % s)

    ev = f.get("evidence")
    if ev is not None:
        if not isinstance(ev, dict):
            bad(where, "evidence must be a mapping {kind, path, summary}")
        else:
            if ev.get("kind") not in EVIDENCE_KINDS:
                bad(where, "evidence.kind %r not one of %s"
                    % (ev.get("kind"), sorted(EVIDENCE_KINDS)))
            if not str(ev.get("summary", "")).strip():
                bad(where, "evidence.summary is required - say what it shows")
            path = ev.get("path")
            if path and not str(path).startswith(("http://", "https://")):
                rel = os.path.join(os.path.dirname(where.split(":")[0]), str(path))
                if not os.path.exists(os.path.join(ROOT, rel)):
                    bad(where, "evidence.path %r does not exist (%s)" % (path, rel))
    elif f.get("kind") in {"re_fact", "protocol", "repair_outcome"}:
        # not fatal, but say it out loud
        print("%s: note - %s finding with no evidence block; consider "
              "kind: observation or add evidence" % (where, f.get("kind")))


def check_findings_file(path, bad):
    rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
    proposal = rel.startswith("proposals/")
    with open(path, encoding="utf-8") as fh:
        try:
            doc = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            bad(rel, "not valid YAML: %s" % exc)
            return 0
    if doc is None:
        return 0
    if not isinstance(doc, list):
        bad(rel, "top level must be a LIST of findings")
        return 0
    seen = set()
    for i, f in enumerate(doc):
        where = "%s:[%d]" % (rel, i)
        check_finding(f, where, bad, proposal)
        fid = isinstance(f, dict) and f.get("id")
        if fid and fid != "pending":
            if fid in seen:
                bad(where, "duplicate id %s in the same file" % fid)
            seen.add(fid)
    return len(doc)


def check_agent_file(path, bad):
    rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
    raw = open(path, encoding="utf-8").read()
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", raw, re.S)
    if not m:
        bad(rel, "must begin with a YAML front-matter block between '---' lines")
        return
    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError as exc:
        bad(rel, "front matter is not valid YAML: %s" % exc)
        return
    body = m.group(2)
    if not isinstance(meta, dict):
        bad(rel, "front matter must be a mapping")
        return

    for key in AGENT_REQUIRED:
        if key not in meta:
            bad(rel, "missing required key %r" % key)

    name = str(meta.get("name", ""))
    stem = os.path.splitext(os.path.basename(path))[0]
    if name != stem:
        bad(rel, "name %r must equal the file name %r" % (name, stem))
    if not str(meta.get("repo", "")).startswith(("https://", "http://")):
        bad(rel, "repo must be a URL")
    if str(meta.get("status")) not in AGENT_STATUS:
        bad(rel, "status %r not one of %s" % (meta.get("status"), sorted(AGENT_STATUS)))
    if not SERVER_RE.match(str(meta.get("server", ""))):
        bad(rel, "server must be 'none' or 'xelixir-plugin:<name>' "
                 "(a server capability only ever runs as a WASM plugin in your "
                 "own tenant sandbox)")

    for key in ("targets", "kb_slices"):
        val = meta.get(key)
        if not isinstance(val, list) or not val:
            bad(rel, "%s must be a non-empty list of slice paths" % key)
            continue
        for slug in val:
            if not slice_exists(str(slug)):
                bad(rel, "%s names %r, which is not a slice under kb/" % (key, slug))

    for slug in (meta.get("kb_slices") or []):
        if str(slug) not in body:
            bad(rel, "the body does not link kb/%s - an agent must document the "
                     "knowledge it pulls from (and pushes back to)" % slug)


def main(argv):
    bad = Problems()
    targets = argv[1:]
    findings_files, agent_files = [], []

    if targets:
        for t in targets:
            p = os.path.abspath(t)
            (agent_files if p.endswith(".md") else findings_files).append(p)
    else:
        for base, dirs, names in os.walk(os.path.join(ROOT, "kb")):
            dirs[:] = [d for d in dirs if d != ".git"]
            for n in names:
                if n == "findings.yaml":
                    findings_files.append(os.path.join(base, n))
        pdir = os.path.join(ROOT, "proposals")
        if os.path.isdir(pdir):
            for n in sorted(os.listdir(pdir)):
                if n.endswith((".yaml", ".yml")) and not n.startswith("TEMPLATE"):
                    findings_files.append(os.path.join(pdir, n))
        adir = os.path.join(ROOT, "agents")
        if os.path.isdir(adir):
            for n in sorted(os.listdir(adir)):
                if n.endswith(".md") and n != "README.md":
                    agent_files.append(os.path.join(adir, n))

    total = 0
    for p in sorted(findings_files):
        total += check_findings_file(p, bad)
    for p in sorted(agent_files):
        check_agent_file(p, bad)

    if bad.n:
        print("\nSCHEMA: %d problem(s). See the docstring of this file, or "
              "CONTRIBUTING.md section 3." % bad.n)
        return 1
    print("schema ok: %d finding(s) in %d file(s), %d agent entr(y/ies)"
          % (total, len(findings_files), len(agent_files)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
