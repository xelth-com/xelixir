# tools/s5pv210-uart-rescue

**Slice:** [`kb/chips/s5pv210`](../../kb/chips/s5pv210/README.md) — read it first.
These three scripts are the mechanics; the slice is the reasoning, and every trap
below is explained there in more detail.

```
irom_uart_boot.py     host-side mask-ROM UART loader: send one payload
build_chain_bl1.py    build a chain first stage from YOUR OWN bootloader image
esp_chain_bench.py    stepwise chain-boot driven through a UART tap's console
```

```sh
pip install pyserial capstone
```

**No image of anybody's is shipped here, and none is embedded in the scripts.** You
bring your own board's bootloader image; the tools locate what they need inside it by
disassembling it at run time.

---

## The bench sequence

The situation: a board whose kernel boots but whose userspace never gives you a
shell, or whose NAND bootloader is gone. Goal: a root shell, without a NAND
programmer and without opening anything you do not have to.

### 0. Prepare

You need your board's **own** bootloader image — from the vendor, from your own
build, or dumped from a working sibling of the same generation. You need **both
stages from that same generation**: the NAND timings differ between generations of
the same product, and a mismatched first stage re-times the NAND so that the first
megabyte reads and everything after it comes back ECC-uncorrectable
(`kb_finding:wjca6hkifegra44evx20`).

```sh
python build_chain_bl1.py --src my-u-boot.bin --dest-base <TEXT_BASE> \
                          --out chain.bin --disasm
```

Read the `--disasm` output once. It prints where it found the copy dispatcher, which
`NFCONF` value it took out of *your* image, and the dead region it is writing into —
three things that are worth a glance before you push code into a board.

### 1. Wire up

To the SoC's **UART2** — which on a finished product is usually not the connector
marked for a PC, and may be at RS-232 levels behind a transceiver that lives on a
different board (slice §2). 115200 8N1. If you can also reach the board's **console**
UART at the same time, do: you want to see what the second stage prints after it
starts, and that comes out of the other connector.

### 2. Fetch → chain

**With a tap** (recommended — it owns the timing and it can hold both images):

```
uart chan 2 <rx> <tx> 115200          # move the tap engine onto the ROM's line
uart irom fetch bl1 <url> --sum16 ...  # once; the images live in the tap's flash
uart irom fetch bl2 <url> --sum16 ...
uart irom go --chain --wait 300000 --watch 1 <console-rx>
```

**Without a tap**, one transaction at a time:

```sh
python irom_uart_boot.py COM5 chain.bin          # sends the first stage
```

…but a full chain from a PC means answering each of the first stage's follow-up
transactions yourself, which is what `esp_chain_bench.py` automates against a tap.

### 3. Short, power, release

Short two NAND data lines to each other on the **powered-off** board, apply power,
and watch the indicator (slice §1). Release as soon as the fallback pattern appears.
Do not bridge pins on a live board, and never touch Vcc/Vss.

From here the ROM spends ~45 s on its NAND and SD timeouts before it starts
streaming `0xAA`. That is why every wait defaults to minutes.

### 4. Prompt

If the chain ran, the second stage is in DDR and running, reading the kernel out of
NAND exactly as it would on a normal boot. Move your tap back to the **console** UART
and you are at a bootloader prompt (or, if the second stage is unpatched, watching a
normal boot).

### 5. `init=/bin/sh`

```
setenv bootargs root=... rootfstype=... console=... rw init=/bin/sh
nand read <load addr> <kernel offset> <length>
bootm <load addr>
```

Take the addresses from **your** board's own `bootcmd` and `bootargs`, not from
anyone's example. This changes nothing in NAND — the next power cycle boots normally.

Then, on that shell: mount `/proc`, look at the filesystem's free space and error
counters, fix what is wrong. Keep the session short — boards of this class often
have a watchdog that resets them after a few quiet minutes — and never run a
whole-filesystem `du` in there.

---

## `irom_uart_boot.py`

Sends one payload through the ROM's UART boot.

```sh
python irom_uart_boot.py PORT IMAGE [--bytes 8192] [--strip-header] [--wait 600]
```

Start it **before** powering the board on; it waits for the first `0xAA`.

Two behaviours that look like bugs and are not:

* It writes `0xCC + length + payload + checksum` as **one** `write()`. That is
  required, not stylistic — the ROM reads the length with a ~0.6 ms FIFO poll
  immediately after matching your `0xCC` (`kb_finding:es2bl4vrrdsb9b1u47lo`).
* It reports success when the `0xBB 0xBB 0xBB` receipt does **not** arrive, as long
  as no error text did. The loaded code re-initialises the UART and eats its own
  receipt.

## `build_chain_bl1.py`

Builds a first stage that pulls the second stage over the same wire.

```sh
python build_chain_bl1.py --src <your image> --dest-base <TEXT_BASE> \
                          --out chain.bin [--disasm]
```

Nothing is hard-coded about your image. The script finds, by disassembly:

* **the second-stage copy dispatcher** — via the relocate check that precedes it
  (`ldr` a mask, `bic` it out of `pc`, `bic` the same mask out of the link address,
  `cmp`, `beq` to the stack/bss block). The instruction after the `beq` is the
  dispatcher; the `beq` target is where our routine branches when the download is
  done. Three of the five words are fixed encodings, which makes the pattern
  specific enough to scan for.
* **the `NFCONF` literal** your first stage's `lowlevel_init` programs — via a
  pc-relative load of `0xB0E00000` followed by the read-modify-write shape
  (`ldr`, `ldr` mask, `bic`, `ldr` value, `orr`, `str`). In Samsung-lineage U-Boot
  1.3.4 first stages we have seen, this literal differs between software
  generations — one programs timings 3 with PageSize 0, the next timings 7 with
  PageSize 1 — which is exactly why it must be read out of the image instead of
  assumed. The injected routine replays `0x01800000 | <that value>`: the ECC-select
  bits a NAND boot leaves, OR'd onto the board's own timings.
* **a dead region to live in** — by walking the image's reachable code twice, once
  as-is and once with the dispatcher already redirected, and subtracting. Code
  reachable *only* through the dispatcher is dead; the largest contiguous run of it
  that fits the routine and its literal pool is where the routine goes. Too small,
  and the script refuses rather than overwriting something live.

What it emits is in
[`kb/chips/s5pv210/evidence/chain-bl1-injected-routine.txt`](../../kb/chips/s5pv210/evidence/chain-bl1-injected-routine.txt),
annotated instruction by instruction. **Not yet emitted** (documented in slice §5):
the NAND chip `RESET` the ROM issues inside its ready/busy probe. On a board whose
probe failed, the chip goes through the whole session without ever receiving one,
and that is the leading suspect for "the first region reads and later pages come
back garbage". Until the generator does it, issue the sequence by hand at the prompt.

The SoC constants it *does* hard-code — the ROM download entry point, the per-packet
limit, the NAND and ECC register addresses and the ROM's own values — are facts about
the silicon and are all in the slice.

## `esp_chain_bench.py`

Drives the stepwise chain through a tap's console, staging each payload as base64.

```sh
python esp_chain_bench.py TAPPORT chain.bin second_stage.bin --bl2-len 0xE0000
```

Use it while you are still learning what a board does — it stops between slices and
a failed slice is one retried verb, because the tap keeps the chain armed and the
slice staged. Once the board is understood, let the tap fetch both images over HTTP
into its own flash and run the whole chain as a single verb: ~3 s against ~25
minutes of acked base64 lines.

## Findings behind these tools

`kb/chips/s5pv210/findings.yaml` —
`iesh8tso6j1n8z4cc245` (boot order and protocol),
`es2bl4vrrdsb9b1u47lo` (the contiguous frame),
`epnlbwufeyqh0ihccyis` (the NAND short),
`0b8qha5wfvtme1fnis1b` (the indicators),
`74tb9a2xwhrsq1x4onwp` (which UART, at what levels),
`ka4th2tuclj4es4gbad2` (the chain design and retry-until-zero),
`1ls66vjcbwlpm9394x9o` (the silent hang and its cause),
`wjca6hkifegra44evx20` (NFCONF per generation),
`ai9woq1s20fi2f829r3r` (the whole thing, proven end to end).

Change a tool, add a finding — see [`CONTRIBUTING.md`](../../CONTRIBUTING.md).
