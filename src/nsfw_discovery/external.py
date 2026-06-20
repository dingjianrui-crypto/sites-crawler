from __future__ import annotations

from urllib.parse import urlparse

from .domain import registrable_domain, same_domain
from .extract import is_media_url
from .models import ExternalCandidate, Link, PageContent


DENYLIST_DOMAINS = {
    "apple.com",
    "cloudflare.com",
    "crisp.chat",
    "discord.com",
    "discord.gg",
    "facebook.com",
    "github.com",
    "google.com",
    "instagram.com",
    "intercom.com",
    "paypal.com",
    "play.google.com",
    "reddit.com",
    "stripe.com",
    "t.me",
    "telegram.me",
    "twitter.com",
    "x.com",
    "youtube.com",
    "zendesk.com",
}


def discover_external_candidates(
    source_domain: str,
    pages: list[PageContent],
    depth: int,
    max_candidates: int,
) -> list[ExternalCandidate]:
    if depth <= 0 or max_candidates <= 0:
        return []

    candidates_by_domain: dict[str, ExternalCandidate] = {}
    for page in pages:
        for link in page.link_details:
            candidate = external_link_candidate(
                source_domain=source_domain,
                source_url=page.url,
                link=link,
                depth=depth,
            )
            if not candidate:
                continue
            existing = candidates_by_domain.get(candidate.domain)
            if not existing or len(candidate.source_context) > len(existing.source_context):
                candidates_by_domain[candidate.domain] = candidate

    candidates = sorted(candidates_by_domain.values(), key=lambda item: item.domain)
    return candidates[:max_candidates]


def external_link_candidate(source_domain: str, source_url: str, link: Link, depth: int) -> ExternalCandidate | None:
    url = normalize_candidate_url(link.url)
    if not url or is_media_url(url) or same_domain(url, source_domain):
        return None
    domain = registrable_domain(url)
    if not domain or is_denylisted(domain):
        return None

    return ExternalCandidate(
        domain=domain,
        url=url,
        source_domain=source_domain,
        source_url=source_url,
        anchor_text=link.text[:500],
        score=0,
        reason="pending_context_screening",
        depth=depth,
        source_context=link.context[:1000],
    )


def normalize_candidate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return ""
    return parsed._replace(fragment="").geturl()


def is_denylisted(domain: str) -> bool:
    return domain in DENYLIST_DOMAINS or any(domain.endswith(f".{blocked}") for blocked in DENYLIST_DOMAINS)
