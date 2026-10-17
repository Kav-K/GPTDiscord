"""Current tool-calling agents for the optional chat integrations."""

import json
import hashlib

import discord

from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_classic.memory import ConversationSummaryBufferMemory
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI

from models.openai_model import Models
from services.environment_service import EnvService
from services.openai_client_service import (
    client_routing,
    RequestOpenAI as _AgentOpenAI,
    RequestAsyncOpenAI as _AgentAsyncOpenAI,
)
from services.usage_service import UsageService


def personal_key_fingerprint(key):
    return hashlib.sha256(key.encode()).digest() if key else None


async def session_key_is_current(message, fingerprint, database):
    """Revoke an old agent's credentials when its creator changes setup."""
    if fingerprint is not None and fingerprint == personal_key_fingerprint(database.get(message.author.id)):
        return True
    guidance = "Your session's API key changed or was removed. Run `/setup` and start a new session. You can still type `end` to close this one."
    try:
        await message.author.send(guidance)
    except discord.HTTPException:
        await message.reply("Run `/setup` and start a new session to continue.", delete_after=30)
    return False


class AgentChatOpenAI(ChatOpenAI):
    def get_num_tokens_from_messages(self, messages, tools=None, **kwargs):
        # Agent memory must not require a separate vocabulary download after
        # producing a paid answer. Reuse the bot's cached, resilient estimates.
        tokens = 3 + sum(
            UsageService.count_tokens_static(message.model_dump_json()) + 3
            for message in messages
        )
        if tools:
            tokens += UsageService.count_tokens_static(json.dumps(tools, default=str))
        return tokens


def create_chat_llm(model, temperature=0, top_p=1, api_key=None, **kwargs):
    if not Models.is_reasoning(model):
        kwargs.update(temperature=temperature, top_p=top_p)
    key = api_key or EnvService.get_openai_token()
    client_kwargs = {
        "api_key": key,
        "base_url": EnvService.get_openai_base_url(),
        **client_routing(key),
    }
    sync_client = _AgentOpenAI(**client_kwargs)
    async_client = _AgentAsyncOpenAI(**client_kwargs)
    return AgentChatOpenAI(
        model=model,
        api_key=key,
        client=sync_client.chat.completions,
        async_client=async_client.chat.completions,
        root_client=sync_client,
        root_async_client=async_client,
        base_url=EnvService.get_openai_base_url(),
        use_responses_api=True,
        **kwargs,
    )


def create_chat_agent(llm, tools, system_message, memory=None):
    if memory is None:
        memory = ConversationSummaryBufferMemory(
            memory_key="memory",
            return_messages=True,
            llm=llm,
            output_key="output",
            max_token_limit=min(16000, Models.get_max_tokens(llm.model_name) // 4),
        )
    memory.output_key = "output"
    prompt = ChatPromptTemplate.from_messages(
        [
            SystemMessage(content=system_message),
            MessagesPlaceholder(variable_name="memory"),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
    )
    return AgentExecutor(
        agent=create_tool_calling_agent(llm, tools, prompt),
        tools=tools,
        memory=memory,
        max_iterations=5,
        return_intermediate_steps=True,
        handle_parsing_errors="Check the tool input and try again.",
    )


def agent_output_text(output):
    """Normalize both Chat Completions strings and Responses content blocks."""
    return AIMessage(content=output).text
