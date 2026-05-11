"""Exception hierarchy for the e-Visitor client."""

from __future__ import annotations

from typing import Any


class EVisitorError(Exception):
    """Base class for all e-Visitor client errors."""


class EVisitorAuthError(EVisitorError):
    """Raised when login/logout fails or the session is rejected."""


class EVisitorHTTPError(EVisitorError):
    """Raised when the API returns an unexpected HTTP status."""

    def __init__(
        self,
        status: int,
        message: str,
        *,
        url: str | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.url = url
        self.body = body


class EVisitorValidationError(EVisitorError):
    """Raised when the API returns a UserMessage/SystemMessage error body."""

    def __init__(
        self,
        user_message: str | None,
        system_message: str | None,
        *,
        status: int | None = None,
        url: str | None = None,
        body: Any = None,
    ) -> None:
        msg = user_message or system_message or "e-Visitor returned an error"
        super().__init__(msg)
        self.user_message = user_message
        self.system_message = system_message
        self.status = status
        self.url = url
        self.body = body
