# The ESP32 UART tap — `xlt_agent`

**Slices:** [`kb/chips/s5pv210`](../kb/chips/s5pv210/README.md) ·
[`kb/research/nand-retention`](../kb/research/nand-retention/README.md)
**Registry entry:** [`agents/xelth-esp32-tap.md`](../agents/xelth-esp32-tap.md)

Several procedures in this repository need something on the serial line that a PC
cannot be: a device that reacts in **under a millisecond**, that can hold a byte
pending in a receiver from before a board is powered on, and that owns a UART
outright for a minute at a time without an operating system deciding otherwise.

That device is a small ESP32-class module running the MIT-licensed `xlt_agent`
firmware. This file describes **what the relevant verbs do**, so that you can use
the firmware — or write your own, or teach an existing tap the same tricks. The
firmware itself is not vendored here.

> **Where to get it:** the `xlt_agent` firmware, ESP-IDF project, MIT-licensed
> (see its own `LICENSE`). Ask us for the current repository location — the pointer
> from this file to a public URL is one of the open items in
> `agents/README.md`. It carries **no board payloads**: the images a rescue uses are
> fetched or staged at run time, because the payload belongs to whoever owns the
> board and the protocol belongs to everybody.

Hardware, on the side that matters: two UARTs' worth of pins, one wired to the
board's console and one to the port you are working on, with an RS-232 transceiver
in front of whichever of them is not at TTL levels. Both at 115200 8N1 for
everything in this repository.

---

## `uart chan <n> <rx> <tx> [baud]` — which UART the engine owns

Moves the whole tap engine — capture ring, bridge, listener, sender, the ROM
loader — to hardware UART `n` on those pins, and remembers it across reboots.

This exists because of a specific shape of problem: **the port you must talk to and
the port you must watch are different connectors**, and on the boards this was built
for they are not even at the same voltage levels. One module, two solder jobs, one
verb to move between them. `uart chan 2 …` to talk to the mask ROM, `uart chan 1 …`
to be back on the console.

## `uart irom fetch bl1|bl2 <url> [--sum16 <hex>] [--sha256 <hex>]`

Downloads an image over HTTP(S) into the module's own flash, once, and chain-loads
it from there for ever after.

The number that justifies this verb: staging a ~900 KB second stage through a
console as base64 took **25 minutes**; the same bytes over HTTP take **~3 seconds**.

Do it safely, because a half-written image in flash is worse than none: invalidate
the old directory record **before** erasing the body, and write the new record only
**after** reading the stored bytes back off flash and re-digesting them. A power cut
then leaves "absent", never "half an image". Report the length, the 16-bit sum (the
same sum the ROM's frame carries) and the SHA-256, and compare them against what the
caller expected.

## `uart irom go --chain [--wait <ms>] [--tail <ms>] [--watch <uart> <rx>]`

The whole chain as one verb: first stage → `LE32(second-stage length)` → the second
stage in `0x15000` slices, each a complete `AA`/`CC`/length/data/sum/`BB`
transaction, read straight out of flash.

Three design points that are load-bearing rather than stylistic:

* **The port is taken once and given back once.** Between packets the target floods
  `0xAA`; re-taking a flooding port was a reproducible hang. Never tear the driver
  down mid-chain.
* **The first wait is minutes, the later ones are seconds.** The operator still has
  to short the NAND pins and power the board (`--wait` default ~150 s), but once the
  chain first stage is running it re-arms the receiver immediately, so a header that
  takes 30 s or a slice that takes 10 s is a fault, not patience.
* **`--tail` keeps listening after the last slice**, silence or not, so that whatever
  the second stage prints after the jump lands in the report.

It reports one line per transaction, a byte histogram, the printable runs, the last
bytes in hex, and a verdict — with `OK?` as a distinct outcome from `OK`, because
**a missing `0xBB` receipt is normal**: the loaded code re-initialises the UART and
eats it.

## `--watch <uart> <rx>` — see the console while you drive the other port

The engine owns one hardware UART at a time, so during a chain the board's own
console is invisible. `--watch` installs a **receive-only** driver on a second UART
(TX left high-impedance, RX pulled up) and drains it *non-blocking* wherever the
transaction already reads, so the primary timing does not move by a tick.

Those bytes go to the capture ring **only** — never into the transaction's own
histogram or byte counts, which would corrupt the diagnosis — behind a marker, and
the tail of them is printed in the report. This is how you see the bootloader banner
and the kernel coming up in the same report that says the last slice landed.

## `uart uboot-stop [--wait <ms>] [--word <chars>] [--period <ms>]`

Stops a vendor U-Boot's autoboot from the console alone, for the class of
bootloader described in [`kb/chips/s5pv210`](../kb/chips/s5pv210/README.md) §8: one
that ignores `bootdelay`, samples the receiver exactly **once** for an
already-pending character, and then wants a short **word** rather than any key.

What the verb does, and why each part:

* From the moment it is armed it **rewrites the first letter of the word every
  `--period` ms** (default 15 — far more often than the flush-to-check gap, far less
  often than a byte time at 115200), so that a character is always pending.
* The instant the countdown line appears in the stream it **stops that letter and
  writes the rest of the word within a millisecond or two**. Order matters: the
  pending lead letter is the one the first read consumes, but a letter written
  *after* the countdown starts lands where the second letter belongs and kills the
  word. Nothing — no delay, no extra letter — goes between the detection and the
  rest.
* It **owns the port outright** for the run, with a fresh driver at receive-threshold
  1 and blocking reads of one tick, because the capture ring's ~20 ms fill latency
  would eat most of the window.
* **Arm it before switching the board on** — hence a `--wait` measured in minutes.

`--word` exists because the stop word is the *vendor's*, not U-Boot's; the default
is whatever your fleet uses and this repository does not name anybody's.

It reports a timeline in `+ms` (banner, countdown line, letters sent, prompt) and a
verdict: `OK`, `MISSED` (the kernel started instead — power-cycle and re-arm) or
`FAILED`.

## `uart send [--ms <wait>] [--no-cr] [--break <ms>] <text…>`

Write to the target and return everything that arrives in the next `wait` ms.

`--break <ms>` is the interesting one: it **holds TX in the space state** for that
long by driving the pin low — a slow `0x00` is *not* a break to a Samsung UART — and
then sends the following key. A Linux console arms **magic SysRq** on the break and
takes the next byte as the command: `h` help, `t` task list, `m` memory, `w` blocked
tasks. That is a diagnostic channel into a kernel whose userspace never gave you a
shell, with no shell involved at all — which is precisely the situation this whole
slice is about.

---

## If you are writing your own tap

The verbs above are a description of a working design, not an API you have to copy.
The parts worth copying, in order of how much time they save:

1. Own the UART for the run; do not multiplex it with a capture task.
2. Write the ROM frame contiguously; treat a missing receipt as success.
3. Retry a download call until it returns zero, from both ends.
4. Watch the other UART passively, and keep those bytes out of your statistics.
5. Keep images in flash and verify them by read-back, not by what you sent.

If you build one, register it in [`agents/`](../agents/README.md) and push back the
findings you make — that is the whole contract
([`CONTRIBUTING.md`](../CONTRIBUTING.md)).
