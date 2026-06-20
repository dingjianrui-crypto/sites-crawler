from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from .models import Contacts, Link

EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")
SOCIAL_HOSTS = {
    "discord.gg",
    "discord.com",
    "t.me",
    "telegram.me",
    "x.com",
    "twitter.com",
    "instagram.com",
    "reddit.com",
}

MEDIA_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".avif",
    ".mp4",
    ".webm",
    ".mov",
    ".m4v",
    ".zip",
    ".pdf",
}


class TextLinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[str] = []
        self.link_details: list[tuple[str, str, int]] = []
        self._active_links: list[tuple[str, list[str], int]] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg", "canvas"}:
            self._skip_depth += 1
            return
        attrs_dict = dict(attrs)
        if tag == "title":
            self._in_title = True
        if tag == "a":
            href = attrs_dict.get("href")
            if href:
                absolute_url = urljoin(self.base_url, href)
                self.links.append(absolute_url)
                self._active_links.append((absolute_url, [], len(self.text)))
        if tag in {"p", "div", "section", "article", "br", "li", "h1", "h2", "h3", "footer"}:
            self.text_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg", "canvas"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "a" and self._active_links:
            url, text_parts, context_start = self._active_links.pop()
            anchor_text = " ".join(" ".join(text_parts).split()).strip()
            self.link_details.append((url, anchor_text, context_start))
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        cleaned = " ".join(data.split())
        if not cleaned:
            return
        if self._in_title:
            self.title_parts.append(cleaned)
        if self._active_links:
            self._active_links[-1][1].append(cleaned)
        self.text_parts.append(cleaned)

    @property
    def title(self) -> str:
        return " ".join(self.title_parts).strip()

    @property
    def text(self) -> str:
        return " ".join(" ".join(self.text_parts).split()).strip()

    def links_with_context(self) -> list[Link]:
        text = self.text
        return [
            Link(url=url, text=anchor_text, context=surrounding_context(text, context_start, anchor_text))
            for url, anchor_text, context_start in self.link_details
        ]


def parse_html(html: str, base_url: str) -> tuple[str, str, list[str], list[Link]]:
    parser = TextLinkParser(base_url)
    parser.feed(html)
    return parser.title, parser.text, dedupe_preserve_order(parser.links), dedupe_links(parser.links_with_context())


def surrounding_context(text: str, start: int, anchor_text: str, radius: int = 350) -> str:
    left = max(0, start - radius)
    right = min(len(text), start + len(anchor_text) + radius)
    return " ".join(text[left:right].split()).strip()


def dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value not in seen:
            output.append(value)
            seen.add(value)
    return output


def dedupe_links(values: list[Link]) -> list[Link]:
    seen: set[tuple[str, str]] = set()
    output: list[Link] = []
    for value in values:
        key = (value.url, value.text)
        if key not in seen:
            output.append(value)
            seen.add(key)
    return output


def is_media_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in MEDIA_EXTENSIONS)


def extract_contacts(text: str, links: list[str]) -> Contacts:
    emails = sorted(set(EMAIL_RE.findall(text)))
    discord: list[str] = []
    telegram: list[str] = []
    other: list[str] = []

    for link in links:
        parsed = urlparse(link)
        if parsed.scheme == "mailto" and parsed.path:
            emails.extend(EMAIL_RE.findall(parsed.path))
            continue
        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if host in {"discord.gg", "discord.com"}:
            discord.append(link)
        elif host in {"t.me", "telegram.me"}:
            telegram.append(link)
        elif host in SOCIAL_HOSTS or any(host.endswith(f".{social}") for social in SOCIAL_HOSTS):
            other.append(link)

    return Contacts(
        emails=sorted(set(emails)),
        discord=dedupe_preserve_order(discord),
        telegram=dedupe_preserve_order(telegram),
        other=dedupe_preserve_order(other),
    )


def looks_js_heavy(text: str, links: list[str]) -> bool:
    lowered = text.lower()
    if len(text) < 350 and len(links) < 4:
        return True
    js_markers = [
        "enable javascript",
        "please enable js",
        "__next",
        "root",
        "app-root",
    ]
    return len(text) < 800 and any(marker in lowered for marker in js_markers)
