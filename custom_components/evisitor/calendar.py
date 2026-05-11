"""calendar entity -- one per facility, listing active prijave as events."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from pyevisitor.encoding import from_dotnet_date

from .const import DOMAIN
from .coordinator import EVisitorCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coord: EVisitorCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EVisitorFacilityCalendar(coord)])


class EVisitorFacilityCalendar(
    CoordinatorEntity[EVisitorCoordinator], CalendarEntity
):
    """A calendar of currently checked-in guests at the facility.

    Events are derived from the coordinator's in-memory snapshot of
    ``ListOfTouristsExtended``; nothing is persisted to .storage.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "facility_calendar"

    def __init__(self, coord: EVisitorCoordinator) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{coord.entry.entry_id}::calendar"
        self._attr_name = "Guests"

    # --- CalendarEntity API ------------------------------------------------

    @property
    def event(self) -> CalendarEvent | None:
        events = self._all_events()
        if not events:
            return None
        # Pick the soonest active or upcoming event.
        now = datetime.now().astimezone()
        future = [e for e in events if e.end >= now]
        if future:
            future.sort(key=lambda e: e.start)
            return future[0]
        events.sort(key=lambda e: e.start)
        return events[-1]

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        out: list[CalendarEvent] = []
        for event in self._all_events():
            if event.end < start_date or event.start > end_date:
                continue
            out.append(event)
        return out

    # --- internal ---------------------------------------------------------

    def _all_events(self) -> list[CalendarEvent]:
        active = (self.coordinator.data or {}).get("active_stays") or []
        events: list[CalendarEvent] = []
        for stay in active:
            start = _dt_or_none(stay.get("TimeStayFrom") or stay.get("StayFrom"))
            end = _dt_or_none(
                stay.get("TimeEstimatedStayUntil") or stay.get("ForeseenStayUntil")
            )
            if start is None or end is None or end <= start:
                continue
            summary = stay.get("SurnameAndName") or "Guest"
            events.append(
                CalendarEvent(
                    summary=summary,
                    start=start,
                    end=end,
                    description=None,  # PII-by-default off
                    location=stay.get("FacilityName"),
                    uid=str(stay.get("ID")) if stay.get("ID") else None,
                )
            )
        return events


def _dt_or_none(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return from_dotnet_date(value)
    except (ValueError, TypeError):
        return None
