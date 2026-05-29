from __future__ import annotations

import asyncio
import logging
import os
import random
import time as _time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx

from .models import Paper

logger = logging.getLogger(__name__)

_ARXIV_PDF_TEMPLATE = "https://arxiv.org/pdf/{arxiv_id}.pdf"
_USER_AGENT = "paper-search/0.2 (mailto:user@example.com)"
_DEFAULT_DOWNLOAD_DIR = "./downloads"
_CHUNK_SIZE = 64 * 1024  # 64 KB

_ARXIV_MIN_INTERVAL = 10.0
_MAX_RETRIES = 3
_BASE_BACKOFF = 30.0
_RETRYABLE_STATUSES = frozenset({429, 503})

_RETRYABLE_ERRORS = (
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
)

DownloadStatus = Literal[
    "success", "no_url", "paywall", "not_found", "http_error", "network_error", "unknown"
]

# Known subscription/paywall publishers — detected by domain suffix.
_PAYWALL_DOMAINS: dict[str, str] = {
    "dl.acm.org": "ACM Digital Library",
    "ieeexplore.ieee.org": "IEEE Xplore",
    "link.springer.com": "Springer",
    "sciencedirect.com": "Elsevier / ScienceDirect",
    "nature.com": "Nature",
    "tandfonline.com": "Taylor & Francis",
    "wiley.com": "Wiley",
    "onlinelibrary.wiley.com": "Wiley",
    "jstor.org": "JSTOR",
    "journals.sagepub.com": "SAGE",
    "pubs.acs.org": "ACS",
    "pubs.rsc.org": "RSC",
    "aps.org": "APS",
    "aip.scitation.org": "AIP",
}

# PDF magic bytes for lightweight validation.
_PDF_MAGIC = b"%PDF-"
_MAGIC_READ_SIZE = 8


@dataclass
class DownloadResult:
    path: Path | None = None
    status: DownloadStatus = "unknown"
    detail: str = ""
    url: str = ""


def _classify_paywall(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    for domain, name in _PAYWALL_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return name
    return None


def _parse_retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After", "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _sanitize_filename(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in " ._-()[]").rstrip()


class Downloader:
    def __init__(self, download_dir: str = _DEFAULT_DOWNLOAD_DIR):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self._rate_lock = asyncio.Lock()
        self._last_request: float = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={"User-Agent": _USER_AGENT},
            timeout=120.0,
            follow_redirects=True,
        )

    async def _await_rate_limit(self) -> None:
        async with self._rate_lock:
            now = _time.monotonic()
            wait = self._last_request + _ARXIV_MIN_INTERVAL - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = _time.monotonic()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def download_arxiv(self, arxiv_id: str) -> DownloadResult:
        url = _ARXIV_PDF_TEMPLATE.format(arxiv_id=arxiv_id)
        filename = f"arxiv_{arxiv_id.replace('/', '_')}.pdf"
        return await self._download(url, filename, rate_limit=True)

    async def download_url(self, url: str, filename: str | None = None) -> DownloadResult:
        if filename is None:
            filename = url.rstrip("/").rsplit("/", 1)[-1]
            if not filename.lower().endswith(".pdf"):
                filename += ".pdf"
        return await self._download(url, filename, rate_limit=False)

    async def download_paper(self, paper: Paper) -> DownloadResult:
        if paper.pdf_url:
            paywall = _classify_paywall(paper.pdf_url)
            if paywall:
                return DownloadResult(
                    status="paywall",
                    detail=f"{paywall} (subscription required)",
                    url=paper.pdf_url,
                )
            return await self.download_url(paper.pdf_url)

        if paper.source == "arxiv":
            arxiv_id = _extract_arxiv_id(paper.source_id)
            if arxiv_id:
                return await self.download_arxiv(arxiv_id)

        return DownloadResult(status="no_url", detail="no PDF URL available")

    async def download_papers(self, papers: list[Paper]) -> dict[str, DownloadResult]:
        results: dict[str, DownloadResult] = {}
        for paper in papers:
            key = paper.doi or paper.title[:60]
            results[key] = await self.download_paper(paper)
        return results

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _download(self, url: str, filename: str, *, rate_limit: bool = False) -> DownloadResult:
        filename = _sanitize_filename(filename)
        save_path = self.download_dir / filename

        async with await self._get_client() as client:
            for attempt in range(_MAX_RETRIES + 1):
                if rate_limit:
                    await self._await_rate_limit()

                try:
                    result = await self._stream_download(client, url, save_path)
                    if result.status == "success":
                        return result

                    # Non-retryable HTTP failure — classify and return immediately
                    if result.status not in ("network_error", "unknown"):
                        if _classify_paywall(url) and result.status == "http_error":
                            return DownloadResult(
                                status="paywall",
                                detail=f"{_classify_paywall(url)} (subscription required) — {result.detail}",
                                url=url,
                            )
                        return result

                    # Rate-limit or retryable — backoff
                    if attempt == _MAX_RETRIES:
                        return result
                    delay = _BASE_BACKOFF * (2**attempt) + random.uniform(0, 1.0)
                    logger.warning(
                        "Download failed for %s, retrying in %.1fs (attempt %d/%d)",
                        url, delay, attempt + 1, _MAX_RETRIES,
                    )
                    await asyncio.sleep(delay)

                except _RETRYABLE_ERRORS as exc:
                    if attempt < _MAX_RETRIES:
                        delay = _BASE_BACKOFF * (2**attempt) + random.uniform(0, 1.0)
                        logger.warning(
                            "Download error (%s) for %s, retrying in %.1fs (attempt %d/%d)",
                            type(exc).__name__, url, delay, attempt + 1, _MAX_RETRIES,
                        )
                        await asyncio.sleep(delay)
                        continue
                    return DownloadResult(
                        status="network_error",
                        detail=f"{type(exc).__name__}: {exc}",
                        url=url,
                    )

                except Exception as exc:
                    logger.warning("Download failed for %s", url, exc_info=True)
                    return DownloadResult(
                        status="unknown",
                        detail=f"{type(exc).__name__}: {exc}",
                        url=url,
                    )

        return DownloadResult(status="unknown", detail="download exhausted retries", url=url)

    async def _stream_download(
        self, client: httpx.AsyncClient, url: str, save_path: Path
    ) -> DownloadResult:
        async with client.stream("GET", url) as response:
            if response.status_code == 200:
                content_type = response.headers.get("content-type", "")
                # Guard: if content-type is HTML on a known paywall domain, it's a login wall
                if "text/html" in content_type and _classify_paywall(url):
                    return DownloadResult(
                        status="paywall",
                        detail=f"{_classify_paywall(url)} (subscription required — got login page)",
                        url=url,
                    )

                # Stream to temp file, then validate and rename
                tmp_path = save_path.with_suffix(save_path.suffix + ".tmp")
                try:
                    with open(tmp_path, "wb") as f:
                        async for chunk in response.aiter_bytes(_CHUNK_SIZE):
                            f.write(chunk)
                except Exception:
                    _remove_file(tmp_path)
                    raise

                # Validate PDF magic bytes
                if not _is_valid_pdf(tmp_path):
                    _remove_file(tmp_path)
                    return DownloadResult(
                        status="http_error",
                        detail="response is not a valid PDF file",
                        url=url,
                    )

                # Atomic rename
                os.replace(tmp_path, save_path)
                logger.info("Downloaded: %s -> %s", url, save_path)
                return DownloadResult(path=save_path, status="success", url=url)

            if response.status_code == 404:
                return DownloadResult(
                    status="not_found",
                    detail=f"404 not found",
                    url=url,
                )

            if response.status_code in (401, 403):
                if _classify_paywall(url):
                    return DownloadResult(
                        status="paywall",
                        detail=f"{_classify_paywall(url)} (subscription required)",
                        url=url,
                    )
                return DownloadResult(
                    status="http_error",
                    detail=f"HTTP {response.status_code}: access denied",
                    url=url,
                )

            if response.status_code in _RETRYABLE_STATUSES:
                retry_after = _parse_retry_after(response)
                if retry_after is not None:
                    await asyncio.sleep(retry_after)
                return DownloadResult(
                    status="network_error",
                    detail=f"HTTP {response.status_code} (rate limited)",
                    url=url,
                )

            return DownloadResult(
                status="http_error",
                detail=f"HTTP {response.status_code}",
                url=url,
            )


def _is_valid_pdf(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(_MAGIC_READ_SIZE)
        return head.startswith(_PDF_MAGIC)
    except OSError:
        return False


def _remove_file(path: Path) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _extract_arxiv_id(source_id: str) -> str | None:
    if source_id.startswith("http://arxiv.org/abs/") or source_id.startswith("https://arxiv.org/abs/"):
        return source_id.rsplit("/", 1)[-1]
    if source_id.startswith("oai:arXiv.org:"):
        return source_id[len("oai:arXiv.org:"):]
    stripped = source_id.rstrip("/")
    if stripped.startswith("arxiv:") or stripped.startswith("arXiv:"):
        return stripped.split(":", 1)[1]
    parts = stripped.split("/")
    if len(parts) == 2 and parts[0].isdigit() and parts[1].replace(".", "").isdigit():
        return stripped
    return None
