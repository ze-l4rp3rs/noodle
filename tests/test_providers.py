"""Provider interface tests (05 section 6, 09 testing matrix).

No real model: adapters are driven through an injected transport (the "fake
provider services" from 09). Conversation conversion, retries, output-cap
reduction, role alternation and tool-choice mapping are all checked here.
"""

from __future__ import annotations

import json
import sys

import pytest

from noodle import providers as P


def _msg(role, text="", tool_calls=(), tool_call_id=None, name=None):
    return P.Message(role=role, text=text, tool_calls=list(tool_calls),
                     tool_call_id=tool_call_id, name=name)


def _spec(name="get_freshness", parameters=None, description="desc"):
    return {"name": name, "description": description,
            "parameters": parameters or {"type": "object", "properties": {}}}


# --------------------------------------------------------------------------- #
# Neutral types
# --------------------------------------------------------------------------- #


def test_completion_defaults_are_empty():
    done = P.Completion()
    assert done.text == "" and done.tool_calls == [] and done.reasoning == ""


def test_toolcall_carries_id_name_args_and_optional_signature():
    call = P.ToolCall(id="c1", name="find_owner", arguments={"ref": "project:x"})
    assert call.signature is None
    signed = P.ToolCall(id="c2", name="t", arguments={}, signature="sig")
    assert signed.signature == "sig"


# --------------------------------------------------------------------------- #
# Scripted provider (the fake model)
# --------------------------------------------------------------------------- #


def test_scripted_provider_returns_queued_replies_and_records_requests():
    first = P.Completion(text="one")
    second = P.Completion(tool_calls=[P.ToolCall("c1", "current_time", {})])
    provider = P.ScriptedProvider([first, second])

    got1 = provider.complete([_msg("user", "hi")], system="sys", model="m", max_tokens=10)
    assert got1 is first
    got2 = provider.complete([_msg("user", "hi")], tools=[_spec()], model="m")
    assert got2 is second

    assert len(provider.requests) == 2
    assert provider.requests[0]["system"] == "sys"
    assert provider.requests[0]["max_tokens"] == 10
    assert provider.requests[1]["tools"][0]["name"] == "get_freshness"


def test_scripted_provider_runs_out_loudly():
    provider = P.ScriptedProvider([])
    with pytest.raises(P.ProviderError):
        provider.complete([_msg("user", "hi")])


def test_scripted_provider_accepts_a_callable_reply():
    provider = P.ScriptedProvider([lambda messages, tools: P.Completion(text=f"{len(messages)}")])
    assert provider.complete([_msg("user", "x"), _msg("user", "y")]).text == "2"


def test_scripted_provider_makes_exactly_one_reply_per_call():
    provider = P.ScriptedProvider([P.Completion(text="a"), P.Completion(text="b")])
    provider.complete([_msg("user", "1")])
    assert len(provider.requests) == 1
    provider.complete([_msg("user", "2")])
    assert len(provider.requests) == 2


# --------------------------------------------------------------------------- #
# Anthropic conversion
# --------------------------------------------------------------------------- #


def test_anthropic_keeps_the_system_prompt_in_its_own_field():
    payload = P.anthropic_payload([_msg("user", "hello")], None, "RULES", "m", 100, 0.0)
    assert payload["system"] == "RULES"
    assert payload["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]


def test_anthropic_renders_tool_requests_and_results_as_content_blocks():
    call = P.ToolCall("tu_1", "find_owner", {"ref": "project:x"})
    payload = P.anthropic_payload(
        [
            _msg("user", "who owns it"),
            _msg("assistant", tool_calls=[call]),
            _msg("tool", text='{"ok":true}', tool_call_id="tu_1", name="find_owner"),
        ],
        [_spec("find_owner", {"type": "object", "properties": {"ref": {"type": "string"}}})],
        None, "m", 100, 0.0,
    )
    assistant = payload["messages"][1]
    assert assistant["role"] == "assistant"
    assert assistant["content"] == [
        {"type": "tool_use", "id": "tu_1", "name": "find_owner", "input": {"ref": "project:x"}}
    ]
    result = payload["messages"][2]
    assert result["role"] == "user"
    assert result["content"][0]["type"] == "tool_result"
    assert result["content"][0]["tool_use_id"] == "tu_1"


def test_anthropic_merges_consecutive_same_role_turns():
    payload = P.anthropic_payload(
        [_msg("user", "one"), _msg("user", "two"), _msg("assistant", "a"), _msg("assistant", "b")],
        None, None, "m", 100, 0.0,
    )
    assert [m["role"] for m in payload["messages"]] == ["user", "assistant"]
    assert payload["messages"][0]["content"] == [
        {"type": "text", "text": "one"}, {"type": "text", "text": "two"}
    ]


def test_anthropic_tools_use_input_schema_and_auto_choice():
    payload = P.anthropic_payload([_msg("user", "x")], [_spec("t", {"type": "object"})], None, "m", 5, 0.0)
    assert payload["tools"] == [{"name": "t", "description": "desc", "input_schema": {"type": "object"}}]
    assert payload["tool_choice"] == {"type": "auto"}


def test_anthropic_omits_tools_when_forced_to_answer():
    payload = P.anthropic_payload([_msg("user", "x")], None, None, "m", 5, 0.0)
    assert "tools" not in payload and "tool_choice" not in payload


def test_parse_anthropic_splits_text_tool_use_and_reasoning():
    data = {
        "model": "m1",
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 3},
        "content": [
            {"type": "thinking", "thinking": "secret chain"},
            {"type": "text", "text": "Let me check."},
            {"type": "tool_use", "id": "tu_9", "name": "get_freshness", "input": {}},
        ],
    }
    done = P.parse_anthropic(data)
    assert done.text == "Let me check."
    assert done.reasoning == "secret chain"
    assert done.tool_calls == [P.ToolCall("tu_9", "get_freshness", {})]
    assert done.model == "m1" and done.stop_reason == "tool_use" and done.usage["input_tokens"] == 3


# --------------------------------------------------------------------------- #
# Google conversion
# --------------------------------------------------------------------------- #


def test_google_uses_model_as_the_assistant_role():
    payload = P.google_payload([_msg("user", "hi"), _msg("assistant", "yo")], None, None, "m", 100, 0.0)
    assert [c["role"] for c in payload["contents"]] == ["user", "model"]


def test_google_puts_the_system_prompt_in_system_instruction():
    payload = P.google_payload([_msg("user", "hi")], None, "RULES", "m", 100, 0.0)
    assert payload["systemInstruction"] == {"parts": [{"text": "RULES"}]}


def test_google_omits_the_schema_for_a_parameterless_tool():
    payload = P.google_payload([_msg("user", "x")], [_spec("current_time")], None, "m", 5, 0.0)
    decl = payload["tools"][0]["functionDeclarations"][0]
    assert decl["name"] == "current_time" and "parameters" not in decl


def test_google_keeps_the_schema_for_a_tool_with_parameters():
    payload = P.google_payload(
        [_msg("user", "x")], [_spec("find_owner", {"type": "object", "properties": {"ref": {"type": "string"}}})],
        None, "m", 5, 0.0,
    )
    decl = payload["tools"][0]["functionDeclarations"][0]
    assert decl["parameters"]["properties"]["ref"] == {"type": "string"}


def test_google_replays_a_tool_request_with_its_signature():
    call = P.ToolCall("fc_1", "get_freshness", {}, signature="sig-xyz")
    payload = P.google_payload([_msg("user", "x"), _msg("assistant", tool_calls=[call])], None, None, "m", 5, 0.0)
    part = payload["contents"][1]["parts"][0]
    assert part["functionCall"]["name"] == "get_freshness"
    assert part["thoughtSignature"] == "sig-xyz"


def test_google_renders_tool_results_as_function_response_parts():
    payload = P.google_payload(
        [_msg("tool", text='{"rows": 1}', tool_call_id="fc_1", name="get_freshness")],
        None, None, "m", 5, 0.0,
    )
    part = payload["contents"][0]["parts"][0]
    assert part["functionResponse"]["name"] == "get_freshness"
    assert part["functionResponse"]["response"] == {"rows": 1}


def test_google_tool_config_is_auto_then_none():
    with_tools = P.google_payload([_msg("user", "x")], [_spec()], None, "m", 5, 0.0)
    assert with_tools["toolConfig"] == {"functionCallingConfig": {"mode": "AUTO"}}
    forced = P.google_payload([_msg("user", "x")], None, None, "m", 5, 0.0)
    assert "toolConfig" not in forced


def test_parse_google_reads_text_and_function_call():
    data = {
        "candidates": [{
            "finishReason": "STOP",
            "content": {"parts": [
                {"text": "Checking now."},
                {"functionCall": {"id": "fc_2", "name": "get_freshness", "args": {}}, "thoughtSignature": "s"},
            ]},
        }],
        "usageMetadata": {"promptTokenCount": 7},
    }
    done = P.parse_google(data)
    assert done.text == "Checking now."
    assert done.tool_calls == [P.ToolCall("fc_2", "get_freshness", {}, signature="s")]
    assert done.usage["promptTokenCount"] == 7


# --------------------------------------------------------------------------- #
# OpenAI-compatible conversion
# --------------------------------------------------------------------------- #


def test_openai_puts_the_system_prompt_as_the_first_message():
    payload = P.openai_payload([_msg("user", "hi")], None, "RULES", "m", 100, 0.0)
    assert payload["messages"][0] == {"role": "system", "content": "RULES"}


def test_openai_renders_tool_calls_and_tool_messages():
    call = P.ToolCall("call_1", "find_owner", {"ref": "project:x"})
    payload = P.openai_payload(
        [_msg("assistant", tool_calls=[call]),
         _msg("tool", text='{"ok":1}', tool_call_id="call_1", name="find_owner")],
        [_spec("find_owner")], None, "m", 5, 0.0,
    )
    assistant = payload["messages"][0]
    assert assistant["tool_calls"][0]["function"]["name"] == "find_owner"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"ref": "project:x"}
    assert payload["messages"][1] == {"role": "tool", "tool_call_id": "call_1", "content": '{"ok":1}'}


def test_parse_openai_reads_content_and_tool_calls():
    data = {"choices": [{"finish_reason": "tool_calls", "message": {
        "content": "One moment.",
        "tool_calls": [{"id": "call_9", "function": {"name": "get_freshness", "arguments": "{}"}}],
    }}], "usage": {"total_tokens": 5}}
    done = P.parse_openai(data)
    assert done.text == "One moment."
    assert done.tool_calls == [P.ToolCall("call_9", "get_freshness", {})]


def test_parse_openai_survives_malformed_arguments():
    data = {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": "c", "function": {"name": "t", "arguments": "{not json"}}]}}]}
    done = P.parse_openai(data)
    assert done.tool_calls[0].arguments == {}  # bad args surface as a validation error later


# --------------------------------------------------------------------------- #
# Retries and output-cap reduction
# --------------------------------------------------------------------------- #


class _Transport:
    """A fake provider service: raises the queued effects, then returns data."""

    def __init__(self, effects, result=None):
        self.effects = list(effects)
        self.result = result or {"content": [{"type": "text", "text": "ok"}]}
        self.calls = 0
        self.payloads = []

    def __call__(self, payload):
        self.calls += 1
        self.payloads.append(payload)
        if self.effects:
            effect = self.effects.pop(0)
            if isinstance(effect, Exception):
                raise effect
        return self.result


def _anthropic(effects, sleeps=None):
    slept = sleeps if sleeps is not None else []
    transport = _Transport(effects)
    provider = P.AnthropicProvider("key", transport=transport, sleep=slept.append)
    return provider, transport, slept


def test_short_rate_limit_is_retried():
    provider, transport, slept = _anthropic([P.RateLimited(0.5)])
    done = provider.complete([_msg("user", "x")], model="m")
    assert done.text == "ok"
    assert transport.calls == 2 and slept == [0.5]


def test_retries_stop_after_the_limit():
    provider, transport, slept = _anthropic([P.RateLimited(1)] * 5)
    with pytest.raises(P.RateLimited):
        provider.complete([_msg("user", "x")], model="m")
    assert transport.calls == provider.max_retries + 1


def test_a_provider_sdk_rate_limit_error_is_translated_and_retried():
    provider, transport, slept = _anthropic([Exception("HTTP 429: too many requests, retry later")])
    done = provider.complete([_msg("user", "x")], model="m")
    assert done.text == "ok"
    assert transport.calls == 2 and slept  # translated to a rate limit and retried


def test_output_cap_is_lowered_and_remembered():
    provider, transport, _ = _anthropic([P.OutputCapExceeded(), P.OutputCapExceeded()])
    done = provider.complete([_msg("user", "x")], model="m", max_tokens=1000)
    assert done.text == "ok"
    assert provider.cap_scale == 0.25
    assert [p["max_tokens"] for p in transport.payloads] == [1000, 500, 250]
    # the reduced cap is remembered for the next call, not just the retry
    provider.complete([_msg("user", "x")], model="m", max_tokens=1000)
    assert transport.payloads[-1]["max_tokens"] == 250


# --------------------------------------------------------------------------- #
# Provider selection
# --------------------------------------------------------------------------- #


def _entry(name, key_env):
    return P.ProviderChoice(name=name, key_env=key_env)


def test_selection_uses_the_single_available_key():
    chosen = P.select_provider(
        [_entry("anthropic", "A_KEY"), _entry("google", "G_KEY")],
        environ={"G_KEY": "secret"}, default=None, interactive=False,
    )
    assert chosen == "google"


def test_selection_asks_when_several_keys_and_a_terminal():
    asked: list[tuple] = []

    def prompt(options, default):
        asked.append((options, default))
        return "anthropic"

    chosen = P.select_provider(
        [_entry("anthropic", "A_KEY"), _entry("google", "G_KEY")],
        environ={"A_KEY": "1", "G_KEY": "2"}, default="google", interactive=True, prompt=prompt,
    )
    assert chosen == "anthropic"
    assert asked and set(asked[0][0]) == {"anthropic", "google"}


def test_selection_uses_the_default_without_a_terminal():
    chosen = P.select_provider(
        [_entry("anthropic", "A_KEY"), _entry("google", "G_KEY")],
        environ={"A_KEY": "1", "G_KEY": "2"}, default="google", interactive=False,
    )
    assert chosen == "google"


def test_selection_fails_listing_the_options_when_no_key():
    with pytest.raises(P.ProviderSelectionError) as exc:
        P.select_provider(
            [_entry("anthropic", "A_KEY"), _entry("google", "G_KEY")],
            environ={}, default=None, interactive=False,
        )
    assert "A_KEY" in str(exc.value) and "G_KEY" in str(exc.value)


def test_selection_fails_when_several_keys_but_no_terminal_and_no_default():
    with pytest.raises(P.ProviderSelectionError):
        P.select_provider(
            [_entry("anthropic", "A_KEY"), _entry("google", "G_KEY")],
            environ={"A_KEY": "1", "G_KEY": "2"}, default=None, interactive=False,
        )


# --------------------------------------------------------------------------- #
# Lazy imports
# --------------------------------------------------------------------------- #


def test_unused_provider_libraries_are_never_imported():
    for module in ("anthropic", "google", "openai"):
        assert module not in sys.modules
