from __future__ import annotations

import os
from html import escape
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

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
        accepted: Optional[bool] = None,
        uncertain: Optional[bool] = None,
        needs_js_review: Optional[bool] = None,
        has_contact: Optional[str] = Query(None, pattern="^(email|discord|telegram)$"),
        q: Optional[str] = None,
    ) -> dict[str, Any]:
        with Database(database_path) as db:
            return db.list_domains(
                page=page,
                page_size=page_size,
                status=empty_to_none(status),
                confidence=empty_to_none(confidence),
                accepted=accepted,
                uncertain=uncertain,
                needs_js_review=needs_js_review,
                has_contact=has_contact,  # type: ignore[arg-type]
                search=empty_to_none(q),
            )

    @app.get("/api/domains/{domain}")
    def api_domain_detail(domain: str) -> dict[str, Any]:
        with Database(database_path) as db:
            detail = db.domain_detail(domain)
        if detail is None:
            raise HTTPException(status_code=404, detail="domain not found")
        return detail

    @app.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        status: Optional[str] = None,
        confidence: Optional[str] = None,
        accepted: Optional[bool] = None,
        uncertain: Optional[bool] = None,
        needs_js_review: Optional[bool] = None,
        has_contact: Optional[str] = Query(None, pattern="^(email|discord|telegram)$"),
        q: Optional[str] = None,
    ) -> HTMLResponse:
        filters = {
            "status": empty_to_none(status),
            "confidence": empty_to_none(confidence),
            "accepted": accepted,
            "uncertain": uncertain,
            "needs_js_review": needs_js_review,
            "has_contact": has_contact,
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
                accepted=accepted,
                uncertain=uncertain,
                needs_js_review=needs_js_review,
                has_contact=has_contact,  # type: ignore[arg-type]
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

    return app


def empty_to_none(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return value


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
    <a href="/">Back</a>
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


def render_contacts(contacts: dict[str, list[str]]) -> str:
    blocks = []
    for key, values in contacts.items():
        links = "".join(f"<li>{escape(value)}</li>" for value in values)
        blocks.append(f"<h3>{escape(key.title())}</h3><ul>{links or '<li>-</li>'}</ul>")
    return "".join(blocks)


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
h1 { margin: 0 0 6px; font-size: 28px; letter-spacing: 0; }
h2 { margin-top: 28px; font-size: 18px; }
h3 { margin: 12px 0 4px; font-size: 14px; }
p { color: #59616d; }
a { color: #0b64c0; text-decoration: none; }
a:hover { text-decoration: underline; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 16px; }
.stat, .detail-grid div { background: white; border: 1px solid #dfe3e8; border-radius: 8px; padding: 12px; }
.stat span, .detail-grid span { display: block; color: #66707d; font-size: 12px; }
.stat strong, .detail-grid strong { display: block; margin-top: 4px; font-size: 20px; }
.filters { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; align-items: end; background: white; border: 1px solid #dfe3e8; border-radius: 8px; padding: 12px; margin-bottom: 16px; }
label { display: grid; gap: 4px; color: #505966; font-size: 12px; }
input, select, button, .secondary { min-height: 36px; border: 1px solid #c8d0d8; border-radius: 6px; padding: 0 10px; background: white; color: #20242a; font: inherit; box-sizing: border-box; }
button { background: #1469c8; color: white; border-color: #1469c8; cursor: pointer; }
.secondary { display: inline-flex; align-items: center; justify-content: center; }
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
