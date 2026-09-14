# proposals/ — findings on their way in

A finding you discovered starts here, as `proposals/<slug>.yaml`, and moves into
`kb/<slice>/findings.yaml` once somebody has reproduced it.

```sh
cp proposals/TEMPLATE.yaml proposals/rom-does-not-answer-below-4v.yaml
$EDITOR proposals/rom-does-not-answer-below-4v.yaml
python tools/kb/check_findings.py
python tools/kb/denylist.py
```

Then open a PR. One finding per file is the easy case; several related ones in one
file is fine when they were learned together.

## What happens to it

1. **You write it.** No `id` — the key is simply absent in `proposals/`, and the
   schema check knows that.
2. **Somebody reads it.** The questions are always the same: could a stranger act on
   this without you in the room; is the evidence there or is it honestly labelled an
   observation; does it contradict a finding we already have.
3. **It is ingested** into the knowledge base with you as `author`, and — this is not
   a judgement of you — **confidence capped at `med`** until it has been reproduced
   on hardware here. It gets a server id at that point.
4. **It is promoted** to the slice's `findings.yaml` with that id, and its
   `confidence` rises when a second, independent observation agrees.

A finding that contradicts an existing one does not edit it. It is a **new** finding
with `supersedes: kb_finding:<old id>`, and the old one is marked `refuted` and left
where it is. The wrong answer is worth keeping: knowing which plausible thing is
false saves the next person exactly as much time as knowing what is true.

## What makes a good proposal

* **Self-contained English.** It will be read years from now, by somebody who has
  never seen the PR it arrived in.
* **Units and addresses explicit.** "≈0.6 ms, a ~1000-iteration FIFO poll" is a fact;
  "fast" is not. Register addresses with the block they belong to.
* **Evidence, or an honest `kind: observation`.** A path under the slice's
  `evidence/` directory, or a plain statement that this was observed and not
  measured. `confidence: low` is respectable and far more useful than a confident
  guess.
* **Negatives are welcome.** `kind: negative` — "this plausible approach does not
  work, and here is how far it got before it failed" — is often the most valuable
  thing in a slice. Two of the S5PV210 findings exist because somebody spent a day
  on advice that turned out to be exactly backwards.
* **Scope it to a class.** If the fact is only true of one company's product, it
  probably belongs in a private note, not here. Ask what is true of the *silicon*,
  the *protocol*, or the *class of software* — and check `tools/kb/denylist.py`
  before you push.

## Proposing a tool, a recipe or a slice

Same door. A tool or recipe PR must carry at least one finding or cite an existing
id (`CONTRIBUTING.md` §2). A whole new slice is a new `kb/<area>/<name>/` with a
`README.md` and a `findings.yaml`, even if it starts with a single finding — say in
the PR what the anchor is and why it is worth its own slice.
