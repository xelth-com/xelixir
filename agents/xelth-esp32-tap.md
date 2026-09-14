---
name: xelth-esp32-tap
repo: https://github.com/xelth-com/xelixir
maintainer: xelth
base: none
targets: [chips/s5pv210, research/nand-retention]
kb_slices: [chips/s5pv210]
server: none
status: proven
---

# xelth-esp32-tap

Our own entry, and the template for yours.

> **Placeholder:** `repo:` points at an organisation that has not been named yet.
> The firmware is MIT-licensed and real; its public location is an open item in
> [`README.md`](README.md#open-items).

## What it does

An ESP32-class module that sits on a board's serial lines and does the things a PC
on the other end of a USB cable cannot:

* **speaks the S5PV210 mask ROM's UART-boot protocol**, including chain-loading a
  whole bootloader in slices through the ROM's own downloader — images fetched over
  HTTP into its own flash, verified by read-back, so a rescue attempt costs one verb
  and three minutes rather than 25 minutes of base64;
* **stops a vendor U-Boot's autoboot** on bootloaders that sample the receiver once
  for an already-pending character and then want a word rather than a key — which
  needs a reaction in a millisecond or two, not a USB round trip;
* **watches a second UART passively** while it drives the first, so the board's
  console is captured during a chain-load without moving the primary timing;
* **drives a break** on TX (the pin held low, not a slow `0x00`), which arms magic
  SysRq on a Linux console — a diagnostic channel into a kernel whose userspace
  never gave anyone a shell.

The verbs, with the reasoning behind each design decision, are in
[`firmware/esp32-tap.md`](../firmware/esp32-tap.md). It carries **no board payloads**:
the images a rescue uses are fetched or staged at run time, because the payload
belongs to whoever owns the board and the protocol belongs to everybody.

## Slices it uses

* [`kb/chips/s5pv210`](../kb/chips/s5pv210/README.md) — the boot order, the UART-boot
  protocol and its contiguous-frame rule, the chain-load design, the NAND/ECC
  bring-up to replay, and console autoboot-stop as a class of behaviour. Everything
  this firmware implements comes out of that slice, and the findings in it
  (`iesh8tso6j1n8z4cc245`, `es2bl4vrrdsb9b1u47lo`, `ka4th2tuclj4es4gbad2`,
  `1ls66vjcbwlpm9394x9o`, `m8r5wum41b4cto6n952r`, `ai9woq1s20fi2f829r3r`,
  `wjca6hkifegra44evx20`) were pushed back from working on it.
* [`kb/research/nand-retention`](../kb/research/nand-retention/README.md) — a target
  of the same work: this tap is how files get onto a board whose rootfs has rotted
  far enough that nothing else can reach it.

## Status: proven

Run on the bench: the full chain from a forced ROM fallback to Linux userland, 203 s
from power-on, and the console autoboot stop to a root shell on boards whose `init`
never reached a getty. See `kb_finding:ai9woq1s20fi2f829r3r` and
`kb_finding:m8r5wum41b4cto6n952r`.

## Server

`none`. Everything above works with the module, a serial line and a laptop. The
firmware can *also* be driven from our fleet server, but nothing in this repository
requires that, and the verbs are the same either way.
