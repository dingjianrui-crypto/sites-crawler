from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Link:
    url: str
    text: str = ""


@dataclass(frozen=True)
class SearchResult:
    query: str
    title: str
    url: str
    snippet: str
    domain: str


@dataclass(frozen=True)
class SearchScreening:
    keep: bool
    reason: str = ""


@dataclass(frozen=True)
class PageContent:
    url: str
    title: str
    text: str
    links: list[str]
    status_code: int
    link_details: list[Link] = field(default_factory=list)
    needs_js_review: bool = False


@dataclass(frozen=True)
class Contacts:
    emails: list[str] = field(default_factory=list)
    discord: list[str] = field(default_factory=list)
    telegram: list[str] = field(default_factory=list)
    other: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Classification:
    provides_ai_nsfw: bool
    description: str
    confidence: str
    relevance_score: int = 0
    flags: list[str] = field(default_factory=list)
    accepted: bool = False
    uncertain: bool = False


@dataclass(frozen=True)
class ExternalCandidate:
    domain: str
    url: str
    source_domain: str
    source_url: str
    anchor_text: str
    score: int
    reason: str
    depth: int
