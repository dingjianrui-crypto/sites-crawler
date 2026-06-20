from __future__ import annotations

import json
from typing import Any

import httpx

from .models import Classification, Contacts, PageContent, SearchResult, SearchScreening


AI_TERMS = {
    "ai",
    "artificial intelligence",
    "generate",
    "generator",
    "image generation",
    "chatbot",
    "model",
    "stable diffusion",
}

NSFW_TERMS = {
    "nsfw",
    "adult",
    "uncensored",
    "porn",
    "erotic",
    "hentai",
    "sexting",
    "nude",
}

SERVICE_TERMS = {
    "create",
    "generate",
    "generator",
    "image generation",
    "chatbot",
    "companion",
    "roleplay",
    "stable diffusion",
}

HIGH_RISK_TERMS = {
    "minor",
    "underage",
    "teen",
    "non-consensual",
    "deepfake",
    "revenge",
}


class LlmClient:
    def __init__(
        self,
        base_url: str | None,
        api_key: str | None,
        model: str,
        timeout: float = 40.0,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    async def classify(
        self,
        domain: str,
        pages: list[PageContent],
        contacts: Contacts,
        topics: list[str] | None = None,
    ) -> Classification:
        if not self.enabled:
            return heuristic_classify(domain, pages)

        query_topics = topics or []
        payload = {
            "model": self.model,
            "temperature": 0,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You classify public website metadata. Return only JSON with keys: "
                        "provides_ai_nsfw boolean, description string, confidence one of "
                        "high/medium/low, relevance_score integer 0-10, flags array of strings, "
                        "accepted boolean, uncertain boolean. Grade relevance_score against the user's "
                        "query topics: 0 means unrelated, 10 means directly matches the queried AI NSFW "
                        "generation/chatbot/companion topic. Keep domains only when relevance_score >= 3. "
                        "Accept only websites that directly provide an AI adult/NSFW generation, "
                        "chatbot, companion, or roleplay product. Reject general-purpose portals, "
                        "news sites, search engines, directories, forums, and social/media platforms "
                        "unless the fetched metadata clearly shows the site itself provides an AI NSFW "
                        "product. Flag illegal, minor-related, or non-consensual indicators. Do not "
                        "describe explicit content graphically."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "domain": domain,
                            "query_topics": query_topics,
                            "pages": [
                                {
                                    "url": page.url,
                                    "title": page.title,
                                    "text": page.text[:4000],
                                    "needs_js_review": page.needs_js_review,
                                }
                                for page in pages[:6]
                            ],
                            "contacts": {
                                "emails": contacts.emails,
                                "discord": contacts.discord,
                                "telegram": contacts.telegram,
                                "other": contacts.other,
                            },
                        },
                        ensure_ascii=True,
                    ),
                },
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        content = data["choices"][0]["message"]["content"]
        classification = parse_classification(content, fallback_domain=domain)
        return apply_evidence_guard(classification, pages)

    async def screen_search_result(self, result: SearchResult) -> SearchScreening:
        if not self.enabled:
            return heuristic_screen_search_result(result)

        payload = {
            "model": self.model,
            "temperature": 0,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You screen search results before crawling. Return only JSON with keys: "
                        "keep boolean and reason string. Decide whether the title, snippet, URL, and "
                        "domain directly match the query topic. Keep only results likely to be websites "
                        "that directly provide AI adult/NSFW generation, chatbot, companion, or roleplay "
                        "services. Reject general-purpose portals, news sites, search engines, directories, "
                        "forums, and unrelated pages."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "query": result.query,
                            "domain": result.domain,
                            "url": result.url,
                            "title": result.title,
                            "snippet": result.snippet,
                        },
                        ensure_ascii=True,
                    ),
                },
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        content = data["choices"][0]["message"]["content"]
        return parse_search_screening(content)


def parse_classification(raw: str | dict[str, Any], fallback_domain: str = "") -> Classification:
    if isinstance(raw, str):
        data = json.loads(raw)
    else:
        data = raw
    confidence = str(data.get("confidence") or "low").lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    provides = bool(data.get("provides_ai_nsfw"))
    relevance_score = parse_relevance_score(data.get("relevance_score"), confidence, provides)
    accepted = provides and relevance_score >= 5
    uncertain = bool(data.get("uncertain")) or (provides and 1 <= relevance_score < 5)
    flags = data.get("flags") or []
    if not isinstance(flags, list):
        flags = [str(flags)]
    description = str(data.get("description") or "")
    if not description and fallback_domain:
        description = f"{fallback_domain} appears to be related to AI adult content."
    return Classification(
        provides_ai_nsfw=provides,
        description=description[:1000],
        confidence=confidence,
        relevance_score=relevance_score,
        flags=[str(flag)[:100] for flag in flags],
        accepted=accepted,
        uncertain=uncertain,
    )


def parse_relevance_score(value: object, confidence: str, provides: bool) -> int:
    if value is not None:
        try:
            return min(10, max(0, int(value)))
        except (TypeError, ValueError):
            pass
    if not provides:
        return 0
    return {"high": 8, "medium": 5, "low": 3}.get(confidence, 0)


def parse_search_screening(raw: str | dict[str, Any]) -> SearchScreening:
    if isinstance(raw, str):
        data = json.loads(raw)
    else:
        data = raw
    return SearchScreening(
        keep=bool(data.get("keep")),
        reason=str(data.get("reason") or "")[:500],
    )


def heuristic_screen_search_result(result: SearchResult) -> SearchScreening:
    text = " ".join([result.domain, result.url, result.title, result.snippet]).lower()
    has_ai = any(term in text for term in AI_TERMS)
    has_nsfw = any(term in text for term in NSFW_TERMS)
    has_service = any(term in text for term in SERVICE_TERMS)
    keep = has_ai and has_nsfw and has_service
    reason = "ai_nsfw_service_terms" if keep else "insufficient_search_result_relevance"
    return SearchScreening(keep=keep, reason=reason)


def apply_evidence_guard(classification: Classification, pages: list[PageContent]) -> Classification:
    if not classification.accepted and not classification.provides_ai_nsfw:
        return classification
    if has_ai_nsfw_service_evidence(pages):
        return classification
    flags = [*classification.flags, "insufficient_ai_nsfw_evidence"]
    return Classification(
        provides_ai_nsfw=False,
        description=classification.description,
        confidence="low",
        relevance_score=0,
        flags=flags,
        accepted=False,
        uncertain=False,
    )


def has_ai_nsfw_service_evidence(pages: list[PageContent]) -> bool:
    for page in pages:
        text = " ".join([page.url, page.title, page.text]).lower()
        has_ai = any(term in text for term in AI_TERMS)
        has_nsfw = any(term in text for term in NSFW_TERMS)
        has_service = any(term in text for term in SERVICE_TERMS)
        if has_ai and has_nsfw and has_service:
            return True
    return False


def heuristic_classify(domain: str, pages: list[PageContent]) -> Classification:
    text = " ".join([domain, *(page.title for page in pages), *(page.text for page in pages)]).lower()
    ai_hits = sorted(term for term in AI_TERMS if term in text)
    nsfw_hits = sorted(term for term in NSFW_TERMS if term in text)
    service_hits = sorted(term for term in SERVICE_TERMS if term in text)
    risk_hits = sorted(term for term in HIGH_RISK_TERMS if term in text)
    provides = bool(ai_hits and nsfw_hits and service_hits)
    relevance_score = 0
    if provides and len(ai_hits) >= 2 and len(nsfw_hits) >= 2:
        confidence = "medium"
        relevance_score = 5
    elif provides:
        confidence = "low"
        relevance_score = 3
    else:
        confidence = "low"
    flags = []
    if risk_hits:
        flags.extend(f"risk:{hit}" for hit in risk_hits)
    if any(page.needs_js_review for page in pages):
        flags.append("needs_js_review")
    return Classification(
        provides_ai_nsfw=provides,
        description=(
            f"{domain} appears to provide AI-generated adult NSFW content."
            if provides
            else f"{domain} was discovered as a candidate but was not verified as an AI adult NSFW site."
        ),
        confidence=confidence,
        relevance_score=relevance_score,
        flags=flags,
        accepted=provides and relevance_score >= 5 and not risk_hits,
        uncertain=provides and (relevance_score < 5 or bool(risk_hits)),
    )
