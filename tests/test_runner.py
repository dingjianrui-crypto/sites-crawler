import pytest

from nsfw_discovery.config import Settings
from nsfw_discovery.models import PageContent, SearchResult, SearchScreening
from nsfw_discovery.runner import ProgressReporter, RunCounters, discover, process_domains
from nsfw_discovery.storage import Database


@pytest.mark.anyio
async def test_discover_screens_search_results_before_queueing(tmp_path, monkeypatch) -> None:
    class FakeSerpApiClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def search(self, query: str, _num_results: int) -> list[SearchResult]:
            return [
                SearchResult(
                    query=query,
                    title="AI NSFW image generator",
                    url="https://example.com",
                    snippet="Create uncensored adult AI images.",
                    domain="example.com",
                ),
                SearchResult(
                    query=query,
                    title="Yahoo Singapore",
                    url="https://sg.yahoo.com",
                    snippet="News, email, search, weather, finance, and sports.",
                    domain="yahoo.com",
                ),
            ]

    class FakeLlmClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def screen_search_result(self, result: SearchResult) -> SearchScreening:
            if result.domain == "example.com":
                return SearchScreening(keep=True, reason="direct service")
            return SearchScreening(keep=False, reason="general portal")

    monkeypatch.setattr("nsfw_discovery.runner.SerpApiClient", FakeSerpApiClient)
    monkeypatch.setattr("nsfw_discovery.runner.LlmClient", FakeLlmClient)

    settings = Settings.from_values(
        db_path=tmp_path / "discovery.sqlite",
        queries=["AI NSFW generator"],
        max_domains=10,
    )
    with Database(settings.db_path) as db:
        await discover(settings, db, ProgressReporter(), counters=RunCounters())
        pending = db.pending_domains(10)
        sources = db.conn.execute(
            "SELECT domain, relevance_score, relevance_reason, queued FROM search_sources ORDER BY domain"
        ).fetchall()

    assert pending == ["example.com"]
    assert [(row["domain"], row["relevance_score"], row["queued"]) for row in sources] == [
        ("example.com", 10, 1),
        ("yahoo.com", 0, 0),
    ]


@pytest.mark.anyio
async def test_process_domains_screens_external_candidates_before_queueing(tmp_path, monkeypatch) -> None:
    class FakeCrawler:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def crawl_domain(self, domain: str, _max_pages: int) -> list[PageContent]:
            return [
                PageContent(
                    url=f"https://{domain}/tools",
                    title="Tools",
                    text="AI NSFW generator directory.",
                    links=[],
                    status_code=200,
                )
            ]

    class FakeLlmClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def screen_external_candidate(self, candidate, _topics):
            if candidate.domain == "keep.example":
                return SearchScreening(keep=True, reason="context direct service")
            return SearchScreening(keep=False, reason="context unrelated")

        async def classify(self, *_args, **_kwargs):
            from nsfw_discovery.models import Classification

            return Classification(
                provides_ai_nsfw=True,
                description="Seed AI NSFW generator.",
                confidence="medium",
                relevance_score=5,
                accepted=True,
            )

    def fake_discover_external_candidates(*_args, **_kwargs):
        from nsfw_discovery.models import ExternalCandidate

        return [
            ExternalCandidate(
                domain="keep.example",
                url="https://keep.example/generator",
                source_domain="seed.example",
                source_url="https://seed.example/tools",
                anchor_text="Generator",
                score=4,
                reason="prefilter",
                depth=1,
                source_context="Uncensored AI generator for adult content.",
            ),
            ExternalCandidate(
                domain="reject.example",
                url="https://reject.example/pricing",
                source_domain="seed.example",
                source_url="https://seed.example/tools",
                anchor_text="Pricing",
                score=4,
                reason="prefilter",
                depth=1,
                source_context="General billing provider.",
            ),
        ]

    monkeypatch.setattr("nsfw_discovery.runner.HtmlCrawler", FakeCrawler)
    monkeypatch.setattr("nsfw_discovery.runner.LlmClient", FakeLlmClient)
    monkeypatch.setattr("nsfw_discovery.runner.discover_external_candidates", fake_discover_external_candidates)

    settings = Settings.from_values(
        db_path=tmp_path / "discovery.sqlite",
        queries=["AI NSFW generator"],
        external_depth=1,
    )
    with Database(settings.db_path) as db:
        db.conn.execute(
            """
            INSERT INTO domains(domain, status, discovery_depth)
            VALUES('seed.example', 'pending', 0)
            """
        )
        counters = RunCounters()
        await process_domains(settings, db, ["seed.example"], ProgressReporter(), counters)
        pending = db.pending_domains(10)
        external = db.conn.execute(
            "SELECT domain, queued, score, reason FROM external_candidates ORDER BY domain"
        ).fetchall()

    assert pending == ["keep.example"]
    assert counters.external_candidates == 2
    assert counters.external_queued == 1
    assert [(row["domain"], row["queued"], row["score"]) for row in external] == [
        ("keep.example", 1, 10),
        ("reject.example", 0, 0),
    ]
