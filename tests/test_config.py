import os

from pathlib import Path

from nsfw_discovery.config import Settings, default_user_agent, load_dotenv


def test_load_dotenv_reads_values_without_overriding_existing(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        """
        # comment
        SERPAPI_API_KEY=from-file
        LLM_BASE_URL="https://example.test/v1"
        export LLM_MODEL='model-from-file'
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("SERPAPI_API_KEY", "from-shell")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    load_dotenv(env_file)

    assert os.environ["SERPAPI_API_KEY"] == "from-shell"
    assert os.environ["LLM_BASE_URL"] == "https://example.test/v1"
    assert os.environ["LLM_MODEL"] == "model-from-file"


def test_load_dotenv_can_override_existing_values(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("SERPAPI_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("SERPAPI_API_KEY", "from-shell")

    load_dotenv(env_file, override=True)

    assert os.environ["SERPAPI_API_KEY"] == "from-file"


def test_default_user_agent_can_be_configured(monkeypatch) -> None:
    monkeypatch.setenv("NSFW_DISCOVERY_USER_AGENT", "Mozilla/5.0 Custom Browser")

    settings = Settings.from_values(db_path=Path("data/discovery.sqlite"))

    assert default_user_agent() == "Mozilla/5.0 Custom Browser"
    assert settings.user_agent == "Mozilla/5.0 Custom Browser"
