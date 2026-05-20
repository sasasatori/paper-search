from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ..models import Author, Paper
from .base import Source


logger = logging.getLogger(__name__)


class DBLPSource(Source):
    name = "dblp"

    async def search(
        self,
        query: str,
        max_results: int = 50,
        author: str | None = None,
        year: int | None = None,
    ) -> list[Paper]:
        q = _build_dblp_query(query, author)
        params = {
            "q": q,
            "format": "json",
            "h": min(max_results, 1000),
            "f": 0,
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://dblp.org/search/publ/api",
                    params=params,
                )
                response.raise_for_status()
                await asyncio.sleep(1.5)
                data = response.json()

            hits = data["result"]["hits"].get("hit", [])
            if isinstance(hits, dict):
                hits = [hits]

            papers: list[Paper] = []
            for hit in hits:
                info = hit["info"]
                paper_year = self._parse_year(info)
                if year is not None and paper_year != year:
                    continue
                papers.append(
                    Paper(
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
                )
            return papers
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            logger.warning("DBLP search failed for query %r: %s", query, exc)
            return []

    def _parse_authors(self, info: dict[str, Any]) -> list[Author]:
        raw_authors = info.get("authors", {}).get("author", [])
        if isinstance(raw_authors, dict):
            raw_authors = [raw_authors]

        authors: list[Author] = []
        for author in raw_authors:
            if isinstance(author, dict):
                name = author.get("text")
                if name:
                    authors.append(Author(name=name, orcid=author.get("@pid")))
        return authors

    def _parse_year(self, info: dict[str, Any]) -> int | None:
        year = info.get("year")
        if year is None:
            return None
        return int(year)


def _build_dblp_query(query: str, author: str | None) -> str:
    if author:
        author_part = author.replace(" ", "_")
        return f"author:{author_part}: {query}"
    return query
