# tvwatch

tvwatch is a watchlist monitor and automated downloader for Ceska televize (iVysilani). It provides both a command-line interface (CLI) for direct show management and syncing, and a Discord bot for multi-server tracking, automated hourly syncs, and episode download triggers.

---

## Features

- Complete Episode Detection: Accurately identifies all playable episodes across multi-season and long-running shows using internal show IDEC resolution and Apollo GraphQL pagination.
- Reliable Deduplication: Canonical URL normalization and episode ID tracking prevent duplicate records and duplicate notification messages.
- Notification Cooldown: Configurable timeout preventing notification spam for reruns and re-aired episodes, with optional silent backfilling when adding older shows.
- Flexible Downloader: Integrated yt-dlp wrapper with patched Ceska televize extractor support, automated ffmpeg merging, and subtitle embedding.
- Dual Interfaces: Full-featured CLI for scripting and automation, alongside an interactive Discord bot with slash commands and one-click download buttons.
- Container Ready: Includes Docker and Docker Compose definitions for daemonized deployment.

---

## Quick Start

### Prerequisites

- Python 3.11 or later
- [uv](https://github.com/astral-sh/uv) (recommended package and environment manager)
- ffmpeg (for merging audio/video streams)

### Local Installation

1. Clone the repository and install dependencies:
   ```bash
   make install
   ```

2. Create an environment file `.env` (optional for CLI, required for Discord bot):
   ```bash
   cp .env.example .env 2>/dev/null || touch .env
   ```

3. Run automated tests to verify the installation:
   ```bash
   make test
   ```

---

## Configuration

Settings can be customized via environment variables or a `.env` file in the project root:

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `DB_PATH` | `episodes.duckdb` | Path to DuckDB database used by CLI. |
| `DATA_DIR` | `./data` | Directory for Discord guild databases and state. |
| `DOWNLOAD_DIR` | `./downloads` | Target directory where downloaded video files are saved. |
| `DISCORD_BOT_TOKEN` | `""` | Discord bot authentication token. |
| `NOTIFICATION_COOLDOWN_DAYS` | `30` | Minimum days before a re-aired episode can trigger another notification. |
| `NOTIFY_ON_INITIAL_ADD` | `false` | When false, existing episodes are silently backfilled when adding a show. |
| `GRAPHQL_PERSISTED_HASH` | `e627db8ae17ccfbb...` | Fallback SHA-256 hash for Ceska televize Apollo persisted queries. |
| `HTTP_TIMEOUT` | `10` | HTTP request timeout in seconds. |
| `USER_AGENT` | Standard browser UA | User-Agent string used for web scraping and API requests. |
| `YTDLP_EXECUTABLE` | `null` | Explicit path to yt-dlp binary (overrides prefix + script name). |
| `YTDLP_PREFIX_DIR` | `/yt-dlp` | Directory containing the patched yt-dlp wrapper script. |
| `YTDLP_SCRIPT_NAME` | `yt-dlp.sh` | Wrapper script name. |
| `YTDLP_OUTPUT_TEMPLATE` | `%(clean_show_dir)s/%(clean_season_dir)s/%(clean_filename)s.%(ext)s` | Output template for yt-dlp (Plex/Jellyfin nested hierarchy). |

---

## Command-Line Interface (CLI)

The CLI executable is `tvwatch` (or `uv run tvwatch`).

### Adding a Show
```bash
tvwatch add "https://www.ceskatelevize.cz/porady/16560311257-tobias-lolness/"
```
The URL is automatically normalized to canonical form (`https://www.ceskatelevize.cz/porady/<slug>/`).

### Listing Shows
```bash
# List active shows
tvwatch list

# List all shows including disabled ones
tvwatch list --all

# Output in JSON format
tvwatch list --json
```

### Disabling a Show
```bash
tvwatch disable "https://www.ceskatelevize.cz/porady/16560311257-tobias-lolness/"
```

### Checking and Syncing Episodes
```bash
# Check active shows and output newly discovered episodes as JSON
tvwatch sync

# Check active shows and automatically download newly discovered episodes
tvwatch sync --download
```

### Manual Episode Download
```bash
tvwatch download "https://www.ceskatelevize.cz/porady/16560311257-tobias-lolness/225384613200001/"
```

---

## Discord Bot

The bot service runs `tvwatch-bot` (or `uv run tvwatch-bot`).

### Setup

1. Create a Discord application in the Discord Developer Portal and retrieve the bot token.
2. Set `DISCORD_BOT_TOKEN=your_token_here` in `.env`.
3. Invite the bot to your Discord server with `bot` and `applications.commands` scopes.
4. Launch the bot:
   ```bash
   tvwatch-bot
   ```

### Slash Commands

- `/set_channel`: Sets the current channel as the destination for automated episode notifications (requires Administrator permissions).
- `/add <url>`: Adds a show to the server watchlist. Existing episodes are backfilled silently unless configured otherwise.
- `/list`: Displays all shows currently tracked on the server in a formatted ASCII table.
- `/episodes <url>`: Displays an ASCII table of all tracked episodes for a show with an interactive download button.
- `/download <url>`: Downloads a specific episode on demand.
- `/status`: Displays server statistics (active/paused shows, total episodes, storage disk usage).
- `/disable <url>`: Pauses tracking for a show (features interactive autocomplete).
- `/remove <url>`: Permanently deletes a show and its history from the database (features interactive autocomplete).
- `/sync`: Manually triggers an immediate episode check for the current server.

---

## Docker Operations

A Makefile is provided to streamline common container operations with Docker Compose:

```bash
# Build container images
make docker-build

# Start services in the background
make docker-up

# View running container status
make docker-ps

# Follow service logs
make docker-logs

# Execute an interactive shell inside the container
make docker-shell

# Run a one-time episode sync inside a container
make docker-sync

# Restart services
make docker-restart

# Stop and remove containers
make docker-down
```

---

## Additional Documentation

Detailed technical documentation is available in the `docs/` directory:

- [System Architecture](docs/architecture.md): Internal data flow, GraphQL resolution, database model, and deduplication logic.
- [Extensibility Guide](docs/extensibility.md): Instructions for adding notification channels, custom download post-processors, and other platforms.
- [Troubleshooting Guide](docs/troubleshooting.md): Diagnosis and remedies for common networking, yt-dlp, DuckDB, and Discord issues.
