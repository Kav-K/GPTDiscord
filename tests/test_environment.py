from services.environment_service import EnvService


def test_standard_api_key_wins(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "standard-test-key")
    monkeypatch.setenv("OPENAI_TOKEN", "legacy-test-key")
    assert EnvService.get_openai_token() == "standard-test-key"
    monkeypatch.delenv("OPENAI_API_KEY")
    assert EnvService.get_openai_token() == "legacy-test-key"


def test_persistent_files_use_data_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("SHARE_DIR", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    path = EnvService.save_path()
    assert path == tmp_path / "data"
    assert path.is_dir()
    (path / "main_db.sqlite").touch()
    assert EnvService.find_shared_file("main_db.sqlite") == path / "main_db.sqlite"


def test_openai_configuration(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1/")
    monkeypatch.setenv("OPENAI_MODEL", "custom-model")
    assert EnvService.get_openai_base_url() == "https://example.test/v1"
    assert EnvService.get_openai_model() == "custom-model"
    monkeypatch.delenv("ENABLE_INDEXING", raising=False)
    assert not EnvService.get_indexing_enabled()
    monkeypatch.setenv("ENABLE_INDEXING", "TRUE")
    assert EnvService.get_indexing_enabled()


def test_discord_proxy_respects_configured_egress_and_no_proxy(monkeypatch):
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    # Windows environment names are case-insensitive: clear aliases first.
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("NO_PROXY", "")
    assert EnvService.get_discord_proxy() == "http://proxy.example:8080"
    monkeypatch.setenv("NO_PROXY", "discord.com")
    assert EnvService.get_discord_proxy() is None


def test_provider_proxy_uses_request_host_and_scheme(monkeypatch):
    monkeypatch.setenv("https_proxy", "http://secure-proxy.example:8080")
    monkeypatch.setenv("http_proxy", "http://plain-proxy.example:8080")
    monkeypatch.setenv("no_proxy", "local.example")
    assert (
        EnvService.get_http_proxy("https://api.openai.com/v1/models")
        == "http://secure-proxy.example:8080"
    )
    assert (
        EnvService.get_http_proxy("http://api.example/v1/models")
        == "http://plain-proxy.example:8080"
    )
    assert EnvService.get_http_proxy("https://local.example/v1/models") is None
    monkeypatch.setenv("no_proxy", "api.example:8443")
    assert EnvService.get_http_proxy("https://api.example:8443/v1/models") is None
    assert (
        EnvService.get_http_proxy("https://api.example:9443/v1/models")
        == "http://secure-proxy.example:8080"
    )
