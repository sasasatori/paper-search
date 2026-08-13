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
from datetime import date

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from .dedup import deduplicate as _dedup
from .download import DownloadResult
from .download import Downloader as _Downloader
from .models import Paper
from .sources import DBLPSource, OpenAlexSource
from .sources.dblp import _base_name

logger = logging.getLogger(__name__)

mcp = FastMCP("paper-search", json_response=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _paper_to_dict(paper: Paper, compact: bool = False) -> dict:
    if compact:
        names = [a.name for a in paper.authors]
        shown = names[:3]
        author_str = ", ".join(shown)
        if len(names) > 3:
            author_str += f" et al. (+{len(names) - 3})"
        return {
            "title": paper.title,
            "authors": author_str,
            "venue": paper.venue,
            "year": paper.year,
            "doi": paper.doi,
            "source": paper.source,
            "citation_count": paper.citation_count,
            "pdf_url": paper.pdf_url,
        }
    return {
        "title": paper.title,
        "authors": [
            {
                "name": a.name,
                "affiliation": a.affiliation,
                "orcid": a.orcid,
                "dblp_pid": a.dblp_pid,
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
    query: str = "",
    max_results: int = 10,
    year: int | None = None,
    author: str | None = None,
    category: str | None = None,
    affiliation: str | None = None,
    venue: str | None = None,
    dedup: bool = True,
    compact: bool = True,
) -> str:
    """Search academic papers across OpenAlex and DBLP with parallel queries.

    Both sources are queried simultaneously via asyncio.gather.
    Results are merged and optionally deduplicated using DOI exact match
    followed by Jaccard title similarity (threshold 0.7).

    When `author` is given and `query` is empty or just repeats the author
    name, DBLP switches to author-centric mode: it resolves the author's
    person PID(s) and returns their full publication list (filtered by
    year/venue). This is far more reliable than keyword search for
    "all papers by person X" queries.

    Note: very recent conferences (past ~3-6 months) may not be indexed yet
    on DBLP/OpenAlex. If results look incomplete for a recent venue+year,
    cross-check the conference's official program page.

    Args:
        query: Search keywords (supports phrase search, e.g. "graph neural network").
               Optional when `author` is provided.
        max_results: Maximum results per source (1-100, default 10)
        year: Optional publication year filter
        author: Optional author name filter
        category: Optional domain/category filter (e.g. "cs.AI", "VLSI")
        affiliation: Optional institution keyword filter (OpenAlex only)
        venue: Optional venue filter (e.g. "HPCA", "ISCA", "NeurIPS"). DBLP:
               native venue syntax; OpenAlex: matched against source name.
        dedup: Enable cross-source deduplication (default true)
        compact: Return slim paper records (default true). Set false for full
                 author lists with affiliations/ORCIDs/abstracts.
    """
    sources = [OpenAlexSource(), DBLPSource()]
    tasks = [
        s.search(query, max_results, author=author, year=year, category=category, affiliation=affiliation, venue=venue)
        for s in sources
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    papers: list[Paper] = []
    errors: list[str] = []
    warnings: list[str] = []
    stats: dict[str, int] = {}

    for source, result in zip(sources, results):
        if isinstance(result, Exception):
            errors.append(f"{source.name}: {result}")
        elif isinstance(result, list):
            stats[source.name] = len(result)
            papers.extend(result)
            last_error = getattr(source, "last_error", None)
            if last_error:
                errors.append(f"{source.name}: {last_error}")
            merged = getattr(source, "last_candidates", [])
            if len(merged) > 1:
                names = ", ".join(f"{c['name']} ({c['pid']})" for c in merged)
                warnings.append(
                    f"dblp: merged papers from {len(merged)} same-name profiles: "
                    f"{names}. Results may include namesakes — use "
                    "get_author_papers with dblp_pid to restrict to one identity."
                )

    before_dedup = len(papers)
    if dedup and len(papers) > 1:
        papers = _dedup(papers)

    if not papers:
        if year is not None and year >= date.today().year - 1:
            warnings.append(
                f"Zero results for year={year}. Very recent publications "
                "(past ~3-6 months) may not be indexed on DBLP/OpenAlex yet — "
                "check the venue's official program page."
            )
        if author:
            warnings.append(
                f"Zero results for author {author!r}. Name disambiguation may be "
                "hiding results — try get_author_papers to inspect candidate "
                "identities (DBLP PIDs + affiliations)."
            )

    output = {
        "total": len(papers),
        "dedup_removed": before_dedup - len(papers) if dedup else 0,
        "per_source": stats,
        "errors": errors,
        "warnings": warnings,
        "papers": [_paper_to_dict(p, compact=compact) for p in papers],
    }
    return json.dumps(output, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_author_papers(
    name: str,
    dblp_pid: str | None = None,
    year: int | None = None,
    venue: str | None = None,
    max_results: int = 100,
    compact: bool = True,
) -> str:
    """List publications for a specific author via DBLP person profiles.

    Resolves the name to DBLP person PID(s) — the canonical CS author
    disambiguation system — and returns the full publication list for each
    candidate, along with their affiliations so you can tell namesakes apart.

    Use this for "all papers by person X (at venue V in year Y)" queries,
    especially for common names where keyword search returns namesakes.

    Note: DBLP is CS-only and lags very recent conferences by weeks/months.

    Args:
        name: Author name, e.g. "Zhenhua Zhu"
        dblp_pid: Skip resolution and use this PID directly (e.g. "07/4259-2")
        year: Optional publication year filter
        venue: Optional venue filter, e.g. "HPCA", "ISCA" (substring match)
        max_results: Max papers per candidate (default 100)
        compact: Return slim paper records (default true)
    """
    source = DBLPSource()

    if dblp_pid:
        candidates = [{"name": name, "pid": dblp_pid, "affiliations": []}]
    else:
        candidates = await source.resolve_authors(name)
        if not candidates:
            return json.dumps(
                {
                    "total": 0,
                    "candidates": [],
                    "papers": [],
                    "warnings": [f"No DBLP author profile found for {name!r}."],
                },
                ensure_ascii=False,
                indent=2,
            )

        base = _base_name(name)
        exact = [c for c in candidates if _base_name(c["name"]) == base]
        candidates = exact or candidates

    all_papers: list[Paper] = []
    per_candidate: list[dict] = []
    seen: set[str] = set()

    for i, cand in enumerate(candidates[:4]):
        if i > 0:
            await asyncio.sleep(1.0)
        papers = await source.author_papers(
            cand["pid"], year=year, venue=venue, max_results=max_results
        )
        per_candidate.append(
            {
                "name": cand["name"],
                "pid": cand["pid"],
                "affiliations": cand["affiliations"],
                "paper_count": len(papers),
            }
        )
        for p in papers:
            if p.source_id not in seen:
                seen.add(p.source_id)
                all_papers.append(p)

    all_papers.sort(key=lambda p: (p.year or 0), reverse=True)

    warnings: list[str] = []
    if source.last_error:
        warnings.append(f"dblp: {source.last_error}")
    if len(per_candidate) > 1:
        warnings.append(
            "Multiple DBLP profiles match this name (homonyms). Papers from all "
            "matches are merged below — use the candidates list with dblp_pid "
            "to restrict to one identity."
        )

    output = {
        "total": len(all_papers),
        "candidates": per_candidate,
        "warnings": warnings,
        "papers": [_paper_to_dict(p, compact=compact) for p in all_papers],
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
