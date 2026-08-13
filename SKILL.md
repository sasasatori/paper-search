---
name: paper-search
description: Use when searching for academic papers. Covers multi-source retrieval (OpenAlex + DBLP via paper-search-mcp MCP), cross-source deduplication, PDF download with paywall detection, DOI metadata lookup, and structured CSV output for literature surveys.
---

# Paper Search

## Overview

Search academic papers via the **paper-search-mcp** MCP server. Two search sources (OpenAlex + DBLP) are queried in parallel, results are deduplicated, and PDFs can be downloaded from arXiv or Open Access URLs with built-in paywall detection.

All tools are MCP function calls — no CLI needed.

## MCP Tools

| Tool | Purpose |
|------|---------|
| `paper-search-mcp_search_papers` | Parallel search across OpenAlex + DBLP with dedup |
| `paper-search-mcp_get_author_papers` | Author-centric listing via DBLP person PIDs (best for "all papers by X") |
| `paper-search-mcp_download_paper` | Download PDF from arXiv ID or direct URL, with paywall detection |
| `paper-search-mcp_deduplicate_papers` | Standalone dedup using DOI + Jaccard title similarity |
| `paper-search-mcp_get_paper_by_doi` | CrossRef DOI metadata lookup |

## When to Use

- User asks to search for academic papers by keyword, author, year, or domain
- User needs to find all papers by a specific researcher
- User mentions "paper search", "find papers", "search papers", "literature survey", "related work"

## Workflow

### Step 1: Search

Use `search_papers` for unified parallel search. It queries OpenAlex and DBLP simultaneously, merges results, and deduplicates automatically.

```
paper-search-mcp_search_papers(query="graph neural network", max_results=10)
```

**Supported filters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `query` | str (default `""`) | Search keywords. Supports phrase search: `"graph neural network"`. Optional when `author` is given. |
| `max_results` | int (default 10) | Max results per source (1-100) |
| `year` | int, optional | Publication year filter (e.g. `2024`) |
| `author` | str, optional | Author name filter |
| `venue` | str, optional | Venue filter (e.g. `"HPCA"`, `"ISCA"`, `"NeurIPS"`). DBLP: native `venue:` syntax; OpenAlex: matched against source name. |
| `category` | str, optional | Domain/category filter (e.g. `"cs.AI"`, `"VLSI"`) |
| `affiliation` | str, optional | Institution keyword filter (OpenAlex only) |
| `dedup` | bool (default true) | Enable cross-source deduplication |
| `compact` | bool (default true) | Slim records (title/authors≤3/venue/year/doi/source/citations). `false` = full author lists, affiliations, ORCIDs, abstracts. |

**Author-centric mode:** when `author` is given and `query` is empty (or just repeats the author name), DBLP resolves the author's person PID(s) and returns their full publication list. Much more reliable than keyword search for "all papers by X".

**Author disambiguation techniques:**
- For "all papers by person X", prefer `get_author_papers(name, year=..., venue=...)` — it returns DBLP PID candidates with affiliations so you can spot namesakes, and a `dblp_pid` parameter pins one identity.
- When `search_papers` merges multiple same-name DBLP profiles it emits a `warnings` entry — check it before trusting author-filtered results.
- Add co-author names: `"Author Name" "Coauthor Name"`
- Add institution keywords in the query or use `affiliation`
- Add domain keywords: `"Author Name" SRAM CIM computing-in-memory`

### Step 2: Author publication lists

For "all papers by person X (at venue V in year Y)", use `get_author_papers`:

```
paper-search-mcp_get_author_papers(name="Zhenhua Zhu", venue="HPCA", year=2025)
paper-search-mcp_get_author_papers(name="Zhenhua Zhu", dblp_pid="07/4259-2")
```

Returns DBLP PID candidates (name, pid, affiliations, paper_count) + merged papers. CS-only coverage; very recent conferences (past weeks) may be missing from DBLP — cross-check the official program.

### Step 3: DOI Lookup

For papers found with incomplete metadata, or to verify a specific paper:
```
paper-search-mcp_get_paper_by_doi(doi="10.1109/tnnls.2020.2978386")
```

### Step 4: Download PDF

Download papers from arXiv by ID, or from any direct PDF URL:
```
# By arXiv ID
paper-search-mcp_download_paper(arxiv_id="1706.03762", download_dir="./downloads")

# By direct URL
paper-search-mcp_download_paper(url="https://arxiv.org/pdf/1706.03762.pdf", download_dir="./downloads")
```

**Paywall detection**: URLs pointing to ACM, IEEE, Springer, Nature, Elsevier, Wiley, JSTOR, and other subscription publishers are automatically detected and reported — no wasted requests.

### Step 5: Deduplicate (standalone)

When you have a list of papers from other sources, use `deduplicate_papers`:
```
paper-search-mcp_deduplicate_papers(papers_json='[{"title":"...","doi":"..."}, ...]')
```

Two-pass dedup: DOI exact match → Jaccard title similarity ≥ 0.7 (sliding window + Union-Find).

### Step 6: Present Results

When summarizing:
1. State total paper count and sources used
2. Highlight top results by citation count
3. Flag any false positives (name collisions) — check the `warnings` field for homonym-merge notices
4. Note source coverage gaps (e.g., DBLP is CS-only; both sources lag very recent conferences by weeks to months — for a conference that just happened, verify against the official program page)

### Step 6: Post-Survey CSV Output (MANDATORY)

After completing a literature survey, compile ALL findings into a structured CSV saved to the project's `docs/` directory.

#### CSV Schema

| Column | Description |
|--------|-------------|
| `Title` | Full paper title (do not truncate) |
| `Authors` | First author + "et al." if many |
| `Venue` | Conference/journal name (ISSCC/JSSC/NeurIPS etc.) |
| `Year` | Publication year |
| `Tier` | **T1** = directly competing / must-cite. **T2** = relevant, strong connection. **T3** = background / methodology reference. |
| `Dimension` | Single-letter code for research dimension |
| `Why_Relevant_to_Project` | **Critical.** 2-3 sentences: (a) what the paper does that's relevant, (b) how the project differs / goes beyond. Be specific. |
| `DOI` | DOI URL or placeholder |

#### Dimension Codes

Define project-specific dimensions. Template:
- **A**: Co-Simulation / Closed-Loop Evaluation
- **B**: Domain-Specific Accelerators
- **C**: Benchmarks & Evaluation Methodology
- **D**: Auto-Generation / Task Composition
- **E**: Infrastructure / Multi-Fidelity / Power
- **F**: Deployment & System Studies

Append legend at CSV bottom:
```
"--- Dimension Legend ---"
"A = <Name>", "<1-line description>"
```

#### Tier Rules
- **T1** ≤ 20% of papers — direct comparison in Related Work
- **T2** ≈ 50% — in your area, cite for context
- **T3** ≤ 30% — background, motivation, methodology

#### Why_Relevant Writing
Each entry answers: (1) What's the connection? (2) What's the difference?

Bad: "This paper is about CIM accelerators."
Good: "首次28nm浮点SRAM CIM宏，混合域outer-product架构。我们的差异: 双模transpose支持训练+推理, 192.3TFLOPS/W vs 72.12。"

#### CSV Encoding (CRITICAL for Chinese)

Always use UTF-8 BOM for Excel/WPS:
```bash
python3 -c "
data = open('docs/survey.csv', 'r', encoding='utf-8').read()
with open('docs/survey.csv', 'w', encoding='utf-8-sig') as f:
    f.write(data)
"
```

#### Output Location
```
docs/<project>_related_work.csv
```

## Source Capabilities

| Source | Search | PDF Download | Notes |
|--------|--------|-------------|-------|
| **OpenAlex** | ✅ | via `pdf_url` in metadata (OA papers) | Broad metadata, good institution filter |
| **DBLP** | ✅ | ❌ | CS bibliography, native author query syntax |
| **arXiv** | ❌ (not in search) | ✅ `download_paper(arxiv_id=...)` | PDF download endpoint not rate-limited |
| **CrossRef** | ❌ (not in search) | ❌ | DOI lookup only (`get_paper_by_doi`) |

**Paywall-protected publishers** (detected, not downloaded): ACM, IEEE Xplore, Springer, Nature, Elsevier/ScienceDirect, Wiley, JSTOR, Taylor & Francis, SAGE, ACS, RSC, APS, AIP.

## Troubleshooting

### OpenAlex returns 0 results for author search
Author name resolution may fail for uncommon names. Try without `author` filter, then manually filter results — or use `get_author_papers` (DBLP PIDs are the better identity system for CS authors anyway).

### DBLP returns empty or errors
DBLP rate-limits aggressively; the client retries with backoff (429/5xx/disconnects) and reports failures in `errors`. If empty persists, retry after a minute. Also: DBLP's `author:Name:` syntax does not match disambiguated profiles (`Name 0002`) — use author-centric mode or `get_author_papers`.

### arXiv download returns nothing
Check the arXiv ID format (e.g. `1706.03762`, not `arXiv:1706.03762`). The PDF download endpoint is separate from the search API and is not subject to the same IP rate limits.

### Many false positives (name collision)
Use `get_author_papers` to enumerate DBLP PID candidates with affiliations, then pin `dblp_pid`. Or add co-author names + institution + domain keywords to the query.

## MCP Server

The MCP server is part of the `paper-search` Python package. Start via opencode config in `~/.config/opencode/opencode.json`:

```json
"paper-search-mcp": {
  "type": "local",
  "command": ["python", "-m", "paper_search.mcp_server"]
}
```

The package is installed via `pip install -e .` from the project root. No API keys required — OpenAlex, DBLP, arXiv PDF download, and CrossRef DOI lookup all work without credentials.