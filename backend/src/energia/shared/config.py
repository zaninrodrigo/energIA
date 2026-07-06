"""Application settings, sourced from environment variables.

Defaults match the local docker-compose database (see /docker-compose.yml and
/env.example at the repository root): host `localhost`, port `5434`, database
and user `energia`, password `changeme`.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


def _resolve_repo_root_env_file(config_file: Path) -> Path:
    """Resolve the repo-root `.env` path relative to this module's own location.

    This module lives at `backend/src/energia/shared/config.py`; four parents up from that
    file is the repository root. Resolving it this way (instead of relying on `.env` being
    CWD-relative) means the repo-root `.env` is honored regardless of which directory the
    process was launched from.
    """
    return config_file.resolve().parents[4] / ".env"


_REPO_ROOT_ENV_FILE = _resolve_repo_root_env_file(Path(__file__))


class Settings(BaseSettings):
    """Runtime configuration for the EnergIA backend.

    `POSTGRES_*` variables assemble the SQLAlchemy connection URL. Setting
    `DATABASE_URL` explicitly takes precedence over the assembled value.
    """

    model_config = SettingsConfigDict(env_file=_REPO_ROOT_ENV_FILE, extra="ignore")

    postgres_host: str = "localhost"
    postgres_host_port: int = 5434
    postgres_db: str = "energia"
    postgres_user: str = "energia"
    postgres_password: SecretStr = SecretStr("changeme")
    database_url: str | None = None

    @property
    def sqlalchemy_database_url(self) -> str:
        """The async SQLAlchemy connection URL, assembled or overridden via DATABASE_URL."""
        if self.database_url:
            return self.database_url
        url = URL.create(
            drivername="postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_host_port,
            database=self.postgres_db,
        )
        return url.render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached Settings instance (FastAPI dependency-friendly)."""
    return Settings()
