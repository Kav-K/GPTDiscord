import discord

async def safe_ctx_respond(*args: discord.ApplicationContext.respond.args, **kwargs: discord.ApplicationContext.respond.kwargs) -> None:
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
    # Get the context from the kwargs
    ctx: discord.ApplicationContext = kwargs.get("ctx", None)
    kwargs.pop("ctx", None)
    
    # Raise an error if context is not provided
    if ctx is None:
        raise ValueError("ctx is a required keyword argument")
    
    try:
        # Try to respond to the interaction
        await ctx.respond(*args, **kwargs)
    except discord.NotFound:  # NotFound is raised when the interaction is not found
        try:
            # If the interaction is not found, try to reply to the message
            if kwargs.get("ephemeral", False):
                kwargs.pop("ephemeral")
                kwargs["delete_after"] = 5
            await ctx.message.reply(*args, **kwargs)
        except (
            discord.NotFound,
            AttributeError,
        ):  # AttributeError is raised when ctx.message is None, NotFound is raised when the message is not found
            # If the message is not found, send a new message to the channel
            if len(args) > 0:
                content = args[0] or ""
                args = args[1:]
            else:
                content = kwargs.get("content", "")
            kwargs["content"] = f"**{ctx.author.mention}** \n{content}".strip(
                "\n"
            ).strip()
            await ctx.channel.send(*args, **kwargs)
