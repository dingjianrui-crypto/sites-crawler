from nsfw_discovery.external import discover_external_candidates, external_link_candidate
from nsfw_discovery.models import Link, PageContent


def test_external_link_candidate_keeps_structurally_valid_external_link() -> None:
    candidate = external_link_candidate(
        source_domain="seed.com",
        source_url="https://seed.com/tools",
        link=Link(
            url="https://example-partner.com/pricing",
            text="Partner",
            context="This nearby text is kept for later context screening.",
        ),
        depth=1,
    )
    assert candidate is not None
    assert candidate.domain == "example-partner.com"
    assert candidate.score == 0
    assert candidate.reason == "pending_context_screening"
    assert candidate.source_context == "This nearby text is kept for later context screening."


def test_external_link_candidate_skips_social_and_same_domain() -> None:
    assert (
        external_link_candidate(
            "seed.com",
            "https://seed.com",
            Link(url="https://x.com/example", text="AI NSFW updates", context="Follow social updates."),
            depth=1,
        )
        is None
    )
    assert (
        external_link_candidate(
            "seed.com",
            "https://seed.com",
            Link(url="https://blog.seed.com/nsfw-ai", text="AI NSFW", context="Internal article."),
            depth=1,
        )
        is None
    )


def test_discover_external_candidates_dedupes_by_best_context() -> None:
    pages = [
        PageContent(
            url="https://seed.com/tools",
            title="Tools",
            text="",
            links=[],
            link_details=[
                Link(
                    url="https://tool.example/nsfw",
                    text="AI",
                    context="Short context.",
                ),
                Link(
                    url="https://tool.example/pricing",
                    text="Partner details",
                    context="Longer context with enough surrounding source page text for the LLM to review.",
                ),
            ],
            status_code=200,
        )
    ]
    candidates = discover_external_candidates("seed.com", pages, depth=1, max_candidates=10)
    assert len(candidates) == 1
    assert candidates[0].domain == "tool.example"
    assert candidates[0].anchor_text == "Partner details"
