# Extensibility Guide

This guide details how to extend tvwatch with new notification channels, download handlers, storage backends, and additional content providers.

---

## 1. Adding Notification Channels

Notifications are decoupled from the core scraping pipeline. When new episodes are discovered, the scraper emits a `SyncResult` object:

```python
class SyncResult(BaseModel):
    source_url: HttpUrl
    tv_series: TVSeries
    new_episodes: List[Episode]
```

### Implementing a Webhook / Push Dispatcher

To forward alerts to a custom HTTP webhook, Slack, Telegram, or Matrix, define a dispatcher function:

```python
import requests
from tvwatch.core.models import SyncResult

def dispatch_webhook(results: list[SyncResult], webhook_url: str) -> None:
    for result in results:
        if not result.new_episodes:
            continue
        
        payload = {
            "series": result.tv_series.name or str(result.source_url),
            "episodes": [
                {
                    "title": ep.name,
                    "url": str(ep.url),
                    "id": ep.idec,
                }
                for ep in result.new_episodes
            ]
        }
        requests.post(webhook_url, json=payload, timeout=10)
```

Hook this dispatcher into CLI workflows or invoke it alongside Discord alerts in `tvwatch/interfaces/`.

---

## 2. Custom Downloader Post-Processing

The downloader invokes `yt-dlp` using base arguments defined in `tvwatch.core.downloader.COMMON_ARGS`.

### Modifying File Naming and Target Formats

To adjust output formats or reorganize downloaded files for media servers like Plex or Jellyfin:

1. Update `COMMON_ARGS` in `src/tvwatch/core/downloader.py`:
   ```python
   # Example: Organize files into series subfolders
   COMMON_ARGS = [
       "-o", "%(series,title)s/Season %(season_number,1)02d/%(series,title)s - S%(season_number,1)02dE%(episode_number,1)02d - %(title)s.%(ext)s",
       "--embed-subs",
       "--embed-metadata",
   ]
   ```

2. Add custom post-processing triggers by extending `download_one()`:
   ```python
   if proc.returncode == 0:
       # Trigger custom post-processing, e.g. media library scan or notification
       notify_media_server(url)
   ```

### Utilizing External Download Accelerators

To enable `aria2c` multi-connection downloading through `yt-dlp`:

Set `YTDLP_EXTRA_ARGS` in `.env`:
```bash
YTDLP_EXTRA_ARGS="--downloader aria2c --downloader-args aria2c:'-s 4 -x 4 -k 1M'"
```

---

## 3. Supporting Additional Streaming Providers

While tvwatch is currently tailored for Ceska televize, the system is designed to accommodate multi-provider monitoring.

### Scraper Interface Pattern

To introduce a new provider (e.g. another national broadcaster or archive service):

1. Define a provider scraper module in `src/tvwatch/providers/` conforming to the interface:
   ```python
   def sync_provider_show(url: str, repo: DuckRepo, logger) -> Optional[SyncResult]:
       ...
   ```

2. Register provider URL patterns in `src/tvwatch/core/config.py`:
   ```python
   ALLOWED_URL_RE: str = r"^https://(www\.)?(ceskatelevize\.cz|otherbroadcaster\.com)/.*"
   ```

3. Route URLs based on hostname in `tvwatch.core.scraper.sync_one_show`:
   ```python
   if "ceskatelevize.cz" in show_url:
       return sync_ct_show(...)
   elif "otherbroadcaster.com" in show_url:
       return sync_other_show(...)
   ```

---

## 4. Storage Engine Adaptation

The repository pattern encapsulated in `DuckRepo` abstracts direct database interactions.

To transition from DuckDB to PostgreSQL or SQLite:

1. Implement the repository methods defined in `src/tvwatch/core/db.py`:
   - `add_or_reactivate_show(url: str) -> str`
   - `disable_show(url: str) -> bool`
   - `list_shows(active: Optional[bool]) -> List[Tuple[str, bool]]`
   - `get_active_urls() -> List[str]`
   - `update_show_metadata(url: str, metadata: Dict[str, Any]) -> None`
   - `record_episode(...) -> bool`
   - `mark_episodes_notified(urls: List[str]) -> None`
2. Update schema initialization to use provider-specific syntax (such as PostgreSQL's `ON CONFLICT (...) DO UPDATE` and native JSONB columns).
