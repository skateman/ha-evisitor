from __future__ import annotations

from datetime import date, time

import pytest
from aioresponses import aioresponses

from pyevisitor import (
    CancelCheckInRequest,
    CancelCheckOutRequest,
    CheckInRequest,
    CheckOutRequest,
    EVisitorClient,
)


AUTH_URL = (
    "https://www.evisitor.hr/testApi/Resources/AspNetFormsAuth/Authentication/Login"
)
LOGOUT_URL = (
    "https://www.evisitor.hr/testApi/Resources/AspNetFormsAuth/Authentication/Logout"
)
REST = "https://www.evisitor.hr/testApi/Rest/"


def _make_check_in() -> CheckInRequest:
    return CheckInRequest(
        facility="123456",
        stay_from=date(2024, 8, 1),
        foreseen_stay_until=date(2024, 8, 7),
        time_stay_from=time(15, 0),
        time_estimated_stay_until=time(10, 0),
        document_type="OI",
        document_number="123456789",
        tourist_name="Ivan",
        tourist_surname="Horvat",
        gender="muški",
        date_of_birth=date(1990, 1, 1),
        citizenship="HRV",
        country_of_birth="HRV",
        country_of_residence="HRV",
        arrival_organisation="OS",
        offered_service_type="Noćenje s doručkom",
        tt_payment_category="14",
        residence_address="Ilica 1",
    )


def test_check_in_payload_uses_yyyymmdd_and_hhmm() -> None:
    req = _make_check_in()
    payload = req.to_payload()
    assert payload["StayFrom"] == "20240801"
    assert payload["ForeseenStayUntil"] == "20240807"
    assert payload["TimeStayFrom"] == "15:00"
    assert payload["TimeEstimatedStayUntil"] == "10:00"
    assert payload["DateOfBirth"] == "19900101"
    assert payload["Facility"] == "123456"
    # Optional fields stripped when None
    assert "TouristMiddleName" not in payload
    assert "BorderCrossingHr" not in payload
    # ID is auto-generated when not provided -- the server requires it.
    assert "ID" in payload
    assert payload["ID"] == req.id  # auto-id is reflected back on the dataclass
    # Generated ID must look like a UUID.
    import uuid
    uuid.UUID(payload["ID"])


def test_check_in_payload_keeps_explicit_id() -> None:
    req = _make_check_in()
    req.id = "11111111-2222-3333-4444-555555555555"
    payload = req.to_payload()
    assert payload["ID"] == "11111111-2222-3333-4444-555555555555"
    # Re-encoding doesn't replace the ID.
    assert req.to_payload()["ID"] == "11111111-2222-3333-4444-555555555555"


def test_check_in_payload_extra_overrides_and_extends() -> None:
    req = _make_check_in()
    req.extra["CustomField"] = "yes"
    payload = req.to_payload()
    assert payload["CustomField"] == "yes"


def test_check_out_payload_shapes() -> None:
    req = CheckOutRequest(
        id="11111111-2222-3333-4444-555555555555",
        check_out_date=date(2024, 8, 6),
        check_out_time=time(9, 30),
    )
    p = req.to_payload()
    assert p == {
        "ID": "11111111-2222-3333-4444-555555555555",
        "CheckOutDate": "20240806",
        "CheckOutTime": "09:30",
    }


def test_check_out_payload_converts_aware_datetime_to_zagreb() -> None:
    """UTC datetimes must be converted to Europe/Zagreb before serialisation.

    Regression for the 2-hour under-report bug: HA hands the coordinator
    UTC-anchored datetimes from ``last_changed``/``dt_util.now()``; the
    eVisitor server interprets the scalar YYYYMMDD / HH:MM payload as
    Croatia local, so dropping tz info shifted every check-out by the
    UTC offset (1-2 hours depending on DST).
    """
    from datetime import datetime, timezone

    moment_utc = datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc)
    req = CheckOutRequest(
        id="11111111-2222-3333-4444-555555555555",
        check_out_date=moment_utc,
        check_out_time=moment_utc,
    )
    p = req.to_payload()
    # 09:00 UTC on a CEST date = 11:00 Zagreb.
    assert p["CheckOutDate"] == "20260526"
    assert p["CheckOutTime"] == "11:00"


def test_check_out_payload_aware_datetime_crossing_midnight() -> None:
    """UTC 22:30 -> Zagreb 00:30 next day. Date must roll over."""
    from datetime import datetime, timezone

    moment_utc = datetime(2026, 5, 26, 22, 30, tzinfo=timezone.utc)
    req = CheckOutRequest(
        id="11111111-2222-3333-4444-555555555555",
        check_out_date=moment_utc,
        check_out_time=moment_utc,
    )
    p = req.to_payload()
    assert p["CheckOutDate"] == "20260527"
    assert p["CheckOutTime"] == "00:30"


def test_check_in_payload_converts_aware_datetime_to_zagreb() -> None:
    """Same conversion path covers CheckIn (arrival + foreseen end)."""
    from datetime import datetime, timezone

    req = _make_check_in()
    req.stay_from = datetime(2026, 5, 22, 20, 0, tzinfo=timezone.utc)
    req.time_stay_from = datetime(2026, 5, 22, 20, 0, tzinfo=timezone.utc)
    req.foreseen_stay_until = datetime(2026, 5, 24, 8, 0, tzinfo=timezone.utc)
    req.time_estimated_stay_until = datetime(2026, 5, 24, 8, 0, tzinfo=timezone.utc)
    payload = req.to_payload()
    # 20:00 UTC -> 22:00 Zagreb (CEST, same date).
    assert payload["StayFrom"] == "20260522"
    assert payload["TimeStayFrom"] == "22:00"
    # 08:00 UTC -> 10:00 Zagreb (CEST, same date).
    assert payload["ForeseenStayUntil"] == "20260524"
    assert payload["TimeEstimatedStayUntil"] == "10:00"


def test_check_out_payload_naive_datetime_unchanged() -> None:
    """Naive datetimes are interpreted as already-local; no conversion."""
    from datetime import datetime

    moment_naive = datetime(2026, 5, 26, 11, 0)
    req = CheckOutRequest(
        id="11111111-2222-3333-4444-555555555555",
        check_out_date=moment_naive,
        check_out_time=moment_naive,
    )
    p = req.to_payload()
    assert p["CheckOutDate"] == "20260526"
    assert p["CheckOutTime"] == "11:00"


def test_cancel_check_in_payload_strips_none() -> None:
    p = CancelCheckInRequest(id="abc").to_payload()
    assert p == {"ID": "abc"}


def test_cancel_check_out_payload_with_reason() -> None:
    p = CancelCheckOutRequest(id="abc", reason="error").to_payload()
    assert p == {"ID": "abc", "Reason": "error"}


async def test_check_in_posts_expected_body(test_config) -> None:
    captured: list[dict] = []

    with aioresponses() as m:
        m.post(AUTH_URL, status=200, body="true", content_type="application/json")
        m.post(LOGOUT_URL, status=200, body="")
        url = REST + "Htz/CheckInTourist/"

        def cb(_url, **kwargs):
            captured.append(kwargs.get("json"))
            from aioresponses.core import CallbackResult

            return CallbackResult(
                status=200,
                payload={"ID": "00000000-0000-0000-0000-000000000001"},
            )

        m.post(url, callback=cb)

        async with EVisitorClient(test_config) as client:
            result = await client.actions.check_in_tourist(_make_check_in())

        assert result["ID"] == "00000000-0000-0000-0000-000000000001"
        assert captured, "request body was not captured"
        body = captured[0]
        assert body["Facility"] == "123456"
        assert body["TouristName"] == "Ivan"
        assert body["StayFrom"] == "20240801"


async def test_check_out_posts_expected_body(test_config) -> None:
    captured: list[dict] = []

    with aioresponses() as m:
        m.post(AUTH_URL, status=200, body="true", content_type="application/json")
        m.post(LOGOUT_URL, status=200, body="")

        def cb(_url, **kwargs):
            captured.append(kwargs.get("json"))
            from aioresponses.core import CallbackResult

            return CallbackResult(status=200, body="")

        m.post(REST + "Htz/CheckOutTourist/", callback=cb)

        async with EVisitorClient(test_config) as client:
            await client.actions.check_out_tourist(
                CheckOutRequest(
                    id="abc",
                    check_out_date=date(2024, 8, 6),
                    check_out_time=time(9, 30),
                )
            )

        assert captured[0] == {
            "ID": "abc",
            "CheckOutDate": "20240806",
            "CheckOutTime": "09:30",
        }
