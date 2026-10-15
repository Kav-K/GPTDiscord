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
