"""
Paper Search MCP Server.

Exposes academic paper search, download, and deduplication tools via MCP.
Reuses the battle-tested sources/, download.py, and dedup.py modules.

Tools:
    search_papers  — parallel search across OpenAlex + DBLP with dedup
    download_paper — download PDF from arXiv ID or direct URL
    deduplicate    — cross-source dedup using DOI + Jaccard title similarity
    get_paper_by_doi — lookup paper metadata by DOI via CrossRef
"""

from __future__ import annotations

import asyncio
import json
import logging

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from .dedup import deduplicate as _dedup
from .download import DownloadResult
from .download import Downloader as _Downloader
from .models import Paper
from .sources import DBLPSource, OpenAlexSource

logger = logging.getLogger(__name__)

mcp = FastMCP("paper-search", json_response=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _paper_to_dict(paper: Paper) -> dict:
    return {
        "title": paper.title,
        "authors": [
            {
                "name": a.name,
                "affiliation": a.affiliation,
                "orcid": a.orcid,
            }
            for a in paper.authors
        ],
        "year": paper.year,
        "publication_date": str(paper.publication_date) if paper.publication_date else None,
        "doi": paper.doi,
        "abstract": paper.abstract,
        "source": paper.source,
        "source_id": paper.source_id,
        "pdf_url": paper.pdf_url,
        "venue": paper.venue,
        "citation_count": paper.citation_count,
        "categories": paper.categories,
    }


def _download_result_to_dict(r: DownloadResult) -> dict:
    return {
        "status": r.status,
        "detail": r.detail,
        "url": r.url,
        "path": str(r.path) if r.path else None,
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def search_papers(
    query: str,
    max_results: int = 10,
    year: int | None = None,
    author: str | None = None,
    category: str | None = None,
    affiliation: str | None = None,
    dedup: bool = True,
) -> str:
    """Search academic papers across OpenAlex and DBLP with parallel queries.

    Both sources are queried simultaneously via asyncio.gather.
    Results are merged and optionally deduplicated using DOI exact match
    followed by Jaccard title similarity (threshold 0.7).

    Args:
        query: Search keywords (supports phrase search, e.g. "graph neural network")
        max_results: Maximum results per source (1-100, default 10)
        year: Optional publication year filter
        author: Optional author name filter
        category: Optional domain/category filter (e.g. "cs.AI", "VLSI")
        affiliation: Optional institution keyword filter (OpenAlex only)
        dedup: Enable cross-source deduplication (default true)
    """
    sources = [OpenAlexSource(), DBLPSource()]
    tasks = [
        s.search(query, max_results, author=author, year=year, category=category, affiliation=affiliation)
        for s in sources
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    papers: list[Paper] = []
    errors: list[str] = []
    stats: dict[str, int] = {}

    for source, result in zip(sources, results):
        if isinstance(result, Exception):
            errors.append(f"{source.name}: {result}")
        elif isinstance(result, list):
            stats[source.name] = len(result)
            papers.extend(result)

    before_dedup = len(papers)
    if dedup and len(papers) > 1:
        papers = _dedup(papers)

    output = {
        "total": len(papers),
        "dedup_removed": before_dedup - len(papers) if dedup else 0,
        "per_source": stats,
        "errors": errors,
        "papers": [_paper_to_dict(p) for p in papers],
    }
    return json.dumps(output, ensure_ascii=False, indent=2)


@mcp.tool()
async def download_paper(
    arxiv_id: str | None = None,
    url: str | None = None,
    download_dir: str = "./downloads",
) -> str:
    """Download a paper PDF from arXiv by ID, or from any direct URL.

    Supports:
    - arXiv ID: e.g. "2106.15928" → downloads from arxiv.org/pdf/
    - Direct URL: any publicly accessible PDF URL

    Paywall detection: URLs to ACM, IEEE, Springer, etc. are identified
    and reported without attempting download.

    Args:
        arxiv_id: arXiv paper ID (e.g. "1706.03762")
        url: Direct PDF URL to download
        download_dir: Directory to save the PDF (default "./downloads")
    """
    if not arxiv_id and not url:
        raise ToolError("Specify either arxiv_id or url.")

    downloader = _Downloader(download_dir=download_dir)

    if arxiv_id:
        result = await downloader.download_arxiv(arxiv_id)
    else:
        result = await downloader.download_url(url)  # type: ignore[arg-type]

    output = _download_result_to_dict(result)
    if result.status != "success":
        output["error"] = True

    return json.dumps(output, ensure_ascii=False, indent=2)


@mcp.tool()
def deduplicate_papers(papers_json: str) -> str:
    """Deduplicate a list of papers using DOI + Jaccard title similarity.

    Pass 1: Exact DOI match (case-insensitive).
    Pass 2: Jaccard similarity ≥ 0.7 on tokenized titles, sliding window + Union-Find.

    Args:
        papers_json: JSON string of paper objects. Each must have at least
                     "title" and "doi" fields. Unknown fields are ignored.
    """
    try:
        raw = json.loads(papers_json)
    except json.JSONDecodeError as e:
        raise ToolError(f"Invalid JSON: {e}") from e

    if not isinstance(raw, list):
        raise ToolError("Input must be a JSON array of paper objects.")

    papers: list[Paper] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        papers.append(
            Paper(
                title=item.get("title", ""),
                authors=[],
                source="unknown",
                source_id=item.get("source_id", item.get("doi", "")),
                doi=item.get("doi"),
                year=item.get("year"),
                abstract=item.get("abstract"),
                pdf_url=item.get("pdf_url"),
                venue=item.get("venue"),
                citation_count=item.get("citation_count"),
                categories=item.get("categories", []),
            )
        )

    if len(papers) <= 1:
        return json.dumps([_paper_to_dict(p) for p in papers], ensure_ascii=False, indent=2)

    merged = _dedup(papers)
    return json.dumps([_paper_to_dict(p) for p in merged], ensure_ascii=False, indent=2)


@mcp.tool()
async def get_paper_by_doi(doi: str) -> str:
    """Look up paper metadata by DOI using the CrossRef API.

    Args:
        doi: Digital Object Identifier (e.g. "10.1038/nature12373")
    """
    import httpx

    url = f"https://api.crossref.org/works/{doi}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return json.dumps({"error": f"DOI not found: {doi}", "doi": doi})
        raise ToolError(f"CrossRef API error: HTTP {e.response.status_code}") from e
    except Exception as e:
        raise ToolError(f"CrossRef request failed: {e}") from e

    msg = data.get("message", {})
    authors = [
        {"name": f"{a.get('given', '')} {a.get('family', '')}".strip()}
        for a in msg.get("author", [])
    ]
    published = msg.get("published-print", {}).get("date-parts", [[None]])[0]
    year = published[0] if published else msg.get("created", {}).get("date-parts", [[None]])[0][0]

    return json.dumps(
        {
            "doi": doi,
            "title": msg.get("title", [""])[0],
            "authors": authors,
            "year": year,
            "venue": msg.get("container-title", [""])[0] or None,
            "publisher": msg.get("publisher"),
            "abstract": msg.get("abstract"),
            "url": msg.get("URL"),
        },
        ensure_ascii=False,
        indent=2,
    )


def main() -> None:
    """Entry point for `python -m paper_search.mcp_server`."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
