# kicad-tools

Tools for drawing a system made of modules in KiCad 10. A module can be a board with its own KiCad
project, a PLD, a bought-in board or a labelled breadboard adapter. Each module is drawn as one
**interface symbol**:

- the symbol's pins are the module's connector pins, read from the module's own source;
- the system schematic places these symbols and draws only the wiring between them.

The symbol is generated, so it cannot drift from the source, and a check fails if it has.

Written for [Sparky1](https://github.com/sparkybrit/sparky1), the MC68030 breadboard computer, whose
`KICAD_MODULES.md` has the reasoning. Nothing in the tools is specific to it.

## How it is used

Each repo that defines modules:

- keeps a **project directory**, by convention `module/`, holding:
  - `modules.toml`, which names the library and describes each module;
  - `interfaces/`, generated;
  - the library `<library>.kicad_sym`, generated;
- has this repo as a git submodule at `lib/kicad-tools`;
- has a `Makefile` in the project directory, a single line: `include ../lib/kicad-tools/module.mk`.

```
make -C module          # re-extract every module that has a path, then rebuild the library
make -C module lib      # rebuild the library from interfaces/ only
make -C module check    # fail if the library is not what interfaces/ generate, or if a module's
                        # source no longer matches its interface file; each difference by pin
```

So a board repo checks its own symbol: change the schematic, run `make -C module`, and commit the
schematic, the interface and the symbol together.

A system repo has each module repo as a submodule, pinned to the revision that is actually built, and adds
each library to its `sym-lib-table`:

```
(lib (name "sram-breadboard-adapter")(type "KiCad")(uri "${KIPRJMOD}/lib/sram-breadboard-adapter/module/sram-breadboard-adapter.kicad_sym"))
```

## modules.toml

Top-level keys:

- `library`: the library's name, which is also its file's stem.
- `keywords`: optional; copied into each symbol's keywords.

Each table is one module, and the table's name is the symbol's name.

| Key | What |
|---|---|
| `description` | One line, copied into the symbol's Description |
| `repo` | The module source's URL, for the record |
| `path` | Where the source is checked out, relative to the project directory (`..` for the repo the project sits in). Without it, the checkout is given on the command line |
| `schematic` / `pld` / `label` / `pins` / `stock` | The source, relative to that checkout. Exactly one; see below |
| `connectors` | The references of the connectors that make up the interface, in order |
| `names` | Optional `{connector = text}`, shown on the symbol |
| `reference` | Optional reference prefix; the default is `A` |
| `sides` | Optional `{left = [...], right = [...]}`: one body with the pins down each side, top to bottom, as they sit on the board (for a PCB, as drawn and turned a quarter-turn clockwise). An entry is a whole connector, `CONN:FIRST-LAST` (a run, either direction: `DIP24:24-13`), or `CONN:P,Q,R`. A `TEXT\|ENTRY` prefix heads that run with `TEXT` |
| `body_width` | Optional, in mm, with `sides`: draw the body as wide as the package (a 0.3 in DIP is 7.62), the pin names outside it, the numbers just inside its edge and the name down the middle |

Without `sides`, each connector is its own unit.

## Where a module's pins come from

| Key | Source | Pin types |
|---|---|---|
| `schematic` | the module's KiCad schematic, through `kicad-cli sch export netlist`: each connector pin is named after its net | passive, power for GND and +5V, NC if unconnected |
| `pld` | the `PIN` lines of a GAL16V8 or GAL22V10 `.pld`, plus its `Revision`, which goes into the symbol's Value (`BUSDECODER rev 03`) | input, or output if the pin has an equation |
| `label` | an [ic-label-generator](https://github.com/sparkybrit/ic-label-generator) label definition: names, pin numbers, and the label's input and output lists | from the label |
| `pins` | a hand-written list, one `number name type` per line. Its header must name the document it came from. `[REF]` starts the next connector | as written |
| `stock` | a stock KiCad 10 symbol, `Lib:Symbol`, flattened and copied | KiCad's |

Names are cleaned on the way in:

- the sheet path is dropped;
- an overbar left open is closed (`~{BR` becomes `~{BR}`);
- an unconnected pin is named `NC`.

A PLD's undeclared pins follow the chip-diagram convention: an unused input-only pin is `IN<n>` (tied low),
and an unused macrocell pin is `NC`.

Each interface records the source's commit, unless the source is in the same repo as the project directory,
since then they are versioned together. A `stock` interface records the KiCad version instead, so a KiCad
update that changes the symbol fails `check` until it is re-extracted on purpose.

## Pin numbers

A pin's **number** is the connector pin, and its **name** is the module's own signal name.

On a module with several connectors, the number is `CONN.N` (`J1.8`). KiCad treats pins with the same
number on different units as one pin, so plain numbers would join J1 pin 1 to J2 pin 1. A single body
needs the numbers unique anyway. The interface files keep the bare number.

## Files

| Path | What |
|---|---|
| `module.mk` | The make targets above, for a project directory to include |
| `tools/extract.py` | Reads a module's interface from its source into `interfaces/<MODULE>.json` |
| `tools/build_lib.py` | Builds the library from `interfaces/` |
| `tools/check.py` | The check above |
| `tools/kisch.py` | Writes schematic sheets, for a system repo's generator |
| `tools/sexp.py`, `tools/symlib.py` | S-expression and symbol-library helpers |

Every tool takes `-C DIR` for the project directory; the default is the current directory.

Requires KiCad 10 (`kicad-cli`) and [uv](https://docs.astral.sh/uv/). Run the tools with
`uv run --no-project python -I tools/<tool>.py`.
