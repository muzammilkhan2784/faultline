"""Environment-driven configuration.

Everything that differs between a laptop, CI and a container lives here so that
no module below `api/` has to read `os.environ` directly.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

#: Path segments that are never worth parsing. ``migrations`` is here on purpose:
#: a Django project has thousands of generated migration modules that add
#: enormous noise and no signal to a call graph.
DEFAULT_EXCLUDE_DIRS: tuple[str, ...] = (
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".tox",
    ".nox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
    "site-packages",
    ".eggs",
    "migrations",
)


class Settings(BaseSettings):
    """Runtime settings, read from the environment or a local ``.env`` file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Neo4j -------------------------------------------------------------
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "faultline-dev"
    neo4j_database: str = "neo4j"
    neo4j_max_connection_pool_size: int = 50

    # --- Ingestion safety (see README > Security) --------------------------
    allowed_root: str | None = None
    max_repo_files: int = 20_000
    max_repo_mb: int = 500
    clone_timeout_seconds: int = 300
    # NoDecode: without it pydantic-settings JSON-decodes a tuple from the environment
    # *before* the CSV validator below runs, so `CORS_ORIGINS=http://a,http://b` crashes.
    exclude_dirs: Annotated[tuple[str, ...], NoDecode] = DEFAULT_EXCLUDE_DIRS

    # --- Writer ------------------------------------------------------------
    #: Rows per UNWIND transaction. Measured on Django (172k rows): 5,000 writes it
    #: in ~9s versus ~13.6s at 1,000, because there are five times fewer round
    #: trips. See benchmarks/results and the README's ingestion section.
    write_batch_size: int = 5_000

    # --- API ---------------------------------------------------------------
    cors_origins: Annotated[tuple[str, ...], NoDecode] = (
        "http://localhost:5173",
        "http://localhost:4173",
    )
    log_level: str = "INFO"
    max_graph_nodes: int = Field(default=300, ge=10, le=5_000)

    @field_validator("cors_origins", "exclude_dirs", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept ``A,B,C`` from the environment as well as a real sequence."""
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    return Settings()
