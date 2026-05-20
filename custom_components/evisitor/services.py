"""Service handlers for the eVisitor integration."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from pyevisitor import EVisitorError

from .const import (
    DOMAIN,
    EVENT_CHECK_IN_FAILED,
    EVENT_CHECK_IN_SUCCEEDED,
    EVENT_CHECK_OUT_FAILED,
    EVENT_CHECK_OUT_SUCCEEDED,
    EVENT_EXTEND_FAILED,
    EVENT_EXTEND_SUCCEEDED,
    SERVICE_CANCEL_CHECK_IN,
    SERVICE_CHECK_IN_PERSON,
    SERVICE_CHECK_OUT_PERSON,
    SERVICE_EXTEND_STAY,
    SERVICE_PURGE_CALENDAR_ARCHIVE,
    SERVICE_REBUILD_CALENDAR_ARCHIVE,
)
from .coordinator import EVisitorCoordinator

_LOGGER = logging.getLogger(__name__)

ATTR_PERSON = "person"
ATTR_FORESEEN_STAY_UNTIL = "foreseen_stay_until"
ATTR_STAY_DAYS = "stay_days"
ATTR_STAY_FROM = "stay_from"
ATTR_REASON = "reason"
ATTR_CHECK_OUT_AT = "check_out_at"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"


_PERSON_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_PERSON): cv.entity_id,
    },
    extra=vol.ALLOW_EXTRA,
)


def _coordinator_for(
    hass: HomeAssistant, person_entity_id: str
) -> EVisitorCoordinator:
    for coord in hass.data.get(DOMAIN, {}).values():
        if person_entity_id in coord.person_map:
            return coord
    raise ServiceValidationError(
        f"No eVisitor integration is configured for {person_entity_id!r}"
    )


def _coordinators(
    hass: HomeAssistant, entry_id: str | None
) -> list[EVisitorCoordinator]:
    """Resolve targeted coordinators.

    With no ``entry_id`` the service operates on every loaded entry;
    typical setups have a single entry, so this is the natural default.
    """
    by_entry: dict[str, EVisitorCoordinator] = hass.data.get(DOMAIN, {})
    if entry_id is None:
        return list(by_entry.values())
    coord = by_entry.get(entry_id)
    if coord is None:
        raise ServiceValidationError(
            f"No loaded eVisitor entry with id {entry_id!r}"
        )
    return [coord]


def _fire(
    hass: HomeAssistant,
    event: str,
    person: str,
    *,
    check_in_id: str | None = None,
    error: str | None = None,
) -> None:
    payload: dict[str, Any] = {ATTR_PERSON: person}
    if check_in_id is not None:
        payload["check_in_id"] = check_in_id
    if error is not None:
        payload["error"] = error
    hass.bus.async_fire(event, payload)


def async_register_services(hass: HomeAssistant) -> None:
    """Register the four eVisitor services. Idempotent."""

    if hass.services.has_service(DOMAIN, SERVICE_CHECK_IN_PERSON):
        return

    async def _check_in(call: ServiceCall) -> None:
        person = call.data[ATTR_PERSON]
        coord = _coordinator_for(hass, person)
        try:
            new_id = await coord.check_in_person(
                person,
                foreseen_stay_until=call.data.get(ATTR_FORESEEN_STAY_UNTIL),
                stay_from=call.data.get(ATTR_STAY_FROM),
            )
        except EVisitorError as err:
            _fire(hass, EVENT_CHECK_IN_FAILED, person, error=str(err))
            raise HomeAssistantError(str(err)) from err
        _fire(hass, EVENT_CHECK_IN_SUCCEEDED, person, check_in_id=new_id)

    async def _check_out(call: ServiceCall) -> None:
        person = call.data[ATTR_PERSON]
        coord = _coordinator_for(hass, person)
        try:
            check_in_id = await coord.check_out_person(
                person,
                check_out_at=call.data.get(ATTR_CHECK_OUT_AT),
            )
        except EVisitorError as err:
            _fire(hass, EVENT_CHECK_OUT_FAILED, person, error=str(err))
            raise HomeAssistantError(str(err)) from err
        _fire(hass, EVENT_CHECK_OUT_SUCCEEDED, person, check_in_id=check_in_id)

    async def _cancel(call: ServiceCall) -> None:
        person = call.data[ATTR_PERSON]
        coord = _coordinator_for(hass, person)
        try:
            check_in_id = await coord.cancel_check_in_person(
                person, reason=call.data.get(ATTR_REASON)
            )
        except EVisitorError as err:
            _fire(hass, EVENT_CHECK_IN_FAILED, person, error=str(err))
            raise HomeAssistantError(str(err)) from err
        _fire(hass, EVENT_CHECK_IN_SUCCEEDED, person, check_in_id=check_in_id)

    async def _extend(call: ServiceCall) -> None:
        person = call.data[ATTR_PERSON]
        until: datetime | None = call.data.get(ATTR_FORESEEN_STAY_UNTIL)
        stay_days = call.data.get(ATTR_STAY_DAYS)
        if until is not None and stay_days is not None:
            raise ServiceValidationError(
                f"Provide either {ATTR_FORESEEN_STAY_UNTIL!r} or "
                f"{ATTR_STAY_DAYS!r}, not both."
            )
        coord = _coordinator_for(hass, person)
        try:
            check_in_id = await coord.extend_stay(
                person,
                foreseen_stay_until=until,
                stay_days=int(stay_days) if stay_days is not None else None,
            )
        except EVisitorError as err:
            _fire(hass, EVENT_EXTEND_FAILED, person, error=str(err))
            raise HomeAssistantError(str(err)) from err
        _fire(hass, EVENT_EXTEND_SUCCEEDED, person, check_in_id=check_in_id)

    schema_check_in = _PERSON_SCHEMA.extend(
        {
            vol.Optional(ATTR_FORESEEN_STAY_UNTIL): cv.datetime,
            vol.Optional(ATTR_STAY_FROM): cv.datetime,
        }
    )
    schema_check_out = _PERSON_SCHEMA.extend(
        {vol.Optional(ATTR_CHECK_OUT_AT): cv.datetime}
    )
    schema_cancel = _PERSON_SCHEMA.extend(
        {vol.Optional(ATTR_REASON): cv.string}
    )
    schema_extend = _PERSON_SCHEMA.extend(
        {
            vol.Optional(ATTR_FORESEEN_STAY_UNTIL): cv.datetime,
            vol.Optional(ATTR_STAY_DAYS): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=30)
            ),
        }
    )
    schema_archive = vol.Schema(
        {vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string},
        extra=vol.ALLOW_EXTRA,
    )

    async def _purge_archive(call: ServiceCall) -> None:
        entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        for coord in _coordinators(hass, entry_id):
            if coord.archive.purge():
                await coord.archive.async_save()
            # Refresh listeners so the calendar redraws.
            coord.async_update_listeners()

    async def _rebuild_archive(call: ServiceCall) -> None:
        entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        for coord in _coordinators(hass, entry_id):
            if coord.archive.purge():
                await coord.archive.async_save()
            # Refetch from eVisitor; the regular sync step in
            # _async_update_data will repopulate the archive from the
            # checked-out subset of the fresh snapshot.
            await coord.async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_CHECK_IN_PERSON, _check_in, schema=schema_check_in)
    hass.services.async_register(DOMAIN, SERVICE_CHECK_OUT_PERSON, _check_out, schema=schema_check_out)
    hass.services.async_register(DOMAIN, SERVICE_CANCEL_CHECK_IN, _cancel, schema=schema_cancel)
    hass.services.async_register(DOMAIN, SERVICE_EXTEND_STAY, _extend, schema=schema_extend)
    hass.services.async_register(
        DOMAIN, SERVICE_PURGE_CALENDAR_ARCHIVE, _purge_archive, schema=schema_archive
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REBUILD_CALENDAR_ARCHIVE, _rebuild_archive, schema=schema_archive
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    for name in (
        SERVICE_CHECK_IN_PERSON,
        SERVICE_CHECK_OUT_PERSON,
        SERVICE_CANCEL_CHECK_IN,
        SERVICE_EXTEND_STAY,
        SERVICE_PURGE_CALENDAR_ARCHIVE,
        SERVICE_REBUILD_CALENDAR_ARCHIVE,
    ):
        hass.services.async_remove(DOMAIN, name)
