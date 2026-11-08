# sparky1-modules

KiCad interface symbols for the modules that make up
[Sparky1](https://github.com/sparkybrit/sparky1), the MC68030 breadboard computer.

Each module - a breadboard adapter, a PLD, a bought-in board - is one symbol, with one unit per connector.
A pin's **number** is the connector pin and its **name** is the module's own signal name. The system
schematic in `sparky1` places these symbols and draws only the wiring between them; it never redraws a
module's insides. The reasoning is in sparky1's `KICAD_MODULES.md`.

## Files

| Path | What |
|---|---|
| `sparky1_modules.kicad_sym` | The library, KiCad 10 format. **Generated - do not edit by hand** |
| `modules.toml` | The modules: each one's repo, schematic and interface connectors |
| `interfaces/<MODULE>.json` | Each module's connector pins as read from its own schematic, and the commit they came from |
| `tools/extract.py` | Reads a module's interface from its repo, via `kicad-cli sch export netlist` |
| `tools/build_lib.py` | Builds the library from `interfaces/` |
| `tools/check.py` | Fails if the library is not what `build_lib.py` generates, or if a module's schematic no longer matches its interface file |
| `tools/sexp.py` | S-expression reader and writer, from sparky1-backplane |

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
pin is `NC`.

## Checking

```
make check                                                            # library against interfaces/
uv run --no-project python -I tools/check.py SRAM_ADAPTER ../sram-breadboard-adapter   # a module against its schematic
```

Each difference is reported by connector and pin, and the exit status is 1. Run the second form in a module's
repo before committing a change to its connectors.

Requires KiCad 10 (`kicad-cli`) and uv.
