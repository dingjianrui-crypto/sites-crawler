from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Literal

from .models import Classification, Contacts, ExternalCandidate, PageContent, SearchResult, SearchScreening


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
  relevance_score INTEGER NOT NULL DEFAULT 0,
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
  relevance_score INTEGER NOT NULL DEFAULT 0,
  relevance_reason TEXT NOT NULL DEFAULT '',
  queued INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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

CREATE TABLE IF NOT EXISTS discovery_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  status TEXT NOT NULL DEFAULT 'queued',
  config_json TEXT NOT NULL,
  counters_json TEXT NOT NULL DEFAULT '{}',
  current_message TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS discovery_task_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL,
  level TEXT NOT NULL DEFAULT 'info',
  message TEXT NOT NULL,
  counters_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(task_id) REFERENCES discovery_tasks(id) ON DELETE CASCADE
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

    def upsert_search_result(
        self,
        result: SearchResult,
        screening: SearchScreening | None = None,
    ) -> bool:
        screening = screening or SearchScreening(keep=True, reason="legacy_insert")
        should_queue = screening.keep
        screening_score = 10 if should_queue else 0
        with self.conn:
            if should_queue:
                self.conn.execute(
                    """
                    INSERT INTO domains(domain, status, discovery_method, discovery_depth, relevance_score)
                    VALUES(?, 'pending', 'search', 0, ?)
                    ON CONFLICT(domain) DO UPDATE SET
                      relevance_score=MAX(relevance_score, excluded.relevance_score),
                      updated_at=CURRENT_TIMESTAMP
                    """,
                    (result.domain, screening_score),
                )
            self.conn.execute(
                """
                INSERT INTO search_sources(
                  domain, query, title, url, snippet, relevance_score, relevance_reason, queued, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(query, url) DO UPDATE SET
                  domain=excluded.domain,
                  title=excluded.title,
                  snippet=excluded.snippet,
                  relevance_score=excluded.relevance_score,
                  relevance_reason=excluded.relevance_reason,
                  queued=excluded.queued,
                  updated_at=CURRENT_TIMESTAMP
                """,
                (
                    result.domain,
                    result.query,
                    result.title,
                    result.url,
                    result.snippet,
                    screening_score,
                    screening.reason,
                    int(should_queue),
                ),
            )
        return should_queue

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

    def delete_domain(self, domain: str) -> bool:
        with self.conn:
            self.conn.execute("DELETE FROM external_candidates WHERE domain=? OR source_domain=?", (domain, domain))
            self.conn.execute("DELETE FROM search_sources WHERE domain=?", (domain,))
            cursor = self.conn.execute("DELETE FROM domains WHERE domain=?", (domain,))
        return cursor.rowcount > 0

    def delete_all_domains(self) -> int:
        with self.conn:
            self.conn.execute("DELETE FROM external_candidates")
            self.conn.execute("DELETE FROM search_sources")
            cursor = self.conn.execute("DELETE FROM domains")
        return cursor.rowcount

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
                    relevance_score=?,
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
                    int(classification.relevance_score),
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

    def create_task(self, config: dict[str, Any]) -> int:
        config_json = json.dumps(config, sort_keys=True)
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO discovery_tasks(config_json, current_message)
                VALUES(?, 'Queued')
                """,
                (config_json,),
            )
        return int(cursor.lastrowid)

    def start_task(self, task_id: int) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE discovery_tasks
                SET status='running',
                    current_message='Starting',
                    error='',
                    started_at=COALESCE(started_at, CURRENT_TIMESTAMP),
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (task_id,),
            )

    def update_task_progress(self, task_id: int, message: str, counters: dict[str, int]) -> None:
        counters_json = json.dumps(counters, sort_keys=True)
        with self.conn:
            self.conn.execute(
                """
                UPDATE discovery_tasks
                SET current_message=?,
                    counters_json=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (message[:1000], counters_json, task_id),
            )
            self.conn.execute(
                """
                INSERT INTO discovery_task_events(task_id, message, counters_json)
                VALUES(?, ?, ?)
                """,
                (task_id, message[:1000], counters_json),
            )

    def finish_task(
        self,
        task_id: int,
        status: Literal["succeeded", "failed", "cancelled"],
        counters: dict[str, int],
        error: str = "",
    ) -> None:
        message = "Completed" if status == "succeeded" else error[:1000] or status.title()
        with self.conn:
            self.conn.execute(
                """
                UPDATE discovery_tasks
                SET status=?,
                    current_message=?,
                    counters_json=?,
                    error=?,
                    finished_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (status, message, json.dumps(counters, sort_keys=True), error[:1000], task_id),
            )
            self.conn.execute(
                """
                INSERT INTO discovery_task_events(task_id, level, message, counters_json)
                VALUES(?, ?, ?, ?)
                """,
                (
                    task_id,
                    "error" if status == "failed" else "info",
                    message,
                    json.dumps(counters, sort_keys=True),
                ),
            )

    def has_running_task(self) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM discovery_tasks WHERE status IN ('queued', 'running') LIMIT 1"
        ).fetchone()
        return row is not None

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT id, status, config_json, counters_json, current_message, error,
                   started_at, finished_at, created_at, updated_at
            FROM discovery_tasks
            WHERE id=?
            """,
            (task_id,),
        ).fetchone()
        return self._task_row(row) if row else None

    def list_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, status, config_json, counters_json, current_message, error,
                   started_at, finished_at, created_at, updated_at
            FROM discovery_tasks
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (max(1, min(500, limit)),),
        ).fetchall()
        return [self._task_row(row) for row in rows]

    def task_events(self, task_id: int, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, task_id, level, message, counters_json, created_at
            FROM discovery_task_events
            WHERE task_id=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (task_id, max(1, min(1000, limit))),
        ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "task_id": int(row["task_id"]),
                "level": row["level"],
                "message": row["message"],
                "counters": json.loads(row["counters_json"] or "{}"),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def export_rows(self, include_uncertain: bool = True) -> list[dict[str, Any]]:
        where = "accepted=1 OR uncertain=1" if include_uncertain else "accepted=1"
        rows = self.conn.execute(
            f"""
            SELECT domain, description, confidence, relevance_score, accepted, uncertain,
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
                "relevance_score": int(row["relevance_score"]),
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
                   confidence, relevance_score, accepted, uncertain, needs_js_review, flags_json,
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
                   confidence, relevance_score, accepted, uncertain, needs_js_review, flags_json,
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
            if "relevance_score" not in domain_columns:
                self.conn.execute(
                    "ALTER TABLE domains ADD COLUMN relevance_score INTEGER NOT NULL DEFAULT 0"
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
            if "relevance_score" not in search_source_columns:
                self.conn.execute(
                    "ALTER TABLE search_sources ADD COLUMN relevance_score INTEGER NOT NULL DEFAULT 0"
                )
            if "relevance_reason" not in search_source_columns:
                self.conn.execute(
                    "ALTER TABLE search_sources ADD COLUMN relevance_reason TEXT NOT NULL DEFAULT ''"
                )
            if "queued" not in search_source_columns:
                self.conn.execute(
                    "ALTER TABLE search_sources ADD COLUMN queued INTEGER NOT NULL DEFAULT 1"
                )
            if self._search_sources_has_domain_fk():
                self._rebuild_search_sources_without_domain_fk()
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

    def _search_sources_has_domain_fk(self) -> bool:
        rows = self.conn.execute("PRAGMA foreign_key_list(search_sources)").fetchall()
        return any(row["table"] == "domains" for row in rows)

    def _rebuild_search_sources_without_domain_fk(self) -> None:
        self.conn.execute("ALTER TABLE search_sources RENAME TO search_sources_old")
        self.conn.execute(
            """
            CREATE TABLE search_sources (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              domain TEXT NOT NULL,
              query TEXT NOT NULL,
              title TEXT NOT NULL,
              url TEXT NOT NULL,
              snippet TEXT NOT NULL,
              relevance_score INTEGER NOT NULL DEFAULT 0,
              relevance_reason TEXT NOT NULL DEFAULT '',
              queued INTEGER NOT NULL DEFAULT 1,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.conn.execute(
            """
            INSERT INTO search_sources(
              id, domain, query, title, url, snippet,
              relevance_score, relevance_reason, queued, updated_at, created_at
            )
            SELECT
              id, domain, query, title, url, snippet,
              relevance_score, relevance_reason, queued, updated_at, created_at
            FROM search_sources_old
            """
        )
        self.conn.execute("DROP TABLE search_sources_old")

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
            "relevance_score": int(row["relevance_score"]),
            "accepted": bool(row["accepted"]),
            "uncertain": bool(row["uncertain"]),
            "needs_js_review": bool(row["needs_js_review"]),
            "flags": json.loads(row["flags_json"]),
            "contacts": json.loads(row["contacts_json"]),
            "error": row["error"],
            "discovered_at": row["discovered_at"],
            "updated_at": row["updated_at"],
        }

    def _task_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "status": row["status"],
            "config": json.loads(row["config_json"]),
            "counters": json.loads(row["counters_json"] or "{}"),
            "current_message": row["current_message"],
            "error": row["error"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
