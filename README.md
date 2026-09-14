# xelixir

**A knowledge base you can run.** Open tools for repairing and reverse-engineering
embedded boards, organised around the *facts* the tools encode — not the other way
round. Everything here works **without our server**: a laptop, a USB-serial adapter
or an ESP32 tap, and the slice of knowledge for the chip in front of you.

## Why a knowledge-shaped repository

A repair tool is worthless without the facts it encodes: which pin, which register,
which timing, which retry. The facts are the durable part — scripts rot, get
rewritten, get ported to another language. So the unit of this repository is the
**knowledge slice**, and tools hang off slices.

That also makes contribution symmetric. Someone who fixes a script has to record
the fact that made the fix necessary; someone who found a fact gets the tools that
consume it. *No fact, no merge* (see `CONTRIBUTING.md`).

## How knowledge flows

```
                       ┌──────────────────────────────┐
                       │  the bench / the field        │
                       │  (a board, a scope, a tap)    │
                       └───────────────┬──────────────┘
                                       │ you observe something
                                       ▼
   kb/<slice>/README.md  ◄──curated──  proposals/<slug>.yaml
        (the guide)                     (one finding, unverified)
             ▲                                   │
             │                                   │ reviewed + reproduced
             │                                   ▼
             └──────────────────────  kb/<slice>/findings.yaml
                                       (verified, stable ids)
                                                 │
                              pulled by          │          consumed by
                     ┌───────────────────────────┴──────────────────┐
                     ▼                                              ▼
              tools/<slice-name>/                            agents/<name>.md
              recipes/<procedure>.md                  (your fork, your repo, a
              firmware/<pointer>.md                    pointer left in ours)
```

Two rules keep the loop honest:

* **Findings are never edited in place.** A correction is a *new* finding whose
  `supersedes:` names the old id. The wrong answer stays readable, because knowing
  which plausible thing is false is worth as much as knowing what is true.
* **A finding carries its evidence or says it is an observation.** `confidence:`
  and `status:` are part of the schema, and `low` is a perfectly respectable value.

Findings that originate on xelth's own knowledge base keep their server id
(`kb_finding:…`) so a slice round-trips: export to this repo, contribute back,
re-import. Contributions that start here get an id when they are ingested.

## What is in here now

| path | what |
|---|---|
| `kb/chips/s5pv210/` | Samsung S5PV210 / S5PC110: boot order, the iROM UART-boot protocol, chain-loading a full bootloader over one serial line, the NAND/ECC state a UART boot leaves behind |
| `kb/research/nand-retention/` | SLC NAND retention bit-rot on decade-old boards: the signature, how to diagnose it against a manifest, how to refresh a rootfs |
| `tools/s5pv210-uart-rescue/` | host-side iROM UART loader, chain-BL1 builder, bench driver for the ESP tap |
| `tools/nand-refresh/` | `nandfresh.sh` — rewrite every file of a yaffs2 rootfs to restart the retention clock |
| `tools/kb/` | `kb_pull.py` (pull a slice), `check_findings.py` (schema), `denylist.py` (what must never be published) |
| `firmware/esp32-tap.md` | pointer to the MIT-licensed ESP32 UART tap firmware and the verbs used here |
| `recipes/` | step-by-step procedures composed from slices |
| `agents/` | the registry: community agents/forks, what they target, what knowledge they pull |
| `proposals/` | incoming findings before verification |

## Quick start

### Pull a slice

```sh
python tools/kb/kb_pull.py chips/s5pv210            # print the guide + findings
python tools/kb/kb_pull.py chips/s5pv210 --list     # just the finding index
python tools/kb/kb_pull.py chips/s5pv210 --out ./my-project/kb   # copy it
```

Read `kb/chips/s5pv210/README.md` and `findings.yaml` **before** you touch a tool
that names that slice. Most of the traps are already written down there; the rest
is what you are about to discover.

### Run a tool

```sh
pip install pyserial capstone

# Build a chain bootloader from YOUR OWN first-stage image
python tools/s5pv210-uart-rescue/build_chain_bl1.py --src my-u-boot.bin --out chain.bin --disasm

# Talk to a board that is sitting in its ROM's UART fallback
python tools/s5pv210-uart-rescue/irom_uart_boot.py COM5 chain.bin
```

Every tool's README names the slice it belongs to and the findings it relies on.

### Contribute a finding

```sh
cp proposals/TEMPLATE.yaml proposals/my-observation.yaml
$EDITOR proposals/my-observation.yaml
python tools/kb/check_findings.py            # schema
python tools/kb/denylist.py                  # nothing that must not be published
git commit -am "finding: <one line>" && open a PR
```

## The knowledge economy (how the base grows)

The knowledge base is **two-way**: it is downloaded *and* uploaded, and knowledge is
paid for with knowledge — or with money, for those in a hurry.

| tier | where | what | price |
|---|---|---|---|
| public slice | this repository (`kb/**`) | curated guides + findings older than the 12-month embargo, or donated by their author | free, CC BY 4.0 |
| live KB | xelth server (`kb_search`, `kb_brief`, slice pull, evidence artefacts) | every shared finding including fresh ones, semantic search, per-device procedures | credits (XC) |
| private layer | xelth server, per firm or user | findings you keep to yourself; quota by tier (free: small, paid: much larger, firm licence: largest) | included |

* Every new account starts with **300 XC** — enough for a couple of projects. A
  finding returned in full costs 1 XC, once per account; a whole slice is priced at
  50 % of its findings; findings you wrote yourself are free to you.
* You refill by **contributing**: a merged proposal is reviewed by us and scored
  0–3 (duplicate / helpful note / verified fact with evidence / new capability or a
  correction of a wrong finding). Payout = score × 50 XC × a volume factor for the
  evidence you brought (files, captures, boards; capped ×3). A second party
  verifying it later adds 50 %. For twelve months you also earn a **10 % royalty**
  on what others spend on your finding — quality keeps paying, spam does not.
* **You decide what goes public.** Everything you record can start private (set
  `default_visibility: private` on your account); nobody but your firm sees it, it
  never reaches this repository or the public export. Publishing a private finding
  (`kb_publish`) sends it to review and earns as above; it costs you nothing but the
  private room it no longer takes up.
* Nothing is credited before review; deny-list material earns nothing and is
  revoked. Money buys the same credits instantly (100 XC = 1 €).

A finding starts in the live KB, earning its author, and ages into this repository.
`python tools/kb/kb_pull.py <slice> --online` fetches the public export straight
from the server (`GET /api/kb/public?anchor=…`), the same content as `kb/`, always
current.

## Licence boundary

* **Code** — `tools/`, `recipes/`, `firmware/`, `agents/`, `proposals/`, CI: **MIT**
  (`LICENSE`). Fork it, ship it, sell it.
* **Knowledge** — everything under `kb/`: **CC BY 4.0** (`kb/LICENSE`). Reuse a
  fact, credit it by `id`/`author`.
* **Not here, ever** — vendor firmware images, ROM dumps, disassembly of anybody
  else's binary, derivatives of either, product-specific secrets, customer data.
  This repository publishes *techniques and facts*, which is what reverse
  engineering for interoperability is allowed to produce (EU Directive 2009/24/EC
  art. 6). It does not redistribute anybody's code. The deny-list in
  `CONTRIBUTING.md` is enforced by CI on every push.

If your repair needs a vendor's own binary, get it from the vendor — it is usually
owed to you, GPL or not. This repository will tell you what to *do* with it.

## Relationship to the xelth server

The xelth server (fleet management, the full knowledge base, publishing, device
access) stays ours and stays authoritative: an agent proposes, the server verifies
and decides, and a community fork earns no extra trust for being a fork. What lives
here is everything that does **not** need it. If your agent needs the *server* to
learn a new behaviour, that ships as a sandboxed WASM plugin in your own tenant —
never as a patch to the core. See `agents/README.md`.
