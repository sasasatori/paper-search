from __future__ import annotations

import asyncio
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .dedup import deduplicate
from .download import Downloader, DownloadResult
from .models import Paper
from .sources import DBLPSource, OpenAlexSource, Source

app = typer.Typer()
console = Console()

_SOURCE_MAP: dict[str, type[Source]] = {
    "openalex": OpenAlexSource,
    "dblp": DBLPSource,
}


@app.command()
def search(
    query: Annotated[
        str,
        typer.Option("--query", "-q", help="Search query string"),
    ],
    sources: Annotated[
        str,
        typer.Option(
            "--sources",
            "-s",
            help="Comma-separated source list: openalex,arxiv,dblp",
        ),
    ] = "openalex,dblp",
    max_results: Annotated[
        int,
        typer.Option("--max-results", "-n", help="Max results per source"),
    ] = 20,
    dedup: Annotated[
        bool,
        typer.Option("--dedup/--no-dedup", help="Enable deduplication"),
    ] = True,
    author: Annotated[
        str | None,
        typer.Option("--author", "-a", help="Filter by author name"),
    ] = None,
    year: Annotated[
        int | None,
        typer.Option("--year", "-y", help="Filter by publication year"),
    ] = None,
    category: Annotated[
        str | None,
        typer.Option("--category", "-c", help="Filter by domain/category (e.g. cs.AI, VLSI, computer vision)"),
    ] = None,
    affiliation: Annotated[
        str | None,
        typer.Option("--affiliation", help="Filter by author affiliation/institution keyword"),
    ] = None,
    download: Annotated[
        bool,
        typer.Option("--download", help="Download PDFs for all results with available URLs"),
    ] = False,
    download_dir: Annotated[
        str,
        typer.Option("--download-dir", help="Directory to save downloaded PDFs"),
    ] = "./downloads",
):
    source_names = [s.strip() for s in sources.split(",") if s.strip()]
    resolved = _resolve_sources(source_names)
    if not resolved:
        console.print("[red]No valid sources specified.[/red]")
        raise typer.Exit(1)

    filters = []
    if author:
        filters.append(f"author={author}")
    if year:
        filters.append(f"year={year}")
    if category:
        filters.append(f"category={category}")
    if affiliation:
        filters.append(f"affiliation={affiliation}")
    filter_str = f" ({', '.join(filters)})" if filters else ""

    console.print(f"[bold]Searching for:[/bold] {query}{filter_str}")
    console.print(f"[bold]Sources:[/bold] {', '.join(source_names)}")

    papers = asyncio.run(_run_searches(resolved, query, max_results, author, year, category, affiliation))
    console.print(f"[dim]Collected {len(papers)} results.[/dim]")

    if dedup and len(papers) > 1:
        before = len(papers)
        papers = deduplicate(papers)
        console.print(f"[dim]After dedup: {len(papers)} ({before - len(papers)} duplicates removed).[/dim]")

    _print_table(papers)

    if download and papers:
        console.print(f"\n[bold]Downloading PDFs to {download_dir}...[/bold]")
        downloader = Downloader(download_dir=download_dir)
        results = asyncio.run(_run_downloads(downloader, papers))
        succeeded = sum(1 for r in results.values() if r.status == "success")
        paywalled = sum(1 for r in results.values() if r.status == "paywall")
        no_url = sum(1 for r in results.values() if r.status == "no_url")
        failed = len(results) - succeeded - paywalled - no_url

        parts = [f"[dim]Downloaded {succeeded} PDF(s)[/dim]"]
        if paywalled:
            parts.append(f"[yellow]{paywalled} behind paywall[/yellow]")
        if no_url:
            parts.append(f"[dim]{no_url} no URL[/dim]")
        if failed:
            parts.append(f"[red]{failed} failed[/red]")
        console.print("  ".join(parts))

        for key, r in results.items():
            if r.status == "success":
                console.print(f"  [green]✓[/green] {key[:60]} → {r.path}")
            elif r.status == "paywall":
                console.print(f"  [yellow]🔒 {r.detail}[/yellow]  ({key[:50]})")
            elif r.status == "no_url":
                console.print(f"  [dim]—[/dim] {key[:60]} (no URL)")
            else:
                console.print(f"  [red]✗[/red] {key[:60]} — {r.detail}")


@app.command()
def download(
    arxiv_id: Annotated[
        str | None,
        typer.Option("--arxiv-id", help="Download PDF from arXiv by paper ID (e.g. 2106.12345)"),
    ] = None,
    url: Annotated[
        str | None,
        typer.Option("--url", "-u", help="Download PDF from a direct URL"),
    ] = None,
    download_dir: Annotated[
        str,
        typer.Option("--download-dir", "-o", help="Directory to save the PDF"),
    ] = "./downloads",
):
    if not arxiv_id and not url:
        console.print("[red]Specify --arxiv-id or --url.[/red]")
        raise typer.Exit(1)

    downloader = Downloader(download_dir=download_dir)

    if arxiv_id:
        console.print(f"[bold]Downloading arXiv {arxiv_id}...[/bold]")
        result = asyncio.run(downloader.download_arxiv(arxiv_id))

    if url:
        console.print(f"[bold]Downloading {url}...[/bold]")
        result = asyncio.run(downloader.download_url(url))

    if result.status == "success":
        console.print(f"[green]✓ Saved to {result.path}[/green]")
    elif result.status == "paywall":
        console.print(f"[yellow]🔒 {result.detail}[/yellow]")
        raise typer.Exit(1)
    elif result.status == "not_found":
        console.print(f"[red]✗ {result.detail}[/red]")
        raise typer.Exit(1)
    else:
        console.print(f"[red]✗ Download failed — {result.detail}[/red]")
        raise typer.Exit(1)


def _resolve_sources(names: list[str]) -> list[Source]:
    instances: list[Source] = []
    for name in names:
        cls = _SOURCE_MAP.get(name)
        if cls is None:
            console.print(f"[yellow]Unknown source: {name!r}, skipping.[/yellow]")
            continue
        instances.append(cls())
    return instances


async def _run_searches(sources: list[Source], query: str, max_results: int, author: str | None, year: int | None, category: str | None, affiliation: str | None) -> list[Paper]:
    tasks = [source.search(query, max_results, author=author, year=year, category=category, affiliation=affiliation) for source in sources]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    papers: list[Paper] = []
    for source, result in zip(sources, results):
        if isinstance(result, Exception):
            console.print(f"[red]{source.name}: search failed — {result}[/red]")
        elif isinstance(result, list):
            console.print(f"[dim]{source.name}: {len(result)} results[/dim]")
            papers.extend(result)
    return papers


async def _run_downloads(downloader: Downloader, papers: list[Paper]) -> dict[str, DownloadResult]:
    results: dict[str, DownloadResult] = {}
    for paper in papers:
        result = await downloader.download_paper(paper)
        key = paper.doi or paper.title[:60]
        results[key] = result
    return results


def _print_table(papers: list[Paper]) -> None:
    if not papers:
        console.print("[yellow]No results found.[/yellow]")
        return

    table = Table(title=f"Search Results ({len(papers)} papers)")
    table.add_column("#", style="dim", width=4)
    table.add_column("Title", style="bold cyan")
    table.add_column("Authors", style="green")
    table.add_column("Year", justify="right")
    table.add_column("Source", style="magenta")
    table.add_column("DOI", style="dim")

    for i, paper in enumerate(papers, 1):
        author_str = ", ".join(a.name for a in paper.authors[:3])
        if len(paper.authors) > 3:
            author_str += " et al."

        table.add_row(
            str(i),
            _truncate(paper.title, 80),
            _truncate(author_str, 40),
            str(paper.year) if paper.year else "-",
            paper.source,
            paper.doi or "-",
        )

    console.print(table)


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


if __name__ == "__main__":
    app()
