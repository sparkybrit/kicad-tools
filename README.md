# sparky1-modules

KiCad interface symbols for the modules that make up
[Sparky1](https://github.com/sparkybrit/sparky1), the MC68030 breadboard computer.

Each module - a breadboard adapter, a PLD, a bought-in board - is one symbol, with one unit per connector
(a PLD's one "connector" is its own pins).
A pin's **number** is the connector pin and its **name** is the module's own signal name. On a module
with several connectors the number is `CONN.N` (`J1.8`): KiCad takes pins with the same number on
different units to be one pin, and would join J1 pin 1 to J2 pin 1. The interface files keep the bare
number. The system
schematic in `sparky1` places these symbols and draws only the wiring between them; it never redraws a
module's insides. The reasoning is in sparky1's `KICAD_MODULES.md`.

## Files

| Path | What |
|---|---|
| `sparky1_modules.kicad_sym` | The library, KiCad 10 format. **Generated - do not edit by hand** |
| `modules.toml` | The modules: each one's repo, schematic and interface connectors |
| `interfaces/<MODULE>.json` | Each module's connector pins as read from its own schematic, and the commit they came from |
| `tools/extract.py` | Reads a module's interface from its source (below) |
| `sources/*.pins` | Hand-transcribed pin lists, each naming the document it came from |
| `tools/build_lib.py` | Builds the library from `interfaces/` |
| `tools/check.py` | Fails if the library is not what `build_lib.py` generates, or if a module's schematic no longer matches its interface file |
| `tools/sexp.py`, `tools/symlib.py` | S-expression and symbol-library helpers, from sparky1-backplane |

## Where each module's pins come from

Each module in `modules.toml` names one source:

| Key | Source | Checked against | Modules |
|---|---|---|---|
| `schematic` | the module's own KiCad schematic, through `kicad-cli sch export netlist` | that schematic | SRAM_ADAPTER, CPU_ADAPTER, ROM_RAM_ADAPTER |
| `pld` | the `PIN` lines of a `.pld`, with its equations deciding input or output | that `.pld` | BUSDECODER, SRAMMANAGER, CFMANAGER, BOOT, WATCHDOG |
| `label` | the part's label in [ic-label-generator](https://github.com/sparkybrit/ic-label-generator) (`defs/*.json`), the one it is wired by: names, pin numbers, and input or output from the label's lists | that label | CF_ADAPTER, DUART |
| `pins` | a pin list in `sources/`, transcribed from a datasheet or document | the file only | NVRAM_PROGRAMMER, RESISTOR_ADAPTER_8, POWER_CLOCK, USB_UART |
| `stock` | a stock KiCad 10 symbol, flattened and renamed into this library | the installed KiCad library | none at present |

A `stock` module's interface records the KiCad version it was copied from; if a KiCad update changes the stock
symbol, `make check` fails until it is re-extracted on purpose.

## Using it

Projects pull this repo in as a git submodule and add the library to their `sym-lib-table`:

```
(lib (name "sparky1_modules")(type "KiCad")(uri "${KIPRJMOD}/lib/sparky1-modules/sparky1_modules.kicad_sym")(options "")(descr ""))
```

## Changing a module's interface

Change the module's own schematic and commit it, then here:

```
uv run --no-project python -I tools/extract.py MODULE ../path/to/module-repo
make
```

Commit the new `interfaces/MODULE.json` and library together. Names are cleaned on the way in: the sheet
path goes, an overbar left unclosed in the source is closed (`~{BR` becomes `~{BR}`), and an unconnected
pin is `NC`. A PLD's undeclared pins follow sparky1's chip-diagram convention: an unused input-only pin is
`IN<n>` (tied low on the board), an unused macrocell pin `NC`. Its pins are typed `input` or `output` (an output
is a pin with an equation), so ERC can check them; a board's signal pins are `passive`.

## Checking

```
make check                                     # library against interfaces/
uv run --no-project python -I tools/check.py SRAM_ADAPTER ../sram-breadboard-adapter BUSDECODER ../sparky1 DUART ../ic-label-generator
                                               # and each MODULE against its source
```

Each difference is reported by connector and pin, and the exit status is 1. Run the second form in a module's
repo before committing a change to its connectors.

Requires KiCad 10 (`kicad-cli`) and uv.
