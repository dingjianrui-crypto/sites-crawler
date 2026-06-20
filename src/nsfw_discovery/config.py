from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_QUERIES = [
    "AI NSFW generator",
    "AI adult image generator",
    "uncensored AI image generator",
    "AI porn generator",
    "AI girlfriend NSFW",
    "adult AI art generator",
    "NSFW AI chatbot",
    "AI hentai generator",
]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
USER_AGENT_ENV = "NSFW_DISCOVERY_USER_AGENT"


def load_dotenv(path: str | Path = ".env", override: bool = False) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = _parse_env_value(value.strip())
        if override or key not in os.environ:
            os.environ[key] = value


def _parse_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value


def default_user_agent() -> str:
    return os.getenv(USER_AGENT_ENV) or DEFAULT_USER_AGENT


@dataclass(frozen=True)
class Settings:
    db_path: Path
    serpapi_api_key: str | None
    llm_base_url: str | None
    llm_api_key: str | None
    llm_model: str
    queries: list[str]
    max_queries: int
    results_per_query: int
    max_domains: int
    max_pages_per_domain: int
    concurrency: int
    timeout_seconds: float
    retry_count: int
    user_agent: str
    external_depth: int
    max_external_candidates: int
    run_search: bool = True
    retry_errors: bool = True

    @classmethod
    def from_args(cls, args: object) -> "Settings":
        query_file = getattr(args, "query_file", None)
        if query_file:
            queries = [
                line.strip()
                for line in Path(query_file).read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
        else:
            queries = list(DEFAULT_QUERIES)

        return cls.from_values(
            db_path=Path(getattr(args, "db")).expanduser(),
            queries=queries,
            max_queries=getattr(args, "max_queries"),
            results_per_query=getattr(args, "results_per_query"),
            max_domains=getattr(args, "max_domains"),
            max_pages_per_domain=getattr(args, "max_pages_per_domain"),
            concurrency=getattr(args, "concurrency"),
            timeout_seconds=getattr(args, "timeout"),
            retry_count=getattr(args, "retries"),
            user_agent=getattr(args, "user_agent"),
            external_depth=getattr(args, "external_depth"),
            max_external_candidates=getattr(args, "max_external_candidates"),
        )

    @classmethod
    def from_values(
        cls,
        *,
        db_path: Path,
        queries: list[str] | None = None,
        max_queries: int | str = 0,
        results_per_query: int | str = 100,
        max_domains: int | str = 5000,
        max_pages_per_domain: int | str = 6,
        concurrency: int | str = 5,
        timeout_seconds: float | str = 30.0,
        retry_count: int | str = 3,
        user_agent: str | None = None,
        external_depth: int | str = 10,
        max_external_candidates: int | str = 1000,
        run_search: bool = True,
        retry_errors: bool = True,
    ) -> "Settings":
        return cls(
            db_path=db_path,
            serpapi_api_key=os.getenv("SERPAPI_API_KEY"),
            llm_base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_api_key=os.getenv("LLM_API_KEY"),
            llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            queries=list(queries or DEFAULT_QUERIES),
            max_queries=max(0, int(max_queries)),
            results_per_query=max(1, int(results_per_query)),
            max_domains=max(1, int(max_domains)),
            max_pages_per_domain=max(1, int(max_pages_per_domain)),
            concurrency=max(1, int(concurrency)),
            timeout_seconds=float(timeout_seconds),
            retry_count=max(0, int(retry_count)),
            user_agent=user_agent or default_user_agent(),
            external_depth=max(0, int(external_depth)),
            max_external_candidates=max(0, int(max_external_candidates)),
            run_search=run_search,
            retry_errors=retry_errors,
        )

    @classmethod
    def from_task_config(cls, db_path: Path, config: dict[str, object]) -> "Settings":
        queries = [
            str(query).strip()
            for query in config.get("queries", DEFAULT_QUERIES)  # type: ignore[arg-type]
            if str(query).strip()
        ]
        return cls.from_values(
            db_path=db_path,
            queries=queries,
            max_queries=config.get("max_queries", 0),  # type: ignore[arg-type]
            results_per_query=config.get("results_per_query", 100),  # type: ignore[arg-type]
            max_domains=config.get("max_domains", 5000),  # type: ignore[arg-type]
            max_pages_per_domain=config.get("max_pages_per_domain", 6),  # type: ignore[arg-type]
            concurrency=config.get("concurrency", 5),  # type: ignore[arg-type]
            timeout_seconds=config.get("timeout_seconds", 30.0),  # type: ignore[arg-type]
            retry_count=config.get("retry_count", 3),  # type: ignore[arg-type]
            user_agent=str(config.get("user_agent") or default_user_agent()),
            external_depth=config.get("external_depth", 10),  # type: ignore[arg-type]
            max_external_candidates=config.get("max_external_candidates", 1000),  # type: ignore[arg-type]
            run_search=bool(config.get("run_search", True)),
            retry_errors=bool(config.get("retry_errors", True)),
        )

    @property
    def selected_queries(self) -> list[str]:
        if self.max_queries == 0:
            return self.queries
        return self.queries[: self.max_queries]
