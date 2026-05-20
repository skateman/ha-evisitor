"""calendar entity -- one per facility, listing guest prijave as events.

Combines two data sources:

1. **Live tier** -- the coordinator's in-memory ``all_stays`` snapshot,
   refreshed on every poll. Carries the freshest shape for currently
   visible prijave (including any still-foreseen end-dates that may move
   as stays get extended).

2. **Archive tier** -- the persistent on-disk dump of past check-outs
   (see :mod:`custom_components.evisitor.archive`). Survives Home
   Assistant restarts and defends against future eVisitor changes that
   might trim ``ListOfTouristsExtended``.

Live wins on uid collision, so a stay that's still in the live snapshot
displays its current shape rather than the snapshot we archived
earlier.
"""

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
    """A calendar of currently checked-in and historical guests."""

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
        """Return live + archived events, live winning on uid collision.

        Reading ``all_stays`` (rather than ``active_stays``) keeps
        checked-out guests visible until the next coordinator refresh
        archives them; the archive tier then keeps showing them long
        after the live tier moves on.
        """
        events: dict[str | None, CalendarEvent] = {}

        # Archive tier first: live entries (added after) overwrite on
        # uid collision, which is the desired "live wins" semantics.
        for uid, archived in self.coordinator.archive.items():
            event = _archive_event(uid, archived)
            if event is not None:
                events[uid] = event

        stays = (self.coordinator.data or {}).get("all_stays") or []
        for stay in stays:
            event = _live_event(stay)
            if event is not None:
                events[event.uid] = event

        return list(events.values())


def _live_event(stay: dict[str, Any]) -> CalendarEvent | None:
    start = _dt_or_none(stay.get("TimeStayFrom") or stay.get("StayFrom"))
    end = _dt_or_none(
        stay.get("TimeEstimatedStayUntil") or stay.get("ForeseenStayUntil")
    )
    if start is None or end is None or end <= start:
        return None
    return CalendarEvent(
        summary=stay.get("SurnameAndName") or "Guest",
        start=start,
        end=end,
        description=None,  # PII-by-default off
        location=stay.get("FacilityName"),
        uid=str(stay.get("ID")) if stay.get("ID") else None,
    )


def _archive_event(uid: str, archived: dict[str, Any]) -> CalendarEvent | None:
    start = _iso_or_none(archived.get("start"))
    end = _iso_or_none(archived.get("end"))
    if start is None or end is None or end <= start:
        return None
    return CalendarEvent(
        summary=archived.get("summary") or "Guest",
        start=start,
        end=end,
        description=None,
        location=archived.get("location"),
        uid=uid,
    )


def _dt_or_none(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return from_dotnet_date(value)
    except (ValueError, TypeError):
        return None


def _iso_or_none(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None

