from __future__ import annotations

import pytest
from aioresponses import aioresponses

from conftest import url_re
from pyevisitor import EVisitorClient


AUTH_URL = (
    "https://www.evisitor.hr/testApi/Resources/AspNetFormsAuth/Authentication/Login"
)
LOGOUT_URL = (
    "https://www.evisitor.hr/testApi/Resources/AspNetFormsAuth/Authentication/Logout"
)
REST = "https://www.evisitor.hr/testApi/Rest/"


async def test_lookup_unknown_name_raises(test_config) -> None:
    client = EVisitorClient(test_config)
    try:
        with pytest.raises(KeyError):
            await client.lookups.fetch("nonexistent_lookup")
    finally:
        await client.close()


async def test_countries_returns_records_list(test_config) -> None:
    with aioresponses() as m:
        m.post(AUTH_URL, status=200, body="true", content_type="application/json")
        m.post(LOGOUT_URL, status=200, body="")
        m.get(
            url_re(REST + "Htz/CountryLookup/"),
            status=200,
            payload={
                "Records": [
                    {"ID": "x", "CodeThreeLetters": "HRV", "NameNational": "Hrvatska"},
                    {"ID": "y", "CodeThreeLetters": "DEU", "NameNational": "Njemačka"},
                ]
            },
        )

        async with EVisitorClient(test_config) as client:
            countries = await client.lookups.countries()

        assert [c["CodeThreeLetters"] for c in countries] == ["HRV", "DEU"]


async def test_known_includes_expected_lookups(test_config) -> None:
    client = EVisitorClient(test_config)
    try:
        known = client.lookups.known
        for required in (
            "country",
            "document_type",
            "arrival_organisation",
            "tt_payment_category",
            "border_crossing_hr",
            "offered_service_type",
        ):
            assert required in known
        # Admin-only lookups (TZ/HTZ role) must not be advertised by the
        # obveznik-targeted library.
        for excluded in (
            "facility_category",
            "facility_code",
            "distance_type",
            "service_type",
            "opening_basis",
            "legal_person_type",
            "payment_payout_type",
        ):
            assert excluded not in known
    finally:
        await client.close()
