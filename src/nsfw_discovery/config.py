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
    external_score_threshold: int
    max_external_candidates: int

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

        return cls(
            db_path=Path(getattr(args, "db")).expanduser(),
            serpapi_api_key=os.getenv("SERPAPI_API_KEY"),
            llm_base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_api_key=os.getenv("LLM_API_KEY"),
            llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            queries=queries,
            max_queries=max(0, int(getattr(args, "max_queries"))),
            results_per_query=max(1, int(getattr(args, "results_per_query"))),
            max_domains=max(1, int(getattr(args, "max_domains"))),
            max_pages_per_domain=max(1, int(getattr(args, "max_pages_per_domain"))),
            concurrency=max(1, int(getattr(args, "concurrency"))),
            timeout_seconds=float(getattr(args, "timeout")),
            retry_count=max(0, int(getattr(args, "retries"))),
            user_agent=getattr(args, "user_agent"),
            external_depth=max(0, int(getattr(args, "external_depth"))),
            external_score_threshold=max(0, int(getattr(args, "external_score_threshold"))),
            max_external_candidates=max(0, int(getattr(args, "max_external_candidates"))),
        )

    @property
    def selected_queries(self) -> list[str]:
        if self.max_queries == 0:
            return self.queries
        return self.queries[: self.max_queries]
