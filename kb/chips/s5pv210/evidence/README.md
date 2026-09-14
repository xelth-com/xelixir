# evidence/ — what may live here

Small artefacts that make a finding checkable:

* **our own** disassembly listings — of code **we** wrote and inject, or of
  register tables and hardware sequences described as such
* captures, timelines and measurements taken on a bench
* register maps and field definitions, with their sources

## What may never live here

* somebody else's binary, or any derivative of one (`*.bin`, patched images)
* a disassembly of somebody else's binary
* a ROM dump, or a listing of one
* vendor datasheets and application notes (`*.pdf`)
* anything carrying a serial number, a customer, or a product-specific layout

The tools in this repository take **your own** board's images as input and locate
what they need by disassembling them at run time, precisely so that nothing of
anybody's has to be checked in here. See `CONTRIBUTING.md` section 4.

## Files

| file | finding |
|---|---|
| `chain-bl1-injected-routine.txt` | `kb_finding:ka4th2tuclj4es4gbad2` — annotated listing of the routine our generator injects |
| `nand-ecc-registers.md` | supports `kb_finding:1ls66vjcbwlpm9394x9o` and `kb_finding:wjca6hkifegra44evx20` — the NAND/ECC register tables and the two ROM bring-up stages |
