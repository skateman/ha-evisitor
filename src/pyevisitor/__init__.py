"""Async Python client for the Croatian e-Visitor Web API."""

from __future__ import annotations

from .client import EVisitorClient
from .config import EVisitorConfig, Environment
from .exceptions import (
    EVisitorAuthError,
    EVisitorError,
    EVisitorHTTPError,
    EVisitorValidationError,
)
from .guests import Guest
from .models import (
    CancelCheckInRequest,
    CancelCheckOutRequest,
    CheckInRequest,
    CheckOutRequest,
    Filter,
    FilterOp,
)

__all__ = [
    "CancelCheckInRequest",
    "CancelCheckOutRequest",
    "CheckInRequest",
    "CheckOutRequest",
    "EVisitorAuthError",
    "EVisitorClient",
    "EVisitorConfig",
    "EVisitorError",
    "EVisitorHTTPError",
    "EVisitorValidationError",
    "Environment",
    "Filter",
    "FilterOp",
    "Guest",
]

__version__ = "0.1.0"
