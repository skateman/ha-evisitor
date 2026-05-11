"""Shared HA-runtime test fixtures.

The heavy ``pytest-homeassistant-custom-component`` plugin auto-loads
when installed, so we don't need to register it manually. We only set
up the integration's local fixtures here.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Make ``custom_components/`` discoverable without installing it.
ROOT = Path(__file__).resolve().parents[2]
CUSTOM_COMPONENTS = ROOT / "custom_components"
sys.path.insert(0, str(CUSTOM_COMPONENTS.parent))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make HA recognise files under custom_components/."""
    yield


@pytest.fixture
def fake_facility() -> dict:
    return {
        "ID": "fac-id-1",
        "Code": "0000001",
        "Name": "Test House",
    }


@pytest.fixture
def fake_unique_guests():
    """Two unique guests built from synthetic ListOfTouristsExtended rows."""
    from datetime import date

    from pyevisitor import Guest

    novak_latest = {
        "ID": "stay-1",
        "FacilityID": "fac-id-1",
        "FacilityName": "Test House",
        "SurnameAndName": "Novák Marek",
        "DatePlaceOfBirth": "15.01.1985 (40) Bratislava Slovačka Republika",
        "Gender": "Muški",
        "Citizenship": "Slovačke Republike",
        "Address": "Hlavná 12 Trnava Slovačka Republika",
        "TravelDocumentTypeNumber": "Osobna iskaznica (strana) XX111111",
        "Note": "Vlasnici kuće za odmor i članovi njegove obitelji",
        "DateTimeOfArrival": "/Date(1900000000000+0100)/",
        "StayFrom": "/Date(1899900000000+0100)/",
        "ForeseenStayUntil": "/Date(1900100000000+0100)/",
        "CheckedOutTourist": True,
    }
    eva_latest = {
        "ID": "stay-2",
        "FacilityID": "fac-id-1",
        "FacilityName": "Test House",
        "SurnameAndName": "Nováková Eva",
        "DatePlaceOfBirth": "20.06.1970 (55) Bratislava Slovačka Republika",
        "Gender": "Ženski",
        "Citizenship": "Slovačke Republike",
        "Address": "Some street 5 Trnava Slovačka Republika",
        "TravelDocumentTypeNumber": "Osobna iskaznica (strana) YY222222",
        "DateTimeOfArrival": "/Date(1900000000000+0100)/",
        "StayFrom": "/Date(1899900000000+0100)/",
        "ForeseenStayUntil": "/Date(1900100000000+0100)/",
        "CheckedOutTourist": True,
    }
    return [
        Guest(name="Novák Marek", date_of_birth=date(1985, 1, 15), stays=(novak_latest,)),
        Guest(name="Nováková Eva", date_of_birth=date(1970, 6, 20), stays=(eva_latest,)),
    ]


@pytest.fixture
def fake_lookup_cache() -> dict:
    return {
        "country": [
            {
                "CodeThreeLetters": "HRV",
                "NameNational": "Hrvatska",
                "NameCitizenships": "Hrvatske",
            },
            {
                "CodeThreeLetters": "SVK",
                "NameNational": "Slovačka Republika",
                "NameCitizenships": "Slovačke Republike",
            },
        ],
        "document_type": [
            {"Code": "027", "Name": "Osobna iskaznica (strana)"},
            {"Code": "001", "Name": "Putovnica"},
        ],
        "arrival_organisation": [
            {"CodeMI": "I", "Name": "Osobno"},
            {"CodeMI": "A", "Name": "Agencijski"},
        ],
        "tt_payment_category": [
            {"Code": "14", "Name": "Turist u ugostiteljskom objektu"},
            {
                "Code": "18",
                "Name": "Vlasnici kuće za odmor i članovi njegove obitelji",
            },
        ],
        "offered_service_type": [
            {"Name": "Noćenje"},
        ],
    }


@pytest.fixture
def fake_client_factory(fake_facility, fake_unique_guests, fake_lookup_cache):
    """A factory that builds a mocked ``EVisitorClient`` instance.

    The integration creates the client inside the coordinator; we patch
    ``custom_components.evisitor.coordinator.EVisitorClient`` to return
    one of these mocks. The mock advertises the same async surface the
    coordinator and config flow use.
    """

    def _make() -> MagicMock:
        client = MagicMock(name="EVisitorClient")
        client.authenticated = True
        client.login = AsyncMock()
        client.logout = AsyncMock()
        client.close = AsyncMock()

        client.browses = MagicMock()
        client.browses.list_facilities = AsyncMock(
            return_value={"Records": [fake_facility]}
        )
        client.browses.get_facility_by_code = AsyncMock(return_value=fake_facility)
        client.browses.list_cancelled_tourists = AsyncMock(
            return_value={"Records": []}
        )
        client.browses.get_cancelled_tourist_by_id = AsyncMock(return_value=None)

        client.guests = MagicMock()
        client.guests.unique = AsyncMock(return_value=fake_unique_guests)
        client.guests.stays = AsyncMock(
            return_value=[g.latest for g in fake_unique_guests]
        )

        client.lookups = MagicMock()
        client.lookups.countries = AsyncMock(return_value=fake_lookup_cache["country"])
        client.lookups.document_types = AsyncMock(
            return_value=fake_lookup_cache["document_type"]
        )
        client.lookups.arrival_organisations = AsyncMock(
            return_value=fake_lookup_cache["arrival_organisation"]
        )
        client.lookups.tt_payment_categories = AsyncMock(
            return_value=fake_lookup_cache["tt_payment_category"]
        )
        client.lookups.offered_service_types = AsyncMock(
            return_value=fake_lookup_cache["offered_service_type"]
        )

        client.actions = MagicMock()
        client.actions.check_in_tourist = AsyncMock()
        client.actions.check_out_tourist = AsyncMock()
        client.actions.cancel_tourist_check_in = AsyncMock()
        client.actions.cancel_tourist_check_out = AsyncMock()
        return client

    return _make
