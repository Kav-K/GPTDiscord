"""Exercise role authorization at Pycord's actual gateway parser boundary."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from discord.utils import SnowflakeList
from discord.webhook.async_ import async_context
from pycord.multicog import Bot
import pytest

from services.access_service import has_any_role, install_access_gate

GUILD = 1061370086331523176
MAINTAINER = 1062146522365571123
OTHER_ROLE = 123456789012345678


def interaction_payload(kind, roles=None):
    data = {
        "id": "1500000000000000000",
        "application_id": "200000000000000000",
        "guild_id": str(GUILD),
        "token": "offline-interaction-token",
        "version": 1,
        "type": kind,
        "member": {
            "user": {
                "id": "42",
                "username": "Tester",
                "discriminator": "0",
                "avatar": None,
            },
            "roles": [] if roles is None else roles,
            "permissions": "0",
        },
        "data": {"id": "300000000000000000", "name": "setup", "type": 1},
    }
    if kind == 3:
        data["data"] = {"custom_id": "retry", "component_type": 2}
    elif kind == 5:
        data["data"] = {"custom_id": "setup-modal", "components": []}
    return data


@pytest.fixture
async def gateway(monkeypatch):
    monkeypatch.setenv("BOT_ACCESS_ROLE_IDS", str(MAINTAINER))
    bot = Bot(intents=discord.Intents.none(), auto_sync_commands=False)
    state = bot._connection
    original = Mock(wraps=state.parsers["INTERACTION_CREATE"])
    state.parsers["INTERACTION_CREATE"] = original
    dispatch = Mock()
    monkeypatch.setattr(state, "dispatch", dispatch)
    view = Mock()
    modal = AsyncMock()
    monkeypatch.setattr(state._view_store, "dispatch", view)
    monkeypatch.setattr(state._modal_store, "dispatch", modal)
    adapter = SimpleNamespace(create_interaction_response=AsyncMock())
    context_token = async_context.set(adapter)
    try:
        yield SimpleNamespace(
            bot=bot,
            state=state,
            original=original,
            dispatch=dispatch,
            view=view,
            modal=modal,
            adapter=adapter,
        )
    finally:
        if bot._tasks:
            await asyncio.gather(*tuple(bot._tasks))
        async_context.reset(context_token)
        await bot.close()


async def drain_denial(gateway):
    tasks = tuple(gateway.bot._tasks)
    assert len(tasks) == 1
    await asyncio.gather(*tasks)


def assert_no_application_dispatch(gateway):
    gateway.original.assert_not_called()
    gateway.dispatch.assert_not_called()
    gateway.view.assert_not_called()
    gateway.modal.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", [2, 3, 4, 5], ids=["command", "component", "autocomplete", "modal"]
)
async def test_denied_gateway_events_stop_before_construction_or_callbacks(
    gateway, monkeypatch, kind
):
    constructor = Mock(side_effect=AssertionError("Denied payload reached Pycord"))
    monkeypatch.setattr("discord.state.Interaction", constructor)
    install_access_gate(gateway.bot)

    gateway.state.parsers["INTERACTION_CREATE"](
        interaction_payload(kind, [str(OTHER_ROLE)])
    )
    await drain_denial(gateway)

    constructor.assert_not_called()
    assert_no_application_dispatch(gateway)
    gateway.adapter.create_interaction_response.assert_awaited_once()
    response = gateway.adapter.create_interaction_response.await_args.kwargs
    if kind == 4:
        assert response["type"] == 8
        assert response["data"] == {"choices": []}
    else:
        assert response["type"] == 4
        assert response["data"]["flags"] == 64
        assert response["data"]["allowed_mentions"] == {"parse": []}
        assert f"<@&{MAINTAINER}>" in response["data"]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", [2, 3, 4, 5], ids=["command", "component", "autocomplete", "modal"]
)
async def test_authorized_raw_ids_reach_real_parser_without_guild_role_cache(
    gateway, kind
):
    assert gateway.state._guilds == {}
    install_access_gate(gateway.bot)
    payload = interaction_payload(kind, [str(MAINTAINER)])

    gateway.state.parsers["INTERACTION_CREATE"](payload)
    await asyncio.sleep(0)  # ModalStore dispatch is scheduled by Pycord itself.

    gateway.original.assert_called_once_with(payload)
    gateway.dispatch.assert_called_once()
    event, interaction = gateway.dispatch.call_args.args
    assert event == "interaction"
    assert isinstance(interaction, discord.Interaction)
    assert isinstance(interaction.user, discord.Member)
    assert isinstance(interaction.user.guild, discord.Object)
    assert MAINTAINER in interaction.user._roles
    assert has_any_role(interaction, [str(MAINTAINER)])
    gateway.adapter.create_interaction_response.assert_not_awaited()
    if kind == 3:
        gateway.view.assert_called_once_with(2, "retry", interaction)
    elif kind == 5:
        gateway.modal.assert_awaited_once_with(42, "setup-modal", interaction)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "removed-role",
        "missing-member",
        "missing-roles",
        "dm",
        "administrator",
        "owner",
        "same-name-role",
    ],
)
async def test_raw_membership_cannot_be_replaced_by_cached_or_privileged_identity(
    gateway, monkeypatch, case
):
    cached_member = SimpleNamespace(
        _roles=SnowflakeList([MAINTAINER]),
        roles=[SimpleNamespace(id=MAINTAINER, name="Maintainer")],
        guild_permissions=discord.Permissions.all(),
    )
    lookup = Mock(
        return_value=SimpleNamespace(get_member=lambda _: cached_member, owner_id=42)
    )
    monkeypatch.setattr(gateway.state, "_get_guild", lookup)
    constructor = Mock(side_effect=AssertionError("Denied payload reached Pycord"))
    monkeypatch.setattr("discord.state.Interaction", constructor)
    install_access_gate(gateway.bot)
    payload = interaction_payload(5, [])  # A previously opened modal must reauthorize.
    if case == "missing-member":
        payload.pop("member")
    elif case == "missing-roles":
        payload["member"].pop("roles")
    elif case == "dm":
        payload.pop("guild_id")
        payload["member"]["roles"] = [str(MAINTAINER)]
    elif case == "administrator":
        payload["member"]["permissions"] = "8"
    elif case == "owner":
        payload["owner_id"] = "42"
    elif case == "same-name-role":
        payload["member"]["roles"] = [str(OTHER_ROLE)]
        payload["member"]["role_names"] = ["Maintainer"]

    gateway.state.parsers["INTERACTION_CREATE"](payload)
    await drain_denial(gateway)

    constructor.assert_not_called()
    lookup.assert_not_called()
    assert_no_application_dispatch(gateway)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "roles", [None, str(MAINTAINER), {}, [True], ["invalid"], ["0"]]
)
async def test_malformed_raw_roles_fail_closed(gateway, roles):
    install_access_gate(gateway.bot)
    payload = interaction_payload(2)
    payload["member"]["roles"] = roles

    gateway.state.parsers["INTERACTION_CREATE"](payload)
    await drain_denial(gateway)

    assert_no_application_dispatch(gateway)


@pytest.mark.asyncio
async def test_unset_gate_leaves_original_parser_and_existing_behavior_untouched(
    gateway, monkeypatch
):
    monkeypatch.delenv("BOT_ACCESS_ROLE_IDS")
    install_access_gate(gateway.bot)
    assert gateway.state.parsers["INTERACTION_CREATE"] is gateway.original
    payload = interaction_payload(2, [])

    gateway.state.parsers["INTERACTION_CREATE"](payload)

    gateway.original.assert_called_once_with(payload)
    gateway.dispatch.assert_called_once()
    gateway.adapter.create_interaction_response.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", " ", "Maintainer", "0", f"{MAINTAINER},"])
async def test_invalid_installation_never_replaces_parser_with_unrestricted_fallback(
    gateway, monkeypatch, value
):
    monkeypatch.setenv("BOT_ACCESS_ROLE_IDS", value)
    with pytest.raises(ValueError, match="BOT_ACCESS_ROLE_IDS"):
        install_access_gate(gateway.bot)
    assert gateway.state.parsers["INTERACTION_CREATE"] is gateway.original


@pytest.mark.asyncio
async def test_installation_snapshots_config_and_preserves_message_member_parsers(
    gateway, monkeypatch
):
    untouched = {
        name: gateway.state.parsers[name]
        for name in (
            "MESSAGE_CREATE",
            "MESSAGE_UPDATE",
            "GUILD_MEMBER_ADD",
            "GUILD_MEMBER_UPDATE",
        )
    }
    install_access_gate(gateway.bot)
    monkeypatch.setenv("BOT_ACCESS_ROLE_IDS", str(OTHER_ROLE))
    authorized = interaction_payload(2, [str(MAINTAINER)])

    gateway.state.parsers["INTERACTION_CREATE"](authorized)
    gateway.state.parsers["INTERACTION_CREATE"](
        interaction_payload(2, [str(OTHER_ROLE)])
    )
    await drain_denial(gateway)

    gateway.original.assert_called_once_with(authorized)
    for name, parser in untouched.items():
        assert gateway.state.parsers[name] is parser


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [2, 3, 4, 5])
async def test_global_everyone_role_accepts_valid_empty_member_roles(
    gateway, monkeypatch, kind
):
    monkeypatch.setenv("BOT_ACCESS_ROLE_IDS", str(GUILD))
    install_access_gate(gateway.bot)
    payload = interaction_payload(kind, [])

    gateway.state.parsers["INTERACTION_CREATE"](payload)
    await asyncio.sleep(0)

    gateway.original.assert_called_once_with(payload)
    gateway.dispatch.assert_called_once()
    gateway.adapter.create_interaction_response.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("guild_id", [None, "", "invalid", "0", "-1", True])
async def test_global_everyone_role_requires_valid_guild_metadata(
    gateway, monkeypatch, guild_id
):
    monkeypatch.setenv("BOT_ACCESS_ROLE_IDS", str(GUILD))
    install_access_gate(gateway.bot)
    payload = interaction_payload(2, [])
    payload["guild_id"] = guild_id
    gateway.state.parsers["INTERACTION_CREATE"](payload)
    await drain_denial(gateway)
    assert_no_application_dispatch(gateway)


@pytest.mark.asyncio
@pytest.mark.parametrize("roles", [None, "", {}, [True], ["invalid"]])
async def test_global_everyone_role_does_not_infer_missing_or_invalid_membership(
    gateway, monkeypatch, roles
):
    monkeypatch.setenv("BOT_ACCESS_ROLE_IDS", str(GUILD))
    install_access_gate(gateway.bot)
    payload = interaction_payload(2)
    payload["member"]["roles"] = roles
    gateway.state.parsers["INTERACTION_CREATE"](payload)
    await drain_denial(gateway)
    assert_no_application_dispatch(gateway)
