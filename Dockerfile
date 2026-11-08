# -----------------------------
# Base: Python + uv + static ffmpeg
# -----------------------------
FROM docker.io/python:3.12-slim AS base

# Copy 'uv' binaries (fast package installs) and static ffmpeg
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY --from=docker.io/mwader/static-ffmpeg:latest /ffmpeg /bin/

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=1

# -----------------------------
# Final image: tvwatch app
# -----------------------------
FROM base AS app

# Workdir and volumes
WORKDIR /app
VOLUME ["/app/data", "/download"]

# Default ENV for your app (override with .env or docker run -e)
# These map to tvwatch.core.config.Config fields
ENV DATA_DIR=/app/data \
    DOWNLOAD_DIR=/download \
    YTDLP_EXECUTABLE= \
    YTDLP_EXTRA_ARGS= \
    YTDLP_PREFIX_DIR= \
    YTDLP_SCRIPT_NAME=

# Create non-root user
RUN useradd -m -u 10001 tvw \
 && chown -R tvw:tvw /app

# Copy project files
COPY --chown=tvw:tvw pyproject.toml uv.lock ./
COPY --chown=tvw:tvw src ./src

# Install package and dependencies system-wide
RUN uv pip install .

USER tvw

# Default entrypoint
ENTRYPOINT ["tvwatch"]
CMD ["--help"]
