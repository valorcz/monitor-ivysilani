# Troubleshooting Guide

This guide describes how to identify and resolve common issues encountered when running tvwatch.

---

## 1. HTTP 403 Forbidden on Web Scraping Requests

### Symptoms
- `sync` operations fail immediately when fetching show pages.
- Logs report `requests.exceptions.HTTPError: 403 Client Error: Forbidden`.

### Cause
Ceska televize uses automated edge bot filtering that rejects generic or missing User-Agent headers.

### Resolution
Ensure `USER_AGENT` in `.env` (or `tvwatch.core.config.CONFIG`) is set to a standard modern browser User-Agent string:
```bash
USER_AGENT="Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
```
The default configuration provides a valid browser User-Agent out of the box.

---

## 2. Downloader Fails with HTTP Error 410: Gone

### Symptoms
- Download attempts fail with an error similar to:
  `ERROR: [CeskaTelevize] Unable to download webpage: HTTP Error 410: Gone (caused by <HTTPError 410: Gone>)`
  specifically referencing `iframe-hash` or `iFramePlayer.php`.

### Cause
Ceska televize decommissioned legacy player endpoints. Official unpatched versions of `yt-dlp` still reference these deprecated endpoints.

### Resolution
Apply the custom extractor patch located in `yt-dlp-patch/ceskatelevize.py`.

- **Local environment**:
  Run:
  ```bash
  make install
  ```
  This command installs dependencies and copies the patched extractor into the active virtual environment's `yt_dlp/extractor/` directory.

- **Docker environment**:
  The included `Dockerfile` automatically clones `yt-dlp` and copies `yt-dlp-patch/ceskatelevize.py` into the container image during the build stage. Rebuild the container:
  ```bash
  make docker-build
  make docker-restart
  ```

---

## 3. DuckDB File Locking / Concurrency Conflicts

### Symptoms
- Application crashes with `duckdb.IOException: Could not set lock on file ... Database is already open by another process`.

### Cause
DuckDB is an embedded database engine that permits only one writing process to hold an exclusive file lock at a time. This error occurs if:
- Both the Discord bot daemon and the CLI tool attempt to access the same DuckDB database file simultaneously.
- Multiple instances of the Discord bot daemon are executed against the same `DATA_DIR`.

### Resolution
- Keep the CLI database (`episodes.duckdb`) and the Discord bot databases (`data/guild_*.duckdb`) isolated as configured by default.
- Ensure only a single instance of `tvwatch-bot` is running per host or container.
- If a stale lock remains after an unexpected system halt, verify no zombie processes are holding open file handles:
  ```bash
  fuser episodes.duckdb
  ```

---

## 4. Discord Slash Commands Not Appearing

### Symptoms
- Slash commands (such as `/add`, `/list`, `/sync`) do not appear in the Discord chat autocomplete interface.

### Cause
- The bot application was not granted the `applications.commands` OAuth2 scope when invited to the server.
- Command tree synchronization has not propagated across the Discord API gateway.

### Resolution
1. Verify bot invitation scopes:
   Generate a new invitation link in the Discord Developer Portal ensuring both `bot` and `applications.commands` scopes are checked.
2. Force command tree re-synchronization:
   The bot automatically requests global synchronization on startup (`await self.tree.sync()`). Global command updates may take up to an hour to propagate across Discord, while server-specific commands register immediately.
3. Check bot permissions:
   The `/set_channel` command requires Administrator permissions in the server where it is executed.
