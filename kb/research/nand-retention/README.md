# NAND data-retention bit-rot on decade-old embedded boards

**Slice:** `research/nand-retention` · **findings:** [`findings.yaml`](findings.yaml) ·
**tool:** [`tools/nand-refresh/`](../../../tools/nand-refresh/)

A board built in 2016 is now ten years old. Its rootfs was written **once**, at the
factory, into raw SLC NAND, and most of it has never been rewritten since. The
device works — until one day an application segfaults, or X refuses to open a font,
or a library loads and behaves like it was compiled by somebody drunk.

Nothing is broken. The charge has drifted.

This slice is the failure mode, how to recognise it, how to prove it without
guessing, and how to reset the clock on a whole filesystem without a programmer.

---

## 1. The signature

**What it looks like from outside:** a single binary or library misbehaves
deterministically, at exactly the same point every time, on a machine that is
otherwise entirely healthy. A segfault always at the same program counter inside
one shared library. An X client dying with `BadAlloc` on `X_OpenFont` at the same
request serial every boot. A utility that reads a file fine and produces nonsense.
Reboots do not help, because the bytes really are wrong.

**What it looks like from inside — and this is the part that misleads everybody:**

* The chip is **not** worn out. It erases and programs cleanly, it has no bad-block
  growth, a `nanddump` of a freshly written region verifies perfectly.
* The rot is in **cold data**: files written once at the factory and never touched.
  Sort your suspects by "when was this last written", not by "how often is this
  read".
* The corruption lands anywhere, including in **directory entries**. One unit's
  X11 bitmap fonts had *filename* bytes flipped — `'.'` (0x2E) → 0x06, `'f'`
  (0x66) → `'F'` (0x46), `'-'` → `'\r'`, a `'-'` dropped entirely. `fonts.dir`
  still listed names whose files now "did not exist", so the X server asked for a
  font that was not there and the application died before it ever reached its
  network phase. A `rm` by the displayed name fails; `od -c` shows why.
* **The error counters lie low.** On a 1-bit hardware-ECC controller, one flipped
  bit per 512 B is corrected, two are detected — and **three are mis-corrected
  silently**. So the kernel's `eccUnfixed` count *understates* the damage, and in
  the worst cases it reads zero while a file is measurably wrong. On one unit the
  counters stayed at 0/0 while reading the rotten library, and only moved after a
  full-filesystem sweep.

That last point is the whole reason this slice exists: **you cannot use the ECC
statistics as your diagnostic.** You have to compare against something known good.

## 2. The physics, briefly

* SLC NAND is rated for ~100 000 program/erase cycles *and* ~10 years of retention
  — but those are not simultaneous. JESD47 qualifies 10 years at ≤10 % of rated
  endurance and **1 year at 100 %**; retention scales roughly inversely with
  cycling. Arrhenius behaviour (Ea ≈ 1.1 eV) puts published SLC curves at about
  10 years below 35 °C and about 1 year at 55 °C for a cycled block. A device in a
  warm cabinet ages faster than the datasheet headline suggests.
* **Read disturb** is a second, independent budget: on the order of 10⁶ reads per
  block for SLC before neighbouring pages start flipping. Every erase resets it.
* **Re-programming a page resets both clocks.** This is charge drift, not oxide
  damage — the cell is fine, its stored level has decayed. Rewriting is the
  mitigation, and it is the vendors' own: flash application notes list "refresh
  data blocks after a pre-defined time" as a supported measure, SSD firmware does
  it as read-reclaim, and the academic version is "Flash Correct-and-Refresh".
* **The wear cost of refreshing is negligible**: an annual full-filesystem rewrite
  spends well under 1 % of a 100 000-cycle budget per decade.

## 3. Why the filesystem will not save you

On **yaffs2** — the usual choice on raw NAND of this vintage:

* There is **no background scrubber and no time-based refresh**. Wear levelling is
  implicit: new chunks go to fresh blocks. A file nobody writes is never moved.
* The error handling is **reactive**. A read whose ECC result is *fixed* or
  *unfixed* marks the block for prioritised garbage collection, and after a few
  strikes the block is retired. But garbage collection **copies live chunks as they
  are**, without re-deriving anything, and — the critical part — **the data is
  returned to the caller even when it was uncorrectable.** Silence, not an error.
* `refresh_period` (default 500: every 500 GC selections, garbage-collect the
  oldest full block) is driven by **write activity**. A rootfs nobody writes never
  triggers it, and vendor 2.6.32 trees may predate the feature entirely.

**UBI** is better but still reactive: it scrubs a physical block on the first
`-EUCLEAN` (copy to a fresh block, torture-test the old one) and moves static data
when the erase-counter spread exceeds a threshold. Better is not the same as
scheduled.

Conclusion: on these boards, **refresh is an operator's job**. Nothing in the stack
will do it for you.

## 4. Diagnosis: the md5-manifest method

You need a reference. There are three, in order of preference.

**(a) The system's own package manifests — no second device needed.** Debian-family
rootfs images carry `/var/lib/dpkg/info/<package>.md5sums`, written at packaging
time. That is a known-good digest for every packaged file, *on the machine itself*:

```sh
# the fast path when you already suspect a library
cd / && md5sum -c /var/lib/dpkg/info/<package>.md5sums

# the full sweep
cd / && for f in /var/lib/dpkg/info/*.md5sums; do
          md5sum -c "$f" 2>/dev/null | grep -v ': OK$'
        done
```

Pair it with a crash: run the failing binary under `catchsegv` (or read the kernel's
fault message), map the faulting program counter to a library through the process
memory map, and check *that* library first. One case went from "the application
segfaults, no idea why" to a named rotten file in three commands this way.

**(b) A healthy sibling of the same generation.** Per-directory digests are enough
to isolate the rotten unit, and two healthy units agreeing with each other is what
proves *which* one is wrong:

```sh
find DIR -type f | sort | xargs md5sum | md5sum
```

**(c) A full-rootfs digest sweep** stored as a manifest, taken while the unit is
healthy, so that future-you has a reference. This is the cheap insurance: do it at
commissioning.

Expect the sweep to find far more than the file that broke. Typical result on a
rotten unit: one runtime-relevant file plus 20–170 cold files that nothing
executes — documentation, static `.a` libraries, unused display drivers, locale and
font data, changelogs. Those are not the fault; they are the **dosimeter**. Their
count tells you how far the rot has gone, which tells you whether you are fixing one
file or refreshing the whole filesystem.

## 5. Repair, then refresh — in that order

**The order matters and getting it wrong bakes the damage in permanently.**

1. **Sweep and restore first.** A 1-bit ECC controller mis-corrects a 3-bit flip
   *silently*, so a rewrite of an already-wrong file writes the wrong bytes back
   with a brand-new, perfectly valid ECC. The rot becomes undetectable and
   permanent. Restore every mismatching file from a healthy source of the same
   package version **before** you refresh anything.
2. **Refresh the filesystem** — `tools/nand-refresh/nandfresh.sh`, section 6.
3. **Refresh the raw partitions** (kernel, bootloader) separately. There is no
   standard "nandrefresh" for these; the practice is `nanddump -o -b` → verify →
   `flash_erase` → `nandwrite -p`, bad-block aware, and the first block only with
   the layout the boot ROM expects (see
   [`kb/chips/s5pv210`](../../chips/s5pv210/README.md) §7 — the ROM's OOB layout is
   **not** the driver's). Keep the dump you took; that is your undo.

Note for anyone hoping a vendor update solves it: a vendor application update
typically replaces only the application directory. It does **not** rewrite the
rotten system files, so the unit comes back from the update still rotten.

## 6. The refresh itself

On a log-structured filesystem like yaffs2, a plain copy-and-rename **is** a
refresh: the copy lands in erased blocks with fresh ECC, and the old chunks become
dirty and are erased by garbage collection later. What the script has to get right
is everything around that:

* `cp -p` to preserve mode, owner and timestamps; then **`cmp` the copy against the
  original**. Two reads that differ mean an unstable page — log it as UNSTABLE and
  **leave that file alone**; it is a restore job, not a refresh job.
* **`mv` onto the original, never an in-place overwrite.** The rename is atomic, and
  a process that already has the file open keeps its old inode, so a running
  application need not be stopped for correctness.
* **Hard links need care.** A truncate-and-write is not atomic and a concurrent
  reader sees a torn file — that is how a `bzip2` and an `fsck` died mid-run in one
  session. Instead: atomically `mv` onto the first name, then re-point every other
  name of the old inode at the fresh file with `ln -f` (also atomic per name).
* **An atomic lock.** Two instances racing on the same file corrupted two hard-link
  groups on one unit, because a test-then-create lock let both in. `mkdir` is the
  atomic primitive available in a POSIX shell; use it.
* **Refuse to start when there is no room.** Free space must exceed the largest file
  plus a margin, and a filesystem past ~90 % full is a refusal, not a challenge.
* **Directory object headers are not rewritten by file traffic.** They rot like
  anything else. A `touch -c -r "$d" "$d"` per directory rewrites the header without
  changing the timestamp.
* **Drive garbage collection at the end**, or the old dirty blocks linger unerased
  until something happens to need them: fill most of the free space with one
  throwaway file, `sync`, delete it, `sync`.
* **Stop the application that owns the rootfs** if you can. yaffs2 has a single
  writer lock, so a pass with the application stopped is 5–8× faster, and there is
  no second writer to race with. Reboot afterwards.

### Cadence

| when | what |
|---|---|
| at commissioning | take a full-rootfs digest manifest and keep it |
| every 2–3 years | a full refresh pass |
| **immediately** | on any increase in the `eccFixed` / `eccUnfixed` counters — remember they understate |
| annually | if you also want to cover the read-disturb budget, or if the unit runs warm |

### Deciding between repair and replacement

When a board of this age starts returning bad bytes, the reflex is to swap it. Two
arguments against, both of which have bitten people:

* **A replacement board of the same age carries exactly the same risk**, because
  this is a property of the age of the *data*, not a fault in the chip.
* **The board usually carries identity and calibration.** Serial number, network and
  licence state, accumulated user data, and — on instruments — per-unit calibration
  constants that the application pushes into other boards at every boot. A foreign
  board carries foreign constants, so a swap implies a full recalibration, while a
  file-level repair keeps every byte of it. On regulated equipment the swap is also
  a *repair* in the regulatory sense, with the post-repair testing that implies,
  where restoring files is service.

Escalate to a board swap when the NAND itself fails: growing bad blocks,
`eccUnfixed` rising on freshly *written* data, the bootloader region affected, or
an actual electrical fault.

## Related

* [`tools/nand-refresh/`](../../../tools/nand-refresh/) — `nandfresh.sh`
* [`kb/chips/s5pv210`](../../chips/s5pv210/README.md) — when the rot has already
  taken the rootfs far enough that there is no shell to run any of this from
