import json

import httpx
import pytest

from nsfw_discovery.llm import (
    LlmClient,
    apply_evidence_guard,
    heuristic_screen_external_candidate,
    heuristic_screen_search_result,
    heuristic_classify,
    parse_classification,
    parse_search_screening,
)
from nsfw_discovery.models import Contacts, ExternalCandidate, PageContent, SearchResult


def test_parse_classification_accepts_medium_ai_nsfw() -> None:
    classification = parse_classification(
        {
            "provides_ai_nsfw": True,
            "description": "Adult AI image generation platform.",
            "confidence": "medium",
            "relevance_score": 5,
            "flags": [],
            "accepted": False,
            "uncertain": False,
        }
    )
    assert classification.accepted
    assert not classification.uncertain
    assert classification.confidence == "medium"
    assert classification.relevance_score == 5


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
            "relevance_score": 8,
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
    assert guarded.relevance_score == 0
    assert "insufficient_ai_nsfw_evidence" in guarded.flags


def test_evidence_guard_keeps_direct_ai_nsfw_service() -> None:
    classification = parse_classification(
        {
            "provides_ai_nsfw": True,
            "description": "Adult AI image generation platform.",
            "confidence": "high",
            "relevance_score": 9,
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
    assert guarded.relevance_score == 9


def test_parse_search_screening_uses_keep_boolean() -> None:
    relevant = parse_search_screening({"reason": "direct match", "keep": True})
    unrelated = parse_search_screening({"reason": "directory", "keep": False})
    assert relevant.keep
    assert relevant.reason == "direct match"
    assert not unrelated.keep


def test_heuristic_screen_search_result_rejects_general_portal() -> None:
    yahoo = SearchResult(
        query="AI NSFW generator",
        title="Yahoo Singapore",
        url="https://sg.yahoo.com",
        snippet="News, email, search, weather, finance, sports, and consumer content.",
        domain="yahoo.com",
    )
    generator = SearchResult(
        query="AI NSFW generator",
        title="AI NSFW image generator",
        url="https://example.com",
        snippet="Create uncensored adult AI images with an image generation model.",
        domain="example.com",
    )
    assert not heuristic_screen_search_result(yahoo).keep
    assert heuristic_screen_search_result(generator).keep


def test_heuristic_screen_external_candidate_uses_source_context() -> None:
    unrelated = ExternalCandidate(
        domain="example.com",
        url="https://example.com/pricing",
        source_domain="seed.com",
        source_url="https://seed.com/tools",
        anchor_text="AI partner",
        score=4,
        reason="ai_terms:ai",
        depth=1,
        source_context="This is a billing partner for general account management.",
    )
    relevant = ExternalCandidate(
        domain="example.com",
        url="https://example.com/generator",
        source_domain="seed.com",
        source_url="https://seed.com/tools",
        anchor_text="Partner",
        score=4,
        reason="ai_terms:ai;nsfw_terms:adult",
        depth=1,
        source_context="This partner provides an uncensored AI image generator for adult content.",
    )
    assert not heuristic_screen_external_candidate(unrelated).keep
    assert heuristic_screen_external_candidate(relevant).keep


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
                                    "relevance_score": 5,
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
    assert captured["json"]["messages"][1]["content"].find("query_topics") != -1
