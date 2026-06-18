from __future__ import annotations

from urllib.parse import urlparse

from .domain import registrable_domain, same_domain
from .extract import is_media_url
from .models import ExternalCandidate, Link, PageContent


AI_TERMS = {
    "ai",
    "artificial intelligence",
    "generator",
    "generate",
    "image generation",
    "chatbot",
    "companion",
    "girlfriend",
    "model",
}

NSFW_TERMS = {
    "nsfw",
    "adult",
    "uncensored",
    "erotic",
    "hentai",
    "porn",
    "nude",
}

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
    score_threshold: int,
    max_candidates: int,
) -> list[ExternalCandidate]:
    if depth <= 0 or max_candidates <= 0:
        return []

    candidates_by_domain: dict[str, ExternalCandidate] = {}
    for page in pages:
        for link in page.link_details:
            candidate = score_external_link(
                source_domain=source_domain,
                source_url=page.url,
                link=link,
                depth=depth,
            )
            if not candidate or candidate.score < score_threshold:
                continue
            existing = candidates_by_domain.get(candidate.domain)
            if not existing or candidate.score > existing.score:
                candidates_by_domain[candidate.domain] = candidate

    candidates = sorted(candidates_by_domain.values(), key=lambda item: (-item.score, item.domain))
    return candidates[:max_candidates]


def score_external_link(source_domain: str, source_url: str, link: Link, depth: int) -> ExternalCandidate | None:
    url = normalize_candidate_url(link.url)
    if not url or is_media_url(url) or same_domain(url, source_domain):
        return None
    domain = registrable_domain(url)
    if not domain or is_denylisted(domain):
        return None

    haystack = " ".join([domain, urlparse(url).path, link.text]).lower()
    ai_hits = sorted(term for term in AI_TERMS if term in haystack)
    nsfw_hits = sorted(term for term in NSFW_TERMS if term in haystack)
    score = 0
    reasons: list[str] = []
    if ai_hits:
        score += 3 if any(term in link.text.lower() for term in ai_hits) else 2
        reasons.append("ai_terms:" + ",".join(ai_hits[:3]))
    if nsfw_hits:
        score += 3 if any(term in link.text.lower() for term in nsfw_hits) else 2
        reasons.append("nsfw_terms:" + ",".join(nsfw_hits[:3]))
    if "generator" in haystack and ("nsfw" in haystack or "adult" in haystack or "uncensored" in haystack):
        score += 2
        reasons.append("generator_context")
    if not ai_hits and not nsfw_hits:
        return None

    return ExternalCandidate(
        domain=domain,
        url=url,
        source_domain=source_domain,
        source_url=source_url,
        anchor_text=link.text[:500],
        score=score,
        reason=";".join(reasons),
        depth=depth,
    )


def normalize_candidate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return ""
    return parsed._replace(fragment="").geturl()


def is_denylisted(domain: str) -> bool:
    return domain in DENYLIST_DOMAINS or any(domain.endswith(f".{blocked}") for blocked in DENYLIST_DOMAINS)
