"""Tests for the persistent calendar archive (v0.5.0)."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.evisitor.archive import (
    ARCHIVE_SCHEMA_VERSION,
    EvisitorArchive,
)
from custom_components.evisitor.const import (
    CONF_API_KEY,
    CONF_ENVIRONMENT,
    CONF_FACILITY_CODE,
    CONF_PASSWORD,
    CONF_PERSON_MAP,
    CONF_USERNAME,
    DOMAIN,
    KEY_CHECK_IN_ID_SEED,
    SERVICE_PURGE_CALENDAR_ARCHIVE,
    SERVICE_REBUILD_CALENDAR_ARCHIVE,
)

PERSON_ENTITY = "person.demo_user"
SEED = "stay-1"
PERSON_OPTIONS = {KEY_CHECK_IN_ID_SEED: SEED}


def _entry(extra_options: dict | None = None) -> MockConfigEntry:
    options = {CONF_PERSON_MAP: {PERSON_ENTITY: PERSON_OPTIONS}}
    if extra_options:
        options.update(extra_options)
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENVIRONMENT: "production",
            CONF_USERNAME: "u",
            CONF_PASSWORD: "p",
            CONF_API_KEY: "",
            CONF_FACILITY_CODE: "0000001",
        },
        options=options,
    )


async def _setup(
    hass: HomeAssistant, client
) -> MockConfigEntry:
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(
        "custom_components.evisitor.coordinator.EVisitorClient",
        return_value=client,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_first_poll_archives_checked_out_stays(
    hass: HomeAssistant, fake_client_factory, fake_unique_guests
) -> None:
    """The fixture's two checked-out guests must land in the archive
    after the first coordinator poll. This is the implicit backfill."""
    client = fake_client_factory()
    entry = await _setup(hass, client)
    coord = hass.data[DOMAIN][entry.entry_id]

    # Both fixture guests have CheckedOutTourist=True -> both archived.
    archived_uids = coord.archive.uids()
    assert archived_uids == {
        fake_unique_guests[0].latest["ID"],
        fake_unique_guests[1].latest["ID"],
    }

    # Persisted shape carries only the four calendar-event fields.
    stored = coord.archive.get(fake_unique_guests[0].latest["ID"])
    assert stored is not None
    assert set(stored) == {"summary", "start", "end", "location"}
    assert stored["summary"] == fake_unique_guests[0].latest["SurnameAndName"]
    assert stored["location"] == "Test House"

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_active_stay_not_archived(
    hass: HomeAssistant, fake_unique_guests, fake_client_factory
) -> None:
    """A still-active stay (CheckedOutTourist=False) must NOT be archived."""
    guests = copy.deepcopy(fake_unique_guests)
    guests[0].latest["CheckedOutTourist"] = False  # Novák Marek = active

    client = fake_client_factory()
    client.guests.unique.return_value = guests
    client.guests.stays.return_value = [g.latest for g in guests]

    entry = await _setup(hass, client)
    coord = hass.data[DOMAIN][entry.entry_id]

    # Only Nováková Eva (still checked-out) is in the archive.
    assert coord.archive.uids() == {guests[1].latest["ID"]}

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_archive_idempotent_on_second_poll(
    hass: HomeAssistant, fake_client_factory
) -> None:
    """A second poll with identical data must not flip the dirty flag."""
    client = fake_client_factory()
    entry = await _setup(hass, client)
    coord = hass.data[DOMAIN][entry.entry_id]

    # First poll has already happened during setup. Trigger another.
    assert not coord.archive.dirty
    await coord.async_refresh()
    await hass.async_block_till_done()
    # No new content -> nothing to write.
    assert not coord.archive.dirty

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_calendar_shows_archive_even_when_live_is_empty(
    hass: HomeAssistant, fake_unique_guests, fake_client_factory
) -> None:
    """If the live tier later returns nothing (e.g. eVisitor stops
    returning historical stays), the calendar still shows archived
    events from previous polls."""
    from custom_components.evisitor.calendar import EVisitorFacilityCalendar

    client = fake_client_factory()
    entry = await _setup(hass, client)
    coord = hass.data[DOMAIN][entry.entry_id]

    # Sanity: archive populated from the initial poll.
    initial_uids = coord.archive.uids()
    assert len(initial_uids) == 2

    # Simulate the live tier going empty (HA restart + eVisitor trim).
    coord.data["all_stays"] = []

    cal = EVisitorFacilityCalendar(coord)
    events = await cal.async_get_events(
        hass,
        datetime(2020, 1, 1, tzinfo=timezone.utc),
        datetime(2040, 1, 1, tzinfo=timezone.utc),
    )
    # Two archived events still surface.
    assert {e.uid for e in events} == initial_uids
    assert {e.summary for e in events} == {
        g.latest["SurnameAndName"] for g in fake_unique_guests
    }

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_calendar_live_wins_over_archive_on_uid_collision(
    hass: HomeAssistant, fake_unique_guests, fake_client_factory
) -> None:
    """When a uid exists in both tiers, the live (fresh) shape wins.

    Guards the case where a stay's end-date moves (extension) and the
    live tier reflects it before the archive does."""
    from custom_components.evisitor.calendar import EVisitorFacilityCalendar

    client = fake_client_factory()
    entry = await _setup(hass, client)
    coord = hass.data[DOMAIN][entry.entry_id]

    target_uid = fake_unique_guests[0].latest["ID"]

    # Tamper the archive entry's summary -- live snapshot still has the
    # original summary, so the calendar should show that one.
    coord.archive.upsert(
        target_uid,
        {
            **coord.archive.get(target_uid),
            "summary": "OLD ARCHIVED VALUE",
        },
    )

    cal = EVisitorFacilityCalendar(coord)
    events = await cal.async_get_events(
        hass,
        datetime(2020, 1, 1, tzinfo=timezone.utc),
        datetime(2040, 1, 1, tzinfo=timezone.utc),
    )
    by_uid = {e.uid: e for e in events}
    assert by_uid[target_uid].summary == fake_unique_guests[0].latest["SurnameAndName"]

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_cancellation_removes_uid_from_archive(
    hass: HomeAssistant, fake_unique_guests, fake_client_factory
) -> None:
    """A uid that turns up in TouristCancelledBrowse must be evicted
    from the archive on the next sync."""
    client = fake_client_factory()
    entry = await _setup(hass, client)
    coord = hass.data[DOMAIN][entry.entry_id]

    novak_uid = fake_unique_guests[0].latest["ID"]
    assert novak_uid in coord.archive.uids()

    # Pretend Novák Marek's prijava got cancelled out-of-band.
    client.browses.list_cancelled_tourists.return_value = {
        "Records": [{"ID": novak_uid}]
    }
    await coord.async_refresh()
    await hass.async_block_till_done()

    assert novak_uid not in coord.archive.uids()
    # The other guest is unaffected.
    assert fake_unique_guests[1].latest["ID"] in coord.archive.uids()

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_purge_service_clears_archive(
    hass: HomeAssistant, fake_client_factory
) -> None:
    client = fake_client_factory()
    entry = await _setup(hass, client)
    coord = hass.data[DOMAIN][entry.entry_id]

    assert coord.archive.uids()  # populated by setup

    await hass.services.async_call(
        DOMAIN,
        SERVICE_PURGE_CALENDAR_ARCHIVE,
        {},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert coord.archive.uids() == set()

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_rebuild_service_repopulates_archive(
    hass: HomeAssistant, fake_client_factory
) -> None:
    """Rebuild = purge + immediate refresh. Archive ends up full again."""
    client = fake_client_factory()
    entry = await _setup(hass, client)
    coord = hass.data[DOMAIN][entry.entry_id]

    initial = coord.archive.uids()
    assert initial  # populated

    await hass.services.async_call(
        DOMAIN,
        SERVICE_REBUILD_CALENDAR_ARCHIVE,
        {},
        blocking=True,
    )
    await hass.async_block_till_done()

    # Same uids end up archived (the mock returns the same fixture).
    assert coord.archive.uids() == initial

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_legacy_v040_residue_is_removed_on_load(
    hass: HomeAssistant, hass_storage, fake_client_factory
) -> None:
    """If the user briefly ran v0.4.0, an ``evisitor_calendar_<entry>``
    file may exist in .storage. The v0.5.0 archive loader deletes it
    so the new archive (different key) can take over cleanly."""
    entry = _entry()
    entry.add_to_hass(hass)

    legacy_key = f"evisitor_calendar_{entry.entry_id}"
    # Pre-seed the legacy store via the in-memory storage fixture.
    hass_storage[legacy_key] = {
        "version": 1,
        "key": legacy_key,
        "data": {"events": {"old-uid": {"summary": "stale"}}},
    }
    assert legacy_key in hass_storage

    client = fake_client_factory()
    with patch(
        "custom_components.evisitor.coordinator.EVisitorClient",
        return_value=client,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # Legacy file is gone.
    assert legacy_key not in hass_storage

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_archive_persists_across_coordinator_reloads(
    hass: HomeAssistant, fake_client_factory
) -> None:
    """Archive contents survive a config-entry reload (which rebuilds
    the coordinator). This is the key win of persistence vs. v0.4.1."""
    client = fake_client_factory()
    entry = await _setup(hass, client)
    coord = hass.data[DOMAIN][entry.entry_id]
    first_uids = coord.archive.uids()
    assert first_uids

    # Reload the entry. Coordinator is rebuilt; archive should load
    # its existing on-disk contents.
    client2 = fake_client_factory()
    # Live tier returns nothing this time (simulates eVisitor trim).
    client2.guests.stays.return_value = []
    client2.guests.unique.return_value = []
    with patch(
        "custom_components.evisitor.coordinator.EVisitorClient",
        return_value=client2,
    ):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    coord2 = hass.data[DOMAIN][entry.entry_id]
    assert coord2 is not coord  # new coordinator instance
    assert coord2.archive.uids() == first_uids

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# Unit-level tests for EvisitorArchive itself (no HA setup needed)
# ---------------------------------------------------------------------------


async def test_archive_upsert_idempotent(hass: HomeAssistant) -> None:
    archive = EvisitorArchive(hass, "unit-1")
    await archive.async_load()

    event = {
        "summary": "Test Guest",
        "start": "2026-05-01T12:00:00+02:00",
        "end": "2026-05-03T10:00:00+02:00",
        "location": "Facility",
    }
    assert archive.upsert("uid-1", event) is True
    assert archive.dirty
    await archive.async_save()
    assert not archive.dirty

    # Re-upserting the same shape is a no-op.
    assert archive.upsert("uid-1", event) is False
    assert not archive.dirty


async def test_archive_normalisation_drops_extra_fields(hass: HomeAssistant) -> None:
    """Only the four calendar-event fields land on disk; everything
    else (PII!) is silently dropped during upsert."""
    archive = EvisitorArchive(hass, "unit-2")
    await archive.async_load()

    archive.upsert(
        "uid-1",
        {
            "summary": "Guest",
            "start": "2026-05-01T12:00:00+02:00",
            "end": "2026-05-03T10:00:00+02:00",
            "location": "Facility",
            # All of the below MUST be dropped.
            "date_of_birth": "1985-01-15",
            "document_number": "XX111111",
            "address": "Some street 5",
            "citizenship": "SVK",
            "telephone": "+421...",
        },
    )
    stored = archive.get("uid-1")
    assert set(stored) == {"summary", "start", "end", "location"}


async def test_archive_events_in_range_filters_correctly(
    hass: HomeAssistant,
) -> None:
    archive = EvisitorArchive(hass, "unit-3")
    await archive.async_load()
    archive.upsert(
        "may",
        {
            "summary": "May",
            "start": "2026-05-01T12:00:00+02:00",
            "end": "2026-05-03T10:00:00+02:00",
            "location": "Facility",
        },
    )
    archive.upsert(
        "jul",
        {
            "summary": "Jul",
            "start": "2026-07-01T12:00:00+02:00",
            "end": "2026-07-03T10:00:00+02:00",
            "location": "Facility",
        },
    )

    june = archive.events_in_range(
        datetime(2026, 6, 1, tzinfo=timezone.utc),
        datetime(2026, 6, 30, tzinfo=timezone.utc),
    )
    assert june == []

    may_only = archive.events_in_range(
        datetime(2026, 5, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 31, tzinfo=timezone.utc),
    )
    assert [uid for uid, _ in may_only] == ["may"]


def test_archive_schema_version_stable() -> None:
    """Schema version must not change without a migration path."""
    assert ARCHIVE_SCHEMA_VERSION == 1
