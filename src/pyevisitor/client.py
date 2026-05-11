"""HTTP client for the e-Visitor Web API."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin

import aiohttp

from .config import EVisitorConfig
from .encoding import Filter, encode_filters
from .exceptions import (
    EVisitorAuthError,
    EVisitorError,
    EVisitorHTTPError,
    EVisitorValidationError,
)

_LOGGER = logging.getLogger(__name__)


def _build_relaxed_ssl_context() -> ssl.SSLContext:
    """SSL context that accepts eVisitor's legacy DH params.

    eVisitor's IIS server presents a 1024-bit DH group which OpenSSL 3 rejects
    at SECLEVEL=2 (the modern default) with ``[SSL: DH_KEY_TOO_SMALL]``. We
    lower SECLEVEL to 1 so the handshake completes. TLS itself, hostname
    verification, and certificate trust are all still enforced.

    NOTE: This calls ``ssl.create_default_context()``, which reads CA
    bundles from disk and is therefore blocking. Callers running inside
    an event loop (Home Assistant, anything async) should call this in
    an executor and pass the result to :class:`EVisitorClient` via the
    ``ssl_context`` parameter.
    """
    ctx = ssl.create_default_context()
    try:
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    except ssl.SSLError:  # pragma: no cover -- LibreSSL etc.
        _LOGGER.debug("Could not lower OpenSSL SECLEVEL to 1", exc_info=True)
    return ctx


class EVisitorClient:
    """Async client wrapping the e-Visitor Rhetos REST + Auth services.

    Two usage patterns:

    1. Async context manager (recommended)::

           async with EVisitorClient(config) as client:
               await client.list_facilities()

    2. Explicit lifecycle::

           client = EVisitorClient(config)
           await client.login()
           ...
           await client.close()
    """

    def __init__(
        self,
        config: EVisitorConfig,
        *,
        session: aiohttp.ClientSession | None = None,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._config = config
        self._external_session = session is not None
        self._session: aiohttp.ClientSession | None = session
        self._authenticated = False
        self._lock = asyncio.Lock()
        # Pre-built TLS context: optional override for callers that need to
        # construct it off the event loop (e.g. Home Assistant). If None,
        # the session will lazily build one on first use.
        self._ssl_context_override = ssl_context

        # Sub-namespaces are wired in client.py to avoid import cycles.
        from .actions import Actions
        from .browses import Browses
        from .guests import Guests
        from .lookups import Lookups

        self.actions = Actions(self)
        self.browses = Browses(self)
        self.guests = Guests(self)
        self.lookups = Lookups(self)

    @property
    def config(self) -> EVisitorConfig:
        return self._config

    @property
    def authenticated(self) -> bool:
        return self._authenticated

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector: aiohttp.BaseConnector | None = None
            if self._config.relax_tls:
                ctx = self._ssl_context_override or _build_relaxed_ssl_context()
                connector = aiohttp.TCPConnector(ssl=ctx)
            self._session = aiohttp.ClientSession(
                cookie_jar=aiohttp.CookieJar(unsafe=False),
                timeout=aiohttp.ClientTimeout(total=self._config.request_timeout),
                connector=connector,
            )
        return self._session

    # -- lifecycle ------------------------------------------------------

    async def __aenter__(self) -> "EVisitorClient":
        await self.login()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if self._authenticated:
                try:
                    await self.logout()
                except (EVisitorError, aiohttp.ClientError):
                    # Best-effort: if logout fails on the way out, the session
                    # is being torn down anyway. Swallow to avoid masking the
                    # original exception (if any).
                    _LOGGER.debug("logout during __aexit__ failed", exc_info=True)
        finally:
            await self.close()

    async def close(self) -> None:
        if self._session is not None and not self._external_session:
            await self._session.close()
        self._session = None
        self._authenticated = False

    # -- auth -----------------------------------------------------------

    async def login(self) -> None:
        """POST credentials to the AspNetFormsAuth Login endpoint."""
        async with self._lock:
            if self._authenticated:
                return

            payload: dict[str, Any] = {
                "userName": self._config.username,
                "password": self._config.password,
                "persistCookie": self._config.persist_cookie,
            }
            if self._config.api_key:
                payload["apikey"] = self._config.api_key

            url = urljoin(self._config.auth_root, "Login")
            async with self.session.post(url, json=payload) as resp:
                body_text = await resp.text()
                if resp.status >= 400:
                    raise EVisitorAuthError(
                        f"Login failed ({resp.status}): {body_text}"
                    )
                try:
                    body = json.loads(body_text) if body_text else None
                except json.JSONDecodeError:
                    raise EVisitorAuthError(
                        f"Login returned non-JSON response: {body_text!r}"
                    ) from None

                if isinstance(body, dict) and (
                    "UserMessage" in body or "SystemMessage" in body
                ):
                    raise EVisitorAuthError(
                        body.get("UserMessage")
                        or body.get("SystemMessage")
                        or "Login rejected"
                    )
                if body is not True:
                    raise EVisitorAuthError(
                        f"Login returned {body!r} (expected true)"
                    )

            self._authenticated = True

    async def logout(self) -> None:
        if self._session is None:
            self._authenticated = False
            return
        url = urljoin(self._config.auth_root, "Logout")
        try:
            async with self._session.post(url) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise EVisitorAuthError(
                        f"Logout failed ({resp.status}): {text}"
                    )
        finally:
            self._authenticated = False

    # -- low level HTTP --------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        filters: Iterable[Filter | dict[str, Any]] | None = None,
        json_body: Any = None,
        require_auth: bool = True,
    ) -> Any:
        """Issue a request and decode the JSON body, raising on errors.

        Resolves ``path`` against :attr:`EVisitorConfig.rest_root`.
        ``filters`` is JSON-encoded into the ``filters`` query parameter
        per the Rhetos browse convention.
        """
        if require_auth and not self._authenticated:
            await self.login()

        url = urljoin(self._config.rest_root, path.lstrip("/"))

        query: dict[str, Any] = {}
        if params:
            query.update({k: v for k, v in params.items() if v is not None})
        encoded_filters = encode_filters(filters)
        if encoded_filters is not None:
            query["filters"] = encoded_filters

        async with self.session.request(
            method,
            url,
            params=query or None,
            json=json_body,
        ) as resp:
            text = await resp.text()
            return self._decode_response(resp.status, str(resp.url), text)

    async def get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        filters: Iterable[Filter | dict[str, Any]] | None = None,
    ) -> Any:
        return await self.request("GET", path, params=params, filters=filters)

    async def post(self, path: str, *, json_body: Any = None) -> Any:
        return await self.request("POST", path, json_body=json_body)

    async def put(self, path: str, *, json_body: Any = None) -> Any:
        return await self.request("PUT", path, json_body=json_body)

    async def delete(self, path: str) -> Any:
        return await self.request("DELETE", path)

    # -- response decoding ----------------------------------------------

    @staticmethod
    def _decode_response(status: int, url: str, text: str) -> Any:
        body: Any = None
        if text:
            try:
                body = json.loads(text)
            except json.JSONDecodeError:
                body = text

        if isinstance(body, dict) and (
            "UserMessage" in body or "SystemMessage" in body
        ) and (status >= 400 or body.get("UserMessage") or body.get("SystemMessage")):
            raise EVisitorValidationError(
                body.get("UserMessage"),
                body.get("SystemMessage"),
                status=status,
                url=url,
                body=body,
            )

        if status >= 400:
            raise EVisitorHTTPError(
                status,
                f"HTTP {status} from {url}: {text!r}",
                url=url,
                body=body,
            )

        return body


__all__ = [
    "EVisitorAuthError",
    "EVisitorClient",
    "EVisitorError",
    "EVisitorHTTPError",
    "EVisitorValidationError",
]
