"""Smoke tests for the integration setup + entities + services.

Mocks ``pyevisitor.EVisitorClient`` so no network is hit.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.evisitor.const import (
    CONF_API_KEY,
    CONF_ENVIRONMENT,
    CONF_FACILITY_CODE,
    CONF_PASSWORD,
    CONF_PERSON_MAP,
    CONF_USERNAME,
    DOMAIN,
    EVENT_CHECK_IN_FAILED,
    EVENT_CHECK_IN_SUCCEEDED,
    KEY_CHECK_IN_ID_SEED,
    SERVICE_CHECK_IN_PERSON,
)


PERSON_ENTITY = "person.demo_user"
SEED = "stay-1"  # Novák Marek -- matches fake_unique_guests in conftest

PERSON_OPTIONS = {
    "check_in_id_seed": SEED,
}


@pytest.fixture
async def setup_integration(hass: HomeAssistant, fake_client_factory):
    """Set up an entry with one mapped person and a fully mocked client."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENVIRONMENT: "production",
            CONF_USERNAME: "u",
            CONF_PASSWORD: "p",
            CONF_API_KEY: "",
            CONF_FACILITY_CODE: "0000001",
        },
        options={CONF_PERSON_MAP: {PERSON_ENTITY: PERSON_OPTIONS}},
    )
    entry.add_to_hass(hass)

    client = fake_client_factory()
    with patch(
        "custom_components.evisitor.coordinator.EVisitorClient",
        return_value=client,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry, client
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_entry_setup_creates_entities_and_services(
    hass: HomeAssistant, setup_integration
) -> None:
    entry, _client = setup_integration

    # All four services should be registered.
    for svc in (
        "check_in_person",
        "check_out_person",
        "cancel_check_in",
        "extend_stay",
    ):
        assert hass.services.has_service(DOMAIN, svc), f"service {svc} missing"

    # Per-person binary_sensor exists and is OFF (no active stay yet).
    state = hass.states.get(f"binary_sensor.demo_user_checked_in")
    assert state is not None
    assert state.state == STATE_OFF

    # Active-count sensor exists.
    sensor_state = hass.states.get("sensor.evisitor_active_guests") or hass.states.get(
        "sensor.test_house_active_guests"
    )
    # Entity name resolution depends on device naming -- look for any sensor
    # the integration created.
    sensors = [s for s in hass.states.async_all("sensor") if s.entity_id.startswith("sensor.")]
    assert any(s.attributes.get("facility_code") == "0000001" for s in sensors)


async def test_check_in_service_dispatches_and_emits_success_event(
    hass: HomeAssistant, setup_integration
) -> None:
    _entry, client = setup_integration

    received: list = []
    hass.bus.async_listen(EVENT_CHECK_IN_SUCCEEDED, lambda evt: received.append(evt))

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CHECK_IN_PERSON,
        {"person": PERSON_ENTITY},
        blocking=True,
    )

    # The mocked client got the POST.
    assert client.actions.check_in_tourist.await_count == 1
    submitted = client.actions.check_in_tourist.await_args.args[0]
    payload = submitted.to_payload()
    assert payload["Facility"] == "0000001"
    assert payload["TouristSurname"] == "Novák"
    assert payload["TouristName"] == "Marek"
    assert payload["DocumentType"] == "027"
    assert payload["Citizenship"] == "SVK"
    assert payload["TTPaymentCategory"] == "18"
    assert "ID" in payload

    assert received, "expected evisitor_check_in_succeeded event"
    assert received[0].data["person"] == PERSON_ENTITY
    assert received[0].data["check_in_id"] == payload["ID"]


async def test_check_in_failure_emits_failed_event(
    hass: HomeAssistant, setup_integration
) -> None:
    from homeassistant.exceptions import HomeAssistantError

    from pyevisitor import EVisitorValidationError

    _entry, client = setup_integration
    client.actions.check_in_tourist.side_effect = EVisitorValidationError(
        "Turist je već prijavljen u navedenom objektu.", None
    )

    received: list = []
    hass.bus.async_listen(EVENT_CHECK_IN_FAILED, lambda evt: received.append(evt))

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CHECK_IN_PERSON,
            {"person": PERSON_ENTITY},
            blocking=True,
        )

    assert received, "expected evisitor_check_in_failed event"
    assert "već prijavljen" in received[0].data["error"]


async def test_check_in_refreshes_seed_without_reload(
    hass: HomeAssistant, setup_integration
) -> None:
    """Successful check-in updates the persisted seed in-place.

    The integration's update listener must NOT reload the entry when
    only ``check_in_id_seed`` changed -- otherwise every check-in would
    cause a costly mid-flight reload.
    """
    entry, _client = setup_integration

    coord = hass.data[DOMAIN][entry.entry_id]
    setup_call_id = id(coord)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CHECK_IN_PERSON,
        {"person": PERSON_ENTITY},
        blocking=True,
    )
    await hass.async_block_till_done()

    # Same coordinator instance survived the seed write -> no reload happened.
    assert id(hass.data[DOMAIN][entry.entry_id]) == setup_call_id

    # The persisted seed got updated.
    new_seed = entry.options[CONF_PERSON_MAP][PERSON_ENTITY][KEY_CHECK_IN_ID_SEED]
    assert new_seed != SEED  # changed from the initial seed
    # Looks like a UUID.
    import uuid as _uuid

    _uuid.UUID(new_seed)


async def test_remove_person_mapping_triggers_reload(
    hass: HomeAssistant, setup_integration
) -> None:
    """Person add/remove must reload (entities need (de)registration)."""
    entry, _client = setup_integration

    coord_before = hass.data[DOMAIN][entry.entry_id]

    new_options = {**entry.options, CONF_PERSON_MAP: {}}
    hass.config_entries.async_update_entry(entry, options=new_options)
    await hass.async_block_till_done()

    # Reload happened -> coordinator was rebuilt (different instance).
    coord_after = hass.data[DOMAIN].get(entry.entry_id)
    assert coord_after is not None
    assert coord_after is not coord_before


async def test_check_in_uses_settings_for_default_stay_window(
    hass: HomeAssistant, fake_client_factory
) -> None:
    """Coordinator-level settings flow into the auto-derived stay window."""
    from datetime import timedelta

    from custom_components.evisitor.const import (
        CONF_SETTINGS,
        SETTING_CHECK_OUT_TIME,
        SETTING_STAY_DURATION_HOURS,
    )
    from pyevisitor.encoding import from_dotnet_date

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENVIRONMENT: "production",
            CONF_USERNAME: "u",
            CONF_PASSWORD: "p",
            CONF_API_KEY: "",
            CONF_FACILITY_CODE: "0000001",
        },
        options={
            CONF_PERSON_MAP: {PERSON_ENTITY: PERSON_OPTIONS},
            CONF_SETTINGS: {
                # 72 h / 11:00 -- different from the integration defaults.
                SETTING_STAY_DURATION_HOURS: 72,
                SETTING_CHECK_OUT_TIME: "11:00",
            },
        },
    )
    entry.add_to_hass(hass)

    client = fake_client_factory()
    with patch(
        "custom_components.evisitor.coordinator.EVisitorClient",
        return_value=client,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        await hass.services.async_call(
            DOMAIN,
            SERVICE_CHECK_IN_PERSON,
            {"person": PERSON_ENTITY},
            blocking=True,
        )

        submitted = client.actions.check_in_tourist.await_args.args[0]
        payload = submitted.to_payload()
        # ForeseenStayUntil should be StayFrom + 3 days, time = 11:00.
        from datetime import datetime, timedelta

        stay_from = datetime.strptime(payload["StayFrom"], "%Y%m%d").date()
        until = datetime.strptime(payload["ForeseenStayUntil"], "%Y%m%d").date()
        assert (until - stay_from) == timedelta(days=3)
        assert payload["TimeEstimatedStayUntil"] == "11:00"

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_check_in_recovers_after_manual_cancel(
    hass: HomeAssistant, fake_client_factory
) -> None:
    """If the seeded prijava was cancelled out-of-band, the integration
    falls back to ``TouristCancelledBrowse`` to recover the guest by
    identity, self-heals the seed, and proceeds with the check-in."""
    # Seed points at a stale UUID that's NOT in unique() (simulating a
    # manually-cancelled prijava).
    stale_seed = "00000000-0000-0000-0000-stalecanceled"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENVIRONMENT: "production",
            CONF_USERNAME: "u",
            CONF_PASSWORD: "p",
            CONF_API_KEY: "",
            CONF_FACILITY_CODE: "0000001",
        },
        options={
            CONF_PERSON_MAP: {
                PERSON_ENTITY: {KEY_CHECK_IN_ID_SEED: stale_seed}
            }
        },
    )
    entry.add_to_hass(hass)

    client = fake_client_factory()
    # TouristCancelledBrowse lookup returns the cancelled prijava with
    # the same identity tuple as Novák Marek in unique().
    client.browses.get_cancelled_tourist_by_id.return_value = {
        "ID": stale_seed,
        "Tourist": "Novák Marek",
        "DatePlaceOfBirth": "15.01.1985 (40) Bratislava Slovačka Republika",
    }

    with patch(
        "custom_components.evisitor.coordinator.EVisitorClient",
        return_value=client,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        await hass.services.async_call(
            DOMAIN,
            SERVICE_CHECK_IN_PERSON,
            {"person": PERSON_ENTITY},
            blocking=True,
        )
        await hass.async_block_till_done()

        # Fallback ran exactly once during the check-in.
        assert client.browses.get_cancelled_tourist_by_id.await_count == 1
        # Check-in itself succeeded.
        assert client.actions.check_in_tourist.await_count == 1
        # Seed was self-healed: it's no longer the stale one and isn't None.
        new_seed = entry.options[CONF_PERSON_MAP][PERSON_ENTITY][KEY_CHECK_IN_ID_SEED]
        assert new_seed != stale_seed
        assert new_seed

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_check_in_fails_loudly_when_seed_unrecoverable(
    hass: HomeAssistant, fake_client_factory
) -> None:
    """If the seed is gone from BOTH unique() and cancelled, fail loudly."""
    from homeassistant.exceptions import HomeAssistantError

    stale_seed = "00000000-0000-0000-0000-completelygone"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENVIRONMENT: "production",
            CONF_USERNAME: "u",
            CONF_PASSWORD: "p",
            CONF_API_KEY: "",
            CONF_FACILITY_CODE: "0000001",
        },
        options={
            CONF_PERSON_MAP: {
                PERSON_ENTITY: {KEY_CHECK_IN_ID_SEED: stale_seed}
            }
        },
    )
    entry.add_to_hass(hass)

    client = fake_client_factory()
    # Cancelled lookup also returns nothing.
    client.browses.get_cancelled_tourist_by_id.return_value = None

    received: list = []
    hass.bus.async_listen(EVENT_CHECK_IN_FAILED, lambda evt: received.append(evt))

    with patch(
        "custom_components.evisitor.coordinator.EVisitorClient",
        return_value=client,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_CHECK_IN_PERSON,
                {"person": PERSON_ENTITY},
                blocking=True,
            )

        # No POST happened.
        assert client.actions.check_in_tourist.await_count == 0
        # Failed event fired with a clear message.
        assert received
        assert "re-map" in received[0].data["error"]

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_extend_stay_with_stay_days_builds_window(
    hass: HomeAssistant, fake_unique_guests, fake_client_factory
) -> None:
    """``extend_stay(person, stay_days=N)`` => ForeseenStayUntil = today + N
    days at the integration's default check-out time, re-using the active
    prijava ID."""
    import copy
    from datetime import datetime, timedelta

    from homeassistant.util import dt as dt_util

    from custom_components.evisitor.const import (
        CONF_SETTINGS,
        SETTING_CHECK_OUT_TIME,
    )

    # Flip Novák Marek to currently-active so extend_stay finds him.
    guests = copy.deepcopy(fake_unique_guests)
    guests[0].latest["CheckedOutTourist"] = False
    # Bump StayFrom to a recent date so the stay is "active" by date too.
    now = dt_util.now()
    yesterday_ts = int((now - timedelta(days=1)).timestamp() * 1000)
    guests[0].latest["StayFrom"] = f"/Date({yesterday_ts}+0100)/"
    guests[0].latest["DateTimeOfArrival"] = f"/Date({yesterday_ts}+0100)/"
    guests[0].latest["ForeseenStayUntil"] = f"/Date({yesterday_ts + 86400000}+0100)/"

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENVIRONMENT: "production",
            CONF_USERNAME: "u",
            CONF_PASSWORD: "p",
            CONF_API_KEY: "",
            CONF_FACILITY_CODE: "0000001",
        },
        options={
            CONF_PERSON_MAP: {PERSON_ENTITY: PERSON_OPTIONS},
            CONF_SETTINGS: {SETTING_CHECK_OUT_TIME: "11:00"},
        },
    )
    entry.add_to_hass(hass)

    client = fake_client_factory()
    client.guests.unique.return_value = guests
    client.guests.stays.return_value = [g.latest for g in guests]
    client.browses.list_tourists.return_value = [g.latest for g in guests]

    with patch(
        "custom_components.evisitor.coordinator.EVisitorClient",
        return_value=client,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        await hass.services.async_call(
            DOMAIN,
            "extend_stay",
            {"person": PERSON_ENTITY, "stay_days": 4},
            blocking=True,
        )

        submitted = client.actions.check_in_tourist.await_args.args[0]
        payload = submitted.to_payload()
        # Same ID re-used (not a new check-in).
        assert payload["ID"] == "stay-1"
        # Today + 4 days @ 11:00 local.
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        expected = midnight + timedelta(days=4)
        assert payload["ForeseenStayUntil"] == expected.strftime("%Y%m%d")
        assert payload["TimeEstimatedStayUntil"] == "11:00"

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_extend_stay_rejects_both_params(
    hass: HomeAssistant, setup_integration
) -> None:
    """Service raises if caller passes both foreseen_stay_until and stay_days."""
    from datetime import datetime, timedelta

    from homeassistant.exceptions import ServiceValidationError

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "extend_stay",
            {
                "person": PERSON_ENTITY,
                "foreseen_stay_until": datetime.now() + timedelta(days=2),
                "stay_days": 2,
            },
            blocking=True,
        )
