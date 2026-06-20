from nsfw_discovery.models import (
    Classification,
    Contacts,
    ExternalCandidate,
    PageContent,
    SearchResult,
    SearchScreening,
)
from nsfw_discovery.storage import Database


def test_database_persists_search_page_and_classification(tmp_path) -> None:
    db_path = tmp_path / "discovery.sqlite"
    with Database(db_path) as db:
        db.upsert_search_result(
            SearchResult(
                query="AI NSFW generator",
                title="Example",
                url="https://example.com",
                snippet="AI adult generator",
                domain="example.com",
            )
        )
        assert db.pending_domains(10) == ["example.com"]
        db.save_page(
            "example.com",
            PageContent(
                url="https://example.com",
                title="Example",
                text="AI adult generator",
                links=[],
                status_code=200,
            ),
        )
        db.save_classification(
            "example.com",
            Classification(
                provides_ai_nsfw=True,
                description="Adult AI image generation platform.",
                confidence="medium",
                relevance_score=5,
                accepted=True,
            ),
            Contacts(emails=["support@example.com"]),
            needs_js_review=False,
        )
        rows = db.export_rows()
        listing = db.list_domains(search="example", has_contact="email")
        detail = db.domain_detail("example.com")
    assert rows[0]["domain"] == "example.com"
    assert rows[0]["relevance_score"] == 5
    assert rows[0]["contacts"]["emails"] == ["support@example.com"]
    assert listing["total"] == 1
    assert listing["items"][0]["domain"] == "example.com"
    assert listing["items"][0]["relevance_score"] == 5
    assert detail is not None
    assert detail["pages"][0]["url"] == "https://example.com"


def test_database_upserts_search_sources_without_duplicates(tmp_path) -> None:
    db_path = tmp_path / "discovery.sqlite"
    with Database(db_path) as db:
        queued = db.upsert_search_result(
            SearchResult(
                query="AI NSFW generator",
                title="Original",
                url="https://example.com/tool",
                snippet="Original snippet",
                domain="example.com",
            ),
            SearchScreening(keep=True, reason="direct match"),
        )
        db.upsert_search_result(
            SearchResult(
                query="AI NSFW generator",
                title="Updated",
                url="https://example.com/tool",
                snippet="Updated snippet",
                domain="example.com",
            ),
            SearchScreening(keep=True, reason="better match"),
        )
        detail = db.domain_detail("example.com")
        source = db.conn.execute(
            "SELECT relevance_score, relevance_reason, queued FROM search_sources"
        ).fetchone()
    assert queued
    assert detail is not None
    assert len(detail["search_sources"]) == 1
    assert detail["search_sources"][0]["title"] == "Updated"
    assert detail["search_sources"][0]["snippet"] == "Updated snippet"
    assert source["relevance_score"] == 10
    assert source["relevance_reason"] == "better match"
    assert source["queued"] == 1


def test_database_stores_rejected_search_source_without_queueing_domain(tmp_path) -> None:
    db_path = tmp_path / "discovery.sqlite"
    with Database(db_path) as db:
        queued = db.upsert_search_result(
            SearchResult(
                query="AI NSFW generator",
                title="Yahoo Singapore",
                url="https://sg.yahoo.com",
                snippet="News, email, search, weather, finance, sports, and consumer content.",
                domain="yahoo.com",
            ),
            SearchScreening(keep=False, reason="general portal"),
        )
        pending = db.pending_domains(10)
        source = db.conn.execute(
            "SELECT domain, relevance_score, relevance_reason, queued FROM search_sources"
        ).fetchone()
    assert not queued
    assert pending == []
    assert source["domain"] == "yahoo.com"
    assert source["relevance_score"] == 0
    assert source["relevance_reason"] == "general portal"
    assert source["queued"] == 0


def test_database_deletes_domain_and_related_rows(tmp_path) -> None:
    db_path = tmp_path / "discovery.sqlite"
    with Database(db_path) as db:
        db.upsert_search_result(
            SearchResult(
                query="AI NSFW generator",
                title="Example",
                url="https://example.com",
                snippet="AI adult generator",
                domain="example.com",
            )
        )
        db.save_page(
            "example.com",
            PageContent(
                url="https://example.com",
                title="Example",
                text="AI adult generator",
                links=[],
                status_code=200,
            ),
        )
        deleted = db.delete_domain("example.com")
        detail = db.domain_detail("example.com")
        source_count = db.conn.execute("SELECT COUNT(*) AS c FROM search_sources").fetchone()["c"]
        page_count = db.conn.execute("SELECT COUNT(*) AS c FROM pages").fetchone()["c"]
    assert deleted
    assert detail is None
    assert source_count == 0
    assert page_count == 0


def test_database_deletes_all_domains_but_keeps_tasks(tmp_path) -> None:
    db_path = tmp_path / "discovery.sqlite"
    with Database(db_path) as db:
        db.upsert_search_result(
            SearchResult(
                query="AI NSFW generator",
                title="Example",
                url="https://example.com",
                snippet="AI adult generator",
                domain="example.com",
            )
        )
        db.upsert_search_result(
            SearchResult(
                query="AI NSFW generator",
                title="Second",
                url="https://second.example",
                snippet="AI adult generator",
                domain="second.example",
            )
        )
        task_id = db.create_task({"queries": ["AI NSFW generator"], "run_search": False})
        deleted = db.delete_all_domains()
        stats = db.stats()
        task = db.get_task(task_id)
        source_count = db.conn.execute("SELECT COUNT(*) AS c FROM search_sources").fetchone()["c"]
    assert deleted == 2
    assert stats["total"] == 0
    assert source_count == 0
    assert task is not None


def test_database_tracks_discovery_tasks(tmp_path) -> None:
    db_path = tmp_path / "discovery.sqlite"
    with Database(db_path) as db:
        task_id = db.create_task({"queries": ["AI NSFW generator"], "run_search": False})
        assert db.has_running_task()
        db.start_task(task_id)
        db.update_task_progress(task_id, "Crawling example.com", {"domains_processed": 0})
        db.finish_task(task_id, "succeeded", {"domains_processed": 1})
        task = db.get_task(task_id)
        events = db.task_events(task_id)
    assert task is not None
    assert task["status"] == "succeeded"
    assert task["config"]["run_search"] is False
    assert task["counters"]["domains_processed"] == 1
    assert len(events) == 2
    with Database(db_path) as db:
        assert not db.has_running_task()


def test_database_queues_external_candidate_as_pending_domain(tmp_path) -> None:
    db_path = tmp_path / "discovery.sqlite"
    with Database(db_path) as db:
        inserted = db.upsert_external_candidate(
            ExternalCandidate(
                domain="external.example",
                url="https://external.example/nsfw-generator",
                source_domain="seed.example",
                source_url="https://seed.example/tools",
                anchor_text="Uncensored AI generator",
                score=8,
                reason="ai_terms:ai,generator;nsfw_terms:uncensored",
                depth=1,
            )
        )
        duplicate = db.upsert_external_candidate(
            ExternalCandidate(
                domain="external.example",
                url="https://external.example/nsfw-generator",
                source_domain="seed.example",
                source_url="https://seed.example/tools",
                anchor_text="Uncensored AI generator",
                score=8,
                reason="ai_terms:ai,generator;nsfw_terms:uncensored",
                depth=1,
            )
        )
        stats = db.stats()
        pending = db.pending_domains(10)
    assert inserted
    assert not duplicate
    assert pending == ["external.example"]
    assert stats["external_candidates"] == 1
    assert stats["external_queued"] == 1
