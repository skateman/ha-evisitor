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

from importlib.metadata import PackageNotFoundError, version

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

# Derived from package metadata so we don't have a third place to bump
# alongside pyproject.toml + custom_components/evisitor/manifest.json.
# Falls back to a sentinel when the package isn't installed (raw source
# checkout without `pip install -e .`).
try:
    __version__ = version("pyevisitor")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
