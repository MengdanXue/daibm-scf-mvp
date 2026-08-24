import pytest

from app.config import FabricGatewaySettings, PostgresSettings


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


def test_fabric_gateway_uses_fixed_internal_default():
    assert FabricGatewaySettings.from_env({}) == FabricGatewaySettings(
        base_url="http://fabric-gateway:8090",
        timeout_seconds=5.0,
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://user:secret@fabric-gateway:8090",
        "http://fabric-gateway:8090/network/private/client.pem",
        "http://fabric-gateway:8090?token=secret",
        "file:///network/private/client.pem",
    ],
)
def test_fabric_gateway_rejects_credentials_paths_and_non_http_urls(url):
    with pytest.raises(ValueError, match="FABRIC_GATEWAY_URL"):
        FabricGatewaySettings.from_env({"FABRIC_GATEWAY_URL": url})
