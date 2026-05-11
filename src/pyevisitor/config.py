"""Configuration for the e-Visitor client (production vs test environment)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class Environment(str, Enum):
    """Which deployment of e-Visitor to talk to."""

    PRODUCTION = "production"
    TEST = "test"

    @classmethod
    def parse(cls, value: str | "Environment") -> "Environment":
        if isinstance(value, cls):
            return value
        # Strip inline comments and whitespace so values copy-pasted from
        # an example .env without dotenv post-processing still work.
        normalized = str(value).split("#", 1)[0].strip().lower()
        if normalized in {"prod", "production", "live"}:
            return cls.PRODUCTION
        if normalized in {"test", "testing", "staging", "sandbox"}:
            return cls.TEST
        raise ValueError(f"Unknown e-Visitor environment: {value!r}")


_ROOTS = {
    Environment.PRODUCTION: "https://www.evisitor.hr/eVisitorRhetos_API",
    Environment.TEST: "https://www.evisitor.hr/testApi",
}


@dataclass(frozen=True)
class EVisitorConfig:
    """Connection configuration for the e-Visitor Web API.

    `api_key` is required by the test environment per the official docs and
    is ignored (but allowed) on production.
    """

    username: str
    password: str
    environment: Environment = Environment.PRODUCTION
    api_key: str | None = None
    persist_cookie: bool = True
    request_timeout: float = 30.0
    # eVisitor's IIS server still negotiates a 1024-bit Diffie-Hellman group
    # which modern OpenSSL builds reject by default with
    # ``[SSL: DH_KEY_TOO_SMALL]``. Setting this to True lets the client build
    # an SSLContext that accepts the weaker params (SECLEVEL=1). It is
    # a tradeoff but unavoidable while talking to eVisitor.
    relax_tls: bool = True

    def __post_init__(self) -> None:
        if not self.username or not self.password:
            raise ValueError("username and password are required")
        if self.environment is Environment.TEST and not self.api_key:
            # Per docs, the test env *should* require an apikey, but in
            # practice some accounts work without one. Warn instead of
            # blocking so callers can try.
            import logging

            logging.getLogger(__name__).warning(
                "EVisitorConfig: api_key is empty for the test environment; "
                "login may fail with 'Application is not registered ...'"
            )

    @property
    def api_root(self) -> str:
        """Root URL without trailing slash, e.g. .../eVisitorRhetos_API ."""
        return _ROOTS[self.environment]

    @property
    def rest_root(self) -> str:
        """Base URL for REST resources, ending with `/Rest/`."""
        return f"{self.api_root}/Rest/"

    @property
    def auth_root(self) -> str:
        """Base URL for the AspNetFormsAuth authentication service."""
        return f"{self.api_root}/Resources/AspNetFormsAuth/Authentication/"

    @classmethod
    def from_env(
        cls,
        prefix: str = "EVISITOR_",
        *,
        env: dict[str, str] | None = None,
    ) -> "EVisitorConfig":
        """Build a config from environment variables.

        Recognised variables (with default prefix):
        - ``EVISITOR_USERNAME``
        - ``EVISITOR_PASSWORD``
        - ``EVISITOR_ENVIRONMENT`` (``test`` or ``production``)
        - ``EVISITOR_API_KEY`` (required for test)
        """
        source = env if env is not None else os.environ
        username = source.get(f"{prefix}USERNAME", "")
        password = source.get(f"{prefix}PASSWORD", "")
        environment = Environment.parse(
            source.get(f"{prefix}ENVIRONMENT", "production"),
        )
        api_key = source.get(f"{prefix}API_KEY") or None
        return cls(
            username=username,
            password=password,
            environment=environment,
            api_key=api_key,
        )
