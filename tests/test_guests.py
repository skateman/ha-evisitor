from __future__ import annotations

from datetime import date

from aioresponses import aioresponses

from conftest import url_re
from pyevisitor import EVisitorClient, Guest
from pyevisitor.guests import (
    _dedupe_records,
    _normalize_name,
    _parse_birth,
    _strip_age,
)


AUTH_URL = (
    "https://www.evisitor.hr/testApi/Resources/AspNetFormsAuth/Authentication/Login"
)
LOGOUT_URL = (
    "https://www.evisitor.hr/testApi/Resources/AspNetFormsAuth/Authentication/Logout"
)
REST = "https://www.evisitor.hr/testApi/Rest/"


# Synthetic rows shaped exactly like ListOfTouristsExtended observed live.
RAW = [
    {
        "ID": "stay-1",
        "FacilityID": "fac-A",
        "FacilityName": "House A",
        "SurnameAndName": "Novák Marek (40)",
        "DatePlaceOfBirth": "15.01.1985 (40) Bratislava Slovačka Republika",
        "Gender": "Muški",
        "Citizenship": "Slovačke Republike",
        "Address": "Some street 1",
        "TravelDocumentTypeNumber": "Osobna iskaznica (strana) AB123",
        "DateTimeOfArrival": "/Date(1772197200000+0100)/",
        "DateTimeOfDeparture": "/Date(1772528400000+0100)/",
        "StayFrom": "/Date(1772146800000+0100)/",
        "ForeseenStayUntil": "/Date(1772492400000+0100)/",
        "CheckedOutTourist": True,
        "Note": "Friends and others",
    },
    {
        "ID": "stay-2",
        "FacilityID": "fac-A",
        "FacilityName": "House A",
        "SurnameAndName": "Novák Marek (40)",
        # Same person but DatePlaceOfBirth varies (city absent in this row).
        "DatePlaceOfBirth": "15.01.1985 (40)  Slovačka Republika",
        "Gender": "Muški",
        "Citizenship": "Slovačke Republike",
        "Address": "Some street 1",
        "TravelDocumentTypeNumber": "Osobna iskaznica (strana) AB123",
        "DateTimeOfArrival": "/Date(1900000000000+0100)/",  # newer
        "DateTimeOfDeparture": "/Date(1900100000000+0100)/",
        "StayFrom": "/Date(1900000000000+0100)/",
        "ForeseenStayUntil": "/Date(1900100000000+0100)/",
        "CheckedOutTourist": False,
        "Note": "Friends and others",
    },
    {
        "ID": "stay-3",
        "FacilityID": "fac-B",
        "FacilityName": "House B",
        "SurnameAndName": "Mikosová Jolana (80)",
        "DatePlaceOfBirth": "14.10.1945 (80) Bratislava Slovačka Republika",
        "Gender": "Ženski",
        "Citizenship": "Slovačke Republike",
        "Address": "Other 2",
        "TravelDocumentTypeNumber": "Putovnica (strana) XX",
        "DateTimeOfArrival": "/Date(1775311200000+0200)/",
        "DateTimeOfDeparture": "/Date(1775631600000+0200)/",
        "StayFrom": "/Date(1775311200000+0200)/",
        "ForeseenStayUntil": "/Date(1775631600000+0200)/",
        "CheckedOutTourist": True,
        "Note": None,
    },
]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_strip_age_removes_trailing_age() -> None:
    assert _strip_age("Novák Marek (40)") == "Novák Marek"
    assert _strip_age("Vasík (László) János (24)") == "Vasík (László) János"
    assert _strip_age(None) == ""
    assert _strip_age("plain") == "plain"


def test_normalize_name_is_case_and_accent_insensitive() -> None:
    assert _normalize_name("Novák Marek") == _normalize_name("novak marek")
    assert _normalize_name("  Novák  Marek  ") == _normalize_name("Novák Marek")
    assert _normalize_name(None) == ""  # type: ignore[arg-type]


def test_parse_birth_extracts_date() -> None:
    assert _parse_birth("15.01.1985 (40) Bratislava Slovačka Republika") == date(
        1985, 1, 15
    )
    assert _parse_birth("15.01.1985 (40)  Slovačka Republika") == date(1985, 1, 15)
    assert _parse_birth("15.01.1985 Some Place") == date(1985, 1, 15)


def test_parse_birth_returns_none_for_garbage() -> None:
    assert _parse_birth(None) is None
    assert _parse_birth("") is None
    assert _parse_birth("garbage value") is None


def test_parse_birth_returns_none_for_impossible_date() -> None:
    # Regex matches digits.dots, but date() raises -> None.
    assert _parse_birth("32.13.1990 (40) Place") is None


# ---------------------------------------------------------------------------
# Dedup behaviour
# ---------------------------------------------------------------------------


def test_dedupe_collapses_same_person_into_one_guest() -> None:
    guests = _dedupe_records(RAW)
    # Novák Marek x2 + Mikosová Jolana x1 -> 2 unique guests.
    assert len(guests) == 2

    by_name = {g.name: g for g in guests}
    novak = by_name["Novák Marek"]
    assert novak.date_of_birth == date(1985, 1, 15)
    assert novak.visit_count == 2
    # Stays are sorted newest-first; full raw records preserved.
    assert [s["ID"] for s in novak.stays] == ["stay-2", "stay-1"]
    assert novak.latest is novak.stays[0]
    # Every original API field is preserved verbatim on the latest stay.
    assert novak.latest["TravelDocumentTypeNumber"] == "Osobna iskaznica (strana) AB123"
    assert novak.latest["Address"] == "Some street 1"
    assert novak.latest["FacilityID"] == "fac-A"
    assert novak.latest["CheckedOutTourist"] is False  # newest stay is active


def test_dedupe_orders_guests_by_most_recent_stay_first() -> None:
    guests = _dedupe_records(RAW)
    # stay-2 (newer) belongs to Novák, stay-3 to Mikosová; Novák leads.
    assert guests[0].name == "Novák Marek"
    assert guests[1].name == "Mikosová Jolana"


def test_dedupe_handles_empty_input() -> None:
    assert _dedupe_records([]) == []


def test_dedupe_handles_missing_dob_buckets_separately() -> None:
    rows = [
        {
            "ID": "a",
            "SurnameAndName": "Same Person (40)",
            "DatePlaceOfBirth": None,
            "DateTimeOfArrival": "/Date(1000000000000+0000)/",
        },
        {
            "ID": "b",
            "SurnameAndName": "Same Person (40)",
            "DatePlaceOfBirth": "01.01.1985 (40) Somewhere",
            "DateTimeOfArrival": "/Date(2000000000000+0000)/",
        },
    ]
    guests = _dedupe_records(rows)
    # Same name but different DOB key (None vs date) -> two entries.
    assert len(guests) == 2


def test_dedupe_accent_insensitive_grouping() -> None:
    rows = [
        {
            "ID": "a",
            "SurnameAndName": "Novák Marek (40)",
            "DatePlaceOfBirth": "15.01.1985 (40) X",
            "DateTimeOfArrival": "/Date(1000000000000+0000)/",
        },
        {
            "ID": "b",
            "SurnameAndName": "NOVAK MAREK (40)",  # no accents, upper case
            "DatePlaceOfBirth": "15.01.1985 (40) Y",
            "DateTimeOfArrival": "/Date(2000000000000+0000)/",
        },
    ]
    guests = _dedupe_records(rows)
    assert len(guests) == 1
    assert guests[0].visit_count == 2


# ---------------------------------------------------------------------------
# Integration via the full client (mocked HTTP)
# ---------------------------------------------------------------------------


async def test_guests_stays_returns_raw_records(test_config) -> None:
    with aioresponses() as m:
        m.post(AUTH_URL, status=200, body="true", content_type="application/json")
        m.post(LOGOUT_URL, status=200, body="")
        m.get(
            url_re(REST + "Htz/ListOfTouristsExtended/"),
            status=200,
            payload={"Records": RAW},
        )

        async with EVisitorClient(test_config) as client:
            stays = await client.guests.stays()

        # One entry per raw row, no dedup, untouched dicts.
        assert len(stays) == len(RAW)
        assert stays == RAW


async def test_guests_unique_via_client(test_config) -> None:
    with aioresponses() as m:
        m.post(AUTH_URL, status=200, body="true", content_type="application/json")
        m.post(LOGOUT_URL, status=200, body="")
        m.get(
            url_re(REST + "Htz/ListOfTouristsExtended/"),
            status=200,
            payload={"Records": RAW},
        )

        async with EVisitorClient(test_config) as client:
            guests = await client.guests.unique()

        assert len(guests) == 2
        assert all(isinstance(g, Guest) for g in guests)
        # Every raw API field on the latest stay is preserved.
        for g in guests:
            assert isinstance(g.latest, dict)
            assert "ID" in g.latest
            assert "DateTimeOfArrival" in g.latest


async def test_stays_total_matches_unique_visit_sum(test_config) -> None:
    """``unique()`` must preserve every stay across all returned guests."""
    with aioresponses() as m:
        m.post(AUTH_URL, status=200, body="true", content_type="application/json")
        m.post(LOGOUT_URL, status=200, body="")
        m.get(
            url_re(REST + "Htz/ListOfTouristsExtended/"),
            status=200,
            payload={"Records": RAW},
            repeat=True,
        )

        async with EVisitorClient(test_config) as client:
            stays = await client.guests.stays()
            unique = await client.guests.unique()

        assert sum(g.visit_count for g in unique) == len(stays)
