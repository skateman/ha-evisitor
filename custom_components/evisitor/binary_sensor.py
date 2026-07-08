"""binary_sensor entities -- one per mapped HA person, true while checked in."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from pyevisitor.encoding import from_dotnet_date

from .const import DOMAIN
from .coordinator import EVisitorCoordinator


def _dt_or_none(value: Any) -> datetime | None:
    """Parse an eVisitor ``/Date(...)/`` string to a datetime, else None."""
    if not value or not isinstance(value, str):
        return None
    try:
        return from_dotnet_date(value)
    except (ValueError, TypeError):
        return None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coord: EVisitorCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(_make_entities(coord), update_before_add=False)

    @callback
    def _resync(_=None) -> None:
        async_add_entities(
            _make_entities(coord, skip=set(_existing_unique_ids(hass, entry))),
            update_before_add=False,
        )

    entry.async_on_unload(coord.async_add_listener(_resync))


def _make_entities(
    coord: EVisitorCoordinator, *, skip: set[str] | None = None
) -> list["EVisitorPersonCheckedIn"]:
    skip = skip or set()
    out: list[EVisitorPersonCheckedIn] = []
    for person in coord.person_map:
        unique_id = f"{coord.entry.entry_id}::{person}::checked_in"
        if unique_id in skip:
            continue
        out.append(EVisitorPersonCheckedIn(coord, person, unique_id))
    return out


def _existing_unique_ids(hass: HomeAssistant, entry: ConfigEntry) -> list[str]:
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    return [
        e.unique_id
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.platform == DOMAIN and e.domain == "binary_sensor"
    ]


class EVisitorPersonCheckedIn(
    CoordinatorEntity[EVisitorCoordinator], BinarySensorEntity
):
    """True while the mapped HA person has an active prijava in eVisitor."""

    _attr_has_entity_name = True
    _attr_translation_key = "person_checked_in"

    def __init__(
        self,
        coord: EVisitorCoordinator,
        person_entity_id: str,
        unique_id: str,
    ) -> None:
        super().__init__(coord)
        self._person = person_entity_id
        self._attr_unique_id = unique_id
        slug = person_entity_id.split(".", 1)[-1]
        self._attr_name = f"{slug} checked in"

    @property
    def is_on(self) -> bool:
        return self.coordinator.find_active_stay_for_person(self._person) is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        stay = self.coordinator.find_active_stay_for_person(self._person)
        if stay is None:
            return None
        # eVisitor splits each timestamp across two fields: a date-only
        # ``StayFrom`` / ``ForeseenStayUntil`` (whose time component is
        # always 00:00) and a time-bearing ``TimeStayFrom`` /
        # ``TimeEstimatedStayUntil`` that carries the real wall clock.
        # Expose the time-bearing field (parsed to a tz-aware datetime)
        # so the attribute reflects the actual check-in / foreseen
        # check-out moment instead of always reading as midnight. Fall
        # back to the date-only field for older records that predate the
        # time component. Mirrors the calendar entity's ``_live_event``.
        return {
            "check_in_id": stay.get("ID"),
            "stay_from": _dt_or_none(
                stay.get("TimeStayFrom") or stay.get("StayFrom")
            ),
            "foreseen_stay_until": _dt_or_none(
                stay.get("TimeEstimatedStayUntil") or stay.get("ForeseenStayUntil")
            ),
            "facility": stay.get("FacilityName"),
        }
