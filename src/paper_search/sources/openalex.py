from __future__ import annotations

import asyncio
from datetime import date
import logging
import os
from typing import Any

import pyalex
from pyalex import Authors as PyAlexAuthors
from pyalex import Works

from ..models import Author, Paper
from .base import Source


pyalex.config.email = "user@example.com"
pyalex.config.api_key = os.getenv("PYALEX_API_KEY")

logger = logging.getLogger(__name__)

_AUTHOR_CANDIDATE_LIMIT = 5


class OpenAlexSource(Source):
    name = "openalex"

    async def search(self, query: str, max_results: int = 50, author: str | None = None, year: int | None = None, category: str | None = None, affiliation: str | None = None) -> list[Paper]:
        if max_results <= 0:
            return []

        try:
            return await asyncio.to_thread(_run_sync_search, query, max_results, author, year, category, affiliation)
        except Exception as exc:
            logger.warning("OpenAlex search failed for query %r: %s", query, exc)
            return []


def _run_sync_search(query: str, max_results: int, author: str | None, year: int | None, category: str | None, affiliation: str | None) -> list[Paper]:
    author_ids: list[str] = []
    if author:
        author_ids = _resolve_author_ids(author, affiliation=affiliation)
        if not author_ids:
            logger.debug("OpenAlex: no author ID found for %r (affiliation=%r)", author, affiliation)
            return []

    papers: list[Paper] = []

    if author_ids:
        papers = _search_by_author_ids(query, max_results, year, author_ids)
    else:
        papers = _search_by_text(query, max_results, year)

    if category:
        papers = _filter_by_category(papers, category)

    return papers


def _search_by_author_ids(query: str, max_results: int, year: int | None, author_ids: list[str]) -> list[Paper]:
    seen_ids: set[str] = set()
    papers: list[Paper] = []

    ids_to_search = author_ids[:_AUTHOR_CANDIDATE_LIMIT]
    per_author = max(max_results // len(ids_to_search), 3)

    for author_id in ids_to_search:
        try:
            w = Works().filter(authorships={"author": {"id": author_id}})
            if year is not None:
                w = w.filter(publication_year=year)

            if query and query.strip():
                search_query = _build_search_query(query)
                w = w.search(search_query)

            works = w.sort(cited_by_count="desc").get(per_page=min(per_author, 200))
        except Exception as exc:
            logger.debug("OpenAlex author filter failed for id %r: %s", author_id, exc)
            continue

        for work in works:
            source_id = _optional_str(work.get("id"))
            if not source_id or source_id in seen_ids:
                continue
            paper = _work_to_paper(work)
            if paper is not None:
                seen_ids.add(source_id)
                papers.append(paper)

    return papers[:max_results]


def _search_by_text(query: str, max_results: int, year: int | None) -> list[Paper]:
    search_query = _build_search_query(query) if query else ""
    if not search_query:
        return []

    w = Works().search(search_query)
    if year is not None:
        w = w.filter(publication_year=year)

    per_page = min(max_results, 200)
    works = w.get(per_page=per_page)

    if not works and '"' in search_query:
        fallback = " AND ".join(search_query.strip('"').split())
        w = Works().search(fallback)
        if year is not None:
            w = w.filter(publication_year=year)
        works = w.get(per_page=per_page)

    papers: list[Paper] = []
    for work in works:
        paper = _work_to_paper(work)
        if paper is not None:
            papers.append(paper)

    return papers


def _resolve_author_ids(name: str, affiliation: str | None = None) -> list[str]:
    try:
        results = PyAlexAuthors().search(name).get(per_page=25)
    except Exception as exc:
        logger.warning("OpenAlex author search failed for %r: %s", name, exc)
        return []

    candidates: list[tuple[str, int]] = []
    name_lower = name.lower()
    affiliation_lower = affiliation.lower() if affiliation else None

    for author in results:
        display_name = _optional_str(author.get("display_name"))
        if not display_name:
            continue
        if name_lower not in display_name.lower():
            continue

        if affiliation_lower:
            last_insts = author.get("last_known_institutions") or []
            if not isinstance(last_insts, list):
                continue
            matched = any(
                isinstance(inst, dict) and affiliation_lower in (_optional_str(inst.get("display_name")) or "").lower()
                for inst in last_insts
            )
            if not matched:
                continue

        author_id = _optional_str(author.get("id"))
        if not author_id:
            continue
        works_count = _optional_int(author.get("works_count")) or 0
        candidates.append((author_id, works_count))

    candidates.sort(key=lambda x: x[1], reverse=True)
    return [cid for cid, _ in candidates]


def _build_search_query(query: str) -> str:
    if '"' in query or len(query.split()) <= 1:
        return query
    return f'"{query}"'


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


def _filter_by_category(papers: list[Paper], category: str) -> list[Paper]:
    category_lower = category.lower()
    filtered: list[Paper] = []
    for paper in papers:
        for cat in paper.categories:
            if category_lower in cat.lower():
                filtered.append(paper)
                break
    return filtered


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
