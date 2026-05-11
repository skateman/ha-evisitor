from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from pyevisitor.encoding import (
    Filter,
    FilterOp,
    encode_filters,
    from_dotnet_date,
    to_dotnet_date,
)


def test_to_dotnet_date_with_offset() -> None:
    # 1426028400000 ms = 2015-03-10 23:00:00 UTC, expressed at +0100
    from datetime import timedelta

    tz = timezone(timedelta(hours=1))
    dt = datetime(2015, 3, 11, 0, 0, 0, tzinfo=tz)
    assert to_dotnet_date(dt) == "/Date(1426028400000+0100)/"


def test_to_dotnet_date_naive_treated_as_utc() -> None:
    dt = datetime(2024, 1, 2, 3, 4, 5)  # naive
    encoded = to_dotnet_date(dt)
    parsed = from_dotnet_date(encoded)
    assert parsed.tzinfo is not None
    assert parsed.astimezone(timezone.utc).replace(tzinfo=None) == dt


def test_to_dotnet_date_date_only() -> None:
    encoded = to_dotnet_date(date(2024, 6, 1))
    assert encoded.startswith("/Date(")
    assert encoded.endswith(")/")
    assert from_dotnet_date(encoded).astimezone(timezone.utc).date() == date(
        2024, 6, 1
    )


def test_from_dotnet_date_invalid() -> None:
    with pytest.raises(ValueError):
        from_dotnet_date("not-a-date")


def test_filter_to_dict_with_enum_op() -> None:
    f = Filter("Code", FilterOp.EQUAL, "ABC")
    assert f.to_dict() == {"Property": "Code", "Operation": "equal", "Value": "ABC"}


def test_filter_value_bool_serialised_as_lowercase_string() -> None:
    f = Filter("Active", FilterOp.EQUAL, True)
    assert f.to_dict()["Value"] == "true"


def test_filter_value_date_encoded_as_dotnet() -> None:
    f = Filter("StayFrom", FilterOp.GREATER_EQUAL, date(2024, 1, 1))
    encoded = f.to_dict()["Value"]
    assert isinstance(encoded, str)
    assert encoded.startswith("/Date(")


def test_encode_filters_matches_documented_format() -> None:
    encoded = encode_filters(
        [
            Filter("Active", "equal", True),
            Filter("CodeTwoLetters", "startswith", "a"),
        ]
    )
    assert encoded is not None
    assert json.loads(encoded) == [
        {"Property": "Active", "Operation": "equal", "Value": "true"},
        {"Property": "CodeTwoLetters", "Operation": "startswith", "Value": "a"},
    ]
    # No spaces in the serialized form (matches docs literal).
    assert " " not in encoded


def test_encode_filters_none_or_empty_returns_none() -> None:
    assert encode_filters(None) is None
    assert encode_filters([]) is None


def test_encode_filters_accepts_dicts() -> None:
    encoded = encode_filters([{"Property": "X", "Operation": "equal", "Value": 1}])
    assert json.loads(encoded) == [{"Property": "X", "Operation": "equal", "Value": 1}]
