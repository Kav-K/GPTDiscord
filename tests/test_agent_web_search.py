"""Native hosted tools and citations, without provider or Discord requests."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("langchain_classic")
pytest.importorskip("langchain_openai")

from langchain_classic.memory import ConversationBufferMemory
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import Tool

from services.agent_service import (
    agent_output_text,
    agent_search_response,
    create_chat_agent,
)
from services.responses_service import Citation, discord_messages


@pytest.mark.parametrize("native", [False, True])
def test_hosted_tools_bind_to_model_but_are_not_local_executor_tools(native):
    browse = Mock(return_value="Page text")
    local_tools = [
        Tool(name="Web-Crawling-Tool", func=browse, description="Read a webpage.")
    ]
    builtin_tools = [{"type": "web_search", "external_web_access": True}]
    output = [{"type": "text", "text": "Current answer."}]
    if native:
        output.insert(
            0,
            {
                "type": "web_search_call",
                "id": "ws_offline",
                "status": "completed",
                "action": {"type": "search", "query": "current release"},
            },
        )
    bind = Mock(return_value=RunnableLambda(lambda prompt: AIMessage(content=output)))
    llm = SimpleNamespace(bind_tools=bind)
    memory = ConversationBufferMemory(
        memory_key="memory", return_messages=True, output_key="output"
    )

    agent = create_chat_agent(
        llm,
        local_tools,
        "Use current sources.",
        memory=memory,
        builtin_tools=builtin_tools if native else None,
    )
    result = agent.invoke({"input": "What changed?"})

    assert bind.call_args.args[0] == local_tools + (builtin_tools if native else [])
    assert agent.tools == local_tools
    assert len(local_tools) == 1
    assert builtin_tools == [{"type": "web_search", "external_web_access": True}]
    assert result["output"] == output
    assert result["intermediate_steps"] == []
    browse.assert_not_called()  # The provider, rather than this bot, runs search.
    assert agent_output_text(result["output"]) == "Current answer."


@pytest.mark.parametrize("annotation_type", ["url_citation", "citation"])
def test_native_and_standardized_citations_remain_clickable(annotation_type):
    text = "The release is current. [1]"
    output = [
        {
            "type": "web_search_call",
            "id": "ws_search",
            "action": {"type": "search", "query": "release"},
        },
        {
            "type": "web_search_call",
            "id": "ws_open",
            "action": {"type": "open_page", "url": "https://example.org/release"},
        },
        {
            "type": "text",
            "text": text,
            "annotations": [
                {
                    "type": annotation_type,
                    "start_index": text.index("[1]"),
                    "end_index": len(text),
                    "title": "Release notes",
                    "url": "https://example.org/release",
                }
            ],
        },
    ]

    result = agent_search_response(output)

    assert result.text == "The release is current. [[1]](<https://example.org/release>)"
    assert result.citations == (
        Citation("Release notes", "https://example.org/release"),
    )
    assert result.search_calls == 1
    assert result.web_tool_calls == 2
    assert (
        "**Sources**\n1. [Release notes](<https://example.org/release>)"
        in "\n".join(discord_messages(result, limit=1900))
    )
    assert output[-1]["annotations"][0]["type"] == annotation_type


def test_citations_preserve_prose_and_deduplicate_across_blocks():
    output = [
        {
            "type": "text",
            "text": "New release",
            "annotations": [
                {
                    "type": "url_citation",
                    "url": "https://example.org/release",
                    "title": "Release",
                    "start_index": 0,
                    "end_index": 11,
                }
            ],
        },
        {
            "type": "output_text",
            "text": "More details. [1]",
            "annotations": [
                {
                    "type": "citation",
                    "url": "https://example.org/release",
                    "title": "Same release",
                    "start_index": 14,
                    "end_index": 17,
                }
            ],
        },
    ]

    result = agent_search_response(output)

    assert result.text == (
        "New release [[1]](<https://example.org/release>)\n\n"
        "More details. [[1]](<https://example.org/release>)"
    )
    assert result.citations == (Citation("Release", "https://example.org/release"),)


def test_citation_urls_and_titles_are_safe_and_links_never_split():
    url = "https://example.org/report?q=[new]"
    output = [
        {
            "type": "text",
            "text": "A cited result. " * 35,
            "annotations": [
                {"type": "citation", "url": "javascript:alert(1)", "title": "Unsafe"},
                {"type": "citation", "url": url, "title": "[Report]\n**source**"},
                {"type": "file_citation", "file_id": "file_not_a_web_source"},
            ],
        }
    ]

    result = agent_search_response(output)
    messages = discord_messages(result, limit=160)

    assert len(result.citations) == 1
    assert result.citations[0].url == "https://example.org/report?q=%5Bnew%5D"
    assert all(len(message) <= 160 for message in messages)
    sources = [message for message in messages if "https://example.org/" in message]
    assert len(sources) == 1
    assert (
        "[\\[Report\\] \\*\\*source\\*\\*](<https://example.org/report?q=%5Bnew%5D>)"
        in sources[0]
    )
    assert "javascript:" not in "\n".join(messages)


@pytest.mark.parametrize(
    "output", ["Legacy answer", [], [{"type": "reasoning", "summary": []}]]
)
def test_non_text_outputs_keep_legacy_fallback(output):
    assert agent_search_response(output) is None


def test_plain_content_blocks_remain_supported_without_invented_sources():
    result = agent_search_response(
        [{"type": "text", "text": "No lookup needed.", "annotations": None}]
    )

    assert result.text == "No lookup needed."
    assert result.citations == ()
    assert result.web_tool_calls == result.search_calls == 0
