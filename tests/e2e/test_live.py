"""Live, read-only e2e tests against the eVisitor API.

All tests in this module are GETs (and the auth POSTs needed to obtain a
session). Nothing here calls ``CheckInTourist``, ``CheckOutTourist`` or any
``Cancel*`` action, so it is safe to run against the production
environment with a normal obveznik account.

Activation:

- ``EVISITOR_E2E=1`` is required.
- ``EVISITOR_USERNAME`` and ``EVISITOR_PASSWORD`` must be set in the
  environment (or in ``.env`` -- load it with ``python-dotenv`` from a
  shell wrapper if needed).
- ``EVISITOR_ENVIRONMENT`` selects ``test`` or ``production``.

Each test logs in and out individually to keep them independent.
"""

from __future__ import annotations

import pytest

from pyevisitor import EVisitorClient, Filter

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def live_client(e2e_config):
    """Logged-in async client for one test, cleaned up afterwards."""
    client = EVisitorClient(e2e_config)
    await client.login()
    try:
        yield client
    finally:
        try:
            if client.authenticated:
                await client.logout()
        except Exception:
            pass
        await client.close()


# ---------------------------------------------------------------------------
# Authentication smoke test
# ---------------------------------------------------------------------------


async def test_login_and_logout(e2e_config) -> None:
    client = EVisitorClient(e2e_config)
    try:
        await client.login()
        assert client.authenticated is True
        await client.logout()
        assert client.authenticated is False
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Facility browse (the obveznik's own facilities)
# ---------------------------------------------------------------------------


async def test_list_facilities_returns_records(live_client) -> None:
    result = await live_client.browses.list_facilities()
    assert isinstance(result, dict)
    records = result.get("Records") or []
    assert isinstance(records, list)
    # Every facility should at least carry an ID, Code and Name.
    for f in records:
        assert "ID" in f and "Code" in f and "Name" in f


async def test_list_facilities_with_total_count(live_client) -> None:
    """Hits the ``/RecordsAndTotalCount`` sub-path."""
    result = await live_client.browses.list_facilities(with_total_count=True)
    assert isinstance(result, dict)
    assert "Records" in result
    assert "TotalCount" in result
    assert result["TotalCount"] >= len(result["Records"])


async def test_get_facility_by_code_roundtrip(live_client) -> None:
    """Pick the first facility, fetch it back by Code, verify identity."""
    listing = await live_client.browses.list_facilities()
    records = (listing or {}).get("Records") or []
    if not records:
        pytest.skip("Account has no facilities to look up")
    first = records[0]
    code = first["Code"]

    fetched = await live_client.browses.get_facility_by_code(code)
    assert fetched is not None
    assert fetched["Code"] == code
    assert fetched["ID"] == first["ID"]


# ---------------------------------------------------------------------------
# Tourist browses (historical / active records, read-only)
# ---------------------------------------------------------------------------


async def test_list_tourists_basic(live_client) -> None:
    result = await live_client.browses.list_tourists()
    assert isinstance(result, dict)
    records = result.get("Records") or []
    assert isinstance(records, list)
    for t in records:
        assert "ID" in t
        # Documented attributes on ListOfTourists (subset).
        for attr in ("FacilityID", "TTPayerID", "DateTimeOfArrival"):
            assert attr in t


async def test_list_tourists_extended(live_client) -> None:
    result = await live_client.browses.list_tourists(extended=True)
    records = (result or {}).get("Records") or []
    # Extended view exposes more attributes per the docs.
    if records:
        sample = records[0]
        for attr in (
            "ID",
            "SurnameAndName",
            "DateTimeOfArrival",
            "TravelDocumentTypeNumber",
        ):
            assert attr in sample


async def test_list_tourists_with_total_count(live_client) -> None:
    result = await live_client.browses.list_tourists(with_total_count=True)
    assert isinstance(result, dict)
    assert "TotalCount" in result and "Records" in result
    assert result["TotalCount"] >= len(result["Records"])


async def test_tourist_check_out_browse(live_client) -> None:
    """``TouristCheckOut`` returns active check-ins ready for check-out.

    Read-only browse; no actual check-out is performed.
    """
    result = await live_client.get("Htz/TouristCheckOut/")
    assert isinstance(result, dict)
    records = result.get("Records") or []
    for r in records:
        assert "ID" in r
        for attr in ("CheckInDate", "FacilityID", "Tourist"):
            assert attr in r


async def test_list_cancelled_tourists(live_client) -> None:
    """Cancelled prijave are retrievable via ``TouristCancelledBrowse``."""
    result = await live_client.browses.list_cancelled_tourists()
    assert isinstance(result, dict)
    records = result.get("Records") or []
    for r in records:
        assert "ID" in r and "Tourist" in r and "Facility" in r


# ---------------------------------------------------------------------------
# Deduplicated guests view (high-level helper)
# ---------------------------------------------------------------------------


async def test_guests_stays_returns_raw_records(live_client) -> None:
    """``stays()`` returns one raw dict per historical check-in."""
    stays = await live_client.guests.stays()
    assert isinstance(stays, list)
    for s in stays:
        assert isinstance(s, dict)
        for required in ("ID", "SurnameAndName", "DatePlaceOfBirth"):
            assert required in s


async def test_guests_unique_aggregates_per_person(live_client) -> None:
    """``unique()`` collapses repeated stays into one Guest per person."""
    guests = await live_client.guests.unique()
    stays = await live_client.guests.stays()
    if not stays:
        pytest.skip("No historical guests on this account to deduplicate")

    assert len(guests) <= len(stays)
    assert sum(g.visit_count for g in guests) == len(stays)

    for g in guests:
        assert g.name and g.visit_count >= 1
        # latest is a raw dict carrying every API field for that stay.
        assert isinstance(g.latest, dict)
        for required in ("ID", "DateTimeOfArrival", "FacilityID"):
            assert required in g.latest


# ---------------------------------------------------------------------------
# Lookups (codetables) -- one test per documented lookup the user might need
# ---------------------------------------------------------------------------


async def test_lookup_countries(live_client) -> None:
    countries = await live_client.lookups.countries()
    assert len(countries) > 100  # Croatia + the world.
    assert any(c.get("CodeThreeLetters") == "HRV" for c in countries)
    sample = countries[0]
    for attr in ("ID", "CodeTwoLetters", "CodeThreeLetters", "NameNational"):
        assert attr in sample


async def test_lookup_document_types(live_client) -> None:
    doc_types = await live_client.lookups.document_types()
    assert len(doc_types) >= 5
    for d in doc_types:
        assert "Code" in d and "Name" in d


async def test_lookup_arrival_organisations(live_client) -> None:
    orgs = await live_client.lookups.arrival_organisations()
    assert orgs, "Expected at least one arrival organisation"
    codes = {o.get("CodeMI") for o in orgs}
    # Per docs there is at minimum the personal arrival code 'I'.
    assert "I" in codes


async def test_lookup_tt_payment_categories(live_client) -> None:
    cats = await live_client.lookups.tt_payment_categories()
    assert cats, "Expected payment categories"
    for c in cats:
        assert "Code" in c and "Name" in c


async def test_lookup_offered_service_types(live_client) -> None:
    services = await live_client.lookups.offered_service_types()
    assert services, "Expected at least one offered service type"
    for s in services:
        assert "Name" in s


async def test_lookup_border_crossings(live_client) -> None:
    """``BorderCrossingHRlookup`` is needed for non-EU tourists."""
    crossings = await live_client.lookups.border_crossings()
    # Croatia has many border crossings; just verify the call works
    # and returns the expected attribute shape.
    assert isinstance(crossings, list)
    for c in crossings[:5]:
        assert "Name" in c


async def test_lookup_settlements_filtered(live_client) -> None:
    """``SettlementLookup`` is huge -- query for one ZIP to keep it small."""
    settlements = await live_client.lookups.settlements(
        filters=[Filter("ZIPCode", "equal", "10000")],  # Zagreb centre
    )
    assert isinstance(settlements, list)
    # The filter may match zero rows on some snapshots; just assert shape.
    for s in settlements[:5]:
        for attr in ("ID", "Name", "ZIPCode"):
            assert attr in s


async def test_lookup_facility_tourist_check_in_for_first_facility(
    live_client,
) -> None:
    """Per-facility info needed before constructing a check-in."""
    facilities = await live_client.browses.list_facilities()
    records = (facilities or {}).get("Records") or []
    if not records:
        pytest.skip("Account has no facilities")
    first = records[0]
    info = await live_client.lookups.fetch(
        "facility_tourist_check_in",
        filters=[Filter("Code", "equal", first["Code"])],
        active_only=False,  # this lookup does not expose Active.
    )
    assert info, "Expected at least one row for the user's own facility"
    row = info[0]
    for attr in ("Code", "ID", "IsVacationHomeCalculation"):
        assert attr in row


# ---------------------------------------------------------------------------
# Smoke test all small lookups that don't require a filter
# ---------------------------------------------------------------------------


# Lookups that are safe to call without filters and are accessible to a
# regular obveznik account. Excluded:
#   - settlement (huge, prefer a ZIP filter)
#   - settlement_zone (needs SettlementHrID)
#   - accommodation_unit_facility_type (needs FacilityID)
#   - accommodation_unit_type_per_subtype (needs FacilitySubcategoryID)
#   - tt_payment_category_for_facility (needs FacilityID)
#   - facility_tourist_check_in (no Active attribute and we test it
#     individually with a code filter above)
#   - tourist_agency (potentially huge; test_lookup_tourist_agency_filtered
#     covers a filtered call)
SAFE_SMALL_LOOKUPS = [
    "facility_subcategory",
    "distance",
    "cash_desk",
    "visa_type",
]


@pytest.mark.parametrize("lookup_name", SAFE_SMALL_LOOKUPS)
async def test_safe_lookup_returns_list(live_client, lookup_name: str) -> None:
    """Each documented obveznik-accessible lookup returns a list of dicts."""
    records = await live_client.lookups.fetch(lookup_name)
    assert isinstance(records, list)
    for r in records[:5]:
        assert "ID" in r
        assert "Name" in r or "CodeMI" in r or "Code" in r
