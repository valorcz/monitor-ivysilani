# -----------------------------
# Base: Python + uv + static ffmpeg
# -----------------------------
FROM docker.io/python:3.12-slim AS base

# Copy 'uv' binaries (fast package installs) and static ffmpeg
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=1

# -----------------------------
# Build & install patched yt-dlp
# -----------------------------
FROM base AS ytdlp-build

# Minimal OS deps; git required for cloning yt-dlp
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git \
 && rm -rf /var/lib/apt/lists/

WORKDIR /yt-dlp
RUN git clone --single-branch https://github.com/yt-dlp/yt-dlp.git ./

# Add your patched extractor (path must match yt-dlp package layout)
# The local file should exist at: ./yt-dlp-patch/ceskatelevize.py
ADD ./yt-dlp-patch/ceskatelevize.py ./yt_dlp/extractor/ceskatelevize.py

# Optional: extras that some sites need (kept from your original snippet)
RUN uv add brotli certifi mutagen requests urllib3 websockets yt_dlp_ejs

# -----------------------------
# Final image: app + patched yt-dlp installed system-wide
# -----------------------------
FROM base AS app

# Copy yt-dlp installation and wrapper from the build stage
COPY --from=docker.io/mwader/static-ffmpeg:latest /ffmpeg /bin/
COPY --from=ytdlp-build /yt-dlp /yt-dlp

# Workdir and volumes
WORKDIR /app
VOLUME ["/app/data", "/download"]

# Default ENV for your app (override with .env or docker run -e)
# These map to tvwatch.core.config.Config fields
ENV DATA_DIR=/app/data \
    DOWNLOAD_DIR=/download \
    YTDLP_EXECUTABLE=/opt/yt-dlp/yt-dlp.sh \
    YTDLP_EXTRA_ARGS= \
    YTDLP_PREFIX_DIR=/opt/yt-dlp \
    YTDLP_SCRIPT_NAME=yt-dlp.sh

# Copy your project (src layout)
# Keep layer caching efficient by copying pyproject first
COPY --chown=tvw:tvw pyproject.toml .
COPY --chown=tvw:tvw src ./src

# Install your package + deps into the system Python using uv
# RUN uv pip install -e .
RUN uv pip install .
RUN uv pip install /yt-dlp/

# (Optional) create a non-root user for better security
RUN useradd -m -u 10001 tvw \
 && chown -R tvw:tvw /app
USER tvw

# Default command shows help; override in docker run/compose
# CMD ["python", "-m", "tvwatch.interfaces.cli"]
ENTRYPOINT ["tvwatch"]
