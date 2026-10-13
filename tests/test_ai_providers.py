from typing import Any

import pytest

from app.services.ai.endpoints import UnsafeEndpoint, check_endpoint
from app.services.ai.providers import (
    ANTHROPIC_URL,
    GEMINI_URL,
    OPENAI_URL,
    AnthropicModel,
    ErrorKind,
    Provider,
    ProviderConfig,
    ProviderError,
    ToolSpec,
    chat_model,
    check_connection,
)

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731
TOOL = ToolSpec("lookup", "Look something up.", {"type": "object", "properties": {"q": {"type": "string"}}})
CONVERSATION = [
    {"role": "user", "content": "What's late?"},
    {"role": "assistant", "content": "Let me check.", "tool_calls": [{"id": "c1", "name": "lookup", "arguments": {"q": "late"}}, {"id": "c2", "name": "lookup", "arguments": {"q": "due"}}]},
    {"role": "tool", "tool_call_id": "c1", "name": "lookup", "content": "{\"items\":[]}"},
    {"role": "tool", "tool_call_id": "c2", "name": "lookup", "content": "{\"items\":[1]}"},
]


class FakeTransport:
    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.status = status
        self.body = body
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, headers: dict[str, str], body: dict[str, Any], timeout: float) -> tuple[int, Any]:
        self.calls.append({"url": url, "headers": headers, "body": body})
        return self.status, self.body


def openai_reply(content: str | None = "Done.", tool_calls: list[dict] | None = None) -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": content, "tool_calls": tool_calls}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30},
    }


def test_openai_request_and_tool_calls() -> None:
    transport = FakeTransport(
        body=openai_reply(None, [{"id": "call_9", "type": "function", "function": {"name": "lookup", "arguments": "{\"q\": \"risks\"}"}}])
    )
    model = chat_model(ProviderConfig(Provider.OPENAI, "gpt-test", "sk-secret"), transport=transport)

    reply = model.complete(system="Be brief.", messages=CONVERSATION, tools=[TOOL], max_tokens=500)

    [call] = transport.calls
    assert call["url"] == OPENAI_URL
    assert call["headers"]["Authorization"] == "Bearer sk-secret"
    body = call["body"]
    assert body["model"] == "gpt-test" and body["max_completion_tokens"] == 500
    assert body["messages"][0] == {"role": "system", "content": "Be brief."}
    assert body["messages"][2]["tool_calls"][0]["function"] == {"name": "lookup", "arguments": "{\"q\": \"late\"}"}
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "c1", "content": "{\"items\":[]}"}
    assert body["tools"][0]["function"]["name"] == "lookup"
    assert reply.tool_calls[0].as_message_part() == {"id": "call_9", "name": "lookup", "arguments": {"q": "risks"}}
    assert (reply.text, reply.input_tokens, reply.output_tokens) == ("", 120, 30)


def test_gemini_azure_and_custom_targets() -> None:
    gemini = FakeTransport(body=openai_reply())
    chat_model(ProviderConfig(Provider.GEMINI, "gemini-test", "g-key"), transport=gemini).complete(
        system="s", messages=[], tools=[], max_tokens=10
    )
    azure = FakeTransport(body=openai_reply())
    chat_model(
        ProviderConfig(Provider.AZURE_OPENAI, "my-deployment", "az-key", base_url="https://acme.openai.azure.com/"),
        transport=azure,
        resolver=PUBLIC,
    ).complete(system="s", messages=[], tools=[], max_tokens=10)
    custom = FakeTransport(body=openai_reply())
    chat_model(
        ProviderConfig(Provider.CUSTOM, "llama", "", base_url="https://llm.example.com/v1"), transport=custom, resolver=PUBLIC
    ).complete(system="s", messages=[], tools=[], max_tokens=10)

    assert gemini.calls[0]["url"] == GEMINI_URL and gemini.calls[0]["body"]["max_tokens"] == 10
    assert azure.calls[0]["url"] == "https://acme.openai.azure.com/openai/deployments/my-deployment/chat/completions?api-version=2024-10-21"
    assert azure.calls[0]["headers"]["api-key"] == "az-key" and "Authorization" not in azure.calls[0]["headers"]
    assert "model" not in azure.calls[0]["body"]
    assert custom.calls[0]["url"] == "https://llm.example.com/v1/chat/completions"
    assert "Authorization" not in custom.calls[0]["headers"]  # no key: self-hosted servers often need none


def test_anthropic_request_and_reply() -> None:
    transport = FakeTransport(
        body={
            "content": [
                {"type": "text", "text": "Checking."},
                {"type": "tool_use", "id": "tu_1", "name": "lookup", "input": {"q": "x"}},
            ],
            "usage": {"input_tokens": 200, "output_tokens": 15},
        }
    )
    model = chat_model(ProviderConfig(Provider.ANTHROPIC, "claude-test", "ak-secret"), transport=transport)

    reply = model.complete(system="Be brief.", messages=CONVERSATION, tools=[TOOL], max_tokens=800)

    [call] = transport.calls
    assert call["url"] == ANTHROPIC_URL and call["headers"]["x-api-key"] == "ak-secret"
    body = call["body"]
    assert body["system"] == "Be brief." and body["tools"][0]["input_schema"] == TOOL.parameters
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assert [b["type"] for b in body["messages"][1]["content"]] == ["text", "tool_use", "tool_use"]
    # Both tool results share one user turn, as the Messages API requires.
    assert [b["tool_use_id"] for b in body["messages"][2]["content"]] == ["c1", "c2"]
    assert reply.text == "Checking." and reply.tool_calls[0].arguments == {"q": "x"}
    assert (reply.input_tokens, reply.output_tokens) == (200, 15)


def test_anthropic_assistant_turn_without_text() -> None:
    wire = AnthropicModel.to_wire([{"role": "assistant", "content": "", "tool_calls": [{"id": "a", "name": "t", "arguments": {}}]}])
    assert wire == [{"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "t", "input": {}}]}]


@pytest.mark.parametrize(
    ("status", "body", "kind"),
    [
        (401, {"error": {"message": "Incorrect API key provided: sk-secret"}}, ErrorKind.BAD_KEY),
        (404, {"error": {"message": "model not found"}}, ErrorKind.MODEL_NOT_FOUND),
        (429, {"error": {"message": "You exceeded your current quota"}}, ErrorKind.RATE_LIMITED),
        (503, "upstream down", ErrorKind.PROVIDER_DOWN),
        (400, {"error": {"message": "This model's maximum context length is 8192 tokens"}}, ErrorKind.TOO_LONG),
        (400, [{"error": {"message": "Function calling is not supported for this model"}}], ErrorKind.NO_TOOLS),
        (400, {"type": "error", "error": {"message": "bad thing"}}, ErrorKind.FAILED),
    ],
)
def test_errors_are_mapped_and_never_echo_the_key(status: int, body: Any, kind: ErrorKind) -> None:
    model = chat_model(ProviderConfig(Provider.OPENAI, "m", "sk-secret"), transport=FakeTransport(status, body))

    with pytest.raises(ProviderError) as raised:
        model.complete(system="s", messages=[], tools=[], max_tokens=10)

    assert raised.value.kind == kind
    assert "sk-secret" not in raised.value.message


def test_unreadable_reply_is_an_error() -> None:
    model = chat_model(ProviderConfig(Provider.OPENAI, "m", "k"), transport=FakeTransport(200, {"nothing": True}))
    with pytest.raises(ProviderError) as raised:
        model.complete(system="s", messages=[], tools=[], max_tokens=10)
    assert raised.value.kind == ErrorKind.FAILED


def test_check_connection_needs_a_tool_call() -> None:
    config = ProviderConfig(Provider.OPENAI, "m", "k")
    ok = FakeTransport(body=openai_reply(None, [{"id": "1", "function": {"name": "ping", "arguments": "{\"ok\": true}"}}]))
    text_only = FakeTransport(body=openai_reply("pong"))

    check_connection(config, transport=ok)
    with pytest.raises(ProviderError) as raised:
        check_connection(config, transport=text_only)

    assert ok.calls[0]["body"]["tool_choice"] == {"type": "function", "function": {"name": "ping"}}
    assert raised.value.kind == ErrorKind.NO_TOOLS


@pytest.mark.parametrize(
    ("url", "addresses"),
    [
        ("http://llm.example.com", ["93.184.216.34"]),
        ("https://user:pw@llm.example.com", ["93.184.216.34"]),
        ("https://localhost:9001", ["127.0.0.1"]),
        ("https://metadata.internal", ["169.254.169.254"]),
        ("https://intranet.example.com", ["10.0.0.5"]),
        ("https://v6.example.com", ["::1"]),
        ("https://nowhere.example.com", []),
    ],
)
def test_unsafe_endpoints_are_refused(url: str, addresses: list[str]) -> None:
    with pytest.raises(UnsafeEndpoint):
        check_endpoint(url, resolver=lambda host: addresses)


def test_custom_endpoint_is_rechecked_before_each_call() -> None:
    config = ProviderConfig(Provider.CUSTOM, "m", "k", base_url="https://llm.example.com")
    model = chat_model(config, transport=FakeTransport(body=openai_reply()), resolver=lambda host: ["10.1.2.3"])

    with pytest.raises(ProviderError) as raised:
        model.complete(system="s", messages=[], tools=[], max_tokens=10)

    assert raised.value.kind == ErrorKind.BAD_ENDPOINT
    assert check_endpoint("https://llm.example.com/v1/", resolver=PUBLIC) == "https://llm.example.com/v1"
