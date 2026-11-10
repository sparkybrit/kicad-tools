# Include from the directory that holds a project's modules.toml:
#
#   include ../lib/kicad-tools/module.mk
#
#   make              re-extract every module with a path, then rebuild the library
#   make lib          rebuild the library from interfaces/ only
#   make check        the library matches interfaces/, and each module with a path its source
UV ?= $(HOME)/.local/bin/uv
KICAD_TOOLS := $(patsubst %/,%,$(dir $(lastword $(MAKEFILE_LIST))))/tools
KT_PY = $(UV) run --no-project python -I

all: interfaces lib

interfaces:
	$(KT_PY) $(KICAD_TOOLS)/extract.py --all

lib:
	$(KT_PY) $(KICAD_TOOLS)/build_lib.py

check:
	$(KT_PY) $(KICAD_TOOLS)/check.py

.PHONY: all interfaces lib check
