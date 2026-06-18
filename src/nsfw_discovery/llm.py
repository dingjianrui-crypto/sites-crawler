from __future__ import annotations

import json
from typing import Any

import httpx

from .models import Classification, Contacts, PageContent


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
    ) -> Classification:
        if not self.enabled:
            return heuristic_classify(domain, pages)

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
                        "high/medium/low, flags array of strings, accepted boolean, uncertain boolean. "
                        "Accept adult AI NSFW businesses. Flag illegal, minor-related, or "
                        "non-consensual indicators. Do not describe explicit content graphically."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "domain": domain,
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
        return parse_classification(content, fallback_domain=domain)


def parse_classification(raw: str | dict[str, Any], fallback_domain: str = "") -> Classification:
    if isinstance(raw, str):
        data = json.loads(raw)
    else:
        data = raw
    confidence = str(data.get("confidence") or "low").lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    provides = bool(data.get("provides_ai_nsfw"))
    uncertain = bool(data.get("uncertain")) or (provides and confidence == "low")
    accepted = bool(data.get("accepted")) or (provides and confidence in {"high", "medium"})
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
        flags=[str(flag)[:100] for flag in flags],
        accepted=accepted,
        uncertain=uncertain,
    )


def heuristic_classify(domain: str, pages: list[PageContent]) -> Classification:
    text = " ".join([domain, *(page.title for page in pages), *(page.text for page in pages)]).lower()
    ai_hits = sorted(term for term in AI_TERMS if term in text)
    nsfw_hits = sorted(term for term in NSFW_TERMS if term in text)
    risk_hits = sorted(term for term in HIGH_RISK_TERMS if term in text)
    provides = bool(ai_hits and nsfw_hits)
    if provides and len(ai_hits) >= 2 and len(nsfw_hits) >= 2:
        confidence = "medium"
    elif provides:
        confidence = "low"
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
        flags=flags,
        accepted=provides and confidence == "medium" and not risk_hits,
        uncertain=provides and (confidence == "low" or bool(risk_hits)),
    )
