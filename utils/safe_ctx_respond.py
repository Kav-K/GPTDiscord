import discord


def safe_remove_list(remove_from, element):
    try:
        remove_from.remove(element)
    except ValueError:
        pass


async def safe_ctx_respond(ctx: discord.ApplicationContext, content: str) -> None:
    """
    Safely responds to a Discord interaction.

    Args:
        *args: Positional arguments to be passed to the `respond` or `reply` method of the context.
        **kwargs: Keyword arguments to be passed to the `respond` or `reply` method of the context.
            `ctx` is a required keyword argument.

    Raises:
        ValueError: If `ctx` is not provided in the `kwargs`.
    
    Examples:
        ```py
        # Respond to an interaction
        await safe_ctx_respond(ctx=ctx, content="Hello World!")
        ```
    """
    try:
        # Try to respond to the interaction
        await ctx.respond(content)
    except discord.NotFound:  # NotFound is raised when the interaction is not found
        try:
            await ctx.message.reply(content)
        except (
                discord.NotFound,
                AttributeError,
        ):  # AttributeError is raised when ctx.message is None, NotFound is raised when the message is not found
            # If the message is not found, send a new message to the channel
            content = f"**{ctx.message.author.mention}** \n{content}".strip(
                "\n"
            ).strip()
            await ctx.channel.send(content)
