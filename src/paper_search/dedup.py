from __future__ import annotations

from .models import Paper


JACCARD_THRESHOLD = 0.7
TITLE_WINDOW_SIZE = 20

_PAPER_FIELDS = (
    "title",
    "authors",
    "year",
    "publication_date",
    "doi",
    "abstract",
    "source",
    "source_id",
    "pdf_url",
    "venue",
    "citation_count",
    "categories",
)


def deduplicate(papers: list[Paper]) -> list[Paper]:
    """Merge duplicate papers by DOI first, then by similar titles."""
    if len(papers) < 2:
        return list(papers)

    after_doi = _deduplicate_by_doi(papers)
    after_title = _deduplicate_by_title(after_doi)

    return [paper for _, paper in after_title]


def _deduplicate_by_doi(papers: list[Paper]) -> list[tuple[int, Paper]]:
    doi_groups: dict[str, list[tuple[int, Paper]]] = {}
    without_doi: list[tuple[int, Paper]] = []

    for index, paper in enumerate(papers):
        doi = paper.doi.strip().lower() if paper.doi else ""
        if doi:
            doi_groups.setdefault(doi, []).append((index, paper))
        else:
            without_doi.append((index, paper))

    merged: list[tuple[int, Paper]] = []

    for group in doi_groups.values():
        first_index = min(index for index, _ in group)
        ordered_papers = [paper for _, paper in sorted(group, key=lambda item: item[0])]
        merged.append((first_index, _merge_papers(ordered_papers)))

    merged.extend(without_doi)
    merged.sort(key=lambda item: item[0])
    return merged


def _deduplicate_by_title(papers: list[tuple[int, Paper]]) -> list[tuple[int, Paper]]:
    if len(papers) < 2:
        return papers

    parents = list(range(len(papers)))
    tokenized = [_tokenize_title(paper.title) for _, paper in papers]
    sorted_indexes = sorted(
        range(len(papers)),
        key=lambda index: (" ".join(sorted(tokenized[index])), papers[index][0]),
    )

    for position, left_index in enumerate(sorted_indexes):
        right_edge = min(position + TITLE_WINDOW_SIZE + 1, len(sorted_indexes))
        for right_index in sorted_indexes[position + 1 : right_edge]:
            if _jaccard(tokenized[left_index], tokenized[right_index]) >= JACCARD_THRESHOLD:
                _union(parents, left_index, right_index)

    groups: dict[int, list[tuple[int, Paper]]] = {}
    for index, item in enumerate(papers):
        groups.setdefault(_find(parents, index), []).append(item)

    merged: list[tuple[int, Paper]] = []
    for group in groups.values():
        ordered_group = sorted(group, key=lambda item: item[0])
        first_index = ordered_group[0][0]
        merged.append((first_index, _merge_papers([paper for _, paper in ordered_group])))

    merged.sort(key=lambda item: item[0])
    return merged


def _tokenize_title(title: str) -> set[str]:
    return set(title.lower().split())


def _jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _merge_papers(dupes: list[Paper]) -> Paper:
    return max(dupes, key=_metadata_score)


def _metadata_score(paper: Paper) -> int:
    return sum(getattr(paper, field) is not None for field in _PAPER_FIELDS)


def _find(parents: list[int], index: int) -> int:
    while parents[index] != index:
        parents[index] = parents[parents[index]]
        index = parents[index]
    return index


def _union(parents: list[int], left: int, right: int) -> None:
    left_root = _find(parents, left)
    right_root = _find(parents, right)
    if left_root != right_root:
        parents[right_root] = left_root
