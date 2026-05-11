from __future__ import annotations

import json

import pytest
from aioresponses import aioresponses

from conftest import url_re
from pyevisitor import (
    EVisitorAuthError,
    EVisitorClient,
    EVisitorHTTPError,
    EVisitorValidationError,
    Filter,
)


AUTH_URL = (
    "https://www.evisitor.hr/testApi/Resources/AspNetFormsAuth/Authentication/Login"
)
LOGOUT_URL = (
    "https://www.evisitor.hr/testApi/Resources/AspNetFormsAuth/Authentication/Logout"
)
REST_BASE = "https://www.evisitor.hr/testApi/Rest/"


async def test_login_success_and_authenticated_flag(test_config) -> None:
    with aioresponses() as m:
        m.post(AUTH_URL, status=200, body="true", content_type="application/json")
        client = EVisitorClient(test_config)
        try:
            await client.login()
            assert client.authenticated is True
        finally:
            await client.close()


async def test_login_returns_false_raises(test_config) -> None:
    with aioresponses() as m:
        m.post(AUTH_URL, status=200, body="false", content_type="application/json")
        client = EVisitorClient(test_config)
        try:
            with pytest.raises(EVisitorAuthError):
                await client.login()
        finally:
            await client.close()


async def test_login_error_body_raises_auth_error(test_config) -> None:
    with aioresponses() as m:
        m.post(
            AUTH_URL,
            status=200,
            payload={
                "UserMessage": None,
                "SystemMessage": (
                    "Application is not registered or is deactivated or "
                    "API key has expired."
                ),
            },
        )
        client = EVisitorClient(test_config)
        try:
            with pytest.raises(EVisitorAuthError, match="API key"):
                await client.login()
        finally:
            await client.close()


async def test_login_http_error(test_config) -> None:
    with aioresponses() as m:
        m.post(AUTH_URL, status=500, body="boom")
        client = EVisitorClient(test_config)
        try:
            with pytest.raises(EVisitorAuthError, match="500"):
                await client.login()
        finally:
            await client.close()


async def test_request_decodes_validation_error(test_config) -> None:
    with aioresponses() as m:
        m.post(AUTH_URL, status=200, body="true", content_type="application/json")
        m.post(
            REST_BASE + "Htz/CheckInTourist/",
            status=400,
            payload={
                "UserMessage": "Turist je već prijavljen u navedenom objektu.",
                "SystemMessage": None,
            },
        )
        client = EVisitorClient(test_config)
        try:
            await client.login()
            with pytest.raises(EVisitorValidationError) as exc:
                await client.post(
                    "Htz/CheckInTourist/", json_body={"Facility": "X"}
                )
            assert exc.value.user_message and "već prijavljen" in exc.value.user_message
            assert exc.value.status == 400
        finally:
            await client.close()


async def test_request_raises_http_error_on_unexpected_500(test_config) -> None:
    with aioresponses() as m:
        m.post(AUTH_URL, status=200, body="true", content_type="application/json")
        m.get(
            url_re(REST_BASE + "Htz/CountryLookup/"),
            status=502,
            body="bad gateway",
        )
        client = EVisitorClient(test_config)
        try:
            await client.login()
            with pytest.raises(EVisitorHTTPError) as exc:
                await client.get("Htz/CountryLookup/", params={"page": 1, "psize": 10})
            assert exc.value.status == 502
        finally:
            await client.close()


async def test_logout_called_on_context_exit(test_config) -> None:
    with aioresponses() as m:
        m.post(AUTH_URL, status=200, body="true", content_type="application/json")
        m.post(LOGOUT_URL, status=200, body="")
        async with EVisitorClient(test_config) as client:
            assert client.authenticated
        assert client.authenticated is False


async def test_request_filters_round_trip_through_query(test_config) -> None:
    """Filters are JSON-encoded into the ``filters`` query param."""

    captured: list[str] = []

    with aioresponses() as m:
        m.post(AUTH_URL, status=200, body="true", content_type="application/json")
        m.post(LOGOUT_URL, status=200, body="")

        from aioresponses.core import CallbackResult

        def cb(url, **_kwargs):
            captured.append(str(url))
            return CallbackResult(
                status=200,
                payload={"Records": [{"ID": "x", "Code": "ABC"}]},
            )

        m.get(url_re(REST_BASE + "Htz/FacilityBrowse/"), callback=cb)

        async with EVisitorClient(test_config) as client:
            res = await client.get(
                "Htz/FacilityBrowse/",
                filters=[Filter("Code", "equal", "ABC")],
            )
            assert res["Records"][0]["Code"] == "ABC"

        assert captured, "request URL was not captured"
        # The filters parameter is JSON-encoded into the URL.
        assert "filters=" in captured[0]
        assert "ABC" in captured[0] or "%41%42%43" in captured[0].upper()
