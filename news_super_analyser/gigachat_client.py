"""Small GigaChat HTTP client used only by the manual AI analysis stage."""

from __future__ import annotations

import os
import re
import ssl
import uuid
from dataclasses import dataclass
from typing import Any, Callable

import requests
import truststore
from requests.adapters import HTTPAdapter

from .ai_prompt import ANALYSIS_JSON_SCHEMA
from .config import load_project_environment


AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
CHAT_URL = "https://api.giga.chat/v1/chat/completions"
DEFAULT_MODEL = "GigaChat-3-Ultra"


class GigaChatError(RuntimeError):
    """A sanitized error that never contains credentials or response bodies."""

    def __init__(
        self,
        message: str,
        *,
        raw_response: str = "",
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.raw_response = raw_response
        self.retryable = retryable
        self.retry_after = retry_after


class GigaChatModelRefusal(GigaChatError):
    """The provider marked the response as a thematic restriction."""


@dataclass(frozen=True)
class GigaChatSettings:
    auth_key: str
    scope: str = "GIGACHAT_API_PERS"
    model: str = DEFAULT_MODEL
    timeout_seconds: float = 90.0
    ca_bundle: str | None = None
    structured_output: bool = False

    @classmethod
    def from_env(cls) -> "GigaChatSettings":
        load_project_environment()
        auth_key = os.getenv("GIGACHAT_AUTH_KEY", "").strip()
        if not auth_key:
            raise GigaChatError("GIGACHAT_AUTH_KEY is not configured")
        try:
            timeout = float(os.getenv("GIGACHAT_TIMEOUT_SECONDS", "90"))
        except ValueError as exc:
            raise GigaChatError("GIGACHAT_TIMEOUT_SECONDS must be numeric") from exc
        if timeout <= 0:
            raise GigaChatError("GIGACHAT_TIMEOUT_SECONDS must be positive")
        return cls(
            auth_key=auth_key,
            scope=os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS").strip()
            or "GIGACHAT_API_PERS",
            model=os.getenv("GIGACHAT_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            timeout_seconds=timeout,
            ca_bundle=os.getenv("GIGACHAT_CA_BUNDLE", "").strip() or None,
            structured_output=_env_bool("GIGACHAT_STRUCTURED_OUTPUT", False),
        )


class GigaChatClient:
    """Authenticate and perform one chat-completions request without retries."""

    def __init__(
        self,
        settings: GigaChatSettings,
        *,
        post: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self._session: requests.Session | None = None
        if post is None:
            self._session = _system_trust_session()
            self._post = self._session.post
        else:
            self._post = post

    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        token = self._access_token()
        payload = {
            "model": self.settings.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if self.settings.structured_output:
            payload["response_format"] = {
                "type": "json_schema",
                "schema": ANALYSIS_JSON_SCHEMA,
                "strict": True,
            }
        try:
            response = self._post(
                CHAT_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=payload,
                timeout=self.settings.timeout_seconds,
                verify=self.settings.ca_bundle or True,
            )
        except requests.RequestException as exc:
            raise GigaChatError(
                f"GigaChat chat transport failure: {type(exc).__name__}",
                retryable=True,
            ) from exc
        self._require_success(
            response,
            "chat",
            url=CHAT_URL,
            model=self.settings.model,
        )
        try:
            choice = response.json()["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise GigaChatError("GigaChat chat response has an unexpected structure") from exc
        raw_content = content if isinstance(content, str) else ""
        if finish_reason == "blacklist":
            raise GigaChatModelRefusal(
                "GigaChat thematic restriction: finish_reason=blacklist",
                raw_response=raw_content,
            )
        if finish_reason not in {None, "stop"}:
            raise GigaChatError(
                f"GigaChat incomplete response: finish_reason={finish_reason}",
                raw_response=raw_content,
            )
        if not isinstance(content, str) or not content.strip():
            raise GigaChatError("GigaChat returned an empty response")
        return content

    def _access_token(self) -> str:
        try:
            response = self._post(
                AUTH_URL,
                headers={
                    "Authorization": f"Basic {self.settings.auth_key}",
                    "RqUID": str(uuid.uuid4()),
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data={"scope": self.settings.scope},
                timeout=min(self.settings.timeout_seconds, 30.0),
                verify=self.settings.ca_bundle or True,
            )
        except requests.RequestException as exc:
            raise GigaChatError(
                f"GigaChat authentication transport failure: {type(exc).__name__}",
                retryable=True,
            ) from exc
        self._require_success(response, "authentication", url=AUTH_URL)
        try:
            token = response.json()["access_token"]
        except (KeyError, TypeError, ValueError) as exc:
            raise GigaChatError(
                "GigaChat authentication response has an unexpected structure"
            ) from exc
        if not isinstance(token, str) or not token:
            raise GigaChatError("GigaChat authentication returned an empty token")
        return token

    @staticmethod
    def _require_success(
        response: Any, stage: str, *, url: str, model: str | None = None
    ) -> None:
        status = int(getattr(response, "status_code", 0))
        if status < 200 or status >= 300:
            details = [
                f"GigaChat {stage} HTTP failure: {status}",
                f"url={url}",
            ]
            if model:
                details.append(f"model={model}")
            diagnostic = _safe_server_diagnostic(response)
            if diagnostic:
                details.append(f"server={diagnostic}")
            raise GigaChatError(
                "; ".join(details),
                retryable=status == 429 or status >= 500,
                retry_after=_response_retry_after(response),
            )


class _TruststoreAdapter(HTTPAdapter):
    """Use Windows/system certificates only for this client's session."""

    def __init__(self) -> None:
        self._ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        super().__init__()

    def init_poolmanager(
        self, connections: int, maxsize: int, block: bool = False, **kwargs: Any
    ) -> None:
        kwargs["ssl_context"] = self._ssl_context
        super().init_poolmanager(connections, maxsize, block=block, **kwargs)

    def proxy_manager_for(self, proxy: str, **proxy_kwargs: Any) -> Any:
        proxy_kwargs["ssl_context"] = self._ssl_context
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def _system_trust_session() -> requests.Session:
    session = requests.Session()
    session.mount("https://", _TruststoreAdapter())
    return session


def _safe_server_diagnostic(response: Any) -> str:
    """Return only short provider error fields, never the whole response body."""
    try:
        payload = response.json()
    except (TypeError, ValueError, AttributeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    parts: list[str] = []
    for key in ("code", "status", "message", "error", "detail"):
        value = payload.get(key)
        if not isinstance(value, (str, int, float)):
            continue
        cleaned = " ".join(str(value).split())
        cleaned = re.sub(
            r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+",
            "[REDACTED]",
            cleaned,
        )
        if len(cleaned) > 240:
            cleaned = cleaned[:237] + "..."
        if cleaned:
            parts.append(f"{key}={cleaned}")
    return ", ".join(parts)[:500]


def _response_retry_after(response: Any) -> float | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        value = headers.get("Retry-After") or headers.get("retry-after")
        return max(0.0, float(value))
    except (AttributeError, TypeError, ValueError):
        return None


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise GigaChatError(f"{name} must be true or false")
