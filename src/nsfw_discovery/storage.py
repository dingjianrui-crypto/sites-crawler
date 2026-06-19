from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Literal

from .models import Classification, Contacts, ExternalCandidate, PageContent, SearchResult


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS domains (
  domain TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'pending',
  discovery_method TEXT NOT NULL DEFAULT 'search',
  discovery_depth INTEGER NOT NULL DEFAULT 0,
  description TEXT NOT NULL DEFAULT '',
  confidence TEXT NOT NULL DEFAULT 'unknown',
  accepted INTEGER NOT NULL DEFAULT 0,
  uncertain INTEGER NOT NULL DEFAULT 0,
  needs_js_review INTEGER NOT NULL DEFAULT 0,
  flags_json TEXT NOT NULL DEFAULT '[]',
  contacts_json TEXT NOT NULL DEFAULT '{"emails":[],"discord":[],"telegram":[],"other":[]}',
  error TEXT NOT NULL DEFAULT '',
  discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS search_sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  domain TEXT NOT NULL,
  query TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  snippet TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(domain) REFERENCES domains(domain) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS pages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  domain TEXT NOT NULL,
  url TEXT NOT NULL,
  title TEXT NOT NULL,
  text_excerpt TEXT NOT NULL,
  status_code INTEGER NOT NULL,
  needs_js_review INTEGER NOT NULL DEFAULT 0,
  fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(domain, url),
  FOREIGN KEY(domain) REFERENCES domains(domain) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS external_candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  domain TEXT NOT NULL,
  url TEXT NOT NULL,
  source_domain TEXT NOT NULL,
  source_url TEXT NOT NULL,
  anchor_text TEXT NOT NULL,
  score INTEGER NOT NULL,
  reason TEXT NOT NULL,
  depth INTEGER NOT NULL,
  queued INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(domain, source_domain, source_url, url)
);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def upsert_search_result(self, result: SearchResult) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO domains(domain, status, discovery_method, discovery_depth)
                VALUES(?, 'pending', 'search', 0)
                ON CONFLICT(domain) DO UPDATE SET updated_at=CURRENT_TIMESTAMP
                """,
                (result.domain,),
            )
            self.conn.execute(
                """
                INSERT INTO search_sources(domain, query, title, url, snippet, updated_at)
                VALUES(?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(query, url) DO UPDATE SET
                  domain=excluded.domain,
                  title=excluded.title,
                  snippet=excluded.snippet,
                  updated_at=CURRENT_TIMESTAMP
                """,
                (result.domain, result.query, result.title, result.url, result.snippet),
            )

    def upsert_external_candidate(self, candidate: ExternalCandidate, queue: bool = True) -> bool:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO external_candidates(
                  domain, url, source_domain, source_url, anchor_text, score, reason, depth, queued
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(domain, source_domain, source_url, url) DO UPDATE SET
                  score=MAX(score, excluded.score),
                  reason=excluded.reason,
                  queued=MAX(queued, excluded.queued)
                """,
                (
                    candidate.domain,
                    candidate.url,
                    candidate.source_domain,
                    candidate.source_url,
                    candidate.anchor_text,
                    candidate.score,
                    candidate.reason,
                    candidate.depth,
                    int(queue),
                ),
            )
            inserted = self.conn.execute(
                """
                INSERT INTO domains(domain, status, discovery_method, discovery_depth)
                VALUES(?, 'pending', 'external_link', ?)
                ON CONFLICT(domain) DO NOTHING
                """,
                (candidate.domain, candidate.depth),
            ).rowcount
        return bool(inserted)

    def pending_domains(self, limit: int, include_errors: bool = False) -> list[str]:
        statuses = "('pending', 'error')" if include_errors else "('pending')"
        status_filter = f"WHERE status IN {statuses}"
        rows = self.conn.execute(
            f"SELECT domain FROM domains {status_filter} ORDER BY discovered_at LIMIT ?",
            (limit,),
        ).fetchall()
        return [row["domain"] for row in rows]

    def domain_depth(self, domain: str) -> int:
        row = self.conn.execute(
            "SELECT discovery_depth FROM domains WHERE domain=?",
            (domain,),
        ).fetchone()
        return int(row["discovery_depth"]) if row else 0

    def mark_crawling(self, domain: str) -> None:
        self._set_status(domain, "crawling")

    def mark_classifying(self, domain: str) -> None:
        self._set_status(domain, "classifying")

    def mark_error(self, domain: str, error: str) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE domains
                SET status='error', error=?, updated_at=CURRENT_TIMESTAMP
                WHERE domain=?
                """,
                (error[:1000], domain),
            )

    def save_page(self, domain: str, page: PageContent) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO pages(domain, url, title, text_excerpt, status_code, needs_js_review)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(domain, url) DO UPDATE SET
                  title=excluded.title,
                  text_excerpt=excluded.text_excerpt,
                  status_code=excluded.status_code,
                  needs_js_review=excluded.needs_js_review,
                  fetched_at=CURRENT_TIMESTAMP
                """,
                (
                    domain,
                    page.url,
                    page.title[:500],
                    page.text[:5000],
                    page.status_code,
                    int(page.needs_js_review),
                ),
            )

    def save_classification(
        self,
        domain: str,
        classification: Classification,
        contacts: Contacts,
        needs_js_review: bool,
    ) -> None:
        contacts_json = json.dumps(
            {
                "emails": contacts.emails,
                "discord": contacts.discord,
                "telegram": contacts.telegram,
                "other": contacts.other,
            },
            sort_keys=True,
        )
        with self.conn:
            self.conn.execute(
                """
                UPDATE domains
                SET status='done',
                    description=?,
                    confidence=?,
                    accepted=?,
                    uncertain=?,
                    needs_js_review=?,
                    flags_json=?,
                    contacts_json=?,
                    error='',
                    updated_at=CURRENT_TIMESTAMP
                WHERE domain=?
                """,
                (
                    classification.description[:1000],
                    classification.confidence,
                    int(classification.accepted),
                    int(classification.uncertain),
                    int(needs_js_review),
                    json.dumps(classification.flags, sort_keys=True),
                    contacts_json,
                    domain,
                ),
            )

    def stats(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS count FROM domains GROUP BY status"
        ).fetchall()
        stats = {row["status"]: int(row["count"]) for row in rows}
        accepted = self.conn.execute("SELECT COUNT(*) AS c FROM domains WHERE accepted=1").fetchone()
        uncertain = self.conn.execute("SELECT COUNT(*) AS c FROM domains WHERE uncertain=1").fetchone()
        stats["accepted"] = int(accepted["c"])
        stats["uncertain"] = int(uncertain["c"])
        external = self.conn.execute("SELECT COUNT(*) AS c FROM external_candidates").fetchone()
        queued_external = self.conn.execute(
            "SELECT COUNT(*) AS c FROM external_candidates WHERE queued=1"
        ).fetchone()
        stats["external_candidates"] = int(external["c"])
        stats["external_queued"] = int(queued_external["c"])
        meta_keys = {"accepted", "uncertain", "external_candidates", "external_queued"}
        stats["total"] = sum(value for key, value in stats.items() if key not in meta_keys)
        return stats

    def export_rows(self, include_uncertain: bool = True) -> list[dict[str, Any]]:
        where = "accepted=1 OR uncertain=1" if include_uncertain else "accepted=1"
        rows = self.conn.execute(
            f"""
            SELECT domain, description, confidence, accepted, uncertain,
                   needs_js_review, flags_json, contacts_json, updated_at
            FROM domains
            WHERE {where}
            ORDER BY accepted DESC, confidence DESC, domain
            """
        ).fetchall()
        return [
            {
                "domain": row["domain"],
                "description": row["description"],
                "confidence": row["confidence"],
                "accepted": bool(row["accepted"]),
                "uncertain": bool(row["uncertain"]),
                "needs_js_review": bool(row["needs_js_review"]),
                "flags": json.loads(row["flags_json"]),
                "contacts": json.loads(row["contacts_json"]),
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def list_domains(
        self,
        page: int = 1,
        page_size: int = 50,
        status: str | None = None,
        confidence: str | None = None,
        accepted: bool | None = None,
        uncertain: bool | None = None,
        needs_js_review: bool | None = None,
        has_contact: Literal["email", "discord", "telegram"] | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        page = max(1, page)
        page_size = min(200, max(1, page_size))
        where, params = self._domain_filters(
            status=status,
            confidence=confidence,
            accepted=accepted,
            uncertain=uncertain,
            needs_js_review=needs_js_review,
            has_contact=has_contact,
            search=search,
        )
        total = self.conn.execute(
            f"SELECT COUNT(*) AS c FROM domains {where}",
            params,
        ).fetchone()["c"]
        rows = self.conn.execute(
            f"""
            SELECT domain, status, discovery_method, discovery_depth, description,
                   confidence, accepted, uncertain, needs_js_review, flags_json,
                   contacts_json, error, discovered_at, updated_at
            FROM domains
            {where}
            ORDER BY accepted DESC, uncertain DESC, updated_at DESC, domain
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        return {
            "items": [self._domain_row(row) for row in rows],
            "total": int(total),
            "page": page,
            "page_size": page_size,
            "pages": max(1, (int(total) + page_size - 1) // page_size),
        }

    def domain_detail(self, domain: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT domain, status, discovery_method, discovery_depth, description,
                   confidence, accepted, uncertain, needs_js_review, flags_json,
                   contacts_json, error, discovered_at, updated_at
            FROM domains
            WHERE domain=?
            """,
            (domain,),
        ).fetchone()
        if not row:
            return None
        detail = self._domain_row(row)
        detail["pages"] = [
            dict(page)
            for page in self.conn.execute(
                """
                SELECT url, title, text_excerpt, status_code, needs_js_review, fetched_at
                FROM pages
                WHERE domain=?
                ORDER BY fetched_at DESC, url
                """,
                (domain,),
            ).fetchall()
        ]
        detail["search_sources"] = [
            dict(source)
            for source in self.conn.execute(
                """
                SELECT query, title, url, snippet, created_at
                FROM search_sources
                WHERE domain=?
                ORDER BY created_at DESC
                """,
                (domain,),
            ).fetchall()
        ]
        detail["external_sources"] = [
            dict(candidate)
            for candidate in self.conn.execute(
                """
                SELECT source_domain, source_url, url, anchor_text, score, reason, depth, queued, created_at
                FROM external_candidates
                WHERE domain=?
                ORDER BY score DESC, created_at DESC
                """,
                (domain,),
            ).fetchall()
        ]
        return detail

    def _set_status(self, domain: str, status: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE domains SET status=?, updated_at=CURRENT_TIMESTAMP WHERE domain=?",
                (status, domain),
            )

    def _migrate(self) -> None:
        domain_columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(domains)").fetchall()
        }
        search_source_columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(search_sources)").fetchall()
        }
        with self.conn:
            if "discovery_method" not in domain_columns:
                self.conn.execute(
                    "ALTER TABLE domains ADD COLUMN discovery_method TEXT NOT NULL DEFAULT 'search'"
                )
            if "discovery_depth" not in domain_columns:
                self.conn.execute(
                    "ALTER TABLE domains ADD COLUMN discovery_depth INTEGER NOT NULL DEFAULT 0"
                )
            if "updated_at" not in search_source_columns:
                self.conn.execute(
                    "ALTER TABLE search_sources ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''"
                )
                self.conn.execute(
                    """
                    UPDATE search_sources
                    SET updated_at=created_at
                    WHERE updated_at=''
                    """
                )
            self.conn.execute(
                """
                DELETE FROM search_sources
                WHERE id NOT IN (
                  SELECT MAX(id)
                  FROM search_sources
                  GROUP BY query, url
                )
                """
            )
            self.conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_search_sources_query_url
                ON search_sources(query, url)
                """
            )

    def _domain_filters(
        self,
        status: str | None = None,
        confidence: str | None = None,
        accepted: bool | None = None,
        uncertain: bool | None = None,
        needs_js_review: bool | None = None,
        has_contact: Literal["email", "discord", "telegram"] | None = None,
        search: str | None = None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if confidence:
            clauses.append("confidence = ?")
            params.append(confidence)
        if accepted is not None:
            clauses.append("accepted = ?")
            params.append(int(accepted))
        if uncertain is not None:
            clauses.append("uncertain = ?")
            params.append(int(uncertain))
        if needs_js_review is not None:
            clauses.append("needs_js_review = ?")
            params.append(int(needs_js_review))
        if has_contact == "email":
            clauses.append("contacts_json LIKE ?")
            params.append('%"emails": ["_%')
        elif has_contact == "discord":
            clauses.append("contacts_json LIKE ?")
            params.append('%"discord": ["_%')
        elif has_contact == "telegram":
            clauses.append("contacts_json LIKE ?")
            params.append('%"telegram": ["_%')
        if search:
            clauses.append("(domain LIKE ? OR description LIKE ? OR contacts_json LIKE ?)")
            pattern = f"%{search}%"
            params.extend([pattern, pattern, pattern])
        if not clauses:
            return "", params
        return "WHERE " + " AND ".join(clauses), params

    def _domain_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "domain": row["domain"],
            "status": row["status"],
            "discovery_method": row["discovery_method"],
            "discovery_depth": int(row["discovery_depth"]),
            "description": row["description"],
            "confidence": row["confidence"],
            "accepted": bool(row["accepted"]),
            "uncertain": bool(row["uncertain"]),
            "needs_js_review": bool(row["needs_js_review"]),
            "flags": json.loads(row["flags_json"]),
            "contacts": json.loads(row["contacts_json"]),
            "error": row["error"],
            "discovered_at": row["discovered_at"],
            "updated_at": row["updated_at"],
        }
