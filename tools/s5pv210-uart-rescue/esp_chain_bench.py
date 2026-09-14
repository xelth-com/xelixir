#!/usr/bin/env python3
"""Bench driver: chain-boot an S5PV210 board through a UART tap, over the tap's console.

Slice: kb/chips/s5pv210 - read it first.
Tap verbs: firmware/esp32-tap.md

This is the *stepwise* form of the chain, kept because it is the one you can watch,
interrupt and retry a single slice of. It stages each payload into the tap over its
console as base64 and then tells the tap to run one ROM transaction with it:

    uart chan 2 <rx> <tx> 115200    move the tap engine onto the ROM's UART2 line
    uart irom reset / load ...      stage the chain first stage (8192 B)
    uart irom chain begin <len>     first stage + the length header;
                                    THIS is where the operator shorts the NAND
                                    data lines and powers the board on
    loop: load <= 0x15000 B slice ; uart irom chain part
    uart chan 1 <rx> <tx> 115200    tap back to the board's console

Note what it costs: base64 through a console is slow - a ~900 KB second stage takes
roughly 25 minutes this way, against about 3 seconds if the tap fetches the same
bytes over HTTP into its own flash and runs the whole chain as one verb. Use this
one while you are still learning what your board does; use the tap's own fetch +
`go --chain` once you know.

Usage:
    esp_chain_bench.py PORT chain_bl1.bin second_stage.bin [--bl2-len 0x...]
                       [--slice 0x15000] [--b64 160] [--log esp_chain.log]

Needs pyserial. The tap is reached on ITS console port (USB), not on the board.
"""
import argparse
import base64
import re
import sys
import time

import serial


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("port", help="the TAP's console port (USB), not the board's")
    ap.add_argument("bl1", help="chain first stage from build_chain_bl1.py")
    ap.add_argument("bl2", help="the whole bootloader image = the second stage")
    ap.add_argument("--bl2-len", type=lambda x: int(x, 0), default=0x80000,
                    help="how much of the second stage to send")
    ap.add_argument("--slice", type=lambda x: int(x, 0), default=0x15000,
                    help="slice size; must stay under the ROM limit 0x15400")
    ap.add_argument("--b64", type=int, default=160, help="base64 chars per console line")
    ap.add_argument("--rx", type=int, default=4, help="tap GPIO wired to the board's TX")
    ap.add_argument("--tx", type=int, default=5, help="tap GPIO wired to the board's RX")
    ap.add_argument("--console-rx", type=int, default=2,
                    help="tap GPIO on the board's own console, to return to at the end")
    ap.add_argument("--console-tx", type=int, default=3)
    ap.add_argument("--log", default="esp_chain.log")
    a = ap.parse_args()

    s = serial.Serial(a.port, 115200, timeout=0.1)
    log = open(a.log, "wb", buffering=0)

    def cmd(line, pat, t=6.0, tries=4):
        for _ in range(tries):
            s.write(line.encode() + b"\r\n")
            buf, end = b"", time.time() + t
            while time.time() < end:
                d = s.read(4096)
                if d:
                    buf += d
                    log.write(d)
                    m = re.search(pat, buf)
                    if m:
                        return m, buf
            log.write(b"[retry]\n")
        return None, buf

    def load(data):
        """Stage `data` into the tap, one acked base64 line at a time."""
        b64 = base64.b64encode(data).decode()
        staged = 0
        for i in range(0, len(b64), a.b64):
            m, _ = cmd("uart irom load " + b64[i:i + a.b64],
                       rb"\+(\d+) byte\(s\), (\d+) staged|exit \d")
            if not m or not m.group(2):
                sys.exit("staging failed at base64 offset %d" % i)
            staged = int(m.group(2))
        return staged

    # Opening the console port resets most taps; let it come up, then leave whatever
    # pass-through mode it booted into.
    time.sleep(14)
    s.write(b"~~~")
    time.sleep(1.5)
    s.write(b"\r\n")
    time.sleep(1.5)
    s.read(65536)

    m, out = cmd("uart chan 2 %d %d 115200" % (a.rx, a.tx),
                 rb"uart chan|chan:|GPIO%d|error|refused" % a.rx, 8)
    print("chan:", (out[-160:] if out else b"").decode("latin1").strip())

    cmd("uart irom reset", rb"dropped|empty")
    bl1 = open(a.bl1, "rb").read()[:8192]
    print("staged first stage: %d B, sum16 0x%04x" % (load(bl1), sum(bl1) & 0xFFFF))

    bl2 = open(a.bl2, "rb").read()[:a.bl2_len]
    print("second stage: %d B in %d slice(s) of 0x%x"
          % (len(bl2), (len(bl2) + a.slice - 1) // a.slice, a.slice))

    print(">>> SHORT THE NAND DATA LINES AND POWER THE BOARD ON NOW "
          "(waiting up to 180 s for the ROM's 0xAA) <<<")
    m, out = cmd("uart irom chain begin %d --wait 180000" % len(bl2),
                 rb"OK \(3 x 0xBB\)|FAILED|exit \d", 200, tries=1)
    print(out[-400:].decode("latin1"))
    if not m or b"FAILED" in out:
        sys.exit("the first stage was not accepted - see the log")

    off, n = 0, 0
    while off < len(bl2):
        part = bl2[off:off + a.slice]
        cmd("uart irom reset", rb"dropped|empty")
        load(part)
        m, out = cmd("uart irom chain part --wait 8000",
                     rb"OK \(3 x 0xBB\)|FAILED|exit \d", 20, tries=1)
        n += 1
        good = bool(m) and b"OK" in m.group(0)
        print("part %d @0x%x %d B: %s" % (n, off, len(part), "OK" if good else "FAIL"))
        if not good:
            print(out[-400:].decode("latin1"))
            # The chain stays armed AND the slice stays staged on the tap, so the
            # retry is one verb rather than another 86 KB of base64.
            sys.exit("slice %d failed - re-run `uart irom chain part` on the tap" % n)
        off += len(part)

    print("second stage delivered; moving the tap back to the board's console")
    cmd("uart chan 1 %d %d 115200" % (a.console_rx, a.console_tx),
        rb"uart chan|chan:|GPIO%d|error" % a.console_rx, 8)
    time.sleep(3)
    s.write(b"uart status\r\n")
    time.sleep(2)
    print(s.read(4096).decode("latin1"))


if __name__ == "__main__":
    main()
