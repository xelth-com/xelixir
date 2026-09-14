# S5PV210 / S5PC110 NAND + ECC register tables

SoC facts. Nothing here is specific to any product; the values are what the mask
ROM and the driver family write, and the field definitions are cross-checked
against Samsung's own `s5p_nand_mlc.c` and `regs-nand.h` as published in
third-party kernel ports (see *Sources*).

## Blocks

| block | base |
|---|---|
| NAND controller | `0xB0E00000` |
| 8-bit/16-bit ECC engine | `0xB0E20000` |

## NAND controller (`0xB0E00000`)

| offset | register | notes |
|---|---|---|
| `+0x00` | `NFCONF` | timings (TACLS/TWRPH0/TWRPH1), AddrCycle, PageSize, MLCFlash, and **[24:23] = ECC-type select** |
| `+0x04` | `NFCONT` | bit 0 MODE · bit 1 nCE0 (**active low: 0 = selected**) · bit 5 InitMECC · bit 7 MECC lock · bit 16 · bit 18 · bits [11:9] RnB/IRQ enables · bit 18 encode direction (4-bit path) |
| `+0x08` | `NFCMMD` | command register; `0xFF` = NAND RESET, `0x90` = READID |
| `+0x0C` | `NFADDR` | address register |
| `+0x10` | `NFDATA` | data port |
| `+0x14` / `+0x18` | `NFMECCDATA0` / `NFMECCDATA1` | read-back ECC fed to the corrector |
| `+0x28` | `NFSTAT` | bit 0 RnB level · **bit 4 RnB transition detect, write-1-to-clear** · illegal-access bits, also W1C |
| `+0x2C` / `+0x30` | `NFESTAT0` / `NFESTAT1` | 1-bit/4-bit error status; `NFESTAT0 & 3`: 0 = clean, 1 = one bit corrected, **2 or 3 = uncorrectable** |
| `+0x34` / `+0x38` | `NFMECC0` / `NFMECC1` | computed main-area ECC |
| `+0x40` | `NFMLCBITPT` | corrected bit pointer |

`NFCONF[24:23]` is a two-bit field: **bit 23 = 8-bit ECC, bit 24 = the MLC/4-bit
select**. A NAND boot leaves `0b11`; after a UART fallback it is `0b00`. A running
1-bit driver's `hwctl` clears bit 24 itself and never touches bit 23, which is why
`0x00807776` is the normal readback on a live SLC system.

## ECC engine (`0xB0E20000`)

| offset | register | notes |
|---|---|---|
| `+0x00` | `NFECCCONF` | **MsgLength = bytes − 1 at bits [25:16]** (512 B → `0x01FF0000`); ECCType at [3:0]: 3 = 8-bit, 5 = 16-bit |
| `+0x20` | `NFECCCONT` | bit 2 INITMECC · bit 16 ENCODE direction · bit 24/25 |
| `+0x30` | `NFECCSTAT` | **bit 24 decode done · bit 25 encode done · bit 31 busy**; the done bits are sticky and write-1-to-clear |
| `+0x40` | `NFECCSECSTAT` | per-sector status |
| `+0x90`.. | `NFECCPRGECC0..` | generated parity |
| `+0xC0`.. | `NFECCERL0..` | error locations |
| `+0xF0`.. | `NFECCERP0..` | error patterns |

## What the mask ROM writes

Stage 1 — always, before it knows whether NAND is alive:

```
NFCONF = 0x7776            absolute store: the upper bits go to 0
NFCONT = 7
  ready/busy probe:  NFCONT &= ~2        (assert nCE0)
                     NFSTAT  = 0x10
                     NFCMMD  = 0xFF      (chip RESET)
                     poll NFSTAT[4], bounded
                     ok -> NFCONT |= 2   ·  fail -> "Nand RnB Detect Error"
```

Stage 2 — **only if the probe succeeded**, i.e. the branch a data-line short skips:

```
NFCONF    = (x & ~0x1800000 & ~0xFF00 & ~0xF0) | 0x1800000 | 0x7700 | 0x70
NFCONT   &= ~0x40000 ;  |= 0x41 ;  &= ~0xE00
NFSTAT   |= 0xF0
NFECCCONF = (x & ~0x3FF0000 & ~0xF) | 0x01FF0000 | 3      ; 512 B, 8-bit
            (if the strap selects 16-bit ECC: ECCType 5 instead of 3)
NFECCCONT &= ~0x3000000 ;  &= ~0x10000
NFECCSTAT |= 0x3000000                                    ; W1C the done bits
NFCONT   |= 0x80 ;  &= ~2
NFSTAT    = 0x10
```

Geometry comes from a strap field: 2 KB/5 cycles/8-bit · 4 KB/5/8-bit ·
4 KB/5/**16**-bit · 2 KB/4/8-bit, with 13 or 26 parity bytes per 512 B accordingly.

Per 512-byte chunk, reading the boot region, the ROM does:
`NFCONT &= ~0x80; NFCONT |= 0x30` (InitMECC | InitSECC) · `NFECCCONT |= 4`
(INITMECC) · 512 data bytes · random-output to `pagesize + 12 + 13·k` · **13 parity
bytes** · `NFCONT |= 0x80` · wait `NFECCSTAT[24]` · W1C it · wait for
`!NFECCSTAT[31]` · correct. On exit it releases nCE0 (`NFCONT |= 2`).

**So the ROM's OOB layout for the boot region is 13 bytes of 8-bit ECC at offset
`12 + 13·k`** — disjoint from the 1-bit driver layout below. Do not read one with
the other.

## What a 1-bit SLC driver of this family uses instead

Selected by NAND ID at `board_nand_init`: large-page SLC → `nand_ecc_type = 1`,
`ecc.size = 512`, `ecc.bytes = 4`, **`eccpos = OOB[40..55]`** of a 64-byte OOB
(16 ECC bytes = 4 per 512-byte chunk). Large-page MLC → type 2 = 4-bit, 8 bytes.

Per page it does: random-output to `mtd->writesize`, `hwctl(READ)`, read
`oobsize − 4`, `calculate`, read the last 4 spare bytes, then for each of the four
512-byte steps: random-output, `hwctl(READ)`, read 512, `calculate`,
`correct(page, oob + eccpos[0] + 4·i)`.

`hwctl` is where the ECC-type bit moves:

```
cur_ecc_mode = mode
NFCONF = (ecc_type == 1) ? NFCONF & ~(1<<24) : NFCONF | (1<<24)
NFCONT |= 0x20   (InitMECC)
NFCONT &= ~0x80  (unlock MECC)
if (ecc_type == 2) NFCONT bit 18 = (mode == WRITE)
```

## Sources

* Samsung's `s5p_nand_mlc.c` as published in third-party S5PV210 NAND ports —
  `S5P_NFECCCONF_MSGLEN_SHIFT 16`, `S5P_NFECCCONF_ECCTYPE_16BIT (5<<0)`,
  `S5P_NFECCCONT_ENCODE (1<<16)`, `INITMECC (1<<2)`,
  `S5P_NFECCSTAT_ECCENCDONE (1<<25)`, `ECCDECDONE (1<<24)`, `ECCBUSY (1<<31)`,
  and the ECC-block offsets 0x00/0x20/0x30/0x40/0x90/0xC0/0xF0.
* `regs-nand.h` of the same lineage — `S3C_NFCONF_ECC_8BIT = (1<<23)`.
* Published S5PV210 NAND write-ups for the `NFCONF` AddrCycle / PageSize /
  MLCFlash / TACLS / TWRPH field layout and `NFCONT` bit 0 MODE / bit 1 nCE0 /
  `NFSTAT` bit 4.
* Our own disassembly of the mask ROM for the two bring-up stages above. That
  listing is not published.
