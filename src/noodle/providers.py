"""Model providers: one interface, several adapters, one service call per call.

The provider makes exactly one service call
(:meth:`Provider.complete`); running tools stays in :mod:`noodle.loop`. Each
adapter converts the neutral message list into its wire format, sends it through
an injectable ``transport`` and parses the reply back into a :class:`Completion`.
Provider SDKs are imported lazily inside the default transport, so importing this
module never pulls in a library for an unused provider (05 section 6).

Reasoning the model returns separately lives on ``Completion.reasoning`` and must
never reach the answer (05 section 1).
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Sequence

# --------------------------------------------------------------------------- #
# Neutral message and completion types shared by every adapter
# --------------------------------------------------------------------------- #


@dataclass
class ToolCall:
    """A model's request to run one tool."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    signature: Optional[str] = None  # Google replays tool requests with this


@dataclass
class Message:
    """A turn in the conversation. ``role`` is one of user / assistant / tool."""

    role: str
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


@dataclass
class Completion:
    """One model reply."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: Optional[str] = None
    stop_reason: Optional[str] = None
    usage: dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""


class ProviderError(Exception):
    """A provider call failed."""


class RateLimited(ProviderError):
    """A short rate limit; retry after ``retry_after`` seconds."""

    def __init__(self, retry_after: float = 1.0):
        self.retry_after = retry_after
        super().__init__(f"rate limited, retry after {retry_after}s")


class OutputCapExceeded(ProviderError):
    """A per-minute output-token cap was hit; lower the requested cap and retry."""


class ProviderSelectionError(ProviderError):
    """No provider could be chosen from the available keys."""


# --------------------------------------------------------------------------- #
# The interface
# --------------------------------------------------------------------------- #


class Provider:
    """The one method the loop depends on. Subclasses implement :meth:`complete`."""

    name = "base"

    def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Optional[list[dict[str, Any]]] = None,
        system: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 2000,
        temperature: float = 0.0,
    ) -> Completion:  # pragma: no cover - interface
        raise NotImplementedError


class ScriptedProvider(Provider):
    """The fake model (09). Returns queued replies and records every request."""

    name = "scripted"

    def __init__(self, replies: Iterable[Any], name: str = "scripted"):
        self.name = name
        self._replies: list[Any] = list(replies)
        self.requests: list[dict[str, Any]] = []

    def queue(self, *replies: Any) -> None:
        self._replies.extend(replies)

    def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Optional[list[dict[str, Any]]] = None,
        system: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 2000,
        temperature: float = 0.0,
    ) -> Completion:
        self.requests.append(
            {
                "messages": list(messages),
                "tools": tools,
                "system": system,
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        if not self._replies:
            raise ProviderError("the scripted provider ran out of replies")
        reply = self._replies.pop(0)
        if callable(reply):
            return reply(messages, tools)
        return reply


# --------------------------------------------------------------------------- #
# Shared request handling: retry short limits, lower a per-minute output cap
# --------------------------------------------------------------------------- #


class _RemoteProvider(Provider):
    """Common retry and cap-reduction logic for HTTP-backed adapters."""

    tokens_field = "max_tokens"

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        transport: Optional[Callable[[dict[str, Any]], Any]] = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 3,
    ):
        self._api_key = api_key
        self._transport = transport or self._default_transport
        self._sleep = sleep
        self.max_retries = max_retries
        self.cap_scale = 1.0  # remembered after a per-minute cap is hit

    def _default_transport(self, payload: dict[str, Any]) -> Any:  # pragma: no cover
        raise NotImplementedError

    def _translate_error(self, exc: Exception) -> Exception:
        """Map a provider SDK error to a retryable signal where possible. The
        adapters share one shape: a rate limit is retried, an output-token cap
        lowers the requested cap and remembers it."""

        text = f"{type(exc).__name__} {exc}".lower()
        if "429" in text or "rate limit" in text or "overloaded" in text or "too many requests" in text:
            return RateLimited()
        if "max_tokens" in text or "max output" in text or ("output" in text and "quota" in text):
            return OutputCapExceeded(str(exc))
        return exc

    def _invoke(self, payload: dict[str, Any]) -> Any:
        try:
            return self._transport(payload)
        except (RateLimited, OutputCapExceeded):
            raise
        except Exception as exc:  # noqa: BLE001 - translated, then handled by _request
            raise self._translate_error(exc) from exc

    def _request(self, payload: dict[str, Any], base_tokens: int) -> Any:
        attempts = 0
        while True:
            try:
                return self._invoke(payload)
            except RateLimited as exc:
                attempts += 1
                if attempts > self.max_retries:
                    raise
                self._sleep(exc.retry_after)
            except OutputCapExceeded:
                if self.cap_scale <= 0.25:
                    raise
                self.cap_scale *= 0.5
                payload = {
                    **payload,
                    self.tokens_field: max(1, int(base_tokens * self.cap_scale)),
                }

    def _scaled(self, max_tokens: int) -> int:
        return max(1, int(max_tokens * self.cap_scale))


def _as_dict(data: Any) -> dict[str, Any]:
    """Accept a plain dict or a provider SDK object."""

    if isinstance(data, dict):
        return data
    for method in ("model_dump", "to_dict", "dict"):
        fn = getattr(data, method, None)
        if callable(fn):
            return fn()
    return dict(data)  # pragma: no cover - last resort


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #


def _anthropic_content(message: Message) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if message.role == "tool":
        blocks.append(
            {"type": "tool_result", "tool_use_id": message.tool_call_id, "content": message.text}
        )
        return blocks
    if message.text:
        blocks.append({"type": "text", "text": message.text})
    for call in message.tool_calls:
        blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "input": dict(call.arguments)})
    return blocks


def _anthropic_role(message: Message) -> str:
    # tool results are shown to the model as a user turn
    return "assistant" if message.role == "assistant" else "user"


def to_anthropic_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Anthropic needs strictly alternating roles, so consecutive same-role
    turns are merged into one content-block list."""

    merged: list[tuple[str, list[dict[str, Any]]]] = []
    for message in messages:
        role = _anthropic_role(message)
        content = _anthropic_content(message)
        if merged and merged[-1][0] == role:
            merged[-1][1].extend(content)
        else:
            merged.append((role, list(content)))
    return [{"role": role, "content": content} for role, content in merged]


def to_anthropic_tools(tools: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"name": t["name"], "description": t.get("description", ""), "input_schema": t["parameters"]}
        for t in tools
    ]


def anthropic_payload(
    messages: Sequence[Message],
    tools: Optional[Sequence[dict[str, Any]]],
    system: Optional[str],
    model: Optional[str],
    max_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": to_anthropic_messages(messages),
    }
    if system:
        payload["system"] = system
    if tools:
        payload["tools"] = to_anthropic_tools(tools)
        payload["tool_choice"] = {"type": "auto"}
    return payload


def parse_anthropic(data: Any) -> Completion:
    data = _as_dict(data)
    texts: list[str] = []
    reasoning: list[str] = []
    calls: list[ToolCall] = []
    for block in data.get("content") or []:
        kind = block.get("type")
        if kind == "text":
            texts.append(block.get("text") or "")
        elif kind == "tool_use":
            calls.append(ToolCall(block.get("id"), block.get("name"), dict(block.get("input") or {})))
        elif kind in ("thinking", "reasoning"):
            reasoning.append(block.get("thinking") or block.get("text") or "")
    return Completion(
        text="".join(texts).strip(),
        tool_calls=calls,
        model=data.get("model"),
        stop_reason=data.get("stop_reason"),
        usage=dict(data.get("usage") or {}),
        reasoning="".join(reasoning).strip(),
    )


class AnthropicProvider(_RemoteProvider):
    """Instructions live in their own field; tools and results are content
    blocks; consecutive turns are merged (05 section 6)."""

    name = "anthropic"
    tokens_field = "max_tokens"

    def _default_transport(self, payload: dict[str, Any]) -> Any:  # pragma: no cover
        import anthropic  # lazy: never imported unless this provider is used

        client = anthropic.Anthropic(api_key=self._api_key)
        return client.messages.create(**payload)

    def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Optional[list[dict[str, Any]]] = None,
        system: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 2000,
        temperature: float = 0.0,
    ) -> Completion:
        payload = anthropic_payload(messages, tools, system, model, self._scaled(max_tokens), temperature)
        data = self._request(payload, base_tokens=max_tokens)
        return parse_anthropic(data)


# --------------------------------------------------------------------------- #
# Google (Gemini)
# --------------------------------------------------------------------------- #


def _json_or_text(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return {"result": text}


def to_google_contents(messages: Sequence[Message]) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    for message in messages:
        parts: list[dict[str, Any]] = []
        if message.role == "tool":
            parts.append(
                {
                    "functionResponse": {
                        "id": message.tool_call_id,
                        "name": message.name,
                        "response": _json_or_text(message.text),
                    }
                }
            )
            role = "user"  # a function response is returned to the model as user
        else:
            role = "model" if message.role == "assistant" else "user"
            if message.text:
                parts.append({"text": message.text})
            for call in message.tool_calls:
                part: dict[str, Any] = {
                    "functionCall": {"id": call.id, "name": call.name, "args": dict(call.arguments)}
                }
                if call.signature:
                    part["thoughtSignature"] = call.signature
                parts.append(part)
        contents.append({"role": role, "parts": parts})
    return contents


def to_google_tools(tools: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    declarations: list[dict[str, Any]] = []
    for t in tools:
        decl: dict[str, Any] = {"name": t["name"], "description": t.get("description", "")}
        parameters = t.get("parameters") or {}
        properties = parameters.get("properties") or {}
        if properties:  # a parameterless tool omits the schema entirely
            decl["parameters"] = parameters
        declarations.append(decl)
    return [{"functionDeclarations": declarations}]


def google_payload(
    messages: Sequence[Message],
    tools: Optional[Sequence[dict[str, Any]]],
    system: Optional[str],
    model: Optional[str],
    max_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "contents": to_google_contents(messages),
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    if tools:
        payload["tools"] = to_google_tools(tools)
        payload["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
    return payload


def parse_google(data: Any) -> Completion:
    data = _as_dict(data)
    candidates = data.get("candidates") or []
    texts: list[str] = []
    calls: list[ToolCall] = []
    stop_reason: Optional[str] = None
    if candidates:
        candidate = candidates[0]
        stop_reason = candidate.get("finishReason")
        for part in (candidate.get("content") or {}).get("parts") or []:
            if part.get("text"):
                texts.append(part["text"])
            if part.get("functionCall"):
                call = part["functionCall"]
                calls.append(
                    ToolCall(
                        call.get("id") or "",
                        call.get("name"),
                        dict(call.get("args") or {}),
                        signature=part.get("thoughtSignature"),
                    )
                )
    return Completion(
        text="".join(texts).strip(),
        tool_calls=calls,
        model=data.get("modelVersion"),
        stop_reason=stop_reason,
        usage=dict(data.get("usageMetadata") or {}),
    )


class GoogleProvider(_RemoteProvider):
    """The assistant role is ``model``; tool requests are replayed with their
    signature; a parameterless tool omits its schema (05 section 6)."""

    name = "google"
    tokens_field = "maxOutputTokens"

    def _default_transport(self, payload: dict[str, Any]) -> Any:  # pragma: no cover
        import google.generativeai as genai  # lazy

        genai.configure(api_key=self._api_key)
        model = genai.GenerativeModel(payload.pop("model"))
        return model.generate_content(**payload)

    def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Optional[list[dict[str, Any]]] = None,
        system: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 2000,
        temperature: float = 0.0,
    ) -> Completion:
        payload = google_payload(messages, tools, system, model, self._scaled(max_tokens), temperature)
        data = self._request(payload, base_tokens=max_tokens)
        return parse_google(data)


# --------------------------------------------------------------------------- #
# OpenAI-compatible (OpenAI, Groq, and any /chat/completions host)
# --------------------------------------------------------------------------- #


def to_openai_messages(messages: Sequence[Message], system: Optional[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})
    for message in messages:
        if message.role == "tool":
            out.append({"role": "tool", "tool_call_id": message.tool_call_id, "content": message.text})
        elif message.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": message.text or None}
            if message.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                    }
                    for call in message.tool_calls
                ]
            out.append(entry)
        else:
            out.append({"role": "user", "content": message.text})
    return out


def to_openai_tools(tools: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                          "parameters": t["parameters"]}}
        for t in tools
    ]


def openai_payload(
    messages: Sequence[Message],
    tools: Optional[Sequence[dict[str, Any]]],
    system: Optional[str],
    model: Optional[str],
    max_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": to_openai_messages(messages, system),
    }
    if tools:
        payload["tools"] = to_openai_tools(tools)
        payload["tool_choice"] = "auto"
    return payload


def parse_openai(data: Any) -> Completion:
    data = _as_dict(data)
    choices = data.get("choices") or []
    if not choices:
        return Completion(model=data.get("model"), usage=dict(data.get("usage") or {}))
    choice = choices[0]
    message = choice.get("message") or {}
    calls: list[ToolCall] = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except ValueError:
            arguments = {}  # bad JSON surfaces as an argument-validation error later
        calls.append(ToolCall(raw.get("id"), function.get("name"), arguments))
    return Completion(
        text=(message.get("content") or "").strip(),
        tool_calls=calls,
        model=data.get("model"),
        stop_reason=choice.get("finish_reason"),
        usage=dict(data.get("usage") or {}),
    )


class OpenAICompatProvider(_RemoteProvider):
    """The OpenAI /chat/completions shape, also used by Groq and similar hosts."""

    name = "openai"
    tokens_field = "max_tokens"

    def __init__(self, api_key: Optional[str] = None, *, base_url: Optional[str] = None, **kwargs: Any):
        super().__init__(api_key, **kwargs)
        self._base_url = base_url

    def _default_transport(self, payload: dict[str, Any]) -> Any:  # pragma: no cover
        import openai  # lazy

        client = openai.OpenAI(api_key=self._api_key, base_url=self._base_url)
        return client.chat.completions.create(**payload)

    def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Optional[list[dict[str, Any]]] = None,
        system: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 2000,
        temperature: float = 0.0,
    ) -> Completion:
        payload = openai_payload(messages, tools, system, model, self._scaled(max_tokens), temperature)
        data = self._request(payload, base_tokens=max_tokens)
        return parse_openai(data)


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ProviderChoice:
    """One configured provider: its adapter name and the env var that holds its key."""

    name: str
    key_env: str


def _terminal() -> bool:  # pragma: no cover - environment probe
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:  # noqa: BLE001
        return False


def _default_prompt(options: list[str], default: Optional[str]) -> str:  # pragma: no cover
    menu = ", ".join(options)
    answer = input(f"Several provider keys are set ({menu}). Use which? [{default or options[0]}] ")
    return (answer or default or options[0]).strip()


def select_provider(
    entries: Sequence[ProviderChoice],
    *,
    environ: Optional[dict[str, str]] = None,
    default: Optional[str] = None,
    interactive: Optional[bool] = None,
    prompt: Optional[Callable[[list[str], Optional[str]], str]] = None,
) -> str:
    """Pick a provider: one key use it; several and a terminal ask; several and
    no terminal use the configured default; none fails listing the options."""

    environ = os.environ if environ is None else environ
    available = [entry for entry in entries if environ.get(entry.key_env)]
    names = [entry.name for entry in available]

    if len(names) == 1:
        return names[0]
    if len(names) > 1:
        if interactive is None:
            interactive = _terminal()
        if interactive:
            pick = prompt or _default_prompt
            choice = pick(names, default)
            if choice not in names:
                raise ProviderSelectionError(f"unknown provider {choice!r}; choose one of {names}")
            return choice
        if default:
            return default
    wanted = [f"{entry.name} (set {entry.key_env})" for entry in entries]
    raise ProviderSelectionError(
        "no provider key is set; set one of: " + ", ".join(wanted) if wanted else "no providers configured"
    )
