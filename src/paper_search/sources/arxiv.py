from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from xml.etree import ElementTree

import httpx

from ..models import Author, Paper
from .base import Source

logger = logging.getLogger(__name__)

_ARXIV_API = "https://export.arxiv.org/api/query"
_ATOM_NS = "http://www.w3.org/2005/Atom"
_ARXIV_NS = "http://arxiv.org/schemas/atom"

_USER_AGENT = "Mozilla/5.0 (compatible; paper-search/0.1; +mailto:user@example.com)"


def _build_query(query: str, author: str | None, category: str | None = None) -> str:
    parts: list[str] = []
    if author:
        parts.append(f'au:"{author}"')
    if query:
        parts.append(f"all:{query}")
    if category:
        parts.append(f"cat:{category}")
    return " AND ".join(parts) if parts else "all:*"


class ArxivSource(Source):
    name = "arxiv"

    async def search(
        self,
        query: str,
        max_results: int = 50,
        author: str | None = None,
        year: int | None = None,
        category: str | None = None,
        affiliation: str | None = None,
    ) -> list[Paper]:
        search_query = _build_query(query, author, category)
        params = {
            "search_query": search_query,
            "start": 0,
            "max_results": min(max_results, 100),
        }

        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": _USER_AGENT},
                timeout=30.0,
            ) as client:
                response = await client.get(_ARXIV_API, params=params)
                response.raise_for_status()

                papers = _parse_atom(response.text)
                if affiliation:
                    papers = _filter_by_affiliation(papers, affiliation)
                if year is not None:
                    papers = [p for p in papers if p.year == year]
                logger.debug("arXiv: %d results for %r", len(papers), query)
                return papers

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                logger.warning("arXiv rate limited (429). Wait and retry later.")
            else:
                logger.warning("arXiv HTTP %d for query %r", exc.response.status_code, query)
            return []
        except Exception:
            logger.warning("arXiv search failed for query %r", query, exc_info=True)
            return []


def _parse_atom(xml_text: str) -> list[Paper]:
    root = ElementTree.fromstring(xml_text)
    papers: list[Paper] = []

    for entry in root.findall(f"{{{_ATOM_NS}}}entry"):
        paper = _entry_to_paper(entry)
        if paper is not None:
            papers.append(paper)

    return papers


def _entry_to_paper(entry: ElementTree.Element) -> Paper | None:
    title_el = entry.find(f"{{{_ATOM_NS}}}title")
    if title_el is None or not title_el.text:
        return None

    title = title_el.text.strip()
    source_id = _text(entry, f"{{{_ATOM_NS}}}id") or ""

    authors = _parse_authors(entry)
    published = _parse_datetime(entry, f"{{{_ATOM_NS}}}published")
    summary = _text(entry, f"{{{_ATOM_NS}}}summary")
    doi = _extract_doi(entry)
    pdf_url = _extract_pdf_url(entry)
    journal_ref = _text(entry, f"{{{_ARXIV_NS}}}journal_ref")
    primary_cat = _text(entry, f"{{{_ARXIV_NS}}}primary_category", "term")
    categories = [c.get("term", "") for c in entry.findall(f"{{{_ATOM_NS}}}category") if c.get("term")]

    return Paper(
        title=title,
        authors=authors,
        year=published.year if published else None,
        publication_date=published.date() if published else None,
        doi=doi,
        abstract=summary.strip() if summary else None,
        source="arxiv",
        source_id=source_id,
        pdf_url=pdf_url,
        venue=journal_ref.strip() if journal_ref else None,
        citation_count=None,
        categories=categories,
    )


def _parse_authors(entry: ElementTree.Element) -> list[Author]:
    authors: list[Author] = []
    for author_el in entry.findall(f"{{{_ATOM_NS}}}author"):
        name_el = author_el.find(f"{{{_ATOM_NS}}}name")
        if name_el is not None and name_el.text:
            affiliation_el = author_el.find(f"{{{_ARXIV_NS}}}affiliation")
            authors.append(
                Author(
                    name=name_el.text.strip(),
                    affiliation=affiliation_el.text.strip() if affiliation_el is not None and affiliation_el.text else None,
                )
            )
    return authors


def _extract_doi(entry: ElementTree.Element) -> str | None:
    for link in entry.findall(f"{{{_ATOM_NS}}}link"):
        title = link.get("title", "")
        href = link.get("href", "")
        if title == "doi" and href:
            return href
        if "doi.org" in href:
            return href
    return None


def _extract_pdf_url(entry: ElementTree.Element) -> str | None:
    for link in entry.findall(f"{{{_ATOM_NS}}}link"):
        if link.get("title") == "pdf":
            return link.get("href")
    return None


def _text(element: ElementTree.Element, tag: str, attr: str | None = None) -> str | None:
    el = element.find(tag)
    if el is None:
        return None
    if attr:
        return el.get(attr)
    return el.text


def _parse_datetime(element: ElementTree.Element, tag: str) -> datetime | None:
    text = _text(element, tag)
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _filter_by_affiliation(papers: list[Paper], affiliation: str) -> list[Paper]:
    affiliation_lower = affiliation.lower()
    filtered: list[Paper] = []
    for paper in papers:
        for author in paper.authors:
            if author.affiliation and affiliation_lower in author.affiliation.lower():
                filtered.append(paper)
                break
    return filtered
