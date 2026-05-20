from __future__ import annotations

import asyncio
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .dedup import deduplicate
from .models import Paper
from .sources import ArxivSource, DBLPSource, OpenAlexSource, Source

app = typer.Typer()
console = Console()

_SOURCE_MAP: dict[str, type[Source]] = {
    "openalex": OpenAlexSource,
    "arxiv": ArxivSource,
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
    ] = "arxiv",
    max_results: Annotated[
        int,
        typer.Option("--max-results", "-n", help="Max results per source"),
    ] = 20,
    dedup: Annotated[
        bool,
        typer.Option("--dedup/--no-dedup", help="Enable deduplication"),
    ] = True,
):
    source_names = [s.strip() for s in sources.split(",") if s.strip()]
    resolved = _resolve_sources(source_names)
    if not resolved:
        console.print("[red]No valid sources specified.[/red]")
        raise typer.Exit(1)

    console.print(f"[bold]Searching for:[/bold] {query}")
    console.print(f"[bold]Sources:[/bold] {', '.join(source_names)}")

    papers = asyncio.run(_run_searches(resolved, query, max_results))
    console.print(f"[dim]Collected {len(papers)} results.[/dim]")

    if dedup and len(papers) > 1:
        before = len(papers)
        papers = deduplicate(papers)
        console.print(f"[dim]After dedup: {len(papers)} ({before - len(papers)} duplicates removed).[/dim]")

    _print_table(papers)


def _resolve_sources(names: list[str]) -> list[Source]:
    instances: list[Source] = []
    for name in names:
        cls = _SOURCE_MAP.get(name)
        if cls is None:
            console.print(f"[yellow]Unknown source: {name!r}, skipping.[/yellow]")
            continue
        instances.append(cls())
    return instances


async def _run_searches(sources: list[Source], query: str, max_results: int) -> list[Paper]:
    tasks = [source.search(query, max_results) for source in sources]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    papers: list[Paper] = []
    for source, result in zip(sources, results):
        if isinstance(result, Exception):
            console.print(f"[red]{source.name}: search failed — {result}[/red]")
        elif isinstance(result, list):
            console.print(f"[dim]{source.name}: {len(result)} results[/dim]")
            papers.extend(result)
    return papers


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
