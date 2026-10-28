from __future__ import annotations

import requests
import pytest

from news_super_analyser.gigachat_client import (
    AUTH_URL,
    CHAT_URL,
    DEFAULT_MODEL,
    GigaChatClient,
    GigaChatError,
    GigaChatModelRefusal,
    GigaChatSettings,
)


class Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


def test_auth_and_chat_contract_returns_raw_text():
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        if url == AUTH_URL:
            return Response({"access_token": "token"})
        return Response({"choices": [{"message": {"content": "  {\"objects\":[]}  "}}]})

    client = GigaChatClient(GigaChatSettings(auth_key="secret"), post=post)
    raw = client.complete(system_prompt="system", user_prompt="user")

    assert raw == '  {"objects":[]}  '
    assert calls[0][1]["data"] == {"scope": "GIGACHAT_API_PERS"}
    assert calls[0][1]["headers"]["Authorization"] == "Basic secret"
    assert calls[1][0] == CHAT_URL
    assert calls[1][1]["headers"]["Authorization"] == "Bearer token"
    assert calls[1][1]["headers"]["Accept"] == "application/json"
    assert calls[1][1]["json"]["temperature"] == 0
    assert calls[1][1]["json"]["model"] == "GigaChat-3-Ultra"


def test_http_error_is_sanitized_without_secret_or_body():
    client = GigaChatClient(
        GigaChatSettings(auth_key="top-secret"),
        post=lambda *_args, **_kwargs: Response({"secret": "body"}, 401),
    )
    with pytest.raises(GigaChatError) as caught:
        client.complete(system_prompt="system", user_prompt="user")
    assert str(caught.value) == (
        "GigaChat authentication HTTP failure: 401; "
        f"url={AUTH_URL}"
    )
    assert "top-secret" not in str(caught.value)
    assert "body" not in str(caught.value)


def test_network_failure_is_sanitized():
    def post(*_args, **_kwargs):
        raise requests.Timeout("sensitive request detail")

    client = GigaChatClient(GigaChatSettings(auth_key="secret"), post=post)
    with pytest.raises(GigaChatError, match="Timeout") as caught:
        client.complete(system_prompt="system", user_prompt="user")
    assert "sensitive" not in str(caught.value)
    assert caught.value.retryable is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500])
def test_chat_http_diagnostics_are_safe_and_include_actual_route_and_model(status):
    calls = 0

    def post(_url, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return Response({"access_token": "token"})
        return Response(
            {
                "status": status,
                "message": "model is unavailable Bearer server-secret",
                "request": "full news text must not be exposed",
            },
            status,
        )

    client = GigaChatClient(
        GigaChatSettings(auth_key="client-secret", model="GigaChat-3-Ultra"),
        post=post,
    )
    with pytest.raises(GigaChatError) as caught:
        client.complete(system_prompt="system", user_prompt="private news text")
    message = str(caught.value)
    assert f"HTTP failure: {status}" in message
    assert f"url={CHAT_URL}" in message
    assert "model=GigaChat-3-Ultra" in message
    assert "server-secret" not in message
    assert "client-secret" not in message
    assert "private news text" not in message
    assert "full news text" not in message


def test_environment_uses_single_default_model_source(monkeypatch):
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "key")
    monkeypatch.delenv("GIGACHAT_MODEL", raising=False)
    assert GigaChatSettings.from_env().model == DEFAULT_MODEL
    monkeypatch.setenv("GIGACHAT_MODEL", "account-specific-model")
    assert GigaChatSettings.from_env().model == "account-specific-model"


def test_structured_output_is_opt_in_and_sends_json_schema():
    chat_payloads = []

    def post(url, **kwargs):
        if url == AUTH_URL:
            return Response({"access_token": "token"})
        chat_payloads.append(kwargs["json"])
        return Response({"choices": [{"message": {"content": '{"objects":[]}'}, "finish_reason": "stop"}]})

    GigaChatClient(GigaChatSettings(auth_key="key"), post=post).complete(
        system_prompt="system", user_prompt="user"
    )
    GigaChatClient(
        GigaChatSettings(auth_key="key", structured_output=True), post=post
    ).complete(system_prompt="system", user_prompt="user")

    assert "response_format" not in chat_payloads[0]
    response_format = chat_payloads[1]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["strict"] is True
    assert response_format["schema"]["required"] == [
        "incident_type", "region", "locality", "summary", "objects"
    ]


def test_blacklist_finish_reason_is_a_model_refusal_and_preserves_raw():
    calls = 0

    def post(_url, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return Response({"access_token": "token"})
        return Response({
            "choices": [{
                "message": {"content": "provider refusal text"},
                "finish_reason": "blacklist",
            }]
        })

    client = GigaChatClient(GigaChatSettings(auth_key="key"), post=post)
    with pytest.raises(GigaChatModelRefusal) as caught:
        client.complete(system_prompt="system", user_prompt="user")
    assert caught.value.raw_response == "provider refusal text"
    assert "blacklist" in str(caught.value)


def test_nonterminal_finish_reason_is_not_accepted_as_complete():
    calls = 0

    def post(_url, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return Response({"access_token": "token"})
        return Response({
            "choices": [{
                "message": {"content": "partial response"},
                "finish_reason": "length",
            }]
        })

    client = GigaChatClient(GigaChatSettings(auth_key="key"), post=post)
    with pytest.raises(GigaChatError, match="finish_reason=length") as caught:
        client.complete(system_prompt="system", user_prompt="user")
    assert caught.value.raw_response == "partial response"


def test_structured_output_environment_flag_is_validated(monkeypatch):
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "key")
    monkeypatch.setenv("GIGACHAT_STRUCTURED_OUTPUT", "true")
    assert GigaChatSettings.from_env().structured_output is True
    monkeypatch.setenv("GIGACHAT_STRUCTURED_OUTPUT", "maybe")
    with pytest.raises(GigaChatError, match="must be true or false"):
        GigaChatSettings.from_env()


@pytest.mark.parametrize("status, retryable", [(400, False), (429, True), (500, True)])
def test_only_transient_http_failures_are_retryable(status, retryable):
    calls = 0

    def post(_url, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return Response({"access_token": "token"})
        return Response({"message": "temporary"}, status)

    client = GigaChatClient(GigaChatSettings(auth_key="key"), post=post)
    with pytest.raises(GigaChatError) as caught:
        client.complete(system_prompt="system", user_prompt="user")
    assert caught.value.retryable is retryable
