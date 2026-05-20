from __future__ import annotations

import asyncio
import logging

import arxiv

arxiv._USER_AGENT = "paper-search/0.1"

from ..models import Author, Paper
from .base import Source


logger = logging.getLogger(__name__)


class ArxivSource(Source):
    name = "arxiv"

    async def search(
        self,
        query: str,
        max_results: int = 50,
    ) -> list[Paper]:
        client = arxiv.Client(page_size=100, delay_seconds=3.0, num_retries=3)
        search = arxiv.Search(query=query, max_results=min(max_results, 100))

        try:
            results = await asyncio.to_thread(lambda: list(client.results(search)))
            return [
                Paper(
                    title=result.title,
                    authors=[Author(name=author.name) for author in result.authors],
                    year=result.published.year,
                    publication_date=result.published.date(),
                    doi=result.doi,
                    abstract=result.summary,
                    source="arxiv",
                    source_id=result.entry_id,
                    pdf_url=result.pdf_url,
                    venue=result.journal_ref,
                    citation_count=None,
                    categories=result.categories,
                )
                for result in results
            ]
        except Exception:
            logger.warning("arXiv search failed for query %r", query, exc_info=True)
            return []
