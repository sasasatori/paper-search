from __future__ import annotations

import asyncio
from datetime import date
import logging
import os
from typing import Any

import pyalex
from pyalex import Works

from ..models import Author, Paper
from .base import Source


pyalex.config.email = "user@example.com"
pyalex.config.api_key = os.getenv("PYALEX_API_KEY")

logger = logging.getLogger(__name__)


class OpenAlexSource(Source):
    name = "openalex"

    async def search(self, query: str, max_results: int = 50) -> list[Paper]:
        if max_results <= 0:
            return []

        try:
            return await asyncio.to_thread(_run_sync_search, query, max_results)
        except Exception as exc:
            logger.warning("OpenAlex search failed for query %r: %s", query, exc)
            return []


def _run_sync_search(query: str, max_results: int) -> list[Paper]:
    works = Works().search(query).get(per_page=min(max_results, 200))
    papers: list[Paper] = []

    for work in works:
        paper = _work_to_paper(work)
        if paper is not None:
            papers.append(paper)

    return papers


def _work_to_paper(work: dict[str, Any]) -> Paper | None:
    title = _optional_str(work.get("title"))
    source_id = _optional_str(work.get("id"))

    if not title or not source_id:
        return None

    primary_location = _dict_or_empty(work.get("primary_location"))
    best_oa_location = _dict_or_empty(work.get("best_oa_location"))
    primary_source = _dict_or_empty(primary_location.get("source"))

    return Paper(
        title=title,
        authors=_extract_authors(work.get("authorships")),
        year=_optional_int(work.get("publication_year")),
        publication_date=_parse_date(work.get("publication_date")),
        doi=_optional_str(work.get("doi")),
        abstract=_optional_str(work.get("abstract")),
        source="openalex",
        source_id=source_id,
        pdf_url=_extract_pdf_url(best_oa_location, primary_location),
        venue=_optional_str(primary_source.get("display_name")),
        citation_count=_optional_int(work.get("cited_by_count")),
        categories=_extract_categories(work.get("topics")),
    )


def _extract_authors(authorships: object) -> list[Author]:
    if not isinstance(authorships, list):
        return []

    authors: list[Author] = []
    for authorship in authorships:
        if not isinstance(authorship, dict):
            continue

        author_data = _dict_or_empty(authorship.get("author"))
        name = _optional_str(author_data.get("display_name"))
        if not name:
            continue

        authors.append(
            Author(
                name=name,
                orcid=_optional_str(author_data.get("orcid")),
                affiliation=_extract_affiliation(authorship.get("institutions")),
            )
        )

    return authors


def _extract_affiliation(institutions: object) -> str | None:
    if not isinstance(institutions, list) or not institutions:
        return None

    first_institution = institutions[0]
    if not isinstance(first_institution, dict):
        return None
    return _optional_str(first_institution.get("display_name"))


def _extract_pdf_url(
    best_oa_location: dict[str, Any],
    primary_location: dict[str, Any],
) -> str | None:
    return _optional_str(best_oa_location.get("pdf_url")) or _optional_str(
        primary_location.get("pdf_url")
    )


def _extract_categories(topics: object) -> list[str]:
    if not isinstance(topics, list):
        return []

    categories: list[str] = []
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        display_name = _optional_str(topic.get("display_name"))
        if display_name:
            categories.append(display_name)
    return categories


def _parse_date(value: object) -> date | None:
    date_string = _optional_str(value)
    if date_string is None:
        return None


    try:
        return date.fromisoformat(date_string)
    except ValueError:
        return None


def _dict_or_empty(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None
