import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable

from .config import CONFIG
from .net import assert_allowed_url

# Static flags that are safe to keep as defaults
COMMON_ARGS = [
    "--no-playlist",
    "--force-ipv4",
    #    "--restrict-filenames",
    "--verbose",
    "--audio-multistreams",
    "-f",
    "bv*+mergeall[vcodec=none]",
    "--embed-subs",
    "--embed-metadata",
    "--all-subs",
    "--no-progress",
    #    "--newline", # if we need to see the progress, let's use the --newline
]


def _base_cmd() -> list[str]:
    """
    Builds the yt-dlp command honoring .env overrides from CONFIG.
    Examples:
      - YTDLP_EXECUTABLE=/opt/bin/yt-dlp.sh
      - YTDLP_PREFIX_DIR=/patched   YTDLP_SCRIPT_NAME=yt-dlp.sh
      - YTDLP_EXTRA_ARGS="uv run"
    """
    cmd = CONFIG.ytdlp_cmd[:]  # copy
    # Ensure we always set the output directory (safe, controlled path)
    cmd += ["-P", CONFIG.DOWNLOAD_DIR]
    cmd += ["-o", CONFIG.YTDLP_OUTPUT_TEMPLATE]
    cmd += COMMON_ARGS
    return cmd


async def _stream_subprocess(proc, logger, prefix: str) -> None:
    """
    Streams stdout of a subprocess line-by-line for real-time visibility.
    """
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        msg = line.decode("utf-8", errors="replace").rstrip()
        if msg:
            logger.info("%s %s", prefix, msg)


def resolved_ytdlp_command_preview() -> str:
    return " ".join(_base_cmd() + ["<URL>"])


async def download_one(url: str, logger) -> tuple[str, bool, str]:
    """
    Download a single episode URL; returns (url, success, error_message).
    Logging is verbose-by-default but not noisy (stdout lines are prefixed).
    """
    assert_allowed_url(url)
    started = time.monotonic()
    cmd = _base_cmd() + [url]

    # Show the exact command at DEBUG level (useful for diagnosing env issues)
    logger.debug("yt-dlp command: %r", cmd)
    logger.info("Starting download: url=%s -> dir=%s", url, CONFIG.DOWNLOAD_DIR)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        err = (
            f"Executable not found. Check YTDLP_EXECUTABLE / YTDLP_PREFIX_DIR / "
            f"YTDLP_SCRIPT_NAME / YTDLP_EXTRA_ARGS. Error: {e}"
        )
        logger.error(err)
        return url, False, err
    except Exception as e:  # noqa: BLE001
        err = f"Failed to start downloader process: {e}"
        logger.error(err)
        return url, False, err

    # Stream stdout in real time; collect stderr once finished
    await _stream_subprocess(proc, logger, prefix="[yt-dlp]")

    await proc.wait()
    duration = time.monotonic() - started

    if proc.returncode == 0:
        logger.info(
            "Download finished OK: url=%s, rc=%s, took=%.2fs",
            url,
            proc.returncode,
            duration,
        )
        return url, True, ""
    else:
        stderr = (
            (await proc.stderr.read() if proc.stderr else b"")
            .decode("utf-8", errors="replace")
            .strip()
        )
        logger.error(
            "Download FAILED: url=%s, rc=%s, took=%.2fs, stderr=%s",
            url,
            proc.returncode,
            duration,
            stderr[:800],  # prevent output spam
        )
        return url, False, stderr


async def download_many(
    urls: Iterable[str] | None,
    logger,
    progress_callback: Callable[[int, int, str, bool | None], Awaitable[None]]
    | None = None,
) -> list[tuple[str, bool, str]]:
    """
    Downloads provided URLs sequentially (simple and predictable).
    Supports progress_callback(current_idx, total_count, url, status)
    where status is None at start, and bool (success/fail) upon completion.
    """
    results: list[tuple[str, bool, str]] = []
    if urls is None:
        return results

    urls_list = list(urls)
    total = len(urls_list)
    ok = 0

    for i, u in enumerate(urls_list, 1):
        logger.info("Queue %d/%d: %s", i, total, u)
        if progress_callback:
            try:
                await progress_callback(i, total, u, None)
            except Exception as ex:  # noqa: BLE001
                logger.warning(f"Error in progress_callback start: {ex}")

        r = await download_one(u, logger)
        results.append(r)
        if r[1]:
            ok += 1
        logger.info("Progress: %d/%d OK", ok, i)

        if progress_callback:
            try:
                await progress_callback(i, total, u, r[1])
            except Exception as ex:  # noqa: BLE001
                logger.warning(f"Error in progress_callback finish: {ex}")

    logger.info("Batch complete: %d/%d OK", ok, total)
    return results
