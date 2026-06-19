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
    assert "Delete" in html.text

    filtered_html = client.get(
        "/",
        params={
            "status": "done",
            "confidence": "",
            "has_contact": "",
            "accepted": "",
            "uncertain": "",
            "needs_js_review": "",
            "page_size": "50",
        },
    )
    assert filtered_html.status_code == 200
    assert "example.com" in filtered_html.text

    filtered_api = client.get(
        "/api/domains",
        params={
            "has_contact": "",
            "accepted": "",
            "uncertain": "",
            "needs_js_review": "",
        },
    )
    assert filtered_api.status_code == 200


def test_domain_delete_button_removes_record(tmp_path) -> None:
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

    client = TestClient(create_app(db_path))
    response = client.post("/domains/example.com/delete", follow_redirects=False)
    assert response.status_code == 303

    detail = client.get("/api/domains/example.com")
    assert detail.status_code == 404


def test_task_pages_and_api(tmp_path) -> None:
    db_path = tmp_path / "discovery.sqlite"
    with Database(db_path) as db:
        task_id = db.create_task({"queries": ["AI NSFW generator"], "run_search": False})
        db.start_task(task_id)
        db.update_task_progress(task_id, "Search discovery disabled", {"search_results": 0})
        db.finish_task(task_id, "succeeded", {"domains_processed": 0})

    client = TestClient(create_app(db_path))
    tasks = client.get("/tasks")
    assert tasks.status_code == 200
    assert f"#{task_id}" in tasks.text

    new_task = client.get("/tasks/new")
    assert new_task.status_code == 200
    assert "AI NSFW generator" in new_task.text

    detail = client.get(f"/tasks/{task_id}")
    assert detail.status_code == 200
    assert "Search discovery disabled" in detail.text

    api_detail = client.get(f"/api/tasks/{task_id}")
    assert api_detail.status_code == 200
    assert api_detail.json()["status"] == "succeeded"
    assert len(api_detail.json()["events"]) == 2

    api_events = client.get(f"/api/tasks/{task_id}/events")
    assert api_events.status_code == 200
    assert api_events.json()[0]["message"] == "Completed"


def test_new_task_page_uses_configured_user_agent(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NSFW_DISCOVERY_USER_AGENT", "Mozilla/5.0 Configured Browser")

    client = TestClient(create_app(tmp_path / "discovery.sqlite"))
    response = client.get("/tasks/new")

    assert response.status_code == 200
    assert "Mozilla/5.0 Configured Browser" in response.text
