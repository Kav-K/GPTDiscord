"""Persist deliberate model selections while allowing automatic defaults to move."""

from types import SimpleNamespace

import pytest

from models import openai_model
from models.openai_model import Model, Models


@pytest.fixture
def settings(monkeypatch, tmp_path):
    database = {}
    monkeypatch.setattr(openai_model, "SETTINGS_DB", database)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setenv("IMAGE_SAVE_PATH", str(tmp_path / "images"))
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    return database


def new_model():
    return Model(SimpleNamespace())


def test_automatic_model_follows_catalog_default_across_restarts(settings, monkeypatch):
    first = new_model()
    assert first.model == Models.DEFAULT == "gpt-6.1-sol"
    assert "model" not in settings

    monkeypatch.setattr(Models, "DEFAULT", Models.GPT_5_4)
    assert new_model().model == Models.GPT_5_4
    assert "model" not in settings


def test_automatic_model_follows_environment_changes(settings, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", Models.GPT_6_1_SOL)
    assert new_model().model == Models.GPT_6_1_SOL

    monkeypatch.setenv("OPENAI_MODEL", "custom-deployment")
    assert new_model().model == "custom-deployment"
    assert "model" not in settings


@pytest.mark.parametrize("selected", [Models.GPT_4_OMEGA, Models.GPT_5_4])
def test_explicit_supported_model_survives_environment_default_change(
    settings, monkeypatch, selected
):
    instance = new_model()
    instance.model = selected
    monkeypatch.setenv("OPENAI_MODEL", Models.GPT_6_1_SOL)

    assert new_model().model == selected
    assert settings["model"] == selected


def test_supported_legacy_selection_is_not_guessed_to_be_an_old_default(
    settings, monkeypatch
):
    settings.update(model=Models.GPT_4_OMEGA, max_tokens=1200, temp=0.4)
    monkeypatch.setenv("OPENAI_MODEL", Models.GPT_6_1_SOL)

    instance = new_model()

    assert instance.model == settings["model"] == Models.GPT_4_OMEGA
    assert instance.max_tokens == 1200
    assert instance.temp == 0.4


def test_retired_saved_model_returns_to_following_defaults(settings, monkeypatch):
    settings["model"] = "gpt-4-vision-preview"
    assert new_model().model == Models.DEFAULT
    assert "model" not in settings

    monkeypatch.setenv("OPENAI_MODEL", Models.GPT_5_4)
    assert new_model().model == Models.GPT_5_4


def test_explicit_custom_model_is_valid_only_for_its_configured_deployment(
    settings, monkeypatch
):
    monkeypatch.setenv("OPENAI_MODEL", "custom-deployment")
    instance = new_model()
    instance.model = "custom-deployment"
    assert new_model().model == "custom-deployment"

    monkeypatch.setenv("OPENAI_MODEL", Models.GPT_6_1_SOL)
    assert new_model().model == Models.GPT_6_1_SOL
    assert "model" not in settings


def test_reset_clears_explicit_model_and_resumes_following_defaults(
    settings, monkeypatch
):
    instance = new_model()
    instance.model = Models.GPT_4_OMEGA
    instance.reset_settings()

    assert instance.model == Models.DEFAULT
    assert "model" not in settings
    monkeypatch.setenv("OPENAI_MODEL", Models.GPT_5_4)
    assert new_model().model == Models.GPT_5_4


def test_disabling_low_usage_mode_resumes_following_defaults(settings, monkeypatch):
    instance = new_model()
    instance.low_usage_mode = True
    assert settings["model"] == Models.LOW_USAGE_MODEL

    instance.low_usage_mode = False

    assert instance.model == Models.DEFAULT
    assert "model" not in settings
    monkeypatch.setenv("OPENAI_MODEL", Models.GPT_5_4)
    assert new_model().model == Models.GPT_5_4
