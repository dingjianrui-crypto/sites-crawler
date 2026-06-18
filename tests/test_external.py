from nsfw_discovery.external import discover_external_candidates, score_external_link
from nsfw_discovery.models import Link, PageContent


def test_score_external_link_keeps_relevant_external_candidate() -> None:
    candidate = score_external_link(
        source_domain="seed.com",
        source_url="https://seed.com/tools",
        link=Link(
            url="https://example-ai.com/nsfw-generator",
            text="Try this uncensored AI generator",
        ),
        depth=1,
    )
    assert candidate is not None
    assert candidate.domain == "example-ai.com"
    assert candidate.score >= 4


def test_score_external_link_skips_social_and_same_domain() -> None:
    assert (
        score_external_link(
            "seed.com",
            "https://seed.com",
            Link(url="https://x.com/example", text="AI NSFW updates"),
            depth=1,
        )
        is None
    )
    assert (
        score_external_link(
            "seed.com",
            "https://seed.com",
            Link(url="https://blog.seed.com/nsfw-ai", text="AI NSFW"),
            depth=1,
        )
        is None
    )


def test_discover_external_candidates_dedupes_by_best_domain_score() -> None:
    pages = [
        PageContent(
            url="https://seed.com/tools",
            title="Tools",
            text="",
            links=[],
            link_details=[
                Link(url="https://tool.example/nsfw", text="AI"),
                Link(url="https://tool.example/nsfw-generator", text="Uncensored AI generator"),
            ],
            status_code=200,
        )
    ]
    candidates = discover_external_candidates("seed.com", pages, depth=1, score_threshold=4, max_candidates=10)
    assert len(candidates) == 1
    assert candidates[0].domain == "tool.example"
    assert candidates[0].anchor_text == "Uncensored AI generator"
