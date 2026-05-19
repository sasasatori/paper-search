from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Paper


class Source(ABC):
    name: str

    @abstractmethod
    async def search(
        self,
        query: str,
        max_results: int = 50,
    ) -> list[Paper]:
        ...
