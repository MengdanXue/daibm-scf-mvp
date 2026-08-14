import pytest

from app.config import PostgresSettings


def test_settings_build_psycopg_url_without_exposing_password():
    settings = PostgresSettings(
        host="postgres",
        port=5432,
        database="daibm_scf",
        user="daibm",
        password="p@ss/word",
    )

    url = settings.sqlalchemy_url

    assert url.drivername == "postgresql+psycopg"
    assert url.host == "postgres"
    assert url.port == 5432
    assert url.database == "daibm_scf"
    assert url.password == "p@ss/word"
    assert "p@ss/word" not in url.render_as_string(hide_password=True)


def test_settings_read_postgres_environment(monkeypatch):
    monkeypatch.setenv("POSTGRES_HOST", "db.internal")
    monkeypatch.setenv("POSTGRES_PORT", "5544")
    monkeypatch.setenv("POSTGRES_DB", "research_demo")
    monkeypatch.setenv("POSTGRES_USER", "researcher")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")

    settings = PostgresSettings.from_env()

    assert settings == PostgresSettings(
        host="db.internal",
        port=5544,
        database="research_demo",
        user="researcher",
        password="secret",
    )


@pytest.mark.parametrize("port", ["not-a-port", "0", "65536"])
def test_settings_reject_invalid_port(monkeypatch, port):
    monkeypatch.setenv("POSTGRES_PORT", port)

    with pytest.raises(ValueError, match="POSTGRES_PORT"):
        PostgresSettings.from_env()
