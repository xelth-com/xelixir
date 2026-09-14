#!/usr/bin/env python3
"""S5PV210 / S5PC110 mask-ROM UART-boot loader (host side).

Slice: kb/chips/s5pv210 - read it first.

When the SoC's mask ROM cannot boot from its primary device (short two NAND data
lines to each other on a powered-OFF board, then apply power) and finds no card on
SD/MMC channel 2, it falls back to UART boot on UART2. This sends it a payload.

Protocol (115200 8N1, see kb_finding:iesh8tso6j1n8z4cc245):
    ROM  -> 0xAA (repeated)     host -> 0xAA
    ROM  -> 0xCC (repeated)     host -> 0xCC
    host -> LE32(len + 6) + len raw bytes + LE16(16-bit sum of those bytes)
    ROM  -> 0xBB 0xBB 0xBB      then jumps to 0xD0020000
    maximum len 0x15400 (85 KB); errors arrive as plain text on the same port.

TWO THINGS THAT WILL COST YOU AN EVENING IF YOU DO NOT KNOW THEM
(kb_finding:es2bl4vrrdsb9b1u47lo):

 1. The frame after the host's 0xCC must be written CONTIGUOUSLY. The ROM's byte
    read is a ~0.6 ms FIFO poll, not a timed wait, and it reads the four length
    bytes with that poll immediately after matching your 0xCC. Waiting for the
    ROM's 0xCC stream to go quiet first - the intuitive approach - yields 0xFF x4,
    a length of 0xFFFFFFFF and "Uart Data Length Over" about 3 s later.
 2. The 0xBB x3 receipt is usually LOST, because the code you just loaded
    re-initialises the UART at once. No receipt AND no error text = success.

WHICH PORT: UART2, which on a finished product is often not the connector marked
for a PC and may be at RS-232 levels behind a transceiver on another board. See the
slice, section 2.

WHAT TO SEND: your own board's first stage - either its stock one (which will then
try to copy a second stage from storage) or, to pull a whole bootloader over the
same wire, a chain first stage built by build_chain_bl1.py from your own image.

Usage:
    irom_uart_boot.py PORT IMAGE [--bytes 8192] [--strip-header] [--wait 600]

Start it BEFORE powering the board on: it waits for the first 0xAA.
Needs pyserial.
"""
import argparse
import struct
import sys
import time

import serial

IROM_MAX = 0x15400


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("port", help="serial port wired to the SoC's UART2")
    ap.add_argument("image", help="payload: a first-stage image of YOUR board")
    ap.add_argument("--bytes", type=int, default=8192,
                    help="how much of the image to send (default 8192 = a BL1)")
    ap.add_argument("--strip-header", action="store_true",
                    help="drop a 16-byte boot header; UART boot expects none, but "
                         "leaving one in place is harmless for an image linked with it")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--wait", type=float, default=600,
                    help="seconds to wait for the first 0xAA (default 600)")
    a = ap.parse_args()

    data = open(a.image, "rb").read()[:a.bytes]
    if a.strip_header:
        data = data[16:]
    if len(data) > IROM_MAX:
        sys.exit("payload %d B is over the ROM limit 0x%X" % (len(data), IROM_MAX))

    s = serial.Serial(a.port, a.baud, timeout=0.2)
    print("[%s] waiting for the ROM's 0xAA - power the board on now, with the "
          "NAND data lines shorted" % a.port)

    t0, log = time.time(), b""
    while time.time() - t0 < a.wait:
        b = s.read(64)
        if not b:
            continue
        log += b
        if b"\xaa" in b:
            print("got 0xAA -> answering")
            s.write(b"\xaa")
            break
        sys.stdout.write(b.decode("latin1"))
        sys.stdout.flush()
    else:
        print("timeout: no 0xAA. Tail: %r" % log[-200:])
        return 2

    ok = False
    t1 = time.time()
    while time.time() - t1 < 5:
        b = s.read(64)
        if b"\xcc" in b:
            ok = True
            break
        if b"\xaa" in b:
            s.write(b"\xaa")
        if b:
            sys.stdout.write(b.decode("latin1"))
    if not ok:
        print("no 0xCC after 0xAA")
        return 3

    # ONE contiguous write: 0xCC + length + payload + checksum. See the docstring.
    n = len(data)
    cks = sum(data) & 0xFFFF
    frame = b"\xcc" + struct.pack("<I", n + 6) + data + struct.pack("<H", cks)
    s.write(frame)
    s.flush()
    print("got 0xCC -> answered; sent %d bytes, sum16 0x%04x" % (n, cks))

    got, t2 = b"", time.time()
    while time.time() - t2 < 5:
        b = s.read(64)
        if b:
            got += b
            sys.stdout.write(b.decode("latin1"))
            sys.stdout.flush()
            if got.count(b"\xbb") >= 3:
                print("\npayload accepted and started (0xBB x3).")
                break
    else:
        if any(w in got for w in (b"Error", b"error")):
            print("\nROM reported an error: %r" % got[-120:])
            return 4
        print("\nno 0xBB x3 - which is NORMAL: the loaded code re-initialises the "
              "UART and the receipt is lost. No error text means it ran.")

    print("--- the port now shows whatever the loaded code prints on UART2 "
          "(the Linux console is a different UART) ---")
    t3 = time.time()
    while time.time() - t3 < 20:
        b = s.read(256)
        if b:
            sys.stdout.write(b.decode("latin1"))
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
