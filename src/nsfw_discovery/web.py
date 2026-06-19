from __future__ import annotations

import asyncio
import os
from dataclasses import asdict
from html import escape
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from .config import DEFAULT_QUERIES, Settings, default_user_agent
from .runner import DatabaseTaskReporter, RunCounters, run_discovery
from .storage import Database


def app_from_env() -> FastAPI:
    return create_app(os.environ.get("NSFW_DISCOVERY_DB", "data/discovery.sqlite"))


def create_app(db_path: str | Path) -> FastAPI:
    app = FastAPI(title="AI NSFW Discovery Dashboard")
    database_path = Path(db_path)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/stats")
    def api_stats() -> dict[str, int]:
        with Database(database_path) as db:
            return db.stats()

    @app.get("/api/domains")
    def api_domains(
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        status: Optional[str] = None,
        confidence: Optional[str] = None,
        accepted: Optional[str] = None,
        uncertain: Optional[str] = None,
        needs_js_review: Optional[str] = None,
        has_contact: Optional[str] = None,
        q: Optional[str] = None,
    ) -> dict[str, Any]:
        with Database(database_path) as db:
            return db.list_domains(
                page=page,
                page_size=page_size,
                status=empty_to_none(status),
                confidence=empty_to_none(confidence),
                accepted=parse_optional_bool(accepted),
                uncertain=parse_optional_bool(uncertain),
                needs_js_review=parse_optional_bool(needs_js_review),
                has_contact=parse_contact_filter(has_contact),
                search=empty_to_none(q),
            )

    @app.get("/api/domains/{domain}")
    def api_domain_detail(domain: str) -> dict[str, Any]:
        with Database(database_path) as db:
            detail = db.domain_detail(domain)
        if detail is None:
            raise HTTPException(status_code=404, detail="domain not found")
        return detail

    @app.get("/api/tasks")
    def api_tasks() -> list[dict[str, Any]]:
        with Database(database_path) as db:
            return db.list_tasks()

    @app.get("/api/tasks/{task_id}")
    def api_task_detail(task_id: int) -> dict[str, Any]:
        with Database(database_path) as db:
            task = db.get_task(task_id)
            events = db.task_events(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        task["events"] = events
        return task

    @app.get("/api/tasks/{task_id}/events")
    def api_task_events(task_id: int) -> list[dict[str, Any]]:
        with Database(database_path) as db:
            if db.get_task(task_id) is None:
                raise HTTPException(status_code=404, detail="task not found")
            return db.task_events(task_id)

    @app.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        status: Optional[str] = None,
        confidence: Optional[str] = None,
        accepted: Optional[str] = None,
        uncertain: Optional[str] = None,
        needs_js_review: Optional[str] = None,
        has_contact: Optional[str] = None,
        q: Optional[str] = None,
    ) -> HTMLResponse:
        filters = {
            "status": empty_to_none(status),
            "confidence": empty_to_none(confidence),
            "accepted": parse_optional_bool(accepted),
            "uncertain": parse_optional_bool(uncertain),
            "needs_js_review": parse_optional_bool(needs_js_review),
            "has_contact": parse_contact_filter(has_contact),
            "q": empty_to_none(q),
            "page_size": page_size,
        }
        with Database(database_path) as db:
            stats = db.stats()
            listing = db.list_domains(
                page=page,
                page_size=page_size,
                status=filters["status"],
                confidence=filters["confidence"],
                accepted=filters["accepted"],
                uncertain=filters["uncertain"],
                needs_js_review=filters["needs_js_review"],
                has_contact=filters["has_contact"],
                search=filters["q"],
            )
        return HTMLResponse(render_index(request, stats, listing, filters))

    @app.get("/domains/{domain}", response_class=HTMLResponse)
    def domain_page(domain: str) -> HTMLResponse:
        with Database(database_path) as db:
            detail = db.domain_detail(domain)
        if detail is None:
            raise HTTPException(status_code=404, detail="domain not found")
        return HTMLResponse(render_detail(detail))

    @app.get("/tasks", response_class=HTMLResponse)
    def tasks_page() -> HTMLResponse:
        with Database(database_path) as db:
            tasks = db.list_tasks()
        return HTMLResponse(render_tasks(tasks))

    @app.get("/tasks/new", response_class=HTMLResponse)
    def new_task_page() -> HTMLResponse:
        return HTMLResponse(render_new_task(default_task_config()))

    @app.post("/tasks")
    async def create_task(request: Request) -> Response:
        body = (await request.body()).decode("utf-8")
        form = {key: values[-1] for key, values in parse_qs(body, keep_blank_values=True).items()}
        config = task_config_from_form(form)
        with Database(database_path) as db:
            if db.has_running_task():
                return HTMLResponse(render_new_task(config, "A discovery task is already queued or running."), status_code=409)
            task_id = db.create_task(config)
        asyncio.create_task(run_task(database_path, task_id))
        return RedirectResponse(f"/tasks/{task_id}", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/tasks/{task_id}", response_class=HTMLResponse)
    def task_page(task_id: int) -> HTMLResponse:
        with Database(database_path) as db:
            task = db.get_task(task_id)
            events = db.task_events(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return HTMLResponse(render_task_detail(task, events))

    return app


def empty_to_none(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return value


def parse_optional_bool(value: str | None) -> bool | None:
    normalized = empty_to_none(value)
    if normalized is None:
        return None
    if normalized.lower() == "true":
        return True
    if normalized.lower() == "false":
        return False
    return None


def parse_contact_filter(value: str | None) -> str | None:
    normalized = empty_to_none(value)
    if normalized in {"email", "discord", "telegram"}:
        return normalized
    return None


async def run_task(db_path: Path, task_id: int) -> None:
    with Database(db_path) as db:
        task = db.get_task(task_id)
    if task is None:
        return
    counters = RunCounters()
    try:
        settings = Settings.from_task_config(db_path, task["config"])
        with Database(db_path) as db:
            reporter = DatabaseTaskReporter(db, task_id)
            counters = await run_discovery(settings, db, reporter)
            db.finish_task(task_id, "succeeded", asdict(counters))
    except Exception as exc:  # noqa: BLE001
        with Database(db_path) as db:
            db.finish_task(task_id, "failed", asdict(counters), str(exc))


def default_task_config() -> dict[str, Any]:
    return {
        "queries": list(DEFAULT_QUERIES),
        "max_queries": 0,
        "results_per_query": 100,
        "max_domains": 5000,
        "max_pages_per_domain": 6,
        "concurrency": 5,
        "timeout_seconds": 30.0,
        "retry_count": 3,
        "user_agent": default_user_agent(),
        "external_depth": 1,
        "external_score_threshold": 4,
        "max_external_candidates": 1000,
        "run_search": True,
        "retry_errors": True,
    }


def task_config_from_form(form: dict[str, str]) -> dict[str, Any]:
    defaults = default_task_config()
    queries = [
        line.strip()
        for line in form.get("queries", "\n".join(defaults["queries"])).splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return {
        "queries": queries or defaults["queries"],
        "max_queries": parse_int(form.get("max_queries"), defaults["max_queries"]),
        "results_per_query": parse_int(form.get("results_per_query"), defaults["results_per_query"]),
        "max_domains": parse_int(form.get("max_domains"), defaults["max_domains"]),
        "max_pages_per_domain": parse_int(
            form.get("max_pages_per_domain"), defaults["max_pages_per_domain"]
        ),
        "concurrency": parse_int(form.get("concurrency"), defaults["concurrency"]),
        "timeout_seconds": parse_float(form.get("timeout_seconds"), defaults["timeout_seconds"]),
        "retry_count": parse_int(form.get("retry_count"), defaults["retry_count"]),
        "user_agent": form.get("user_agent") or defaults["user_agent"],
        "external_depth": parse_int(form.get("external_depth"), defaults["external_depth"]),
        "external_score_threshold": parse_int(
            form.get("external_score_threshold"), defaults["external_score_threshold"]
        ),
        "max_external_candidates": parse_int(
            form.get("max_external_candidates"), defaults["max_external_candidates"]
        ),
        "run_search": form.get("run_search") == "on",
        "retry_errors": form.get("retry_errors") == "on",
    }


def parse_int(value: str | None, default: object) -> int:
    try:
        return int(value) if value not in (None, "") else int(default)
    except ValueError:
        return int(default)


def parse_float(value: str | None, default: object) -> float:
    try:
        return float(value) if value not in (None, "") else float(default)
    except ValueError:
        return float(default)


def render_index(
    request: Request,
    stats: dict[str, int],
    listing: dict[str, Any],
    filters: dict[str, Any],
) -> str:
    rows = "\n".join(render_domain_row(item) for item in listing["items"])
    stat_cards = "\n".join(
        f"<div class='stat'><span>{escape(key.replace('_', ' ').title())}</span><strong>{value}</strong></div>"
        for key, value in sorted(stats.items())
    )
    prev_url = page_url(request, max(1, listing["page"] - 1), filters)
    next_url = page_url(request, min(listing["pages"], listing["page"] + 1), filters)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI NSFW Discovery</title>
  {STYLE}
</head>
	<body>
	  <main>
	    {render_nav("domains")}
	    <header>
	      <h1>AI NSFW Discovery</h1>
	      <p>Read-only view of domains, contacts, crawl status, and classification results.</p>
	    </header>
    <section class="stats">{stat_cards}</section>
    {render_filters(filters)}
    <section class="table-wrap">
      <div class="table-meta">
        <span>{listing["total"]} records</span>
        <span>Page {listing["page"]} of {listing["pages"]}</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>Domain</th>
            <th>Description</th>
            <th>Confidence</th>
            <th>Status</th>
            <th>Contacts</th>
            <th>Flags</th>
            <th>Updated</th>
          </tr>
        </thead>
        <tbody>{rows or "<tr><td colspan='7' class='empty'>No records match the current filters.</td></tr>"}</tbody>
      </table>
    </section>
    <nav class="pager">
      <a href="{prev_url}">Previous</a>
      <a href="{next_url}">Next</a>
    </nav>
  </main>
</body>
</html>"""


def render_filters(filters: dict[str, Any]) -> str:
    return f"""<form class="filters" method="get" action="/">
  <label>Search <input name="q" value="{escape(filters.get("q") or "")}" placeholder="domain, description, contact"></label>
  <label>Status {select("status", ["", "pending", "crawling", "classifying", "done", "error"], filters.get("status"))}</label>
  <label>Confidence {select("confidence", ["", "high", "medium", "low", "unknown"], filters.get("confidence"))}</label>
  <label>Contact {select("has_contact", ["", "email", "discord", "telegram"], filters.get("has_contact"))}</label>
  <label>Accepted {select_bool("accepted", filters.get("accepted"))}</label>
  <label>Uncertain {select_bool("uncertain", filters.get("uncertain"))}</label>
  <label>Needs JS {select_bool("needs_js_review", filters.get("needs_js_review"))}</label>
  <label>Page Size <input name="page_size" type="number" min="1" max="200" value="{int(filters.get("page_size") or 50)}"></label>
  <button type="submit">Apply</button>
  <a class="secondary" href="/">Reset</a>
</form>"""


def render_domain_row(item: dict[str, Any]) -> str:
    contacts = item["contacts"]
    contact_bits = []
    for key in ("emails", "discord", "telegram", "other"):
        count = len(contacts.get(key, []))
        if count:
            contact_bits.append(f"{escape(key)}:{count}")
    badges = [
        badge("accepted", item["accepted"]),
        badge("uncertain", item["uncertain"]),
        badge("js", item["needs_js_review"]),
    ]
    flags = ", ".join(item["flags"])
    return f"""<tr>
  <td><a href="/domains/{escape(item["domain"])}">{escape(item["domain"])}</a><small>{escape(item["discovery_method"])} d{item["discovery_depth"]}</small></td>
  <td>{escape(item["description"] or item["error"] or "")}</td>
  <td>{escape(item["confidence"])}</td>
  <td>{''.join(badges)}<small>{escape(item["status"])}</small></td>
  <td>{escape(", ".join(contact_bits) or "-")}</td>
  <td>{escape(flags or "-")}</td>
  <td>{escape(item["updated_at"])}</td>
</tr>"""


def render_detail(detail: dict[str, Any]) -> str:
    contacts = detail["contacts"]
    sources = "".join(
        f"<li><a href='{escape(source['url'])}'>{escape(source['title'] or source['url'])}</a><small>{escape(source['query'])}</small></li>"
        for source in detail["search_sources"]
    )
    pages = "".join(
        f"<li><a href='{escape(page['url'])}'>{escape(page['title'] or page['url'])}</a><p>{escape(page['text_excerpt'][:500])}</p></li>"
        for page in detail["pages"]
    )
    external_sources = "".join(
        f"<li><a href='{escape(source['source_url'])}'>{escape(source['source_domain'])}</a><small>score {source['score']} {escape(source['reason'])}</small><p>{escape(source['anchor_text'])}</p></li>"
        for source in detail["external_sources"]
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(detail["domain"])}</title>
  {STYLE}
</head>
	<body>
	  <main>
	    {render_nav("domains")}
	    <header>
	      <h1>{escape(detail["domain"])}</h1>
      <p>{escape(detail["description"] or detail["error"] or "")}</p>
    </header>
    <section class="detail-grid">
      <div><span>Status</span><strong>{escape(detail["status"])}</strong></div>
      <div><span>Confidence</span><strong>{escape(detail["confidence"])}</strong></div>
      <div><span>Discovery</span><strong>{escape(detail["discovery_method"])} d{detail["discovery_depth"]}</strong></div>
      <div><span>Updated</span><strong>{escape(detail["updated_at"])}</strong></div>
    </section>
    <section><h2>Contacts</h2>{render_contacts(contacts)}</section>
    <section><h2>Flags</h2><p>{escape(", ".join(detail["flags"]) or "-")}</p></section>
    <section><h2>Search Sources</h2><ul>{sources or "<li>-</li>"}</ul></section>
    <section><h2>External Sources</h2><ul>{external_sources or "<li>-</li>"}</ul></section>
    <section><h2>Fetched Pages</h2><ul>{pages or "<li>-</li>"}</ul></section>
  </main>
</body>
	</html>"""


def render_tasks(tasks: list[dict[str, Any]]) -> str:
    rows = "\n".join(render_task_row(task) for task in tasks)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Discovery Tasks</title>
  {STYLE}
</head>
<body>
  <main>
    {render_nav("tasks")}
    <header class="split-header">
      <div>
        <h1>Discovery Tasks</h1>
        <p>Start crawler runs and inspect task history.</p>
      </div>
      <a class="button-link" href="/tasks/new">New Task</a>
    </header>
    <section class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Status</th>
            <th>Current</th>
            <th>Counters</th>
            <th>Queries</th>
            <th>Created</th>
            <th>Finished</th>
          </tr>
        </thead>
        <tbody>{rows or "<tr><td colspan='7' class='empty'>No tasks yet.</td></tr>"}</tbody>
      </table>
    </section>
  </main>
</body>
</html>"""


def render_task_row(task: dict[str, Any]) -> str:
    counters = task["counters"]
    config = task["config"]
    counter_text = (
        f"results {counters.get('search_results', 0)}, "
        f"processed {counters.get('domains_processed', 0)}, "
        f"errors {counters.get('errors', 0)}"
    )
    return f"""<tr>
  <td><a href="/tasks/{task["id"]}">#{task["id"]}</a></td>
  <td>{escape(task["status"])}</td>
  <td>{escape(task["current_message"] or "-")}</td>
  <td>{escape(counter_text)}</td>
  <td>{len(config.get("queries", []))}</td>
  <td>{escape(task["created_at"])}</td>
  <td>{escape(task["finished_at"] or "-")}</td>
</tr>"""


def render_new_task(config: dict[str, Any], error: str = "") -> str:
    queries = "\n".join(config.get("queries", DEFAULT_QUERIES))
    error_html = f"<p class='error'>{escape(error)}</p>" if error else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>New Discovery Task</title>
  {STYLE}
</head>
<body>
  <main>
    {render_nav("tasks")}
    <header>
      <h1>New Discovery Task</h1>
      <p>Configure a crawler run from the dashboard.</p>
    </header>
    {error_html}
    <form class="task-form" method="post" action="/tasks">
      <label class="wide">Queries <textarea name="queries" rows="8">{escape(queries)}</textarea></label>
      <label class="check-label"><input type="checkbox" name="run_search"{checked(config.get("run_search"))}> Run search discovery</label>
      <label class="check-label"><input type="checkbox" name="retry_errors"{checked(config.get("retry_errors"))}> Retry failed domains</label>
      {number_input("max_queries", "Max Queries", config)}
      {number_input("results_per_query", "Results Per Query", config)}
      {number_input("max_domains", "Max Domains", config)}
      {number_input("max_pages_per_domain", "Max Pages Per Domain", config)}
      {number_input("concurrency", "Concurrency", config)}
      {number_input("timeout_seconds", "Timeout Seconds", config, step="0.5")}
      {number_input("retry_count", "Retries", config)}
      {number_input("external_depth", "External Depth", config)}
      {number_input("external_score_threshold", "External Score Threshold", config)}
      {number_input("max_external_candidates", "Max External Candidates", config)}
      <label class="wide">User Agent <input name="user_agent" value="{escape(str(config.get("user_agent") or default_user_agent()))}"></label>
      <div class="form-actions wide">
        <button type="submit">Start Task</button>
        <a class="secondary" href="/tasks">Cancel</a>
      </div>
    </form>
  </main>
</body>
</html>"""


def render_task_detail(task: dict[str, Any], events: list[dict[str, Any]]) -> str:
    counters = task["counters"]
    config = task["config"]
    rows = "\n".join(render_event_row(event) for event in events)
    refresh = "<meta http-equiv='refresh' content='3'>" if task["status"] in {"queued", "running"} else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {refresh}
  <title>Task #{task["id"]}</title>
  {STYLE}
</head>
<body>
  <main>
    {render_nav("tasks")}
    <header>
      <h1>Task #{task["id"]}</h1>
      <p>{escape(task["current_message"] or task["status"])}</p>
    </header>
    <section class="detail-grid">
      <div><span>Status</span><strong>{escape(task["status"])}</strong></div>
      <div><span>Search Results</span><strong>{int(counters.get("search_results", 0))}</strong></div>
      <div><span>Processed</span><strong>{int(counters.get("domains_processed", 0))}</strong></div>
      <div><span>Errors</span><strong>{int(counters.get("errors", 0))}</strong></div>
      <div><span>Accepted</span><strong>{int(counters.get("accepted", 0))}</strong></div>
      <div><span>Uncertain</span><strong>{int(counters.get("uncertain", 0))}</strong></div>
    </section>
    <section>
      <h2>Configuration</h2>
      <pre>{escape(render_task_config(config))}</pre>
    </section>
    <section>
      <h2>Events</h2>
      <section class="table-wrap">
        <table>
          <thead><tr><th>Time</th><th>Level</th><th>Message</th><th>Counters</th></tr></thead>
          <tbody>{rows or "<tr><td colspan='4' class='empty'>No events yet.</td></tr>"}</tbody>
        </table>
      </section>
    </section>
  </main>
</body>
</html>"""


def render_event_row(event: dict[str, Any]) -> str:
    counters = event["counters"]
    counter_text = (
        f"results {counters.get('search_results', 0)}, "
        f"processed {counters.get('domains_processed', 0)}, "
        f"errors {counters.get('errors', 0)}"
    )
    return f"""<tr>
  <td>{escape(event["created_at"])}</td>
  <td>{escape(event["level"])}</td>
  <td>{escape(event["message"])}</td>
  <td>{escape(counter_text)}</td>
</tr>"""


def render_contacts(contacts: dict[str, list[str]]) -> str:
    blocks = []
    for key, values in contacts.items():
        links = "".join(f"<li>{escape(value)}</li>" for value in values)
        blocks.append(f"<h3>{escape(key.title())}</h3><ul>{links or '<li>-</li>'}</ul>")
    return "".join(blocks)


def render_nav(active: str) -> str:
    return f"""<nav class="top-nav">
  <a class="{active_class(active, "domains")}" href="/">Domains</a>
  <a class="{active_class(active, "tasks")}" href="/tasks">Tasks</a>
</nav>"""


def active_class(active: str, name: str) -> str:
    return "active" if active == name else ""


def checked(value: object) -> str:
    return " checked" if bool(value) else ""


def number_input(
    name: str,
    label: str,
    config: dict[str, Any],
    *,
    step: str = "1",
) -> str:
    value = escape(str(config.get(name, "")))
    return f"<label>{escape(label)} <input name='{escape(name)}' type='number' min='0' step='{escape(step)}' value='{value}'></label>"


def render_task_config(config: dict[str, Any]) -> str:
    display = dict(config)
    display["queries"] = "\n".join(config.get("queries", []))
    return "\n".join(f"{key}: {value}" for key, value in display.items())


def select(name: str, options: list[str], selected: str | None) -> str:
    option_html = []
    for option in options:
        label = option or "any"
        selected_attr = " selected" if option == (selected or "") else ""
        option_html.append(f"<option value='{escape(option)}'{selected_attr}>{escape(label)}</option>")
    return f"<select name='{name}'>{''.join(option_html)}</select>"


def select_bool(name: str, selected: bool | None) -> str:
    selected_value = "" if selected is None else str(selected).lower()
    return select(name, ["", "true", "false"], selected_value)


def badge(name: str, enabled: bool) -> str:
    if not enabled:
        return ""
    return f"<span class='badge'>{escape(name)}</span>"


def page_url(request: Request, page: int, filters: dict[str, Any]) -> str:
    params = {
        key: value
        for key, value in filters.items()
        if value is not None and value != ""
    }
    params["page"] = page
    return f"{request.url.path}?{urlencode(params)}"


STYLE = """<style>
:root { color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
body { margin: 0; background: #f6f7f9; color: #20242a; }
	main { width: min(1440px, calc(100vw - 32px)); margin: 0 auto; padding: 28px 0 48px; }
	header { margin-bottom: 20px; }
	.split-header { display: flex; align-items: end; justify-content: space-between; gap: 16px; }
	h1 { margin: 0 0 6px; font-size: 28px; letter-spacing: 0; }
	h2 { margin-top: 28px; font-size: 18px; }
	h3 { margin: 12px 0 4px; font-size: 14px; }
	p { color: #59616d; }
	a { color: #0b64c0; text-decoration: none; }
	a:hover { text-decoration: underline; }
	pre { overflow: auto; background: white; border: 1px solid #dfe3e8; border-radius: 8px; padding: 12px; color: #2f3742; }
	.top-nav { display: flex; gap: 8px; margin-bottom: 20px; }
	.top-nav a { padding: 8px 12px; border: 1px solid #d3d9df; border-radius: 6px; color: #334155; background: white; }
	.top-nav a.active { background: #1469c8; border-color: #1469c8; color: white; }
	.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 16px; }
	.stat, .detail-grid div { background: white; border: 1px solid #dfe3e8; border-radius: 8px; padding: 12px; }
	.stat span, .detail-grid span { display: block; color: #66707d; font-size: 12px; }
	.stat strong, .detail-grid strong { display: block; margin-top: 4px; font-size: 20px; }
	.filters, .task-form { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 10px; align-items: end; background: white; border: 1px solid #dfe3e8; border-radius: 8px; padding: 12px; margin-bottom: 16px; }
	label { display: grid; gap: 4px; color: #505966; font-size: 12px; }
	input, select, button, .secondary, textarea, .button-link { min-height: 36px; border: 1px solid #c8d0d8; border-radius: 6px; padding: 0 10px; background: white; color: #20242a; font: inherit; box-sizing: border-box; }
	textarea { min-height: 160px; padding: 10px; resize: vertical; }
	input[type="checkbox"] { min-height: auto; width: 16px; height: 16px; margin: 0; }
	.check-label { display: flex; align-items: center; gap: 8px; min-height: 36px; }
	button { background: #1469c8; color: white; border-color: #1469c8; cursor: pointer; }
	.secondary, .button-link { display: inline-flex; align-items: center; justify-content: center; }
	.button-link { background: #1469c8; color: white; border-color: #1469c8; }
	.wide { grid-column: 1 / -1; }
	.form-actions { display: flex; gap: 10px; }
	.error { padding: 10px 12px; border: 1px solid #f1b8b8; background: #fff4f4; border-radius: 8px; color: #a12f2f; }
	.table-wrap { background: white; border: 1px solid #dfe3e8; border-radius: 8px; overflow: auto; }
.table-meta { display: flex; justify-content: space-between; padding: 12px; color: #66707d; border-bottom: 1px solid #e7ebef; }
table { width: 100%; border-collapse: collapse; min-width: 1100px; }
th, td { text-align: left; vertical-align: top; padding: 10px 12px; border-bottom: 1px solid #edf0f3; font-size: 13px; }
th { color: #4b5563; background: #fafbfc; }
td small { display: block; color: #7a8491; margin-top: 4px; }
.badge { display: inline-block; margin: 0 4px 4px 0; padding: 2px 6px; border-radius: 999px; background: #e8f1fb; color: #185fa6; font-size: 12px; }
.empty { text-align: center; color: #66707d; padding: 24px; }
.pager { display: flex; justify-content: flex-end; gap: 10px; margin-top: 12px; }
.pager a { background: white; border: 1px solid #d3d9df; border-radius: 6px; padding: 8px 12px; }
.detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }
section ul { background: white; border: 1px solid #dfe3e8; border-radius: 8px; padding: 12px 12px 12px 28px; }
section li { margin-bottom: 10px; }
</style>"""
