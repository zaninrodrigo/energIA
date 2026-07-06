"""Unit tests for energia.shared.config.Settings."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from energia.shared.config import Settings, _resolve_repo_root_env_file

_ENV_VARS = (
    "POSTGRES_HOST",
    "POSTGRES_HOST_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "DATABASE_URL",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Ensure no ambient env var leaks into a test that expects defaults or explicit overrides."""
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    yield


def test_settings_default_values_match_local_compose() -> None:
    settings = Settings(_env_file=None)

    assert settings.postgres_host == "localhost"
    assert settings.postgres_host_port == 5434
    assert settings.postgres_db == "energia"
    assert settings.postgres_user == "energia"
    assert settings.postgres_password.get_secret_value() == "changeme"
    assert settings.database_url is None


def test_settings_assembles_database_url_from_postgres_vars() -> None:
    settings = Settings(_env_file=None)

    assert settings.sqlalchemy_database_url == (
        "postgresql+asyncpg://energia:changeme@localhost:5434/energia"
    )


def test_settings_reads_postgres_overrides_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "db.internal")
    monkeypatch.setenv("POSTGRES_HOST_PORT", "6000")
    monkeypatch.setenv("POSTGRES_DB", "other_db")
    monkeypatch.setenv("POSTGRES_USER", "other_user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")

    settings = Settings(_env_file=None)

    assert settings.sqlalchemy_database_url == (
        "postgresql+asyncpg://other_user:secret@db.internal:6000/other_db"
    )


def test_database_url_env_overrides_assembled_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://custom-host/custom-db")

    settings = Settings(_env_file=None)

    assert settings.sqlalchemy_database_url == "postgresql+asyncpg://custom-host/custom-db"


def test_get_settings_returns_cached_instance() -> None:
    from energia.shared.config import get_settings

    first = get_settings()
    second = get_settings()

    assert first is second


def test_sqlalchemy_database_url_escapes_special_characters_in_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A naive f-string DSN breaks on `@`/`:`/`/` in the password; URL.create() must escape them."""
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss:w/ord")

    settings = Settings(_env_file=None)
    parsed = make_url(settings.sqlalchemy_database_url)

    assert parsed.password == "p@ss:w/ord"
    assert parsed.username == "energia"
    assert parsed.host == "localhost"


def test_settings_repr_does_not_leak_the_password() -> None:
    settings = Settings(_env_file=None, postgres_password="supersecret")

    assert "supersecret" not in repr(settings)
    assert "supersecret" not in str(settings)


def test_resolve_repo_root_env_file_computes_repo_root_relative_to_module_location(
    tmp_path: Path,
) -> None:
    """`config.py` lives at `backend/src/energia/shared/config.py`; repo root is 4 parents up."""
    fake_config_module = tmp_path / "backend" / "src" / "energia" / "shared" / "config.py"
    fake_config_module.parent.mkdir(parents=True)
    fake_config_module.touch()

    resolved = _resolve_repo_root_env_file(fake_config_module)

    assert resolved == tmp_path / ".env"


def test_env_file_at_computed_repo_root_location_is_honored(tmp_path: Path) -> None:
    """A `.env` at the location `_resolve_repo_root_env_file` computes is actually read."""
    fake_config_module = tmp_path / "backend" / "src" / "energia" / "shared" / "config.py"
    fake_config_module.parent.mkdir(parents=True)
    fake_config_module.touch()
    env_file = _resolve_repo_root_env_file(fake_config_module)
    env_file.write_text("POSTGRES_HOST=env-file-host\n")

    settings = Settings(_env_file=env_file)

    assert settings.postgres_host == "env-file-host"
