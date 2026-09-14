#!/usr/bin/env python3
"""Build a UART-chain first stage from YOUR OWN S5PV210 bootloader image.

Slice: kb/chips/s5pv210 - read it first.

    python build_chain_bl1.py --src my-u-boot.bin --dest-base <TEXT_BASE>
                              --out chain.bin [--disasm]

The first 8 KB of a Samsung-lineage bootloader image is the mask-ROM-loadable first
stage (16-byte boot header + code). This script takes YOUR image, redirects that
first stage's storage-copy dispatcher into a region of the image that becomes
unreachable the moment the dispatcher is redirected, and writes there a routine
that instead:

  1. calls the ROM's own UART downloader (0xD0008640) for a 4-byte packet carrying
     LE32(total second-stage length) into internal SRAM, RETRYING UNTIL IT RETURNS 0;
  2. loops, calling it again for each slice straight into DDR, retrying each slice
     the same way, until `total` bytes have landed;
  3. replays the NAND-controller and ECC bring-up that the ROM performs only in its
     successful-NAND-boot branch - the branch that is skipped when you force the
     UART fallback, and whose absence makes a U-Boot 1.3.4 of this family hang
     silently on the first ECC-protected page read;
  4. stamps the boot-device register INFORM3 = 2 (NAND) and falls into the first
     stage's own stack-set / bss-clear / jump-to-second-stage block.

NOTHING OF YOUR IMAGE IS EMBEDDED IN THIS SCRIPT, and no image is shipped with it.
Everything image-specific is LOCATED BY DISASSEMBLY of the source you pass, which is
also why the same script works across generations - see "How it finds things" below,
and `--disasm` to see exactly what it produced.

Output: `--out`, an 8192-byte image to hand to the ROM (irom_uart_boot.py, or a tap
that speaks the same protocol). Feed the *whole* bootloader image as the second
stage. Both must come from the board's OWN software generation
(kb_finding:wjca6hkifegra44evx20).

Needs `capstone`.


HOW IT FINDS THINGS (no offsets are hard-coded; all of this is pattern matching)
================================================================================

* **The copy dispatcher.** Early in every first stage of this lineage there is a
  relocate check of the shape

      ldr r0,[pc,#x] ; bic r1,pc,r0 ; ldr r2,[pc,#y] ; bic r2,r2,r0
      cmp r1,r2      ; beq <stack/bss block>

  i.e. "mask the low bits off the program counter, compare against the masked link
  address, and if we are already where we were linked, skip the copy". The
  instruction after that `beq` is the call that copies the second stage in from
  storage - that is the dispatcher we redirect - and the `beq` target is the
  stack/bss/jump block we branch to when our download loop is done. Three of those
  five words are fixed encodings (`bic r1,pc,r0`, `bic r2,r2,r0`, `cmp r1,r2`),
  which makes the pattern specific enough to scan for blindly over the first KB.

* **The NFCONF literal.** The first stage's own `lowlevel_init` programs the NAND
  controller's timing and geometry with a read-modify-write:

      ldr rN,=0xB0E00000 ; ldr r1,[rN] ; ldr r2,=mask ; bic r1,r1,r2
      ldr r2,=value      ; orr r1,r1,r2 ; str r1,[rN]

  The script finds it by looking for a pc-relative load of the literal
  `0xB0E00000` followed by that exact instruction shape, and reads `value` out of
  the literal pool. **This value differs between software generations of the same
  board** - in Samsung-lineage U-Boot 1.3.4 first stages we have seen, one
  generation programs timings 3 with PageSize 0 and the next programs timings 7
  with PageSize 1 - and hard-coding either one silently re-times the other board's
  NAND, so that the bootloader region reads fine and everything past it comes back
  ECC-uncorrectable. Our routine therefore replays `0x01800000 | <this image's own
  value>`: the ECC-select bits a NAND boot leaves, OR'd onto the board's own
  timings, and nothing else.

* **The dead region.** Redirecting the dispatcher makes a cluster of code
  unreachable: the copy helper it called, and the storage reader that helper called.
  The script walks the image twice from its entry point - once as-is, once with the
  dispatcher already redirected - and subtracts the two reachable sets. Whatever is
  reachable ONLY through the dispatcher is dead, and the largest contiguous run of
  it that is big enough for the routine plus its literal pool is where the routine
  goes. If no such run exists the script refuses rather than overwriting something
  live. (The walk is a small recursive-descent disassembly: follow `b`/`bl` targets,
  stop at an unconditional branch, an `ldr pc`/`mov pc`, or an `ldm` that pops `pc`.)

So the only things baked into this script are facts about the **SoC** - the ROM's
download entry point, its per-packet limit, the NAND and ECC register addresses and
the values the ROM writes. Those are in kb/chips/s5pv210/.
"""
import argparse
import struct

from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM

# ---- SoC facts (kb/chips/s5pv210/findings.yaml, evidence/nand-ecc-registers.md) --
UART_DL   = 0xD0008640     # ROM uart_download(r0=port, r1=0x23, r2=dest, r3=maxlen)
MAXLEN    = 0x00015400     # ROM per-packet maximum
SCRATCH   = 0xD0030000     # free internal SRAM: the 4-byte header packet lands here
LENPTR    = 0xD00374CC     # where the ROM stores the length it last received
INF_REG   = 0xE010F000     # INF_REG base; INFORM3 (+0xC) = boot-device tag
NF_BASE   = 0xB0E00000     # NAND controller: NFCONF +00, NFCONT +04, NFSTAT +28
NF_ECCSEL = 0x01800000     # NFCONF[24:23] as a NAND boot leaves them
ECC_BASE  = 0xB0E20000     # ECC engine: NFECCCONF +00, NFECCCONT +20, NFECCSTAT +30
ECC_MASK  = 0xFC00FFF0     # ~0x03FF000F
ECC_VAL   = 0x01FF0003     # MsgLength 512 B (bytes-1 at [25:16]), ECC type 3 = 8-bit

# ---- where the received second stage is written --------------------------------
# The link address of the second stage (--dest-base) is a property of YOUR image,
# not of the SoC, so there is deliberately no default: pass the TEXT_BASE the image
# was linked at. It is your build's TEXT_BASE; on an image you did not build, the
# words the first stage stores as _armboot_start / _bss_start / _end give it away
# (file offset = address - TEXT_BASE must hold), and so does a disassembly whose
# pc-relative literals only resolve sensibly at one base.

POOL_WORDS = 11
NEED = 0xD4 + 4 * POOL_WORDS    # bytes the injected routine occupies


# ------------------------------------------------------------------ analysis
def u32(d, off):
    return struct.unpack_from("<I", d, off)[0]


def pcrel(d, at):
    """If d[at] is `ldr rX,[pc,#imm]`, return the literal's address, else None."""
    w = u32(d, at)
    if (w & 0x0F7F0000) != 0x059F0000 and (w & 0x0F7F0000) != 0x051F0000:
        return None
    imm = w & 0xFFF
    return at + 8 + (imm if (w >> 23) & 1 else -imm)


def find_dispatch(d):
    """Locate the relocate check (see the module docstring).

    Returns (dispatcher address = the instruction after the beq,
             stack/bss/jump block address = the beq target)."""
    for at in range(0x10, 0x400, 4):
        if u32(d, at + 0x04) != 0xE1CF1000:      # bic r1, pc, r0
            continue
        if u32(d, at + 0x0C) != 0xE1C22000:      # bic r2, r2, r0
            continue
        if u32(d, at + 0x10) != 0xE1510002:      # cmp r1, r2
            continue
        beq = u32(d, at + 0x14)
        if (beq & 0xFF000000) != 0x0A000000:     # beq #imm
            continue
        off = beq & 0xFFFFFF
        if off & 0x800000:
            off -= 0x1000000
        return at + 0x18, (at + 0x14) + 8 + off * 4
    raise SystemExit("could not locate the second-stage copy dispatcher - is this "
                     "image really a first stage of this lineage?")


def find_nfconf(d):
    """Locate the first stage's own NFCONF programming; return (value, address)."""
    for at in range(0x10, 0x2000 - 0x1C, 4):
        lit = pcrel(d, at)
        if lit is None or lit + 4 > 0x2000 or u32(d, lit) != NF_BASE:
            continue
        rd = (u32(d, at) >> 12) & 0xF
        if u32(d, at + 0x04) != 0xE5901000 | (rd << 16):        # ldr r1,[rd]
            continue
        if u32(d, at + 0x0C) != 0xE1C11002:                     # bic r1,r1,r2
            continue
        if u32(d, at + 0x14) != 0xE1811002:                     # orr r1,r1,r2
            continue
        if u32(d, at + 0x18) != 0xE5801000 | (rd << 16):        # str r1,[rd]
            continue
        val = pcrel(d, at + 0x10)
        if val is not None and val + 4 <= 0x2000:
            return u32(d, val), at
    raise SystemExit("could not locate the first stage's NFCONF programming - "
                     "refusing to guess a NAND timing value (see the docstring)")


def _walk(d, entry):
    """Recursive-descent reachability walk from `entry`.

    Returns (instruction addresses, pc-relative literal addresses) reached."""
    md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    code, lits, stack = set(), set(), [entry]
    while stack:
        pc = stack.pop()
        while 0x10 <= pc < 0x2000 and pc not in code:
            code.add(pc)
            lit = pcrel(d, pc)
            if lit is not None and 0 <= lit < 0x2000:
                lits.add(lit)
            ins = next(md.disasm(bytes(d[pc:pc + 4]), pc), None)
            if ins is None:
                break
            m, o = ins.mnemonic, ins.op_str
            if m.startswith("bl") and m[:3] != "bic" and o.startswith("#"):
                stack.append(int(o[1:], 0))
            elif m[0] == "b" and m[:3] != "bic" and m[:2] != "bl" and o.startswith("#"):
                t = int(o[1:], 0)
                if m == "b":
                    pc = t
                    continue
                stack.append(t)
            if m == "b":
                break
            if m in ("ldr", "mov") and o.startswith("pc,"):
                break
            if m == "ldm" and "pc" in o:
                break
            pc += 4
    return code, lits


def _bl_targets(d, at, span):
    """`bl #imm` targets within d[at:at+span]."""
    out = []
    for o in range(at, at + span, 4):
        w = u32(d, o)
        if (w & 0xFF000000) != 0xEB000000:
            continue
        off = w & 0xFFFFFF
        if off & 0x800000:
            off -= 0x1000000
        t = o + 8 + off * 4
        if 0x10 <= t < 0x2000:
            out.append(t)
    return out


def find_dead(d, dispatch, bss, need):
    """Find the storage-copy cluster that dies when the dispatcher is redirected.

    Returns (start, end) of the largest usable contiguous dead run."""
    entry = 0x10 + 8 + ((u32(d, 0x10) & 0xFFFFFF) << 2)      # `b <entry>` at 0x10
    live_code, _ = _walk(d, entry)

    patched = bytearray(d)
    struct.pack_into("<I", patched, dispatch,
                     0xEA000000 | (((bss - (dispatch + 8)) >> 2) & 0xFFFFFF))
    no_code, _ = _walk(patched, entry)
    dead = live_code - no_code            # reachable ONLY through the dispatcher

    def run_of(x):
        i = j = x
        while i - 4 in dead:
            i -= 4
        while j in dead:
            j += 4
        return i, j

    best = None
    for helper in _bl_targets(d, dispatch, bss - dispatch):
        for cand in _bl_targets(d, helper, 0x40):
            if cand not in dead:
                continue
            i, j = run_of(cand)
            if j - cand < need:
                continue
            if best is None or j - cand > best[1] - best[0]:
                best = (cand, j)
    if best is None:
        raise SystemExit("no dead second-stage-copy cluster large enough for the "
                         "routine (%d bytes needed) - refusing to overwrite live "
                         "code" % need)
    return best


# ------------------------------------------------------------------ codegen
def ldr_pc(rd, at, target):
    imm = target - (at + 8)
    assert 0 <= imm < 4096 and imm % 4 == 0, (hex(at), hex(target), imm)
    return 0xE59F0000 | (rd << 12) | imm


def bl_or_b(at, target, link=False):
    imm = target - (at + 8)
    assert imm % 4 == 0
    off = (imm // 4) & 0xFFFFFF
    return (0xEB000000 if link else 0xEA000000) | off


def build(src, out, dest_base, verbose=True):
    d = bytearray(open(src, "rb").read()[:0x2000])   # the first stage = first 8 KB
    if len(d) < 0x2000:
        raise SystemExit("source is shorter than 8 KB - that is not a first stage")

    DISPATCH, BSS = find_dispatch(d)
    nfconf_lit, nfconf_at = find_nfconf(d)
    NF_CONF = NF_ECCSEL | nfconf_lit
    S, dead_end = find_dead(d, DISPATCH, BSS, NEED)

    if verbose:
        print("src            %s" % src)
        print("dispatcher     0x%x  -> stack/bss/jump block 0x%x" % (DISPATCH, BSS))
        print("NFCONF (image) 0x%08x (lowlevel_init @0x%x) -> replaying 0x%08x"
              % (nfconf_lit, nfconf_at, NF_CONF))
        print("dead region    0x%x..0x%x (%d bytes, need %d)"
              % (S, dead_end, dead_end - S, NEED))

    def w32(off, val):
        d[off:off + 4] = struct.pack("<I", val)

    code_len = 0xD4            # bytes of code before the literal pool
    P = S + code_len
    (P_dest, P_fn, P_maxlen, P_scratch, P_lenptr, P_infreg,
     P_nfbase, P_nfconf, P_eccbase, P_eccmask, P_eccval) = [P + 4 * i for i in range(11)]

    instrs = []
    a = S

    def emit(word):
        nonlocal a
        instrs.append((a, word))
        a += 4

    # --- header transaction, retried until the ROM downloader returns 0 ----------
    # Return codes: 0 = ok, 10 = negotiation timed out (~1.5 s), 15 = length over,
    # 20 = checksum. The host is routinely not ready on the first try, so retry the
    # SAME call rather than reading a garbage total and jumping into empty DDR.
    HDR = a
    emit(ldr_pc(2, a, P_scratch))     # ldr r2,=SCRATCH
    emit(0xE3A00002)                  # mov r0,#2          ; port = UART2
    emit(0xE3A01023)                  # mov r1,#0x23       ; mode
    emit(ldr_pc(3, a, P_maxlen))      # ldr r3,=MAXLEN
    emit(ldr_pc(12, a, P_fn))         # ldr ip,=UART_DL
    emit(0xE12FFF3C)                  # blx ip
    emit(0xE3500000)                  # cmp r0,#0
    emit((bl_or_b(a, HDR) & 0x00FFFFFF) | 0x1A000000)   # bne hdr
    emit(ldr_pc(0, a, P_scratch))     # ldr r0,=SCRATCH
    emit(0xE5900000)                  # ldr r0,[r0]        ; r0 = total length
    emit(ldr_pc(4, a, P_dest))        # ldr r4,=DEST_BASE
    emit(0xE0845000)                  # add r5,r4,r0       ; r5 = end pointer
    LOOP = a
    emit(0xE1540005)                  # cmp r4,r5
    emit(0)                           # placeholder: bcs done
    BCS_IDX = len(instrs) - 1
    SLICE = a
    emit(0xE3A00002)                  # mov r0,#2
    emit(0xE3A01023)                  # mov r1,#0x23
    emit(0xE1A02004)                  # mov r2,r4          ; same dest on a retry
    emit(ldr_pc(3, a, P_maxlen))      # ldr r3,=MAXLEN
    emit(ldr_pc(12, a, P_fn))         # ldr ip,=UART_DL
    emit(0xE12FFF3C)                  # blx ip
    emit(0xE3500000)                  # cmp r0,#0
    emit((bl_or_b(a, SLICE) & 0x00FFFFFF) | 0x1A000000)  # bne slice ; r4 not advanced
    emit(ldr_pc(0, a, P_lenptr))      # ldr r0,=LENPTR
    emit(0xE5900000)                  # ldr r0,[r0]        ; r0 = received length
    emit(0xE0844000)                  # add r4,r4,r0
    emit(bl_or_b(a, LOOP))            # b loop
    DONE = a
    instrs[BCS_IDX] = (instrs[BCS_IDX][0],
                       (bl_or_b(instrs[BCS_IDX][0], DONE) & 0x00FFFFFF) | 0x2A000000)

    # --- replay the ROM's NAND-controller + ECC bring-up -------------------------
    # On a UART fallback the ROM never reaches its post-ready/busy-probe setup, so
    # the whole ECC block is at reset defaults and NFCONF[24:23] == 0. U-Boot 1.3.4
    # of this family programs almost nothing in board_nand_init, arms an ECC decode
    # on the first environment page read and then spins forever on NFECCSTAT with no
    # timeout - the silent hang right after "NAND: <size> MB".
    # NF_CONF keeps the IMAGE'S OWN timing/geometry nibbles and only adds the
    # ECC-select bits; an absolute constant here would silently re-time the NAND of
    # a board from another generation. See the docstring and
    # kb_finding:wjca6hkifegra44evx20.
    emit(ldr_pc(0, a, P_nfbase))      # ldr r0,=NF_BASE
    emit(ldr_pc(1, a, P_nfconf))      # ldr r1,=NF_CONF
    emit(0xE5801000)                  # str r1,[r0]        ; NFCONF
    emit(0xE3A010C5)                  # mov r1,#0xc5
    emit(0xE5801004)                  # str r1,[r0,#4]     ; NFCONT
    emit(0xE3A010F0)                  # mov r1,#0xf0
    emit(0xE5801028)                  # str r1,[r0,#0x28]  ; NFSTAT, W1C all detects
    emit(ldr_pc(2, a, P_eccbase))     # ldr r2,=ECC_BASE
    emit(0xE5923000)                  # ldr r3,[r2]        ; NFECCCONF
    emit(ldr_pc(1, a, P_eccmask))     # ldr r1,=~0x03FF000F
    emit(0xE0033001)                  # and r3,r3,r1
    emit(ldr_pc(1, a, P_eccval))      # ldr r1,=0x01FF0003
    emit(0xE1833001)                  # orr r3,r3,r1
    emit(0xE5823000)                  # str r3,[r2]        ; 512 B message, 8-bit ECC
    emit(0xE5923020)                  # ldr r3,[r2,#0x20]  ; NFECCCONT
    emit(0xE3C33403)                  # bic r3,r3,#0x03000000
    emit(0xE3C33801)                  # bic r3,r3,#0x00010000
    emit(0xE5823020)                  # str r3,[r2,#0x20]
    emit(0xE5923030)                  # ldr r3,[r2,#0x30]  ; NFECCSTAT
    emit(0xE3833403)                  # orr r3,r3,#0x03000000
    emit(0xE5823030)                  # str r3,[r2,#0x30]  ; clear sticky done bits
    emit(0xE3A01010)                  # mov r1,#0x10
    emit(0xE5801028)                  # str r1,[r0,#0x28]  ; clear RnB trans. detect

    # --- tell the second stage it booted from NAND -------------------------------
    # Its environment-location dispatch reads INFORM3: 2 = NAND. After a UART boot
    # that register holds something else, and the dispatch falls somewhere you do
    # not want it to.
    emit(ldr_pc(0, a, P_infreg))      # ldr r0,=INF_REG
    emit(0xE3A01002)                  # mov r1,#2
    emit(0xE580100C)                  # str r1,[r0,#0xc]   ; INFORM3 = 2 (NAND)
    emit((bl_or_b(a, BSS) & 0x00FFFFFF) | 0xEA000000)   # b <stack/bss/jump block>

    last_addr = instrs[-1][0]
    assert last_addr + 4 == P, (hex(last_addr + 4), hex(P), "code_len mismatch")

    for at, word in instrs:
        w32(at, word)

    w32(P_dest,    dest_base)
    w32(P_fn,      UART_DL)
    w32(P_maxlen,  MAXLEN)
    w32(P_scratch, SCRATCH)
    w32(P_lenptr,  LENPTR)
    w32(P_infreg,  INF_REG)
    w32(P_nfbase,  NF_BASE)
    w32(P_nfconf,  NF_CONF)
    w32(P_eccbase, ECC_BASE)
    w32(P_eccmask, ECC_MASK)
    w32(P_eccval,  ECC_VAL)

    # Code and pool must stay inside the cluster that is dead once the dispatcher is
    # redirected. If this fires, the image's dead run is too small - do not "fix" it
    # by moving the routine somewhere live.
    assert P + 4 * POOL_WORDS <= dead_end, hex(P + 4 * POOL_WORDS)

    # --- redirect the copy dispatch at our routine -------------------------------
    w32(DISPATCH, bl_or_b(DISPATCH, S))

    # --- recompute the boot header's checksum ------------------------------------
    # word2 of the 16-byte header is the sum of every byte after it.
    chk = sum(d[0x10:0x2000]) & 0xFFFFFFFF
    w32(0x8, chk)                     # word0 (download size) is left alone

    open(out, "wb").write(d)
    sum16 = sum(d) & 0xFFFF
    if verbose:
        print("wrote %s, %d bytes" % (out, len(d)))
        print("header checksum word2 = 0x%x" % chk)
        print("routine 0x%x..0x%x, literal pool 0x%x, loop exit 0x%x" % (S, P, P, DONE))
        print("sum16 = %04x   (the 16-bit sum the ROM frame carries)" % sum16)
    return d, S, P, sum16


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True,
                    help="YOUR bootloader image; its first 8 KB is the first stage")
    ap.add_argument("--out", default="chain_bl1.bin", help="output chain first stage")
    ap.add_argument("--dest-base", type=lambda x: int(x, 0), required=True,
                    help="link address (TEXT_BASE) of the second stage in DDR - a "
                         "property of YOUR image; there is no sensible default")
    ap.add_argument("--disasm", action="store_true",
                    help="disassemble the injected routine - do read this once")
    args = ap.parse_args()

    d, S, P, sum16 = build(args.src, args.out, args.dest_base)

    if args.disasm:
        md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        print("\n--- injected routine 0x%x..0x%x ---" % (S, P))
        for ins in md.disasm(bytes(d[S:P]), S):
            lit = pcrel(d, ins.address)
            note = ""
            if lit is not None and lit + 4 <= 0x2000:
                note = "   ; =0x%08x" % u32(d, lit)
            print("  %04x  %-8s %-28s%s" % (ins.address, ins.mnemonic, ins.op_str, note))
        print("--- literal pool ---")
        for i in range(POOL_WORDS):
            print("  %04x  .word 0x%08x" % (P + 4 * i, u32(d, P + 4 * i)))


if __name__ == "__main__":
    main()
