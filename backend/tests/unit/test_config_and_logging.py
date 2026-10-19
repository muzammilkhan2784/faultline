"""Settings are read from real environment variables, exactly as docker-compose
supplies them. A test that only constructs ``Settings(...)`` misses parsing bugs:
the first version of this crashed the API container on ``CORS_ORIGINS=http://x``."""

from __future__ import annotations

import json
import logging

import pytest

from faultline.config import DEFAULT_EXCLUDE_DIRS, Settings, get_settings
from faultline.logging import JsonFormatter, configure_logging, get_logger


def test_single_cors_origin_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173")
    assert Settings().cors_origins == ("http://localhost:5173",)


def test_comma_separated_cors_origins_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.example, http://b.example ,")
    assert Settings().cors_origins == ("http://a.example", "http://b.example")


def test_exclude_dirs_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXCLUDE_DIRS", "vendor,third_party")
    assert Settings().exclude_dirs == ("vendor", "third_party")


def test_defaults_when_the_environment_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CORS_ORIGINS", "EXCLUDE_DIRS", "NEO4J_URI"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.exclude_dirs == DEFAULT_EXCLUDE_DIRS
    assert "migrations" in settings.exclude_dirs
    assert settings.neo4j_uri == "bolt://localhost:7687"
    assert settings.write_batch_size == 5_000


def test_scalar_settings_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEO4J_URI", "bolt://neo4j:7687")
    monkeypatch.setenv("MAX_REPO_FILES", "123")
    monkeypatch.setenv("ALLOWED_ROOT", "/repos")
    settings = Settings()
    assert settings.neo4j_uri == "bolt://neo4j:7687"
    assert settings.max_repo_files == 123
    assert settings.allowed_root == "/repos"


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    assert get_settings() is get_settings()


# -- logging ------------------------------------------------------------------------------


def test_json_formatter_keeps_extra_fields() -> None:
    record = logging.LogRecord(
        "faultline.x", logging.INFO, __file__, 1, "written %s", ("ok",), None
    )
    record.repo_id = "r1"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["msg"] == "written ok"
    assert payload["level"] == "INFO"
    assert payload["repo_id"] == "r1"


def test_json_formatter_includes_exceptions() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
    assert "ValueError: boom" in json.loads(JsonFormatter().format(record))["exc"]


def test_configure_logging_is_idempotent_and_quietens_the_driver() -> None:
    configure_logging("DEBUG")
    configure_logging("INFO", json_output=False)
    assert len(logging.getLogger().handlers) == 1
    assert logging.getLogger().level == logging.INFO
    assert logging.getLogger("neo4j").level == logging.WARNING
    assert get_logger("faultline.test").name == "faultline.test"
