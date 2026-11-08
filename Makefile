PY = uv run --no-project python -I

.PHONY: lib
lib:
	$(PY) tools/build_lib.py
