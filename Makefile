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
	@for d in .venv/lib*/python*/site-packages/yt_dlp/extractor; do \
		if [ -d "$$d" ]; then \
			cp yt-dlp-patch/ceskatelevize.py "$$d/"; \
			echo "Patched yt-dlp extractor in $$d"; \
		fi \
	done

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
	$(PYTHON) ruff format src tests

# --------------------------
#  Docker & Docker Compose
# --------------------------
docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-restart:
	docker compose restart

docker-logs:
	docker compose logs -f

docker-ps:
	docker compose ps

docker-shell:
	docker compose run --rm --entrypoint /bin/bash scraper

docker-sync:
	docker compose run --rm --entrypoint tvwatch scraper sync

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
	@echo "  make install         - install package + pytest into uv environment"
	@echo "  make test            - run tests (quiet mode)"
	@echo "  make test-fast       - run tests, stop on first failure"
	@echo "  make test-watch      - re-run tests automatically on changes"
	@echo "  make lint            - run ruff linter"
	@echo "  make format          - auto-fix and format with ruff"
	@echo "  make docker-build    - build container images with docker compose"
	@echo "  make docker-up       - start services in background"
	@echo "  make docker-down     - stop and remove containers"
	@echo "  make docker-restart  - restart running services"
	@echo "  make docker-logs     - follow container logs"
	@echo "  make docker-ps       - view status of containers"
	@echo "  make docker-shell    - run interactive bash inside container"
	@echo "  make docker-sync     - run one-time tvwatch sync in container"
	@echo "  make clean           - remove caches and build artifacts"

