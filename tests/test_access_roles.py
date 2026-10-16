"""Role authorization must not depend on Discord's guild-role cache."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord.utils import SnowflakeList
import pytest

from services.access_service import (
    get_access_role_ids,
    has_any_role,
    member_has_access,
    message_has_access,
)

GUILD = 1061370086331523176
MAINTAINER = 1062146522365571123


class UnresolvedMember:
    id = 42
    bot = False
    guild = SimpleNamespace(id=GUILD)
    _roles = SnowflakeList([MAINTAINER])

    @property
    def roles(self):
        raise AttributeError("Guild role cache is unavailable")


def context(raw_roles):
    return SimpleNamespace(
        user=UnresolvedMember(),
        guild=SimpleNamespace(id=GUILD),
        respond=AsyncMock(),
        interaction=SimpleNamespace(
            _raw_data={"guild_id": str(GUILD), "member": {"roles": raw_roles}}
        ),
    )


@pytest.mark.parametrize(
    "value",
    ["", " ", "Maintainer", "0", "-1", f"{MAINTAINER},", f"{MAINTAINER},invalid"],
)
def test_invalid_global_restrictions_never_become_unrestricted(monkeypatch, value):
    monkeypatch.setenv("BOT_ACCESS_ROLE_IDS", value)
    with pytest.raises(ValueError, match="BOT_ACCESS_ROLE_IDS"):
        get_access_role_ids()


def test_global_restriction_is_optional_and_trims_numeric_ids(monkeypatch):
    monkeypatch.delenv("BOT_ACCESS_ROLE_IDS", raising=False)
    assert get_access_role_ids() is None
    monkeypatch.setenv("BOT_ACCESS_ROLE_IDS", f" {MAINTAINER}, {MAINTAINER} ")
    assert get_access_role_ids() == frozenset({MAINTAINER})


def test_numeric_legacy_roles_use_current_raw_ids_when_role_cache_is_absent():
    ctx = context([str(MAINTAINER)])
    assert has_any_role(ctx, [f" {MAINTAINER} "])
    assert has_any_role(ctx.user, [str(MAINTAINER)])  # SnowflakeList fallback.
    assert has_any_role(ctx, ["@everyone"])


@pytest.mark.parametrize("raw_roles", [[], ["123"], None, "1062146522365571123"])
def test_current_payload_cannot_fall_back_to_cached_maintainer_role(raw_roles):
    assert not has_any_role(context(raw_roles), [str(MAINTAINER)])


def test_named_feature_roles_preserve_case_whitespace_and_everyone_semantics():
    member = SimpleNamespace(
        guild=SimpleNamespace(id=GUILD),
        _roles=SnowflakeList([123]),
        roles=[
            SimpleNamespace(id=GUILD, name="@everyone"),
            SimpleNamespace(id=123, name="Maintainer"),
        ],
    )
    assert has_any_role(member, [" maintainer "])
    assert has_any_role(member, [" @EVERYONE "])
    assert has_any_role(member, [None])
    assert not has_any_role(member, [])
    assert not has_any_role(member, [str(MAINTAINER)])


@pytest.fixture
def check_module(monkeypatch):
    monkeypatch.setenv("ALLOWED_GUILDS", str(GUILD))
    return importlib.import_module("models.check_model")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", ["admin", "dalle", "gpt", "index", "translator", "search"]
)
async def test_every_legacy_command_check_accepts_raw_role_ids(
    check_module, monkeypatch, name
):
    monkeypatch.setattr(check_module, name.upper() + "_ROLES", [f" {MAINTAINER} "])
    ctx = context([str(MAINTAINER)])
    assert await getattr(check_module.Check, f"check_{name}_roles")()(ctx)
    ctx.respond.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_denial_formats_role_mentions_without_python_list_repr(
    check_module, monkeypatch
):
    monkeypatch.setattr(check_module, "GPT_ROLES", [f" {MAINTAINER} ", " @everyone "])
    ctx = SimpleNamespace(
        user=SimpleNamespace(roles=[]), guild=None, respond=AsyncMock()
    )
    assert not await check_module.Check.check_gpt_roles()(ctx)
    response = ctx.respond.await_args
    assert f"<@&{MAINTAINER}>" in response.args[0]
    assert "@everyone" in response.args[0]
    assert "['" not in response.args[0]
    assert response.kwargs["ephemeral"] is True
    assert response.kwargs["allowed_mentions"].everyone is False


def message(raw_roles, *, member_field=True, guild=True):
    raw = {"guild_id": str(GUILD)} if guild else {}
    if member_field:
        raw["member"] = {"roles": raw_roles}
    http = SimpleNamespace(get_member=AsyncMock())
    return SimpleNamespace(
        _raw_data=raw,
        _state=SimpleNamespace(http=http),
        guild=SimpleNamespace(id=GUILD) if guild else None,
        author=UnresolvedMember(),
    )


@pytest.mark.asyncio
async def test_messages_use_gateway_role_ids_without_role_cache_or_network(monkeypatch):
    monkeypatch.setenv("BOT_ACCESS_ROLE_IDS", str(MAINTAINER))
    item = message([str(MAINTAINER)])
    assert await message_has_access(item)
    item._state.http.get_member.assert_not_awaited()
    item._raw_data["member"]["roles"] = []
    assert not await message_has_access(
        item
    )  # Cached Maintainer membership cannot override revocation.
    item._state.http.get_member.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "roles,expected", [([str(MAINTAINER)], True), ([], False), (["123"], False)]
)
async def test_partial_message_edits_fetch_fresh_membership_instead_of_cached_roles(
    monkeypatch, roles, expected
):
    monkeypatch.setenv("BOT_ACCESS_ROLE_IDS", str(MAINTAINER))
    item = message(None, member_field=False)
    item._state.http.get_member.return_value = {"roles": roles}
    assert await message_has_access(item) is expected
    item._state.http.get_member.assert_awaited_once_with(GUILD, 42)
    # The later tag/channel/bypass feature check must see the REST-verified
    # snapshot too, not an absent payload or the stale cached Member roles.
    assert has_any_role(item, [str(MAINTAINER)]) is expected
    assert item._raw_data["member"]["roles"] == sorted(map(int, roles))


@pytest.mark.asyncio
async def test_failed_membership_lookup_and_direct_messages_fail_closed(monkeypatch):
    monkeypatch.setenv("BOT_ACCESS_ROLE_IDS", str(MAINTAINER))
    item = message(None, member_field=False)
    item._state.http.get_member.side_effect = RuntimeError("Membership unavailable")
    assert not await message_has_access(item)
    dm = message([str(MAINTAINER)], guild=False)
    assert not await message_has_access(dm)
    dm._state.http.get_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_unset_message_and_join_gate_preserves_existing_behavior(monkeypatch):
    monkeypatch.delenv("BOT_ACCESS_ROLE_IDS", raising=False)
    assert await message_has_access(SimpleNamespace())
    assert member_has_access(SimpleNamespace())


def test_welcome_message_gate_uses_exact_member_role_ids(monkeypatch):
    monkeypatch.setenv("BOT_ACCESS_ROLE_IDS", str(MAINTAINER))
    assert member_has_access(UnresolvedMember())
    member = SimpleNamespace(
        guild=SimpleNamespace(id=GUILD),
        _roles=SnowflakeList([123]),
        bot=False,
        roles=[SimpleNamespace(id=123, name="Maintainer")],
    )
    assert not member_has_access(member)


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["on_message", "on_message_edit", "on_member_join"])
async def test_denied_core_events_stop_before_attachments_moderation_or_provider_work(
    monkeypatch, event
):
    monkeypatch.setenv("BOT_ACCESS_ROLE_IDS", str(MAINTAINER))
    monkeypatch.setenv("ALLOWED_GUILDS", str(GUILD))
    module = importlib.import_module("cogs.text_service_cog")
    item = message([])
    # There are deliberately no model/state/file attributes: denial must return first.
    cog = SimpleNamespace()
    if event == "on_member_join":
        item = SimpleNamespace(
            guild=SimpleNamespace(id=GUILD), _roles=SnowflakeList([])
        )
    args = (None, item) if event == "on_message_edit" else (item,)
    await getattr(module.GPT3ComCon, event)(cog, *args)


@pytest.mark.asyncio
async def test_native_web_command_uses_raw_id_roles_without_resolved_cache(monkeypatch):
    monkeypatch.setenv("ALLOWED_GUILDS", str(GUILD))
    monkeypatch.setenv("SEARCH_ROLES", str(MAINTAINER))
    monkeypatch.setenv("USER_INPUT_API_KEYS", "false")
    from cogs.web_search_cog import WebSearchCog
    from services.responses_service import SearchResponse

    provider = SimpleNamespace(
        search=AsyncMock(return_value=SearchResponse("Answer", (), "gpt-6-luna", {}, 0))
    )
    cog = WebSearchCog(None, responses_service=provider)
    ctx = context([str(MAINTAINER)])
    ctx.defer = AsyncMock()
    await WebSearchCog.web.callback(cog, ctx, "Question", "gpt-6-luna")
    ctx.defer.assert_awaited_once()
    provider.search.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module_name,class_name",
    [
        ("cogs.code_interpreter_service_cog", "CodeInterpreterService"),
        ("cogs.index_service_cog", "IndexService"),
        ("cogs.search_service_cog", "SearchService"),
    ],
)
async def test_denied_optional_provider_listeners_return_before_state_access(
    monkeypatch, module_name, class_name
):
    monkeypatch.setenv("BOT_ACCESS_ROLE_IDS", str(MAINTAINER))
    monkeypatch.setenv("ALLOWED_GUILDS", str(GUILD))
    pytest.importorskip("llama_index.core")
    pytest.importorskip("langchain_openai")
    if class_name == "CodeInterpreterService":
        pytest.importorskip("e2b_code_interpreter")
    module = importlib.import_module(module_name)
    await getattr(module, class_name).on_message(SimpleNamespace(), message([]))


def test_application_startup_installs_the_gateway_gate_before_connecting(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "PYTHONPATH": str(root),
        "PYTHON_DOTENV_DISABLED": "1",
        "DISCORD_TOKEN": "offline-bot-key",
        "OPENAI_API_KEY": "offline-openai-key",
        "ALLOWED_GUILDS": str(GUILD),
        "BOT_ACCESS_ROLE_IDS": str(MAINTAINER),
        "DATA_DIR": str(tmp_path),
        "SHARE_DIR": str(tmp_path),
        "ENABLE_INDEXING": "false",
        "USER_INPUT_API_KEYS": "false",
        "HEALTH_SERVICE_ENABLED": "false",
    }
    for key in (
        "PINECONE_TOKEN",
        "E2B_API_KEY",
        "GOOGLE_SEARCH_API_KEY",
        "DEEPL_TOKEN",
    ):
        env.pop(key, None)
    script = """
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from discord.webhook.async_ import async_context
from pycord.multicog import Bot
import gpt3discord

async def start(bot, token):
    adapter = SimpleNamespace(create_interaction_response=AsyncMock())
    handle = async_context.set(adapter)
    try:
        bot._connection.parsers["INTERACTION_CREATE"]({
            "id": "123", "token": "offline-interaction-token", "type": 4,
            "guild_id": "1061370086331523176", "member": {"roles": []},
        })
        await asyncio.gather(*tuple(bot._tasks))
        adapter.create_interaction_response.assert_awaited_once()
        assert adapter.create_interaction_response.await_args.kwargs["type"] == 8
        assert adapter.create_interaction_response.await_args.kwargs["data"] == {"choices": []}
        print("ACCESS_GATE_INSTALLED")
    finally:
        async_context.reset(handle)

Bot.start = start
asyncio.run(gpt3discord.main())
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ACCESS_GATE_INSTALLED" in result.stdout
