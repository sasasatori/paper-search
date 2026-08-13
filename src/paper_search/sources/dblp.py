from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any
from xml.etree import ElementTree

import httpx

from ..models import Author, Paper
from .base import Source


logger = logging.getLogger(__name__)

_PUBL_API = "https://dblp.org/search/publ/api"
_AUTHOR_API = "https://dblp.org/search/author/api"
_PERSON_XML = "https://dblp.org/pid/{pid}.xml"
_USER_AGENT = "paper-search/0.2 (mailto:user@example.com)"

_MAX_AUTHOR_CANDIDATES = 4
_DISAMBIG_SUFFIX = re.compile(r"\s+\d{4}$")

_MAX_RETRIES = 3
_BASE_BACKOFF = 2.0
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_RETRYABLE_ERRORS = (
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
)


async def _get_with_retry(client: httpx.AsyncClient, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in _RETRYABLE_STATUSES and attempt < _MAX_RETRIES:
                retry_after = exc.response.headers.get("Retry-After", "").strip()
                delay = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else _BASE_BACKOFF * (2 ** attempt)
                await asyncio.sleep(delay + random.uniform(0, 0.5))
                continue
            raise
        except _RETRYABLE_ERRORS:
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_BASE_BACKOFF * (2 ** attempt) + random.uniform(0, 0.5))
                continue
            raise
    raise RuntimeError("unreachable")


def _matches_category(paper: Paper, category: str) -> bool:
    if not category:
        return True
    category_lower = category.lower()
    if paper.venue and category_lower in paper.venue.lower():
        return True
    for cat_name in paper.categories:
        if category_lower in cat_name.lower():
            return True
    return False


def _matches_venue(paper: Paper, venue: str | None) -> bool:
    if not venue:
        return True
    return venue.lower() in (paper.venue or "").lower()


def _base_name(name: str) -> str:
    """Strip DBLP disambiguation suffix: 'Zhenhua Zhu 0002' -> 'Zhenhua Zhu'."""
    return _DISAMBIG_SUFFIX.sub("", name).strip().lower()


def _is_author_only_query(query: str, author: str) -> bool:
    """True when the free-text query adds nothing beyond the author name."""
    if not query or not query.strip():
        return True
    return query.strip().lower() == author.strip().lower()


class DBLPSource(Source):
    name = "dblp"

    def __init__(self) -> None:
        # Set whenever a request fails; lets callers surface degraded states
        # instead of silently treating them as "zero results".
        self.last_error: str | None = None
        # DBLP profiles merged into the last author-centric search, so callers
        # can warn about homonym contamination.
        self.last_candidates: list[dict[str, Any]] = []

    async def search(
        self,
        query: str,
        max_results: int = 50,
        author: str | None = None,
        year: int | None = None,
        category: str | None = None,
        affiliation: str | None = None,
        venue: str | None = None,
    ) -> list[Paper]:
        self.last_error = None

        # Author-centric path: `author:Name:` query syntax does NOT match
        # disambiguated profiles ("Name 0002"), so resolve PIDs and pull the
        # full publication records instead.
        if author and _is_author_only_query(query, author):
            return await self._search_by_author(author, max_results, year, category, venue)

        q = _build_dblp_query(query, author, venue)
        params = {
            "q": q,
            "format": "json",
            "h": min(max_results, 1000),
            "f": 0,
        }

        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": _USER_AGENT}, timeout=30.0
            ) as client:
                response = await _get_with_retry(client, _PUBL_API, params=params)
                data = response.json()
        except httpx.HTTPStatusError as exc:
            self.last_error = f"HTTP {exc.response.status_code}"
            logger.warning("DBLP search failed for query %r: HTTP %s", query, exc.response.status_code)
            return []
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            self.last_error = str(exc)
            logger.warning("DBLP search failed for query %r: %s", query, exc)
            return []

        hits = data["result"]["hits"].get("hit", [])
        if isinstance(hits, dict):
            hits = [hits]

        papers: list[Paper] = []
        for hit in hits:
            info = hit["info"]
            paper_year = self._parse_year(info)
            if year is not None and paper_year != year:
                continue
            paper = Paper(
                title=info["title"],
                authors=self._parse_authors(info),
                year=paper_year,
                publication_date=None,
                doi=info.get("doi"),
                abstract=None,
                source="dblp",
                source_id=info["key"],
                pdf_url=None,
                venue=info.get("venue"),
                citation_count=None,
                categories=[],
            )
            if not _matches_venue(paper, venue):
                continue
            if category and not _matches_category(paper, category):
                continue
            papers.append(paper)
        return papers

    # ------------------------------------------------------------------
    # Author-centric workflow (PID resolution + full record fetch)
    # ------------------------------------------------------------------

    async def resolve_authors(self, name: str, limit: int = 10) -> list[dict[str, Any]]:
        """Resolve a name to DBLP author candidates with PIDs and affiliations."""
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": _USER_AGENT}, timeout=30.0
            ) as client:
                response = await _get_with_retry(
                    client, _AUTHOR_API, params={"q": name, "format": "json", "h": limit}
                )
                data = response.json()
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            self.last_error = f"author API: {exc}"
            logger.warning("DBLP author search failed for %r: %s", name, exc)
            return []

        hits = data.get("result", {}).get("hits", {}).get("hit", [])
        if isinstance(hits, dict):
            hits = [hits]

        candidates: list[dict[str, Any]] = []
        for hit in hits:
            info = hit.get("info", {})
            url = info.get("url") or ""
            pid = url.rstrip("/").rsplit("/", 2)
            pid_str = "/".join(pid[-2:]) if len(pid) >= 2 else ""
            if not pid_str:
                continue
            notes = info.get("notes", {}).get("note", [])
            if isinstance(notes, dict):
                notes = [notes]
            affiliations = [n.get("text", "") for n in notes if isinstance(n, dict) and n.get("text")]
            candidates.append(
                {"name": info.get("author", ""), "pid": pid_str, "affiliations": affiliations}
            )
        return candidates

    async def author_papers(
        self,
        pid: str,
        year: int | None = None,
        venue: str | None = None,
        max_results: int = 500,
    ) -> list[Paper]:
        """Fetch the full publication list for a DBLP person PID (e.g. '07/4259-2')."""
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": _USER_AGENT}, timeout=60.0
            ) as client:
                response = await _get_with_retry(client, _PERSON_XML.format(pid=pid))
        except httpx.HTTPStatusError as exc:
            self.last_error = f"pid {pid}: HTTP {exc.response.status_code}"
            logger.warning("DBLP person fetch failed for pid %r: HTTP %s", pid, exc.response.status_code)
            return []
        except httpx.HTTPError as exc:
            self.last_error = f"pid {pid}: {exc}"
            logger.warning("DBLP person fetch failed for pid %r: %s", pid, exc)
            return []

        papers = parse_person_xml(response.text)
        filtered = [
            p
            for p in papers
            if (year is None or p.year == year) and _matches_venue(p, venue)
        ]
        filtered.sort(key=lambda p: (p.year or 0), reverse=True)
        return filtered[:max_results]

    async def _search_by_author(
        self,
        author: str,
        max_results: int,
        year: int | None,
        category: str | None,
        venue: str | None,
    ) -> list[Paper]:
        candidates = await self.resolve_authors(author)
        if not candidates:
            return []

        # Prefer candidates whose base name matches exactly (drops near-miss
        # spellings like "Zhenhuan Zhu"); keep homonyms (0001/0002/...).
        exact = [c for c in candidates if _base_name(c["name"]) == _base_name(author)]
        selected = (exact or candidates)[:_MAX_AUTHOR_CANDIDATES]
        self.last_candidates = selected

        papers: list[Paper] = []
        seen_keys: set[str] = set()
        for i, cand in enumerate(selected):
            if i > 0:
                await asyncio.sleep(1.0)  # politeness towards dblp.org
            for p in await self.author_papers(cand["pid"], year=year, venue=venue):
                if p.source_id in seen_keys:
                    continue
                seen_keys.add(p.source_id)
                if category and not _matches_category(p, category):
                    continue
                papers.append(p)

        papers.sort(key=lambda p: (p.year or 0), reverse=True)
        return papers[:max_results]

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_authors(self, info: dict[str, Any]) -> list[Author]:
        raw_authors = info.get("authors", {}).get("author", [])
        if isinstance(raw_authors, dict):
            raw_authors = [raw_authors]

        authors: list[Author] = []
        for author in raw_authors:
            if isinstance(author, dict):
                name = author.get("text")
                if name:
                    authors.append(Author(name=name, dblp_pid=author.get("@pid")))
        return authors

    def _parse_year(self, info: dict[str, Any]) -> int | None:
        year = info.get("year")
        if year is None:
            return None
        return int(year)


def _build_dblp_query(query: str, author: str | None, venue: str | None = None) -> str:
    parts: list[str] = []
    if author and author.strip():
        author_part = author.strip().replace(" ", "_")
        parts.append(f"author:{author_part}:")
    # Avoid echoing the author name as free text: papers by an author do not
    # contain their own name in title/venue fields, so the free-text clause
    # would poison relevance ranking and filter out nearly everything.
    if query and query.strip() and not (author and _is_author_only_query(query, author)):
        parts.append(query.strip())
    if venue and venue.strip():
        parts.append(f"venue:{venue.strip()}")
    return " ".join(parts)


def parse_person_xml(xml_text: str) -> list[Paper]:
    """Parse a DBLP person page (pid XML) into Paper records."""
    root = ElementTree.fromstring(xml_text)
    papers: list[Paper] = []

    for r in root.findall("r"):
        if not len(r):
            continue
        rec = r[0]
        if rec.tag not in ("article", "inproceedings", "proceedings", "incollection", "book", "phdthesis", "mastersthesis"):
            continue

        title_el = rec.find("title")
        title = "".join(title_el.itertext()).strip() if title_el is not None else ""

        authors: list[Author] = []
        for a in rec.findall("author"):
            name = (a.text or "").strip()
            if name:
                authors.append(Author(name=name, dblp_pid=a.get("pid") or None))

        year_text = rec.findtext("year")
        try:
            year = int(year_text) if year_text else None
        except ValueError:
            year = None

        venue = rec.findtext("booktitle") or rec.findtext("journal")

        doi: str | None = None
        for ee in rec.findall("ee"):
            href = (ee.text or "").strip()
            if "doi.org/" in href:
                doi = href.split("doi.org/", 1)[1]
                break

        papers.append(
            Paper(
                title=title,
                authors=authors,
                year=year,
                publication_date=None,
                doi=doi,
                abstract=None,
                source="dblp",
                source_id=rec.get("key") or "",
                pdf_url=None,
                venue=venue,
                citation_count=None,
                categories=[],
            )
        )

    return papers
