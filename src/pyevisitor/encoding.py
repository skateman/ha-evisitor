"""Encoding helpers for the e-Visitor API.

Two formats need special handling:

1. **.NET JSON Date** -- ``"/Date(<ms-since-epoch>+HHMM)/"`` -- used in
   most browse filter values, in some response fields, and in DateTime
   attributes of entities.
2. **Browse filters** -- a JSON-encoded list of
   ``{"Property", "Operation", "Value"}`` dicts passed as the ``filters``
   query string parameter.

CheckIn/CheckOut date and time fields use plain ``YYYYMMDD`` and ``HH:MM``
strings; those conversions live in :mod:`pyevisitor.models`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Iterable

_DOTNET_DATE_RE = re.compile(
    r"^/Date\((?P<ms>-?\d+)(?P<tz>[+-]\d{4})?\)/$",
)


def to_dotnet_date(value: datetime | date) -> str:
    """Encode a datetime/date as ``"/Date(<ms>+HHMM)/"``.

    Naive datetimes are treated as UTC; dates as midnight UTC. The
    timezone offset is always emitted, matching the format the docs show
    (``"/Date(1426028400000+0100)/"``).
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        ms = int(value.timestamp() * 1000)
        offset = value.utcoffset() or timezone.utc.utcoffset(value)
    else:
        dt = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
        ms = int(dt.timestamp() * 1000)
        offset = timezone.utc.utcoffset(dt)

    total_minutes = int((offset.total_seconds() if offset else 0) // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    hours, minutes = divmod(total_minutes, 60)
    return f"/Date({ms}{sign}{hours:02d}{minutes:02d})/"


def from_dotnet_date(value: str) -> datetime:
    """Parse a ``"/Date(<ms>+HHMM)/"`` string into a timezone-aware datetime."""
    if not isinstance(value, str):
        raise TypeError(f"expected str, got {type(value).__name__}")
    match = _DOTNET_DATE_RE.match(value)
    if not match:
        raise ValueError(f"not a .NET JSON Date string: {value!r}")
    ms = int(match.group("ms"))
    tz_str = match.group("tz")
    if tz_str:
        sign = 1 if tz_str[0] == "+" else -1
        hours = int(tz_str[1:3])
        minutes = int(tz_str[3:5])
        offset_minutes = sign * (hours * 60 + minutes)
        from datetime import timedelta

        tz = timezone(timedelta(minutes=offset_minutes))
    else:
        tz = timezone.utc
    return datetime.fromtimestamp(ms / 1000, tz=tz)


class FilterOp(str, Enum):
    """Operations supported by Browse/Entity GET filters."""

    EQUAL = "equal"
    NOT_EQUAL = "notequal"
    GREATER = "greater"
    GREATER_EQUAL = "greaterequal"
    LESS = "less"
    LESS_EQUAL = "lessequal"
    STARTS_WITH = "startswith"
    CONTAINS = "contains"
    DATE_IN = "datein"


@dataclass(frozen=True)
class Filter:
    """One filter clause for a browse/entity GET call."""

    property: str
    operation: FilterOp | str
    value: Any

    def to_dict(self) -> dict[str, Any]:
        op = self.operation
        if isinstance(op, FilterOp):
            op = op.value
        value = _encode_filter_value(self.value)
        return {"Property": self.property, "Operation": op, "Value": value}


def _encode_filter_value(value: Any) -> Any:
    if isinstance(value, datetime) or isinstance(value, date):
        return to_dotnet_date(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return None
    return value


def encode_filters(filters: Iterable[Filter | dict[str, Any]] | None) -> str | None:
    """Serialize an iterable of :class:`Filter` (or dicts) for the URL."""
    if not filters:
        return None
    out: list[dict[str, Any]] = []
    for f in filters:
        if isinstance(f, Filter):
            out.append(f.to_dict())
        elif isinstance(f, dict):
            out.append(f)
        else:
            raise TypeError(f"unsupported filter: {f!r}")
    # No spaces, matches the docs exactly.
    return json.dumps(out, separators=(",", ":"), ensure_ascii=False)
