from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path
from typing import Any

from .config import Settings, default_user_agent, load_dotenv
from .runner import ProgressReporter, RunCounters, run_discovery
from .storage import Database

try:
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
except ImportError:  # pragma: no cover
    Console = None
    Live = None
    Panel = None
    Table = None


class RichProgressReporter(ProgressReporter):
    def __init__(self) -> None:
        self.console = Console() if Console else None
        self.live: Any = None
        self.message = "Starting"
        self.counters = RunCounters()

    def start(self) -> None:
        if Live and self.console:
            self.live = Live(self._render(), console=self.console, refresh_per_second=4)
            self.live.start()

    def stop(self) -> None:
        if self.live:
            self.live.update(self._render())
            self.live.stop()

    def update(self, message: str, counters: RunCounters) -> None:
        self.message = message
        self.counters = counters
        if self.live:
            self.live.update(self._render())
        else:
            super().update(message, counters)

    def _render(self) -> Any:
        if not Table or not Panel:
            return self.message
        table = Table.grid(expand=True)
        table.add_column(justify="left")
        table.add_column(justify="right")
        table.add_row("Current", self.message)
        table.add_row("Search results", str(self.counters.search_results))
        table.add_row("Domains processed", str(self.counters.domains_processed))
        table.add_row("Accepted", str(self.counters.accepted))
        table.add_row("Uncertain", str(self.counters.uncertain))
        table.add_row("External candidates", str(self.counters.external_candidates))
        table.add_row("External queued", str(self.counters.external_queued))
        table.add_row("Errors", str(self.counters.errors))
        return Panel(table, title="AI NSFW Discovery", border_style="cyan")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nsfw-discovery",
        description="Discover AI-generated adult NSFW websites and public contacts.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    run = subcommands.add_parser("run", help="Run or resume discovery.")
    add_common_db_arg(run)
    run.add_argument("--query-file", help="Optional newline-delimited query file.")
    run.add_argument("--max-queries", type=int, default=0, help="0 means use all configured queries.")
    run.add_argument("--results-per-query", type=int, default=100)
    run.add_argument("--max-domains", type=int, default=5000)
    run.add_argument("--max-pages-per-domain", type=int, default=6)
    run.add_argument("--concurrency", type=int, default=5)
    run.add_argument("--timeout", type=float, default=30.0)
    run.add_argument("--retries", type=int, default=3)
    run.add_argument("--user-agent", default=default_user_agent())
    run.add_argument("--external-depth", type=int, default=1, help="External-link discovery depth. 0 disables it.")
    run.add_argument("--external-score-threshold", type=int, default=4)
    run.add_argument("--max-external-candidates", type=int, default=1000)
    run.add_argument(
        "--env-file",
        default=".env",
        help="Environment file to load before reading API settings. Use empty string to disable.",
    )
    run.add_argument(
        "--resume",
        action="store_true",
        help="Resume pending/error domains from the database. Existing discovered domains are always reused.",
    )

    status = subcommands.add_parser("status", help="Show database status.")
    add_common_db_arg(status)

    export = subcommands.add_parser("export", help="Export accepted and uncertain records.")
    add_common_db_arg(export)
    export.add_argument("--format", choices=["json", "csv"], default="json")
    export.add_argument("--output", required=True)
    export.add_argument("--accepted-only", action="store_true")

    serve = subcommands.add_parser("serve", help="Run the read-only HTML dashboard.")
    add_common_db_arg(serve)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--reload", action="store_true", help="Enable uvicorn auto-reload for development.")

    return parser


def add_common_db_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default="data/discovery.sqlite", help="SQLite database path.")


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    env_file = getattr(args, "env_file", None)
    if env_file:
        load_dotenv(env_file)
    if args.command == "run":
        settings = Settings.from_args(args)
        with Database(settings.db_path) as db:
            reporter = RichProgressReporter()
            counters = asyncio.run(run_discovery(settings, db, reporter))
        print(
            "Run complete: "
            f"results={counters.search_results}, processed={counters.domains_processed}, "
            f"accepted={counters.accepted}, uncertain={counters.uncertain}, "
            f"external={counters.external_candidates}/{counters.external_queued}, errors={counters.errors}"
        )
    elif args.command == "status":
        with Database(Path(args.db)) as db:
            print(json.dumps(db.stats(), indent=2, sort_keys=True))
    elif args.command == "export":
        with Database(Path(args.db)) as db:
            rows = db.export_rows(include_uncertain=not args.accepted_only)
        write_export(rows, Path(args.output), args.format)
        print(f"Exported {len(rows)} records to {args.output}")
    elif args.command == "serve":
        serve_dashboard(Path(args.db), args.host, args.port, args.reload)


def write_export(rows: list[dict[str, Any]], output: Path, format_name: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if format_name == "json":
        output.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
        return
    fieldnames = [
        "domain",
        "description",
        "confidence",
        "relevance_score",
        "accepted",
        "uncertain",
        "needs_js_review",
        "flags",
        "contacts",
        "updated_at",
    ]
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            serialized = dict(row)
            serialized["flags"] = json.dumps(serialized["flags"], sort_keys=True)
            serialized["contacts"] = json.dumps(serialized["contacts"], sort_keys=True)
            writer.writerow(serialized)


def serve_dashboard(db_path: Path, host: str, port: int, reload: bool) -> None:
    import os

    import uvicorn

    os.environ["NSFW_DISCOVERY_DB"] = str(db_path)
    if reload:
        uvicorn.run(
            "nsfw_discovery.web:app_from_env",
            factory=True,
            host=host,
            port=port,
            reload=True,
            log_level="info",
        )
        return

    from .web import create_app

    uvicorn.run(create_app(db_path), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
