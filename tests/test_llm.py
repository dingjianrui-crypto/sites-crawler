import json

import httpx
import pytest

from nsfw_discovery.llm import LlmClient, heuristic_classify, parse_classification
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
