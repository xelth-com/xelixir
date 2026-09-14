# Contributing

This repository is a knowledge base with tools attached. The contract below is
short on purpose — it is meant to be read by a human in two minutes and by an
agent in one.

## The contract (6 points)

### 1. Work on a slice

Pull it first:

```sh
python tools/kb/kb_pull.py chips/s5pv210
```

Read the slice's `README.md` (the curated guide) and its `findings.yaml` (the
raw, dated, attributed facts) **before** you touch a tool that names that slice.
Every tool README says which slice it belongs to. If the slice you need does not
exist yet, propose it: a new `kb/<area>/<name>/` with a `README.md` and a
`findings.yaml`, even if it starts with one finding.

### 2. No fact, no merge

Every PR that changes a tool, a recipe or a firmware pointer must **add at least
one finding, or reference an existing finding id** in its description.

* new fact → `proposals/<slug>.yaml`, one finding, no `id:` (an id is assigned on
  ingest)
* known fact → cite `kb_finding:<id>` in the PR body and, where it matters, in a
  code comment

A change with no fact behind it is either a typo fix (fine, say so) or a change
nobody can evaluate (not fine). If a tool was wrong, *something* about the world
made it wrong — write that down. The bug you fixed is the cheap part; the reason
it was a bug is the expensive part.

Corrections never overwrite. A finding that replaces another gets a new entry
with `supersedes: kb_finding:<old id>`, and the old one stays with
`status: refuted` (or `verified` if it was right but narrower).

### 3. English, explicit units, evidence or an honest "observation"

* Findings are written in English, self-contained — readable without the PR that
  introduced them, without a chat log, without you.
* Register addresses in hex with the block they belong to; times with units;
  voltages with a reference. "Fast enough" is not a fact; "≈0.6 ms, a ~1000
  iteration FIFO poll" is.
* Give `evidence:` a path under the slice's `evidence/` directory, or set
  `kind: observation` and say plainly that it is one. `confidence: low` is a
  respectable value and much more useful than a confident guess.
* Say what you did **not** see. A `kind: negative` finding ("this plausible
  approach does not work, here is how far it got") saves the next person a week.

### 4. The deny-list (CI-enforced)

Run it yourself before you push:

```sh
python tools/kb/denylist.py
```

The list lives in `.denylist` and CI runs the same scan on every push and PR. It
refuses:

* **product names of protected devices** and vendor brand names — describe the
  *class* instead: "a Samsung-lineage U-Boot 1.3.4 board", "the vendor's stop
  word", "the analog-board connector carries UART2 on the board we worked on"
* **customer, unit and board serial numbers**, customer names, phone numbers,
  addresses
* **vendor binaries and derivatives**: no `*.bin`, no `*.dis`, no ROM dumps, no
  disassembly of somebody else's image, no patched copy of a vendor image
* **file offsets inside a vendor image presented as facts about a product.**
  Describe the *code pattern* the tool matches on ("the relocate check: `ldr` a
  mask, `bic` it out of `pc`, compare against the link address, `beq` to the bss
  block") and let the tool find the offset by disassembly. An offset is a fact
  about one binary you happen to hold; a pattern is a fact about the world.
* **vendor stop words, passwords, service codes, NAND block roles**, partition
  maps that only make sense for one product
* **binary artefacts** of any kind: `*.bin`, `*.dis`, `*.pdf`, dumps

None of this is about hiding knowledge. It is the line between *publishing a
technique* (lawful, useful, what this repository is for) and *redistributing
somebody else's product* (not ours to do). Everything the deny-list strips exists
— it lives in a private knowledge base, and the public slice describes the class
so that the technique still works for you on your own board.

### 5. Agents that need a server

Community agents are welcome and they may fork anything here. If your agent needs
the **xelth server** to understand something new — a new importer, a new device
procedure — it ships as a **WASM plugin in your own tenant's sandbox**
(`plugin_install` → the operator enables it → `plugin_import_run` /
`plugin_device_run`). A plugin sees only the bytes it is handed, or an immutable
snapshot of one allow-listed device. It never becomes core server code by being
popular.

Anything that touches the shared schema, the core protocol, tenancy, signing/OTA
authority, or the meaning of an existing server verb is **not** a plugin job:
file a request. The dividing line is *new leaf behaviour in your own sandbox is
yours to ship; a change to the shared structure is ours to make.*

Register your agent with `agents/<name>.md` — see `agents/README.md` for the
schema and what the `proven` label means.

### 6. What a merged finding earns

A proposal that passes review is ingested into the live knowledge base under your
name and **scored** by us — 0 (duplicate, unsupported), 1 (a note that helps),
2 (a verified fact with evidence), 3 (a new capability, or a correction of a wrong
finding). The score, times 50 credits, times a volume factor for the evidence you
brought, lands on your account; a later independent verification adds 50 %, and
for twelve months you receive 10 % of what other users spend on that finding.
Credits buy access to the live base (fresh findings, artefacts, semantic search) —
see `README.md` § *The knowledge economy*. Corrections (`supersedes:`) are the
best-paid class because they fix the base for everyone. Deny-list material earns
nothing and, if it slipped through, is revoked together with its credits.

## Mechanics

```sh
python tools/kb/check_findings.py     # findings.yaml + agents/*.md schema
python tools/kb/denylist.py           # the scan above
```

Both run in CI (`.github/workflows/check.yml`) on every push and pull request.
Both must pass. `denylist.py` needs nothing but Python 3.8; `check_findings.py`
needs `pyyaml` (`pip install pyyaml`). `kb_pull.py --list` deliberately works
without it too, because a workshop laptop is allowed to have nothing installed.

Commit messages: `finding: …`, `tool(<slice>): …`, `kb(<slice>): …`,
`recipe: …`, `agents: …`.
