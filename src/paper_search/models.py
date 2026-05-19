from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


SourceName = Literal["openalex", "arxiv", "dblp", "ieee", "acm"]


class Author(BaseModel):
    name: str
    orcid: str | None = None
    affiliation: str | None = None


class Paper(BaseModel):
    title: str
    authors: list[Author]
    year: int | None = None
    publication_date: date | None = None
    doi: str | None = None
    abstract: str | None = None
    source: SourceName
    source_id: str = Field(description="OpenAlex ID, arXiv ID, or DBLP key")
    pdf_url: str | None = None
    venue: str | None = None
    citation_count: int | None = None
    categories: list[str] = Field(default_factory=list)
