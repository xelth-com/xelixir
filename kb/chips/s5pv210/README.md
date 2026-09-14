# Samsung S5PV210 / S5PC110 — getting into a board that has no shell

**Slice:** `chips/s5pv210` · **findings:** [`findings.yaml`](findings.yaml) ·
**tools:** [`tools/s5pv210-uart-rescue/`](../../../tools/s5pv210-uart-rescue/) ·
**recipe:** [uart-rescue-of-a-board-without-shell](../../../recipes/uart-rescue-of-a-board-without-shell.md)

The S5PV210 (and its twin the S5PC110) is an ARM Cortex-A8 SoC from around 2010
that is still in the field in industrial and medical equipment, usually running a
Samsung-lineage U-Boot 1.3.4 and a 2.6.32 kernel on raw NAND with a yaffs2 rootfs.

This slice is about one situation: **the board boots, but you cannot get a shell.**
The kernel comes up and `init` stalls before any getty, or userspace crash-loops,
or the rootfs is full, or the bootloader environment is a coin flip and no key
stops autoboot. There is nothing to log into, nothing to `ssh` to, and the thing
you actually need is a write channel that bypasses Linux — and, if the NAND
bootloader is gone too, bypasses NAND as well.

The SoC has one: the mask ROM ("iROM") will accept a bootloader over a plain
serial line. This document is how that works, and everything that bites on the way.

---

## 1. Boot order, and how to steer it

The ROM tries boot devices in a fixed order, selected by the OM strap pins:

```
  OM-selected primary device  (on a NAND board: NAND)
        │  first-stage (BL1) checksum fails?
        ▼
  SD/MMC channel 2
        │  no card?
        ▼
  UART boot on UART2          ← the one we want
        │  host never answers?
        ▼
  USB boot                    ← "Enumeration TimeOut Error", then it loops back
```

So the whole trick is to make the primary device's BL1 read *fail*. The ROM then
walks down the list of its own accord, and two steps later it is asking a serial
port to send it a bootloader.

The ROM narrates this on UART2 in plain text. The strings worth recognising:

| string | what it means |
|---|---|
| `Nand RnB Detect Error` | the ROM's NAND RESET + ready/busy probe failed — the fallback is armed |
| `SD Init Error` | no card on channel 2, moving on to UART |
| `Uart negotiation Error` | the host did not answer `0xAA`/`0xCC` in time |
| `Uart Data Length Over` | the length field the ROM read was nonsense (see §3 — almost always *your* framing) |
| `Uart Check Sum Error` | the 16-bit sum did not match what the ROM computed as it stored |
| `Enumeration TimeOut Error` | UART gave up too, USB boot also found nobody |

### Forcing the fallback: the NAND-short trick

On a powered-off board, short **two NAND data lines to each other**, then apply
power. The ROM's read of the first-stage image comes back corrupt, its checksum
fails, and it falls through to SD → UART.

On a TSOP-48 x8 NAND the data lines are IO0–IO3 = pins 29–32 and IO4–IO7 =
pins 41–44 (pins 45–48 are not connected). The pair that is reliable to bridge
with a probe tip by hand is the **8th and 9th pin counting from the pin-48
corner**, i.e. pins 40/41. **Never** touch Vcc/Vss (pins 12/13 and 36/37) — those
are the two ways to damage a board that was otherwise fine.

Rules that come from doing this rather than reading about it:

* **Short first, then power.** Bridging a pin pair on a live board dumps a charged
  rail through a pin. Short the powered-off board, apply power, release once the
  fallback is confirmed.
* **Confirm it worked before you wait.** A weak contact lets a ROM retry succeed
  and the board simply boots normally — which looks identical to "the trick does
  not work on this board". Watch an indicator (below), not the clock.
* **Release once the fallback is entered.** NAND reads normally again afterwards,
  which is exactly what you want: the bootloader you are about to load will read
  the kernel out of that same NAND.

### Indicators when you have no console

Boards in this class usually carry a small row of status LEDs driven directly by
the boot path, and they tell you the two things you need before any serial data
exists:

* **normal boot** — one steady pattern (on the board family we worked on: only the
  middle LED of three).
* **ROM fallback entered** — a *different* pattern within about a second of
  power-on (there: all three lit dimly at once). This is the signal to release the
  short.
* **uploaded first stage accepted and running** — the pattern reverts to the normal
  one, because your code has taken over the pins.

Check what your own board does once, with a console attached, and then you can work
on the next one blind. Timing on the board family we measured: the ROM spends about
45 s on its NAND and SD timeouts before it starts streaming on UART.

---

## 2. Which UART, and at what levels

UART**2** is the ROM's boot port. That is a SoC fact and is not negotiable — the
ROM does not scan.

On a finished product UART2 is usually *not* the port marked for a PC. On the board
family we worked on, UART1 is the Linux console (brought out on a DB9 as pins 1 TX
/ 4 RX / 5 GND) and **the analog-board connector carries UART2**, through an
RS-232 transceiver of the MAX3232 class that sits on the other board. Two
consequences:

* A 3.3 V TTL tap cannot be wired to that connector directly: it needs its own
  transceiver in between, or you use a plain USB-RS-232 adapter.
* Your Linux console and the ROM's boot port are **different physical connectors**.
  During a chain-load you want to watch both — see `--watch` in
  [`firmware/esp32-tap.md`](../../../firmware/esp32-tap.md).

Line settings: **115200 8N1**, no flow control. Device nodes on this SoC's 2.6.32
vendor trees are `/dev/s3c2410_serialN`, not `ttySACN`, even though the console
kernel argument is `console=ttySAC1` — a detail that costs an hour the first time.

---

## 3. The UART-boot protocol (and the one rule everybody gets wrong)

```
  target  →  0xAA 0xAA 0xAA ...        (5 attempts, ~300 ms apart)
  host    →  0xAA                       once
  target  →  0xCC 0xCC 0xCC ...
  host    →  0xCC  LE32(len+6)  <len bytes>  LE16(sum16)     ← ONE contiguous write
  target  →  0xBB 0xBB 0xBB             then jumps to the payload
```

* The payload is stored at **`0xD0020000`** in internal SRAM and jumped to
  directly. UART and USB boot expect **no 16-byte header** there — but leaving the
  header in place is harmless if the image was linked with it, because those four
  words happen to execute as conditional ANDs.
* **Maximum payload `0x15400` (85 KB)** per transaction. A whole U-Boot does not
  fit; that is what §4 is for.
* `sum16` is a plain 16-bit sum of the payload bytes. The ROM recomputes it *from
  memory as it stores*, so a match also verifies the DDR/SRAM write for free.

### The contiguous-frame rule

**The ROM's byte-read routine is a ~1000-iteration FIFO poll — roughly 0.6 ms —
not a timed wait.** It reads the four length bytes with that same short poll
*immediately* after matching your `0xCC`.

So the length must follow your `0xCC` echo **with no gap at all**: write
`0xCC + LE32(len+6) + payload + LE16(sum)` as one contiguous stream. The two
checksum bytes use the short poll as well, so they go back-to-back with the last
payload byte. Only the payload loop itself is unbounded.

The intuitive approach — echo `0xCC`, then stay silent, wait for the target's
`0xCC` stream to *stop*, and only then send length and payload — **fails**, and it
fails in a way that looks like a hardware problem: four poll timeouts return
`0xFF`, the length reads as `0xFFFFFFFF`, and about three seconds later the ROM
prints `Uart Data Length Over` and falls through to USB boot. This is the wall the
2011 XDA "UnBrickable" work hit on the same SoC family, and the "never pipeline,
wait for silence" advice that came out of it is exactly backwards. Disassembling
the ROM's receive helpers settles it: the negotiate helper sends its byte, polls
*once*, and repeats — it is not waiting for you, it is shouting periodically.

### Reading the result

**The `0xBB 0xBB 0xBB` receipt is usually lost.** The code you just loaded
re-initialises the UART as its first act, and the receipt dies in the reset. So:

> **absence of `0xBB` together with absence of error text = success.**

Treat a missing receipt as `OK?`, not as failure, and judge by what the loaded
code does next.

Throughput at 115200: 8192 bytes in about 0.7 s, 86 KB in about 7.5 s.

---

## 4. Chain-loading a whole bootloader over one serial line

85 KB per transaction is not enough for a real U-Boot (~900 KB). The way through is
to make the *first* payload a first-stage image that calls the ROM's own downloader
again, as many times as it takes.

The ROM exposes its UART download routine as a callable subroutine (on this SoC at
`0xD0008640`, taking port, mode, destination and max-length, returning 0 on
success; the negotiate helper it uses sits at `0xD00085D0` and the byte read at
`0xD0008594`). Your first stage does not have to reimplement the protocol — it
re-arms the ROM's.

Design of the chain first stage, in the order the pieces matter:

1. **Take your own board's BL1** (the first 8 KB of its own bootloader image) and
   redirect its NAND/SD copy dispatcher into a region of the image that becomes
   unreachable once you have redirected it. You are replacing the "copy the second
   stage from storage" step with "receive the second stage from the wire". Using
   the board's own BL1 is not laziness: it already did the DRAM bring-up, the pin
   mux and the clock setup for *this* board, and you do not want to redo any of it.
   `build_chain_bl1.py` locates every image-specific address by disassembly, so it
   works on any BL1 of this lineage (see the tool's README for how).
2. **First transaction: a 4-byte header** — `LE32(total length of the second
   stage)` — into a scratch address in internal SRAM.
3. **Then a loop of slices** of `0x15000` each, straight into DDR at the second
   stage's link address, advancing by the length the ROM reports it received.
4. **Retry every call until it returns 0.** This is not defensive style, it is
   load-bearing: the downloader waits only about 1.5 s (5 × 300 ms) for the host,
   and the host is routinely not ready on the first try. A first revision that
   ignored the return code jumped into unwritten DDR and the board went silent with
   the LEDs back to normal — which reads as "the first stage crashed" and sends you
   debugging the wrong thing. Retry the *same* call, with the same destination, and
   do not advance the pointer.
5. **Replay the ROM's NAND-controller and ECC bring-up** before jumping (§5). This
   is the step whose absence produces the silent hang in §6.
6. **Stamp the boot-device register** (`INFORM3`, `0xE010F00C`) with the value that
   means "booted from NAND" (2), then fall into the first stage's own stack/bss
   setup and jump to the second stage. U-Boot's environment code dispatches on that
   register; after a UART boot it holds something that does not mean what you want.

Timing for the whole thing on a real board: about 203 s from power-on to Linux
userland — roughly 45 s of ROM NAND/SD timeouts, 0.7 s for the 8 KB first stage,
then 11 slices of 86 KB at ~7.5 s each plus handshakes.

A note on where the images live. Staging a 900 KB second stage through a console as
base64 takes about 25 minutes. Fetching it over HTTP into the tap's own flash takes
about 3 seconds, and then every subsequent attempt costs nothing. If your tap can
reach a network, put the images in the tap.

---

## 5. The NAND/ECC state a UART boot does *not* leave behind

This is the heart of the slice, and it generalises far beyond this SoC: **a ROM
that boots from NAND configures the NAND controller as a side effect, and a driver
written against that ROM silently depends on it.**

The ROM's NAND bring-up has two stages. The first always runs:

```
  NFCONF (0xB0E00000) = 0x7776          absolute store — the upper bits go to 0
  NFCONT (0xB0E00004) = 7
  then the RnB probe: NFCONT &= ~2 (assert nCE0); NFSTAT = 0x10;
                      NFCMMD = 0xFF (chip RESET); poll NFSTAT[4] with a timeout
                      success -> NFCONT |= 2  ·  failure -> "Nand RnB Detect Error"
```

The second stage runs **only if that probe succeeded** — i.e. exactly the branch
that is skipped when you short the data lines:

```
  NFCONF    = (x & ~0x1800000 & ~0xFF00 & ~0xF0) | 0x1800000 | 0x7700 | 0x70
  NFCONT   &= ~0x40000 ; |= 0x41 ; &= ~0xE00
  NFSTAT   |= 0xF0                                  clear ALL transition-detect bits
  NFECCCONF (0xB0E20000) = (x & ~0x3FF0000 & ~0xF) | 0x01FF0000 | 3
                                                    MsgLength 512 B, ECC type 3 = 8-bit
  NFECCCONT (0xB0E20020) &= ~0x3000000 ; &= ~0x10000
  NFECCSTAT (0xB0E20030) |= 0x3000000               W1C the sticky ENC/DEC done bits
  NFCONT   |= 0x80 ; &= ~2
  NFSTAT    = 0x10
```

Register map for this SoC, since half the confusion is people using the wrong one:

| block | base | registers |
|---|---|---|
| NAND controller | `0xB0E00000` | NFCONF +00, NFCONT +04, NFCMMD +08, NFADDR +0C, NFDATA +10, NFMECCDATA0/1 +14/+18, NFSTAT +28, NFESTAT0 +2C, NFESTAT1 +30, NFMECC0/1 +34/+38, NFMLCBITPT +40 |
| ECC engine | `0xB0E20000` | NFECCCONF +00, NFECCCONT +20, NFECCSTAT +30, NFECCSECSTAT +40, NFECCPRGECC0.. +90, NFECCERL0.. +C0, NFECCERP0.. +F0 |

Field facts worth having in front of you:

* `NFCONF[24:23]` is a 2-bit ECC-type selector. Bit 23 = 8-bit ECC, bit 24 = the
  MLC/4-bit select. A NAND boot leaves `0b11`; after a UART fallback it is `0b00`.
* `NFCONT`: bit 0 = MODE, bit 1 = nCE0 (**active low — 0 means selected**), bit 5 =
  InitMECC, bit 7 = MECC lock.
* `NFSTAT` bit 4 = RnB transition detect, write-1-to-clear.
* `NFECCCONF` MsgLength is *bytes − 1* at bits [25:16], so 512 B is `0x01FF0000`;
  ECC type 3 = 8-bit, 5 = 16-bit.
* `NFECCSTAT` bit 24 = decode done, bit 25 = encode done, bit 31 = busy.
* The geometry the ROM picks comes from a strap field: 2 KB/5 cycles/8-bit ECC,
  4 KB/5/8-bit, 4 KB/5/16-bit or 2 KB/4/8-bit.

**What to replay, and what not to.** The chain first stage should reproduce the
second block above, with one important amendment:

| store | value | note |
|---|---|---|
| `NFCONF` | `0x01800000 \| <BL1's own value>` | see below — do **not** hard-code |
| `NFCONT` | `0xC5` | MODE, nCE0 asserted, `0x40`, `0x80`, `~0xE00`, `~0x40000` |
| `NFSTAT` | `0xF0` | clear every transition-detect bit |
| `NFECCCONF` | RMW → `0x01FF0003` | 512 B message, 8-bit ECC |
| `NFECCCONT` | `&= ~0x03010000` | decode direction |
| `NFECCSTAT` | `\|= 0x03000000` | clear sticky done bits |
| `NFSTAT` | `0x10` | clear RnB transition detect |
| chip `RESET` | `NFCMMD = 0xFF` + poll `NFSTAT[4]` | **recommended, see below** — the one ROM step a naive replay misses |

The three `NFECC*` writes are read-modify-write on purpose: their reset values are
not something to assume, and the ROM itself uses RMW there.

### NFCONF must come from the board's own BL1, never from a constant

The upper bits `[24:23]` are the ECC select and are the same everywhere. The
**lower nibbles are NAND timings and page geometry, and they differ between
software generations of the same product.** Two BL1s we compared were
instruction-for-instruction identical except for exactly one literal: the value
their `lowlevel_init` programs into `NFCONF` — `0x3332` (timings 3, PageSize 0) in
one generation, `0x7776` (timings 7, PageSize 1) in the next.

A chain first stage that hard-codes `0x01807776` therefore silently *re-times* the
NAND of an older board. The symptom is beautifully misleading: the first megabyte
or so reads perfectly, and then every later read comes back ECC-uncorrectable while
the same blocks read fine under a normal NAND boot. The data is intact — a raw dump
shows a valid image — the controller is simply being driven wrong.

`build_chain_bl1.py` reads the value out of the source BL1's own `lowlevel_init`
and ORs only the ECC-select bits onto it. **Rule: fetch the first *and* second
stage of the board's own generation.** If you do not know which generation you
have, dump the first pages of the bootloader partition from a working prompt and
compare.

### The chip RESET the replay must not skip

The ROM issues a NAND `RESET` (`NFCMMD = 0xFF`) inside its ready/busy probe — and
on a board where that probe *failed*, the chip never receives a RESET during the
whole session: the ROM did not complete one, U-Boot's `board_nand_init` only issues
READID, and a naive replay of the second block above does not issue one either.

"The first region reads perfectly and later pages come back garbage" is the
classic signature of a NAND left in a stale internal mode (a cache-read state, a
random-output column counter, a feature setting), not of a mis-set ECC field — a
wrong ECC configuration would fail region zero too.

So the replay should end with a chip RESET. **This is a recommendation, not yet
generated by `build_chain_bl1.py`** — the generator emits the seven stores in the
table above and nothing more; until it does, issue the sequence by hand at a
prompt (`mw.l`), where it also serves as a no-power-cycle recovery of a controller
that has been left in a bad state:

```
  NFCONT  = 0xC1        MODE, nCE0 asserted
  NFSTAT  = 0x10        clear RnB transition detect
  NFCMMD  = 0xFF        NAND RESET
  poll NFSTAT bit 4 until set (bound the loop with a counter, as the ROM does)
  NFSTAT  = 0x10
  NFCONT  = 0xC5        back to the post-setup value
```

---

## 6. Why U-Boot 1.3.4 hangs right after `NAND:` when it was loaded over UART

The symptom: the banner prints, `NAND:` prints, the size prints, and then nothing,
forever, with no error and no further output. Identical binary, identical board;
booted from NAND it works, chain-loaded into DDR it hangs.

The mechanism, in order:

1. `start_armboot` calls `nand_init()`, which prints the size. `nand_scan` only
   issues RESET/READID, which are status-polled and use no ECC — which is why the
   size *does* print.
2. Next call is `env_relocate()`. `env_init` in the classic `env_nand.c` sets
   `gd->env_valid = 1` unconditionally, so the "is the environment valid" branch is
   always taken.
3. `env_relocate_spec()` is a **switch on the boot-device register** `INFORM3`
   (`0xE010F00C`): 1, 2 = NAND, 3, 4 …, anything else falls through to
   `use_default()` and prints `*** Warning - using default environment`. Both a
   NAND boot and a chain boot land in the NAND branch, because the first stage
   writes that register from the OM straps.
4. The NAND branch does the **first real ECC-protected page read** of the session,
   to fetch the saved environment.
5. And the S5PV210 NAND driver in this U-Boot has **four unbounded, timeout-free
   hardware polls**: `while(!(NFSTAT & 1))` (ready/busy), `while(!(NFECCSTAT &
   (1<<25)))` (encode done), `while(!(NFECCSTAT & (1<<24)))` (decode done) and
   `while(NFECCSTAT < 0)` (busy). Each is five instructions with no printf and no
   counter.
6. `board_nand_init` programs **almost nothing** — it clears one `NFCONT` bit and
   otherwise inherits whatever the ROM left. After a UART fallback the entire
   `0xB0E20000` block is at reset defaults and `NFCONF[24:23]` is `0b00`, so the
   ECC engine is configured for a different message length and ECC type than the
   driver assumes, the done bit never asserts, and the read spins forever.

Hence §5. Replay the ROM's bring-up before you jump, and the read completes.

Two things worth knowing even though they are not the cause:

* A one-store **diagnostic**: write `INFORM3 = 0` after the download loop and
  before the jump. The environment dispatch then falls through to `use_default()`,
  prints its warning, and reaches the prompt **without touching NAND at all**. One
  boot tells you whether the fault is in the NAND read path or somewhere in printf
  / malloc / DRAM. Do not ship this — it disables reading the saved environment.
* The ROM's UART helper leaves **PWM timer 4 armed** with its interrupt enabled.
  This looks like a promising culprit and is not: U-Boot's `timer_init` rewrites
  the timer registers, and interrupts are masked from the first instruction of BL1
  with no interrupt-controller enable anywhere. Ruled out by reading, not by
  guessing. (Clearing it anyway costs two stores, and the chain first stage may as
  well also stop the watchdog and clear the interrupt-controller enables.)

---

## 7. 1-bit main ECC versus the 8-bit ECC block — they coexist and they disagree

Two different ECC schemes are live on the same chip at different times, and reading
a page written under one with the other returns garbage. This matters whenever you
dump, rewrite or verify a boot region.

* **The ROM, reading the boot region**, uses the 8-bit ECC engine and lays out
  **13 parity bytes per 512-byte chunk at OOB offset `12 + 13·k`**.
* **U-Boot's live driver, reading everything else**, selects ECC by NAND ID at
  `board_nand_init`: a large-page SLC chip gets `nand_ecc_type = 1` = **1-bit main
  ECC, 4 bytes per 512-byte chunk, at OOB[40..55]** of a 64-byte OOB. (MLC gets
  type 2 = 4-bit; small page gets type 1 as well.)

Those two layouts are **completely disjoint**. The boot region is not readable with
the driver's layout and vice versa.

More facts from the same disassembly, each of which has misled somebody:

* **The 8-bit ECC functions in that U-Boot are dead code.** A full literal scan of
  the image finds no pointer to the 8-bit enable/calculate/correct routines.
  Nothing in that U-Boot ever programs `NFECCCONF`/`NFECCCONT`/`NFECCSECSTAT` at
  run time. The only live touches of the `0xB0E20000` block are the three
  spin-waits, and they are reached only when `nand_ecc_type != 1`. So on an SLC
  board the ECC-engine configuration you replay is *irrelevant to the running
  driver* — it matters to the ROM and to nothing else. That is not a reason to get
  it wrong, but it is a reason not to blame it.
* **`NFCONF` bit 24 is not read-only.** A readback of `0x00807776` on a running
  system is exactly what the driver's `hwctl` produces (`bic r3, r3, #0x1000000`)
  when `nand_ecc_type == 1`. It proves two things at once: the 1-bit path is
  selected, and `hwctl` has actually run. Bit 23 stays set because no live code
  path ever clears it.
* **`nand read` has no raw path and no offset special-casing.** The block loop is
  `block_isbad()` then `mtd->read()`, and `mtd->read` goes through the full
  hardware-ECC page read for the bootloader region and the kernel region alike. A
  return of `-74` (`-EBADMSG`) means the ECC statistics' failure counter moved.
* The 1-bit correct routine switches on `NFESTAT0 & 3`: 0 = clean, 1 = one bit
  corrected, **2 or 3 = uncorrectable**, which prints and returns −1.
* At the U-Boot prompt of this vintage, **every `nand read` length must be erase-
  block aligned** (`0x20000`). A misaligned length is rejected by the command
  parser with `ERROR: Length ... is not block aligned` and *never touches the
  NAND* — so a batch of probe reads can all "fail" while telling you nothing
  whatsoever about the hardware. Check that you are not reading a wall of parser
  errors as evidence.

---

## 8. Stopping autoboot from the console alone — a class of behaviour

The last resort in this slice is the deterministic one (chain-load). The *cheap*
one, when the NAND bootloader still runs, is to stop autoboot and get a prompt. On
vendor builds of this lineage that is not the `bootdelay` mechanism you know, and
it is worth describing as a class because the same pattern shows up across vendors:

* **`bootdelay` may be ignored entirely.** A vendor `abortboot()` can open its
  window only if a character is **already pending** in the console receiver at the
  instant of the check — a single sample, not a countdown loop. Nothing pending and
  autoboot proceeds with no prompt and no second chance, while the familiar
  `Hit any key to stop autoboot: 3` line may not even be printed.
* **The stop key may be a word, not a key.** Once the window opens, the first N
  characters read have to spell a specific short string chosen by the vendor.
  Anything read before the first correct character just burns a countdown tick.
* **The receiver behaves as a one-byte holding register** and something between the
  banner and the check flushes it, so a single early byte is lost.

Which gives the technique, and it is a technique, not a product fact:

> **Keep the first letter of the stop word permanently *pending* from before
> power-on** — rewrite it every ~15 ms, far more often than the flush-to-check gap
> and far less often than a byte time at 115200 — and the moment the prompt line
> appears in the stream, stop that letter and write **the rest of the word within a
> millisecond or two**, with nothing in between. A letter written *after* the
> countdown starts lands where the second letter belongs and kills the word.

That reaction time is why this belongs in a tap's firmware rather than in a PC loop
over USB: a host sending a letter every 600 ms and reacting over a USB round trip is
too slow. The crude variant — stream the whole word continuously at ~9 sends/s from
before power-on — works too and needs no state machine, but on some builds it is a
lottery (about one boot in three), because something between the banner and the
check consumes or reorders the pending bytes. When the console stop is a lottery,
the chain-load is the deterministic path.

If you do get a prompt, the fastest way to a shell on a box whose userspace is
broken is to boot the stock kernel with `init=/bin/sh`:

```
setenv bootargs root=/dev/mtdblock2 rootfstype=yaffs2 console=ttySAC1,115200n81 rw init=/bin/sh
nand read 20008000 <kernel offset> <length>
bootm 20008000
```

Take the load address, the kernel offset and the length from **the board's own
`bootcmd`** (`printenv`, or `strings` on a dump of its bootloader partition); the
rootfs device and filesystem type from its own `bootargs`. Do not copy somebody
else's numbers — that is exactly the kind of "fact" that is true for one product
and wrong for yours.

That changes the environment **in RAM only** — nothing is written to NAND, so the
unit boots normally on the next power cycle. Keep console lines short (a tap's line
buffer is often a couple of hundred characters), keep the session short (these
boards frequently have a watchdog that resets them after a few minutes of a shell
doing nothing visible), and never run a whole-filesystem `du` in there.

---

## 9. What is deliberately not in this slice

No ROM dump, no ROM disassembly, no vendor bootloader image, no derivative of one,
no offsets into anybody's binary, no vendor stop word, no product-specific NAND
block roles. Those are facts about one company's product, not about this SoC, and
they are not ours to publish.

Everything above is a fact about the **silicon** and about the **class of software**
it runs, which is what reverse engineering for interoperability is for. The tools
take *your own* board's images as input and locate what they need by disassembling
them, so nothing of anybody's is embedded in this repository — see
[`tools/s5pv210-uart-rescue/README.md`](../../../tools/s5pv210-uart-rescue/README.md).

## Related

* [`kb/research/nand-retention`](../../research/nand-retention/) — why a board of
  this age has unreadable files in the first place, and what to do about it.
* [`recipes/uart-rescue-of-a-board-without-shell.md`](../../../recipes/uart-rescue-of-a-board-without-shell.md)
  — the whole procedure end to end.
* [`firmware/esp32-tap.md`](../../../firmware/esp32-tap.md) — the tap that drives it.
