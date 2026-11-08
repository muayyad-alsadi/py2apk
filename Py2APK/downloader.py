"""Optional parallel downloads.

``aiohttp`` is used when it is already installed, with at most five transfers
at once. Otherwise each transfer uses ``urllib.request``.
"""

from __future__ import annotations

import asyncio
import urllib.error
import urllib.request
from pathlib import Path
from typing import Sequence


USER_AGENT = "Py2APK"
PARALLEL_DOWNLOADS = 5


class DownloadError(Exception):
    def __init__(self, url: str, status: int) -> None:
        super().__init__(f"HTTP {status} for {url}")
        self.url = url
        self.status = status


def download_files(jobs: Sequence[tuple[str, Path]], on_complete=None) -> list[str]:
    """Download ``(url, destination)`` pairs.

    Existing destinations are skipped. HTTP 404 URLs are returned. Any other
    HTTP failure raises ``DownloadError``.
    """
    pending = [(url, path) for url, path in jobs if not path.is_file()]
    if not pending:
        return []
    try:
        import aiohttp
    except ImportError:
        return _download_sequential(pending, on_complete)
    return asyncio.run(_download_parallel(pending, aiohttp, on_complete))


def _download_sequential(jobs: Sequence[tuple[str, Path]], on_complete) -> list[str]:
    missing: list[str] = []
    for url, destination in jobs:
        try:
            _download_urllib(url, destination)
        except DownloadError as error:
            if error.status != 404:
                raise
            missing.append(url)
            continue
        if on_complete is not None:
            on_complete()
    return missing


def _download_urllib(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request) as response:
            payload = response.read()
    except urllib.error.HTTPError as error:
        raise DownloadError(url, error.code) from error
    _store(destination, payload)


async def _download_parallel(jobs: Sequence[tuple[str, Path]], aiohttp, on_complete) -> list[str]:
    semaphore = asyncio.Semaphore(PARALLEL_DOWNLOADS)
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": USER_AGENT}) as session:
        results = await asyncio.gather(
            *(_one(session, semaphore, url, path, on_complete) for url, path in jobs)
        )
    return [url for url in results if url is not None]


async def _one(session, semaphore, url: str, destination: Path, on_complete) -> str | None:
    async with semaphore:
        async with session.get(url) as response:
            if response.status == 404:
                return url
            response.raise_for_status()
            payload = await response.read()
    _store(destination, payload)
    if on_complete is not None:
        on_complete()
    return None


def _store(destination: Path, payload: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    _ = partial.write_bytes(payload)
    partial.replace(destination)
