from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from aioresponses import aioresponses

from conftest import url_re
from pyevisitor import EVisitorClient, Filter


AUTH_URL = (
    "https://www.evisitor.hr/testApi/Resources/AspNetFormsAuth/Authentication/Login"
)
LOGOUT_URL = (
    "https://www.evisitor.hr/testApi/Resources/AspNetFormsAuth/Authentication/Logout"
)
REST = "https://www.evisitor.hr/testApi/Rest/"


async def test_list_tourists_default_uses_list_of_tourists(test_config) -> None:
    seen: list[str] = []
    with aioresponses() as m:
        m.post(AUTH_URL, status=200, body="true", content_type="application/json")

        from aioresponses.core import CallbackResult

        def cb(url, **_kwargs):
            seen.append(str(url))
            return CallbackResult(status=200, payload={"Records": []})

        m.get(url_re(REST + "Htz/ListOfTourists/"), callback=cb)

        async with EVisitorClient(test_config) as client:
            await client.browses.list_tourists()

        assert any("ListOfTourists/" in u and "Extended" not in u for u in seen)


async def test_list_tourists_extended_switch(test_config) -> None:
    seen: list[str] = []
    with aioresponses() as m:
        m.post(AUTH_URL, status=200, body="true", content_type="application/json")

        from aioresponses.core import CallbackResult

        def cb(url, **_kwargs):
            seen.append(str(url))
            return CallbackResult(status=200, payload={"Records": []})

        m.get(url_re(REST + "Htz/ListOfTouristsExtended/"), callback=cb)

        async with EVisitorClient(test_config) as client:
            await client.browses.list_tourists(extended=True)

        assert any("ListOfTouristsExtended/" in u for u in seen)


async def test_list_facilities_with_total_count_uses_subpath(test_config) -> None:
    seen: list[str] = []
    with aioresponses() as m:
        m.post(AUTH_URL, status=200, body="true", content_type="application/json")

        from aioresponses.core import CallbackResult

        def cb(url, **_kwargs):
            seen.append(str(url))
            return CallbackResult(
                status=200,
                payload={"Records": [], "TotalCount": 0},
            )

        m.get(
            url_re(REST + "Htz/FacilityBrowse/RecordsAndTotalCount"),
            callback=cb,
        )

        async with EVisitorClient(test_config) as client:
            await client.browses.list_facilities(with_total_count=True)

        assert any("RecordsAndTotalCount" in u for u in seen)


async def test_list_cancelled_tourists_uses_correct_path(test_config) -> None:
    seen: list[str] = []
    with aioresponses() as m:
        m.post(AUTH_URL, status=200, body="true", content_type="application/json")
        m.post(LOGOUT_URL, status=200, body="")

        from aioresponses.core import CallbackResult

        def cb(url, **_kwargs):
            seen.append(str(url))
            return CallbackResult(status=200, payload={"Records": []})

        m.get(url_re(REST + "Htz/TouristCancelledBrowse/"), callback=cb)

        async with EVisitorClient(test_config) as client:
            await client.browses.list_cancelled_tourists()

        assert any("TouristCancelledBrowse" in u for u in seen)


async def test_get_cancelled_tourist_by_id_returns_first(test_config) -> None:
    with aioresponses() as m:
        m.post(AUTH_URL, status=200, body="true", content_type="application/json")
        m.post(LOGOUT_URL, status=200, body="")
        m.get(
            url_re(REST + "Htz/TouristCancelledBrowse/"),
            status=200,
            payload={
                "Records": [
                    {"ID": "abc", "Tourist": "John Doe (40)"}
                ]
            },
        )

        async with EVisitorClient(test_config) as client:
            row = await client.browses.get_cancelled_tourist_by_id("abc")

        assert row is not None
        assert row["ID"] == "abc"


async def test_get_cancelled_tourist_by_id_missing_returns_none(test_config) -> None:
    with aioresponses() as m:
        m.post(AUTH_URL, status=200, body="true", content_type="application/json")
        m.post(LOGOUT_URL, status=200, body="")
        m.get(
            url_re(REST + "Htz/TouristCancelledBrowse/"),
            status=200,
            payload={"Records": []},
        )

        async with EVisitorClient(test_config) as client:
            row = await client.browses.get_cancelled_tourist_by_id("missing")

        assert row is None


async def test_get_facility_by_code_returns_first_record(test_config) -> None:
    seen: list[str] = []
    with aioresponses() as m:
        m.post(AUTH_URL, status=200, body="true", content_type="application/json")

        from aioresponses.core import CallbackResult

        def cb(url, **_kwargs):
            seen.append(str(url))
            return CallbackResult(
                status=200,
                payload={
                    "Records": [
                        {"ID": "facility-1", "Code": "654321", "Name": "Test"}
                    ]
                },
            )

        m.get(url_re(REST + "Htz/FacilityBrowse/"), callback=cb)

        async with EVisitorClient(test_config) as client:
            facility = await client.browses.get_facility_by_code("654321")

        assert facility is not None
        assert facility["Code"] == "654321"
        assert seen, "no request was captured"
        # filters query parameter is encoded JSON containing Code = 654321
        qs = parse_qs(urlsplit(seen[0]).query)
        assert "filters" in qs
        assert "654321" in qs["filters"][0]
