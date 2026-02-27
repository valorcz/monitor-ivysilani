# --------------------------
#  Project Makefile for tvwatch
# --------------------------

# Use uv for everything
PYTHON = uv run
UV_PIP = uv pip

# Default target
.DEFAULT_GOAL := test

# --------------------------
#  Installation
# --------------------------
install:
	$(UV_PIP) install -e .
	$(UV_PIP) install pytest
	cp yt-dlp-patch/ceskatelevize.py .venv/lib/python3.12/site-packages/yt_dlp/extractor/

# --------------------------
#  Test runners
# --------------------------
test:
	$(PYTHON) pytest -q

test-fast:
	$(PYTHON) pytest -x

# optional: requires `pip install pytest-watch`
test-watch:
	$(PYTHON) ptw

# --------------------------
#  Linting & formatting
# --------------------------
lint:
	$(PYTHON) ruff check src tests

format:
	$(PYTHON) ruff check src tests --fix
	$(PYTHON) black src tests

# --------------------------
#  Cleanup
# --------------------------
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	rm -rf .ruff_cache
	rm -rf dist build

# --------------------------
#  Help
# --------------------------
help:
	@echo "Available targets:"
	@echo "  make install      - install package + pytest into uv environment"
	@echo "  make test         - run tests (quiet mode)"
	@echo "  make test-fast    - run tests, stop on first failure"
	@echo "  make test-watch   - re-run tests automatically on changes"
	@echo "  make lint         - run ruff linter"
	@echo "  make format       - auto-fix with ruff + black"
	@echo "  make clean        - remove caches and build artifacts"

