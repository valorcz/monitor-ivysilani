import asyncio
from typing import Iterable, List, Tuple
from .config import CONFIG
from .net import assert_allowed_url

YT_DLP_ARGS_BASE = [
    "uv",
    "run",
    "/yt-dlp/yt-dlp.sh",
    "-P",
    CONFIG.DOWNLOAD_DIR,
    "--no-playlist",
    "--force-ipv4",
    "--restrict-filenames",
    "--verbose",
    "-o",
    "%(title)s_[%(id)s].%(ext)s",
    "--audio-multistreams",
    "-f",
    "bv*+mergeall[vcodec=none]",
    "--embed-subs",
    "--embed-metadata",
    "--all-subs",
    "--newline",
]


async def download_one(url: str, logger) -> Tuple[str, bool, str]:
    """
    Download a single episode URL; returns (url, success, error_message).
    """
    assert_allowed_url(url)
    proc = await asyncio.create_subprocess_exec(
        *YT_DLP_ARGS_BASE,
        url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Stream logs (optional, concise)
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        logger.info("[yt-dlp] %s", line.decode("utf-8", errors="replace").strip())

    await proc.wait()
    if proc.returncode == 0:
        return url, True, ""
    else:
        stderr = (await proc.stderr.read() if proc.stderr else b"").decode().strip()
        return url, False, stderr


async def download_many(urls: Iterable[str], logger) -> List[Tuple[str, bool, str]]:
    results = []
    for u in urls:
        results.append(await download_one(u, logger))
    return results
