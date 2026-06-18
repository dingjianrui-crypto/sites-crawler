from fastapi.testclient import TestClient

from nsfw_discovery.models import Classification, Contacts, PageContent, SearchResult
from nsfw_discovery.storage import Database
from nsfw_discovery.web import create_app


def test_dashboard_api_and_html(tmp_path) -> None:
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
        db.save_classification(
            "example.com",
            Classification(
                provides_ai_nsfw=True,
                description="Adult AI image generation platform.",
                confidence="medium",
                accepted=True,
            ),
            Contacts(emails=["support@example.com"]),
            needs_js_review=False,
        )

    client = TestClient(create_app(db_path))
    stats = client.get("/api/stats")
    assert stats.status_code == 200
    assert stats.json()["accepted"] == 1

    domains = client.get("/api/domains", params={"has_contact": "email"})
    assert domains.status_code == 200
    assert domains.json()["items"][0]["domain"] == "example.com"

    detail = client.get("/api/domains/example.com")
    assert detail.status_code == 200
    assert detail.json()["contacts"]["emails"] == ["support@example.com"]

    html = client.get("/")
    assert html.status_code == 200
    assert "example.com" in html.text
