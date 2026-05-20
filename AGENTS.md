# AGENTS.md

This file provides guidance to AI coding agents working in this repository.

## Project Overview

**academic-paper-search** — 学术论文自动化搜索工具（P1 优先级）。

A CLI tool that aggregates paper search results across multiple academic databases (OpenAlex, DBLP, arXiv) with deduplication.

## Workflow

1. Search across OpenAlex, DBLP, arXiv (and future sources) via their APIs
2. Support filtering by keyword, author, and year
3. Cross-source deduplication (DOI exact match + Jaccard title similarity)
4. Output unified results as rich terminal table
5. (Future) PDF download and structured report generation

## Architecture

```
src/paper_search/
├── models.py           # Pydantic models: Paper, Author, SourceName enum
├── sources/
│   ├── base.py         # Source ABC with async search(query, author, year, max_results)
│   ├── arxiv.py        # httpx-based Atom XML API client
│   ├── dblp.py         # httpx-based DBLP REST API client
│   └── openalex.py     # pyalex wrapper with phrase search fallback
├── dedup.py            # Two-pass: DOI exact match + Jaccard title similarity (Union-Find)
├── cli.py              # Typer CLI: --query, --author, --year, --sources, --max-results, --dedup
├── __init__.py
└── sources/__init__.py
```

### Key Technical Points

- **Async parallel queries**: All sources queried simultaneously via `asyncio.gather`
- **OpenAlex phrase search**: Multi-word queries wrapped in double quotes; falls back to AND if zero results
- **DBLP author filter**: Native `author:First_Last:` syntax (spaces → underscores)
- **Rate limit handling**: Graceful degradation — rate-limited sources return empty list, don't crash pipeline
- **Dedup engine**: Pass 1: DOI exact match (case-insensitive). Pass 2: Jaccard title similarity ≥0.7 on tokenized words, sliding window + Union-Find for O(n) grouping
- **No external PDF download yet** — PDF URLs are extracted and stored in metadata

### Data Model

```
Paper(title, authors: list[Author], year, publication_date,
      doi, abstract, source: SourceName, source_id,
      pdf_url, venue, citation_count, categories)

Author(name, affiliation, orcid)

SourceName: Literal["arxiv", "dblp", "openalex", ...]
```

## Current Status

**Phase 1 complete** — Core search pipeline working:

| Feature | Source | Status |
|---------|--------|--------|
| Keyword search | All | ✅ |
| Author filter | DBLP (native), OpenAlex (embedded), arXiv (code ready) | ✅ |
| Year filter | All | ✅ |
| Deduplication | Cross-source | ✅ |
| CLI | --query/-q, --author/-a, --year/-y, --sources/-s | ✅ |
| arXiv (API) | httpx Atom XML | ✅ code ready, ⚠️ IP currently rate-limited |

## Build & Test Commands

```bash
# Install
pip install -e .

# Run CLI
PYTHONPATH=src python -m paper_search.cli -q "query" -s dblp,openalex -n 8 --dedup
PYTHONPATH=src python -m paper_search.cli -q "query" -a "Author Name" -y 2024 -s dblp

# Syntax check all files
python -m py_compile src/paper_search/models.py
python -m py_compile src/paper_search/sources/*.py
python -m py_compile src/paper_search/dedup.py
python -m py_compile src/paper_search/cli.py
```

## Code Style

- Python 3.11+, async/await throughout
- Pydantic v2 for data models
- ABC for source adapter interface
- httpx for HTTP clients (no requests)
- Type annotations on all public interfaces
- No `as any`, `@ts-ignore`, or type suppression

## Planned (Phase 2+)

- PDF download with rate-limiting and local storage
- IEEE / ACM source adapters
- Structured report output (Markdown/JSON/CSV)
- ArXiv category taxonomy scraper (from ArXivSpider reference)
- Cookie/session management for gated sources
