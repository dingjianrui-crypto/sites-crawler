from __future__ import annotations

from typing import Any

import httpx

from .domain import registrable_domain
from .models import SearchResult


class SerpApiClient:
    def __init__(self, api_key: str, timeout: float = 20.0) -> None:
        self.api_key = api_key
        self.timeout = timeout

    async def search(self, query: str, num_results: int) -> list[SearchResult]:
        params = {
            "engine": "google",
            "q": query,
            "hl": "en",
            "gl": "us",
            "num": min(num_results, 100),
            "api_key": self.api_key,
        }
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get("https://serpapi.com/search.json", params=params)
            response.raise_for_status()
            payload = response.json()
        return parse_serpapi_results(query, payload, num_results)


def parse_serpapi_results(query: str, payload: dict[str, Any], limit: int) -> list[SearchResult]:
    results: list[SearchResult] = []
    for item in payload.get("organic_results", []):
        url = item.get("link") or item.get("redirect_link") or ""
        domain = registrable_domain(url)
        if not url or not domain:
            continue
        results.append(
            SearchResult(
                query=query,
                title=str(item.get("title") or ""),
                url=url,
                snippet=str(item.get("snippet") or ""),
                domain=domain,
            )
        )
        if len(results) >= limit:
            break
    return results
