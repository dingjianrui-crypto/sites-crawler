import pytest

from nsfw_discovery.config import Settings
from nsfw_discovery.models import SearchResult, SearchScreening
from nsfw_discovery.runner import ProgressReporter, RunCounters, discover
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
