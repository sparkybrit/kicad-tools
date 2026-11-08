PY = uv run --no-project python -I

.PHONY: lib check
lib:
	$(PY) tools/build_lib.py

# Library against interfaces/ only. To check a module against its own schematic:
#   $(PY) tools/check.py MODULE path/to/module-repo
check:
	$(PY) tools/check.py
