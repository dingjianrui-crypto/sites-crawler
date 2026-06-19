import json

import httpx
import pytest

from nsfw_discovery.llm import (
    LlmClient,
    apply_evidence_guard,
    heuristic_classify,
    parse_classification,
)
from nsfw_discovery.models import Contacts, PageContent


def test_parse_classification_accepts_medium_ai_nsfw() -> None:
    classification = parse_classification(
        {
            "provides_ai_nsfw": True,
            "description": "Adult AI image generation platform.",
            "confidence": "medium",
            "flags": [],
            "accepted": True,
            "uncertain": False,
        }
    )
    assert classification.accepted
    assert not classification.uncertain
    assert classification.confidence == "medium"


def test_heuristic_classify_flags_candidate_without_credentials() -> None:
    page = PageContent(
        url="https://example.com",
        title="AI NSFW image generator",
        text="Create uncensored adult AI images with an image generation model.",
        links=[],
        status_code=200,
    )
    classification = heuristic_classify("example.com", [page])
    assert classification.provides_ai_nsfw
    assert classification.accepted


def test_evidence_guard_rejects_general_portal_false_positive() -> None:
    classification = parse_classification(
        {
            "provides_ai_nsfw": True,
            "description": (
                "Yahoo Singapore is a general-purpose web portal offering news, email, "
                "search, weather, finance, sports, and other general consumer content and services."
            ),
            "confidence": "high",
            "flags": [],
            "accepted": True,
            "uncertain": False,
        }
    )
    guarded = apply_evidence_guard(
        classification,
        [
            PageContent(
                url="https://sg.yahoo.com",
                title="Yahoo Singapore",
                text=(
                    "Yahoo Singapore offers news, email, search, weather, finance, sports, "
                    "and general consumer content."
                ),
                links=[],
                status_code=200,
            )
        ],
    )
    assert not guarded.provides_ai_nsfw
    assert not guarded.accepted
    assert guarded.confidence == "low"
    assert "insufficient_ai_nsfw_evidence" in guarded.flags


def test_evidence_guard_keeps_direct_ai_nsfw_service() -> None:
    classification = parse_classification(
        {
            "provides_ai_nsfw": True,
            "description": "Adult AI image generation platform.",
            "confidence": "high",
            "flags": [],
            "accepted": True,
            "uncertain": False,
        }
    )
    guarded = apply_evidence_guard(
        classification,
        [
            PageContent(
                url="https://example.com",
                title="AI NSFW image generator",
                text="Create uncensored adult AI images with an image generation model.",
                links=[],
                status_code=200,
            )
        ],
    )
    assert guarded.accepted
    assert guarded.confidence == "high"


@pytest.mark.anyio
async def test_llm_payload_disables_thinking(monkeypatch) -> None:
    captured = {}

    async def fake_post(self, url, headers=None, json=None):
        captured["json"] = json
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [
                    {
                        "message": {
                            "content": json_module.dumps(
                                {
                                    "provides_ai_nsfw": True,
                                    "description": "Adult AI image platform.",
                                    "confidence": "medium",
                                    "flags": [],
                                    "accepted": True,
                                    "uncertain": False,
                                }
                            )
                        }
                    }
                ]
            },
        )

    json_module = json
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    client = LlmClient("https://llm.example/v1", "key", "model")
    await client.classify(
        "example.com",
        [
            PageContent(
                url="https://example.com",
                title="Example",
                text="AI NSFW generator",
                links=[],
                status_code=200,
            )
        ],
        Contacts(),
    )
    assert captured["json"]["thinking"] == {"type": "disabled"}
