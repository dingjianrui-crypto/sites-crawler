from __future__ import annotations

from urllib.parse import urljoin, urlparse

import httpx

from .domain import same_domain
from .extract import is_media_url, looks_js_heavy, parse_html
from .models import PageContent


PRIORITY_PATH_HINTS = (
    "about",
    "contact",
    "support",
    "pricing",
    "terms",
    "privacy",
)


class HtmlCrawler:
    def __init__(self, timeout: float, retries: int, user_agent: str) -> None:
        self.timeout = timeout
        self.retries = retries
        self.user_agent = user_agent

    async def crawl_domain(self, domain: str, max_pages: int) -> list[PageContent]:
        start_urls = [f"https://{domain}/", f"http://{domain}/"]
        pages: list[PageContent] = []
        discovered_links: list[str] = []

        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml"},
        ) as client:
            first_page = None
            for url in start_urls:
                first_page = await self._fetch_page(client, url)
                if first_page:
                    break
            if not first_page:
                return []
            pages.append(first_page)
            discovered_links.extend(first_page.links)

            for url in select_priority_links(domain, discovered_links, max_pages - 1):
                page = await self._fetch_page(client, url)
                if page:
                    pages.append(page)
                if len(pages) >= max_pages:
                    break
        return pages

    async def _fetch_page(self, client: httpx.AsyncClient, url: str) -> PageContent | None:
        if is_media_url(url):
            return None
        last_error: Exception | None = None
        for _attempt in range(self.retries + 1):
            try:
                response = await client.get(url)
                content_type = response.headers.get("content-type", "").lower()
                if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                    return None
                title, text, links, link_details = parse_html(response.text, str(response.url))
                return PageContent(
                    url=str(response.url),
                    title=title,
                    text=text,
                    links=links,
                    link_details=link_details,
                    status_code=response.status_code,
                    needs_js_review=looks_js_heavy(text, links),
                )
            except httpx.HTTPError as exc:
                last_error = exc
        if last_error:
            return None
        return None


def select_priority_links(domain: str, links: list[str], limit: int) -> list[str]:
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for link in links:
        clean = normalize_link(link)
        if not clean or clean in seen or is_media_url(clean) or not same_domain(clean, domain):
            continue
        seen.add(clean)
        path = urlparse(clean).path.lower()
        score = 100
        for index, hint in enumerate(PRIORITY_PATH_HINTS):
            if hint in path:
                score = index
                break
        if score < 100:
            scored.append((score, clean))
    scored.sort(key=lambda item: (item[0], len(item[1]), item[1]))
    return [url for _score, url in scored[:limit]]


def normalize_link(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return ""
    path = parsed.path or "/"
    return parsed._replace(path=path, params="", query="", fragment="").geturl()
