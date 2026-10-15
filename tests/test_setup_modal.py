import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.fixture
def setup_modal(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    module = importlib.import_module("services.text_service")
    validate = AsyncMock()
    monkeypatch.setattr(module.Model, "send_test_request", validate)
    return module.SetupModal, validate


def modal_context(value):
    class KeyDatabase(dict):
        commit = Mock()

    modal = SimpleNamespace(
        children=[SimpleNamespace(value=value)], USER_KEY_DB=KeyDatabase()
    )
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=42),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    return modal, interaction


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key",
    [
        "sk-proj-project_scoped-key-123",
        "sk-svcacct-service-key-123",
        "compatible-provider-key",
    ],
)
async def test_current_keys_are_validated_then_saved_per_user(setup_modal, key):
    modal_type, validate = setup_modal
    modal, interaction = modal_context(f"  {key}\n")

    await modal_type.callback(modal, interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    validate.assert_awaited_once_with(key)
    assert modal.USER_KEY_DB == {42: key}
    modal.USER_KEY_DB.commit.assert_called_once()
    assert interaction.followup.send.call_args.kwargs["ephemeral"] is True
    assert key not in interaction.followup.send.call_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key", ["", "  ", "key with space", "key\x00value", "x" * 4097]
)
async def test_malformed_keys_never_reach_provider_or_storage(setup_modal, key):
    modal_type, validate = setup_modal
    modal, interaction = modal_context(key)

    await modal_type.callback(modal, interaction)

    validate.assert_not_awaited()
    assert not modal.USER_KEY_DB
    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_provider_errors_never_echo_key_or_account_details(setup_modal):
    modal_type, validate = setup_modal
    key = "sk-proj-private-key"
    validate.side_effect = ValueError(f"Rejected {key}; organization secret-org")
    modal, interaction = modal_context(key)

    await modal_type.callback(modal, interaction)

    assert not modal.USER_KEY_DB
    response = interaction.followup.send.call_args.args[0]
    assert key not in response
    assert "secret-org" not in response
    assert interaction.followup.send.call_args.kwargs["ephemeral"] is True
