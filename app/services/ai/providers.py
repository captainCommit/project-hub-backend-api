"""Talking to model providers through one small interface.

Nearly every provider speaks one of two wire formats, so two adapters cover them all:
- OpenAI Chat Completions: OpenAI, Azure OpenAI, Google Gemini (its OpenAI-compatible endpoint), and any
  OpenAI-compatible server (OpenRouter, Groq, Mistral, Ollama, vLLM, ...).
- Anthropic Messages: Claude.

Everything else in the assistant uses the neutral shapes below and never sees a provider's own format.
Messages are plain dicts so they can be stored as JSON:
    {"role": "user", "content": "..."}
    {"role": "assistant", "content": "...", "tool_calls": [{"id": "...", "name": "...", "arguments": {...}}]}
    {"role": "tool", "tool_call_id": "...", "name": "...", "content": "..."}
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from app.services.ai.endpoints import Resolver, UnsafeEndpoint, check_endpoint, resolve_host


class Provider(str, Enum):
    OPENAI = "OPENAI"
    ANTHROPIC = "ANTHROPIC"
    GEMINI = "GEMINI"
    AZURE_OPENAI = "AZURE_OPENAI"
    CUSTOM = "CUSTOM"


PROVIDER_LABELS = {
    Provider.OPENAI: "OpenAI",
    Provider.ANTHROPIC: "Anthropic",
    Provider.GEMINI: "Google Gemini",
    Provider.AZURE_OPENAI: "Azure OpenAI",
    Provider.CUSTOM: "Custom endpoint",
}
# Owners type the endpoint for these; the others have fixed, well-known URLs.
PROVIDERS_WITH_ENDPOINT = {Provider.AZURE_OPENAI, Provider.CUSTOM}

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_AZURE_API_VERSION = "2024-10-21"
REQUEST_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class ProviderConfig:
    provider: Provider
    # For Azure OpenAI this is the deployment name.
    model: str
    api_key: str
    # Azure OpenAI: the resource endpoint. Custom: the base URL that /chat/completions is added to.
    base_url: str | None = None
    azure_api_version: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    # JSON Schema for the arguments object.
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def as_message_part(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class ModelReply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class ErrorKind(str, Enum):
    BAD_KEY = "BAD_KEY"
    RATE_LIMITED = "RATE_LIMITED"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    PROVIDER_DOWN = "PROVIDER_DOWN"
    TOO_LONG = "TOO_LONG"
    NO_TOOLS = "NO_TOOLS"
    BAD_ENDPOINT = "BAD_ENDPOINT"
    FAILED = "FAILED"


class ProviderError(Exception):
    """A provider call failed. The message is safe to show the account owner (it never contains the key)."""

    def __init__(self, kind: ErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


# (url, headers, body, timeout) -> (HTTP status, parsed JSON or raw text). Tests pass a fake.
Transport = Callable[[str, dict[str, str], dict[str, Any], float], tuple[int, Any]]


def requests_transport(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float) -> tuple[int, Any]:
    import requests

    try:
        response = requests.post(url, headers=headers, json=body, timeout=timeout, allow_redirects=False)
    except requests.Timeout as exc:
        raise ProviderError(ErrorKind.PROVIDER_DOWN, "The provider took too long to answer.") from exc
    except requests.RequestException as exc:
        raise ProviderError(ErrorKind.PROVIDER_DOWN, "The provider could not be reached.") from exc
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, response.text


class ChatModel(Protocol):
    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        max_tokens: int,
        force_tool: str | None = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> ModelReply: ...


def chat_model(
    config: ProviderConfig, *, transport: Transport = requests_transport, resolver: Resolver = resolve_host
) -> ChatModel:
    if config.provider == Provider.ANTHROPIC:
        return AnthropicModel(config, transport)
    return ChatCompletionsModel(config, transport, resolver)


# --- Errors ------------------------------------------------------------------------------------------------


def _provider_message(body: Any) -> str:
    if isinstance(body, list) and body:
        body = body[0]
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict):
            return str(error.get("message") or "")
        if isinstance(error, str):
            return error
    if isinstance(body, str):
        return body
    return ""


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "[key]") if secret else text


def raise_for_status(status: int, body: Any, config: ProviderConfig) -> None:
    if 200 <= status < 300:
        return
    detail = _redact(" ".join(_provider_message(body).split())[:300], config.api_key)
    lowered = detail.lower()
    if status in (401, 403):
        raise ProviderError(ErrorKind.BAD_KEY, "The provider rejected the API key." + (f" ({detail})" if detail else ""))
    if status == 404:
        raise ProviderError(
            ErrorKind.MODEL_NOT_FOUND, f"The provider doesn't recognise the model \"{config.model}\"" + (f" ({detail})" if detail else ".")
        )
    if status == 429:
        raise ProviderError(
            ErrorKind.RATE_LIMITED,
            "The provider is limiting requests, or the account with the provider is out of credit."
            + (f" ({detail})" if detail else ""),
        )
    if status >= 500:
        raise ProviderError(ErrorKind.PROVIDER_DOWN, "The provider had a problem answering. Try again shortly.")
    if any(phrase in lowered for phrase in ("context length", "context_length", "too long", "maximum context", "too many tokens")):
        raise ProviderError(ErrorKind.TOO_LONG, "The conversation is too long for this model. Start a new chat.")
    if ("tool" in lowered or "function" in lowered) and any(phrase in lowered for phrase in ("not support", "unsupported", "does not support")):
        raise ProviderError(ErrorKind.NO_TOOLS, "This model doesn't support tool calling, which the assistant needs.")
    raise ProviderError(ErrorKind.FAILED, f"The provider refused the request ({status})" + (f": {detail}" if detail else "."))


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# --- OpenAI Chat Completions (OpenAI, Azure OpenAI, Gemini, custom) -----------------------------------------


class ChatCompletionsModel:
    def __init__(self, config: ProviderConfig, transport: Transport, resolver: Resolver) -> None:
        self.config = config
        self.transport = transport
        self.resolver = resolver

    def request_target(self) -> tuple[str, dict[str, str]]:
        config = self.config
        headers = {"Content-Type": "application/json"}
        if config.provider == Provider.OPENAI:
            url = OPENAI_URL
        elif config.provider == Provider.GEMINI:
            url = GEMINI_URL
        else:
            try:
                base = check_endpoint(config.base_url or "", resolver=self.resolver)
            except UnsafeEndpoint as exc:
                raise ProviderError(ErrorKind.BAD_ENDPOINT, str(exc)) from exc
            if config.provider == Provider.AZURE_OPENAI:
                version = config.azure_api_version or DEFAULT_AZURE_API_VERSION
                url = f"{base}/openai/deployments/{config.model}/chat/completions?api-version={version}"
                headers["api-key"] = config.api_key
                return url, headers
            url = f"{base}/chat/completions"
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        return url, headers

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        max_tokens: int,
        force_tool: str | None = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> ModelReply:
        url, headers = self.request_target()
        body: dict[str, Any] = {"messages": [{"role": "system", "content": system}, *map(self.to_wire, messages)]}
        if self.config.provider != Provider.AZURE_OPENAI:
            body["model"] = self.config.model
        # OpenAI's newer models only accept max_completion_tokens; most compatible servers only max_tokens.
        token_field = "max_completion_tokens" if self.config.provider in (Provider.OPENAI, Provider.AZURE_OPENAI) else "max_tokens"
        body[token_field] = max_tokens
        if tools:
            body["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools
            ]
        if force_tool:
            body["tool_choice"] = {"type": "function", "function": {"name": force_tool}}
        status, data = self.transport(url, headers, body, timeout)
        raise_for_status(status, data, self.config)
        return self.from_wire(data)

    @staticmethod
    def to_wire(message: dict[str, Any]) -> dict[str, Any]:
        role = message["role"]
        if role == "tool":
            return {"role": "tool", "tool_call_id": message["tool_call_id"], "content": message["content"]}
        if role == "assistant" and message.get("tool_calls"):
            return {
                "role": "assistant",
                "content": message.get("content") or None,
                "tool_calls": [
                    {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                    for c in message["tool_calls"]
                ],
            }
        return {"role": role, "content": message.get("content") or ""}

    def from_wire(self, data: Any) -> ModelReply:
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(ErrorKind.FAILED, "The provider sent an answer the assistant couldn't read.") from exc
        calls = []
        for index, call in enumerate(message.get("tool_calls") or []):
            function = call.get("function") or {}
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except ValueError:
                arguments = {}
            calls.append(
                ToolCall(
                    id=str(call.get("id") or f"call_{index}"),
                    name=str(function.get("name") or ""),
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )
        usage = data.get("usage") or {}
        return ModelReply(
            text=message.get("content") or "",
            tool_calls=calls,
            input_tokens=_int(usage.get("prompt_tokens")),
            output_tokens=_int(usage.get("completion_tokens")),
        )


# --- Anthropic Messages ------------------------------------------------------------------------------------


class AnthropicModel:
    def __init__(self, config: ProviderConfig, transport: Transport) -> None:
        self.config = config
        self.transport = transport

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        max_tokens: int,
        force_tool: str | None = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> ModelReply:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.config.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }
        body: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": self.to_wire(messages),
        }
        if tools:
            body["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]
        if force_tool:
            body["tool_choice"] = {"type": "tool", "name": force_tool}
        status, data = self.transport(ANTHROPIC_URL, headers, body, timeout)
        raise_for_status(status, data, self.config)
        return self.from_wire(data)

    @staticmethod
    def to_wire(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Tool results become tool_result blocks in a user turn; consecutive ones share that turn."""
        wire: list[dict[str, Any]] = []
        for message in messages:
            role = message["role"]
            if role == "tool":
                block = {"type": "tool_result", "tool_use_id": message["tool_call_id"], "content": message["content"]}
                previous = wire[-1] if wire else None
                if previous and previous["role"] == "user" and isinstance(previous["content"], list):
                    previous["content"].append(block)
                else:
                    wire.append({"role": "user", "content": [block]})
            elif role == "assistant":
                blocks: list[dict[str, Any]] = []
                if message.get("content"):
                    blocks.append({"type": "text", "text": message["content"]})
                for call in message.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": call["id"], "name": call["name"], "input": call["arguments"]})
                wire.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            else:
                wire.append({"role": "user", "content": message.get("content") or ""})
        return wire

    @staticmethod
    def from_wire(data: Any) -> ModelReply:
        if not isinstance(data, dict) or not isinstance(data.get("content"), list):
            raise ProviderError(ErrorKind.FAILED, "The provider sent an answer the assistant couldn't read.")
        texts, calls = [], []
        for block in data["content"]:
            if block.get("type") == "text":
                texts.append(block.get("text") or "")
            elif block.get("type") == "tool_use":
                arguments = block.get("input")
                calls.append(
                    ToolCall(
                        id=str(block.get("id") or ""),
                        name=str(block.get("name") or ""),
                        arguments=arguments if isinstance(arguments, dict) else {},
                    )
                )
        usage = data.get("usage") or {}
        return ModelReply(
            text="".join(texts),
            tool_calls=calls,
            input_tokens=_int(usage.get("input_tokens")),
            output_tokens=_int(usage.get("output_tokens")),
        )


# --- Checking a key ----------------------------------------------------------------------------------------

PING_TOOL = ToolSpec(
    name="ping",
    description="Confirms the connection works.",
    parameters={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
)


def check_connection(
    config: ProviderConfig, *, transport: Transport = requests_transport, resolver: Resolver = resolve_host
) -> ModelReply:
    """A tiny request that must come back as a tool call: the assistant is useless without tool calling."""
    reply = chat_model(config, transport=transport, resolver=resolver).complete(
        system="You are checking a connection.",
        messages=[{"role": "user", "content": "Call the ping tool with ok set to true."}],
        tools=[PING_TOOL],
        max_tokens=512,
        force_tool=PING_TOOL.name,
        timeout=20,
    )
    if not any(call.name == PING_TOOL.name for call in reply.tool_calls):
        raise ProviderError(ErrorKind.NO_TOOLS, "This model didn't make a tool call, which the assistant needs.")
    return reply

