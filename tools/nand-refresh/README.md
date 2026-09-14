# tools/nand-refresh

**Slice:** [`kb/research/nand-retention`](../../kb/research/nand-retention/README.md)
— read it before running anything here. The *order of operations* is the part that
matters, and it is in the slice, not in this README.

## `nandfresh.sh`

Rewrites every regular file of a NAND rootfs so that its retention clock restarts.
POSIX shell, coreutils only (`cp`, `cmp`, `mv`, `ln`, `find`, `sync`) — it runs on a
2.6.32 box with BusyBox and nothing installed, which is the whole point.

```sh
sh nandfresh.sh --dry                     # read everything twice, write nothing
sh nandfresh.sh                           # the real pass
sh nandfresh.sh --roots "/usr /lib /etc"  # part of the tree
```

Environment: `MAX_USE` (default 90 %), `MARGIN_KB` (8192), `SYNC_EVERY` (100),
`STATE_DIR` (`/var/tmp`, where the report lands), `EXCLUDE`, `UNIT` (report label).

### Start with `--dry`

The dry pass reads every file **twice** and compares the digests. A file that reads
differently twice has an unstable page — that is a *restore* job, not a refresh job,
and refreshing it would freeze the wrong bytes in place. The dry pass writes
nothing, so it is safe on a unit you have not decided about yet.

### Do the sweep first

`nandfresh.sh` deliberately does **not** verify content against anything. It cannot:
on a 1-bit-ECC controller a three-bit flip is mis-corrected *silently*, so the file
it reads looks perfectly fine and is wrong. Rewriting it gives those wrong bytes a
brand-new valid ECC and the damage becomes undetectable and permanent.

So: **md5 sweep against the rootfs's own package manifests (or a healthy sibling),
restore every mismatch, and only then run this.** The slice has the commands.

### What it guarantees

* atomic replacement (`cp -p` → `cmp` → `mv`), never an in-place overwrite, so a
  running application keeps its open inode and need not be stopped for correctness
* hard-link groups re-pointed with `ln -f` rather than rewritten, because a
  truncate-and-write is not atomic and a concurrent reader gets a torn file
* an atomic (`mkdir`) lock, because two instances racing on one file have already
  corrupted a hard-link group once
* preflight refusal when the filesystem is over `MAX_USE` or free space is below the
  largest file plus a margin
* directory object headers rewritten, and garbage collection driven at the end so
  the dirty blocks are actually erased
* a timestamped report with the filesystem's own counters before and after

### What it does not do

* touch the raw kernel or bootloader partitions — those need
  `nanddump` → verify → `flash_erase` → `nandwrite`, bad-block aware, and the first
  block only with the layout the boot ROM expects (see
  [`kb/chips/s5pv210`](../../kb/chips/s5pv210/README.md) §7)
* touch anything in `EXCLUDE`: live state directories, databases, `/tmp`, `/proc`,
  `/sys`, `/dev`, `/var/log`. **Add your own application's state directory** — the
  default list cannot know about it
* fix a file. It refreshes what is there, whatever that is.

### Running it

Stop the application that owns the rootfs first if you can: yaffs2 has a single
writer lock, so a pass with it stopped is 5–8× faster and there is no second writer.
Reboot afterwards. Expect the pass to take a while — it is bounded by NAND write
speed, not by the script.

### Findings behind this tool

`kb/research/nand-retention/findings.yaml` — the yaffs2 behaviour (no scrubber,
rotten data returned silently), the refresh procedure and its ordering, the physics
that sets the cadence, and the two case reports that produced both.
