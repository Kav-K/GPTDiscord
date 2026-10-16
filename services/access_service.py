"""Optional role-ID authorization before application callbacks and provider work."""

from collections.abc import Sequence, Set
import logging
import os

logger = logging.getLogger(__name__)


def get_access_role_ids():
    """Unset preserves existing access; invalid restrictions must never open access."""
    value = os.getenv("BOT_ACCESS_ROLE_IDS")
    if value is None:
        return None
    parts = [part.strip() for part in value.split(",")]
    if not parts or any(not part.isdecimal() or int(part) <= 0 for part in parts):
        raise ValueError(
            "BOT_ACCESS_ROLE_IDS must contain positive, comma-separated role IDs; remove it to disable the restriction."
        )
    return frozenset(map(int, parts))


def _role_ids(values):
    if isinstance(values, (str, bytes)) or not isinstance(values, (Sequence, Set)):
        return None
    try:
        if any(
            isinstance(value, bool) or not str(value).isdecimal() or int(value) <= 0
            for value in values
        ):
            return None
        return {int(value) for value in values}
    except (TypeError, ValueError):
        return None


def _raw_interaction(subject):
    value = getattr(subject, "_raw_data", None)
    if isinstance(value, dict):
        return value
    interaction = getattr(subject, "interaction", None)
    value = getattr(interaction, "_raw_data", None)
    return value if isinstance(value, dict) else None


def _context_roles(subject, include_names=False):
    """Fresh interaction IDs are authoritative, even when empty or unresolvable."""
    raw = _raw_interaction(subject)
    member = (
        getattr(subject, "user", None) or getattr(subject, "author", None) or subject
    )
    guild = getattr(subject, "guild", None) or getattr(member, "guild", None)
    guild_id = getattr(guild, "id", None)
    if raw is not None:
        guild_id = raw.get("guild_id") or guild_id
        payload = raw.get("member")
        ids = _role_ids(payload.get("roles")) if isinstance(payload, dict) else None
        # Do not fall back to a cached Member when the incoming roles are absent.
        ids = ids if ids is not None else set()
    else:
        ids = _role_ids(getattr(member, "_roles", None))
        if ids is None:
            try:
                ids = {
                    int(role.id) for role in member.roles if getattr(role, "id", None)
                }
            except (AttributeError, TypeError, ValueError):
                ids = set()
    if guild_id:
        # Discord omits the implicit @everyone role from member.roles payloads.
        ids.add(int(guild_id))
    names = set()
    if not include_names:
        return ids, names
    try:
        resolved_roles = member.roles
    except AttributeError:
        resolved_roles = []
    for role in resolved_roles:
        role_id = getattr(role, "id", None)
        if raw is None or (role_id is not None and int(role_id) in ids):
            names.add(str(role.name).strip().casefold())
    if guild_id:
        names.add("@everyone")
    return ids, names


def has_any_role(subject, configured_roles):
    """Legacy feature roles accept exact IDs or case-insensitive, trimmed names."""
    if configured_roles is None or configured_roles == [None]:
        return True
    if isinstance(configured_roles, str):
        configured_roles = configured_roles.split(",")
    configured = {
        str(role).strip().casefold()
        for role in configured_roles
        if role is not None and str(role).strip()
    }
    ids, names = _context_roles(
        subject, include_names=any(not role.isdecimal() for role in configured)
    )
    return any(
        (int(role) in ids) if role.isdecimal() else (role in names)
        for role in configured
    )


def _payload_has_access(payload, required):
    if not payload.get("guild_id"):
        return False
    member = payload.get("member")
    roles = _role_ids(member.get("roles")) if isinstance(member, dict) else None
    return roles is not None and bool(required.intersection(roles))


async def message_has_access(message):
    """Authorize messages/edits before listeners touch files, state, or providers."""
    required = get_access_role_ids()
    if required is None:
        return True
    raw = getattr(message, "_raw_data", None)
    raw = raw if isinstance(raw, dict) else {}
    guild_id = raw.get("guild_id") or getattr(
        getattr(message, "guild", None), "id", None
    )
    author = getattr(message, "author", None)
    if not guild_id or author is None or getattr(author, "bot", False):
        return False
    member = raw.get("member")
    if isinstance(member, dict) and "roles" in member:
        roles = _role_ids(member["roles"])
        return roles is not None and bool(required.intersection(roles))
    # Partial edit events need a current membership lookup, never cached role names.
    try:
        member = await message._state.http.get_member(int(guild_id), int(author.id))
        roles = _role_ids(member.get("roles"))
    except Exception:
        return False
    if roles is None:
        return False
    # Preserve this verified snapshot for subsequent feature-level role checks.
    # Copy the raw payload instead of mutating the SDK's shared Member cache.
    previous_member = raw.get("member")
    message._raw_data = {
        **raw,
        "guild_id": str(guild_id),
        "member": {
            **(previous_member if isinstance(previous_member, dict) else {}),
            "roles": sorted(roles),
        },
    }
    return bool(required.intersection(roles))


def member_has_access(member):
    """Member-join roles are supplied by the gateway, independent of role cache."""
    required = get_access_role_ids()
    if required is None:
        return True
    if not getattr(member, "guild", None) or getattr(member, "bot", False):
        return False
    roles = _role_ids(getattr(member, "_roles", None))
    return roles is not None and bool(required.intersection(roles))


async def _deny_interaction(bot, data, required):
    from discord.webhook.async_ import async_context

    try:
        if data["type"] == 4:  # Autocomplete cannot receive a message response.
            response_type, payload = 8, {"choices": []}
        else:
            response_type, payload = 4, {
                "content": "You need one of these roles to use this bot: "
                + ", ".join(f"<@&{role}>" for role in sorted(required))
                + ".",
                "flags": 64,
                "allowed_mentions": {"parse": []},
            }
        await async_context.get().create_interaction_response(
            int(data["id"]),
            data["token"],
            session=bot.http._HTTPClient__session,
            type=response_type,
            data=payload,
            proxy=bot.http.proxy,
            proxy_auth=bot.http.proxy_auth,
        )
    except Exception as error:
        # Interaction tokens can occur in HTTP errors; log no payload or exception text.
        logger.warning(
            "Could not send role-restriction response (%s)", type(error).__name__
        )


def install_access_gate(bot):
    """Intercept the gateway parser before Pycord dispatches views or modals.

    on_interaction/global command checks run too late for components and modals.
    Leave message/member parsers intact so SDK lifecycle/cache updates still work;
    their application listeners use the helpers above instead.
    """
    required = get_access_role_ids()
    if required is None:
        return
    original = bot._connection.parsers["INTERACTION_CREATE"]

    def guarded(data):
        if _payload_has_access(data, required):
            return original(data)
        task = bot.loop.create_task(_deny_interaction(bot, data, required))
        bot._tasks.add(task)
        task.add_done_callback(bot._tasks.discard)

    bot._connection.parsers["INTERACTION_CREATE"] = guarded
