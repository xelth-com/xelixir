# Recipe: getting a root shell on a board that has none

**Composed from:** [`kb/chips/s5pv210`](../kb/chips/s5pv210/README.md) ·
[`kb/research/nand-retention`](../kb/research/nand-retention/README.md)
**Tools:** [`tools/s5pv210-uart-rescue/`](../tools/s5pv210-uart-rescue/) ·
[`tools/nand-refresh/`](../tools/nand-refresh/) ·
[`firmware/esp32-tap.md`](../firmware/esp32-tap.md)

## When this applies

An embedded Linux board — S5PV210 class, U-Boot 1.3.4 of the Samsung lineage,
2.6.32 kernel, yaffs2 on raw NAND — where:

* the kernel boots but `init` never reaches a getty, **or**
* userspace crash-loops, **or**
* the rootfs is full and nothing can start, **or**
* the NAND bootloader itself is unreadable,

and pressing keys at boot does nothing. You need a shell, and there is no network,
no login and no service menu to give you one.

## Before you touch anything

**Know which software generation the board is.** You will need its own bootloader
image, and NAND timings differ between generations of the same product; a mismatched
first stage re-times the NAND so that the first megabyte reads fine and everything
after it comes back ECC-uncorrectable. If you have a working sibling of the same
generation, dump its bootloader partition now, while it is easy.

**Have both stages ready**: a chain first stage built from that image
(`build_chain_bl1.py`), and the whole image as the second stage.

**Decide which door you are trying.** There are two, and they are not equal:

| | console autoboot stop | ROM UART chain-load |
|---|---|---|
| opens the unit? | no | yes (NAND pins) |
| needs the vendor's stop word? | yes | no |
| works with a dead NAND bootloader? | no | **yes** |
| deterministic? | **not always** — can be ~1 boot in 3 | yes |
| time to a prompt | ~1 min | ~3 min |

Try the console first when the bootloader still runs: it needs nothing but the
external port. Fall back to the chain when it does not, or when the lottery
irritates you.

---

## Path A — stop autoboot from the console

1. Attach to the board's **console** UART (115200 8N1; on a finished product it is
   often hidden on an unexpected pin pair of a connector marked for something else,
   and it is the only port that emits anything at power-on, which is how you find
   it).
2. **Arm before power.** With a tap: `uart uboot-stop --wait 300000`. By hand:
   stream the stop word continuously (~9 times a second, no carriage return) from
   before power-on.
3. Power on. The banner arrives tens of seconds later, the prompt a few seconds
   after that.
4. Missed it? Power-cycle and re-arm. If several attempts miss, go to path B — that
   failure is documented behaviour, not your wiring
   (`kb_finding:m8r5wum41b4cto6n952r`).

## Path B — chain-load a bootloader through the mask ROM

1. **Wire to UART2**, which on a finished product is usually a different connector
   from the console and may be at RS-232 levels behind a transceiver on another
   board (slice §2). Wire the console too, if you can, and watch it passively.
2. **Load the images**: `uart irom fetch bl1 <url>` + `fetch bl2 <url>` once into the
   tap's flash, or stage them with `esp_chain_bench.py`.
3. **Arm**: `uart irom go --chain --wait 300000 --watch 1 <console-rx>`.
4. **Short, power, release.** Short two NAND data lines to each other on the
   **powered-off** board (on a TSOP-48 x8, pins 40/41 are the pair you can bridge by
   hand; **never** Vcc/Vss at 12/13 and 36/37), then apply power, then release as
   soon as the indicator changes — on the boards we worked on, all three LEDs lighting
   at once instead of just the middle one. NAND reads normally again afterwards,
   which is what lets the loaded bootloader fetch the kernel.
5. **Wait.** The ROM spends ~45 s on its NAND and SD timeouts first. Then the first
   stage lands in under a second and the second stage in eleven slices of ~7.5 s.
   About 3 minutes from power-on.
6. Move the tap back to the console. You are at a prompt, or watching a normal boot
   if your second stage was unpatched.

## Then — from prompt to shell

```
setenv bootargs root=... rootfstype=... console=... rw init=/bin/sh
nand read <load addr> <kernel offset> <length>
bootm <load addr>
```

Take every address from **your** board's own `bootcmd` and `bootargs`
(`printenv`, or `strings` over a dump of its bootloader partition). This writes
nothing to NAND — the next power cycle boots normally.

Twenty seconds later: `uid=0`.

```sh
mount -t proc proc /proc
mount -t sysfs sysfs /sys
```

## Then — diagnose, in this order

**1. Is it full?**

```sh
df -k /
echo t > /tmp/probe; echo rc=$?        # rc != 0 on a full filesystem
grep -i 'nFreeChunks\|nErasedBlocks' /proc/yaffs
```

A full rootfs is the boring answer and a common one on a device that has been
logging or exporting for a decade. Free regenerable space first — package caches,
`/var/lib/apt/lists`, your application's own logs — never user data you have not
been told you may delete. `sync; sync` before powering down.

**2. Is it rotten?**

```sh
grep -i 'eccFixed\|eccUnfixed' /proc/yaffs
cd / && for f in /var/lib/dpkg/info/*.md5sums; do
          md5sum -c "$f" 2>/dev/null | grep -v ': OK$'
        done
```

The counters **understate** — a 1-bit ECC controller mis-corrects a three-bit flip
silently — so the manifest sweep is the diagnostic, not the counters. If the
application segfaults, run it under `catchsegv`, map the faulting address to a
library through the memory map, and check that library first. See
[`kb/research/nand-retention`](../kb/research/nand-retention/README.md).

**3. Restore, then refresh.** Copy healthy files in from a sibling of the same
generation (paced and verified in chunks if you are pushing them through a serial
console — an unpaced `base64 -d` stream fails and the remainder lands in your
shell), and only then run
[`tools/nand-refresh/nandfresh.sh`](../tools/nand-refresh/nandfresh.sh). Refreshing
first would give the wrong bytes a fresh valid ECC and make the damage permanent.

## Two things that will bite you

* **The watchdog.** Boards of this class reset themselves after a few minutes of a
  rescue shell doing nothing visible. Keep sessions short, keep commands fast, and
  never run a whole-filesystem `du` in there.
* **Console line limits.** A tap's console line buffer is often a couple of hundred
  characters. Keep `setenv` lines under it, or they arrive truncated and you spend
  ten minutes wondering why the kernel got the wrong root device.

## When to stop and swap the board instead

When the NAND itself is failing — bad blocks growing, `eccUnfixed` rising on data
you just *wrote*, the bootloader region affected — or when there is an electrical
fault. Not because files are rotten: a replacement board of the same age carries
exactly the same rot risk, and usually carries the unit's identity, its accumulated
data and its calibration with it.
