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
- **OpenAlex author filter**: name resolved to candidate Author IDs, OR-combined into one `authorships.author.id` filter (no per-author quota split)
- **DBLP author-centric mode**: `author:Name:` syntax does NOT match disambiguated profiles (`Name 0002`), so when the query is empty/equals the author name, resolve PIDs via `dblp.org/search/author/api` and fetch full records from `dblp.org/pid/{pid}.xml`. Never echo the author name as free text — it poisons relevance ranking.
- **Venue filter**: `venue` param on all sources — DBLP maps to native `venue:` query syntax; OpenAlex/arXiv filter client-side (substring, case-insensitive)
- **Rate limit handling**: DBLP retries 429/5xx/disconnects with exponential backoff + `Retry-After`; failures surface via `last_error` (MCP reports them in `errors`, never silently zero)
- **Dedup engine**: Pass 1: DOI exact match (case-insensitive). Pass 2: Jaccard title similarity ≥0.7 on tokenized words, sliding window + Union-Find for O(n) grouping
- **No external PDF download yet** — PDF URLs are extracted and stored in metadata

### Data Model

```
Paper(title, authors: list[Author], year, publication_date,
      doi, abstract, source: SourceName, source_id,
      pdf_url, venue, citation_count, categories)

Author(name, affiliation, orcid, dblp_pid)

SourceName: Literal["arxiv", "dblp", "openalex", ...]
```

## Current Status

**Phase 1 complete** — Core search pipeline working:

| Feature | Source | Status |
|---------|--------|--------|
| Keyword search | All | ✅ |
| Author filter | DBLP (PID resolution), OpenAlex (ID OR-combine), arXiv (code ready) | ✅ |
| Venue filter | All (`--venue` / `venue=` param) | ✅ |
| Year filter | All | ✅ |
| Deduplication | Cross-source | ✅ |
| CLI | --query/-q, --author/-a, --year/-y, --venue/-v, --sources/-s | ✅ |
| MCP tools | search_papers, get_author_papers, download_paper, deduplicate_papers, get_paper_by_doi | ✅ |
| MCP compact mode | `compact=true` (default) slims paper records ~60% | ✅ |
| arXiv (API) | httpx Atom XML | ✅ code ready, ⚠️ IP currently rate-limited |

## Build & Test Commands

```bash
# Install
pip install -e .

# Run CLI
PYTHONPATH=src python -m paper_search.cli -q "query" -s dblp,openalex -n 8 --dedup
PYTHONPATH=src python -m paper_search.cli -q "query" -a "Author Name" -y 2024 -v HPCA -s dblp

# Unit tests (stdlib unittest, no extra deps)
PYTHONPATH=src python -m unittest discover -s tests -v

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
