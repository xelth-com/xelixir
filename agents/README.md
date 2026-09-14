# agents/ — the registry

You may fork anything in this repository, and anything in our agent and firmware
projects that carries an open licence. Build your own agent, run it on your own
devices, publish it to your own git. Nothing here asks for permission.

What this directory asks for is a **pointer**: one file, `agents/<name>.md`, saying
what your agent is, what it targets, and which knowledge slices it consumes. A mark,
not a hand-over. It is how the next person with the same board finds your work
instead of redoing it.

## The entry

`agents/<name>.md` — YAML front matter, then prose. The file name **is** the `name`.

```yaml
---
name: my-rescuer
repo: https://github.com/<user>/<repo>
maintainer: <handle>
base: xelth/xlt_agent@<commit>        # or: none
targets: [chips/s5pv210, research/nand-retention]
kb_slices: [chips/s5pv210]            # what it pulls AND pushes back to
server: none                          # or: xelixir-plugin:<name>
status: experimental                  # or: proven
---

## What it does
…a paragraph a stranger can act on…

## Slices it uses
- `kb/chips/s5pv210` — what it reads out of it, what it has contributed back
```

| field | meaning |
|---|---|
| `name` | must equal the file name |
| `repo` | where the code actually is |
| `maintainer` | who to ask |
| `base` | what you forked, pinned to a commit — or `none` if it is yours from scratch |
| `targets` | the slices your agent is *about* |
| `kb_slices` | the slices it **pulls from, and is expected to push findings back to** |
| `server` | `none`, or the WASM plugin it needs (see below) |
| `status` | `experimental`, or `proven` — see the trust label |

CI (`tools/kb/check_findings.py`) refuses an entry whose `targets` or `kb_slices`
name a slice that does not exist, and one whose body does not link every slice it
claims to pull from. That second rule exists because a registry of agents that do
not say what knowledge they act on is a list of names.

## The trust label

* **`experimental`** — you say it works. That is worth publishing; most things start
  here.
* **`proven`** — **we ran it on the bench.** Only a maintainer of this repository
  sets that, after running it against real hardware. Do not set it on your own
  entry; open a PR with `experimental` and ask.

An agent gets no extra trust for being a fork of ours, and none for being popular.

## Agents that need the server

The xelth server stays authoritative: an agent **proposes**, the server **verifies
and decides**, and a community agent talks to it through the same authenticated,
tenant-scoped surface as ours does.

If your agent needs the *server* to learn something new — a new importer, a new
device procedure — that ships as a **WASM plugin in your own tenant's sandbox**
(`plugin_install` → the operator enables it → `plugin_import_run` /
`plugin_device_run`). A plugin sees only the bytes it is handed (an importer) or an
immutable snapshot of one allow-listed device (a procedure). Declare it as
`server: xelixir-plugin:<name>`.

Anything that touches the shared schema, the core protocol or transport, tenancy and
isolation, signing or update authority, or the **meaning of an existing server verb**
is not a plugin job. File a request; we make that change in the core.

> *New leaf behaviour in your own sandbox is yours to ship. A change to the shared
> structure is ours to make.*

Everything in this repository that needs no server at all — which is most of it —
needs none of this. `server: none` is the normal answer.

## Open items

* A public URL for the `xlt_agent` firmware, and the organisation this repository
  itself lives under — **operator to name**. Until then `firmware/esp32-tap.md` and
  our own entry below point at a placeholder.
* The hub page that lists these entries with their trust labels.
