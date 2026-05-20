"""Calendar entity -- shows checked-in guests, optionally persisted.

The calendar always reads live data from the coordinator's last poll
(``all_stays`` -- everything ``ListOfTouristsExtended`` returned, not
just currently-checked-in). When the ``persist_calendar_history``
setting is enabled, each refresh also archives a minimal record per
prijava to ``.storage/evisitor_calendar_<entry_id>`` so:

* checked-out guests stay visible across HA restarts,
* eventually-archived eVisitor records still show in HA's calendar UI.

The archived record only contains what the calendar surfaces in the UI
already (name + age via ``SurnameAndName``, start/end, facility name,
opaque prijava UUID). All other PII (DOB, document number, address,
citizenship, etc.) stays out of disk -- same as without persistence.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from pyevisitor.encoding import from_dotnet_date

from .const import (
    DEFAULT_PERSIST_CALENDAR,
    DOMAIN,
    SETTING_PERSIST_CALENDAR,
)
from .coordinator import EVisitorCoordinator

# Bump when the persisted shape changes.
_STORAGE_VERSION = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coord: EVisitorCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EVisitorFacilityCalendar(coord)])


class EVisitorFacilityCalendar(
    CoordinatorEntity[EVisitorCoordinator], CalendarEntity
):
    """A calendar of guests at the facility, past and present.

    Live data (from the coordinator's last poll) drives currently-active
    and recently-fetched events. When the ``persist_calendar_history``
    setting is enabled, an opt-in disk archive supplements the live view
    with previously-seen prijave that have since aged out of eVisitor.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "facility_calendar"

    def __init__(self, coord: EVisitorCoordinator) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{coord.entry.entry_id}::calendar"
        self._attr_name = "Guests"
        # uid -> {"summary", "start", "end", "location"}; "start"/"end"
        # are raw .NET-date strings as returned by the API. Loaded
        # lazily in async_added_to_hass when persistence is enabled.
        self._archive: dict[str, dict[str, Any]] = {}
        self._store: Store | None = None

    @property
    def _persist_enabled(self) -> bool:
        return bool(
            self.coordinator.settings.get(
                SETTING_PERSIST_CALENDAR, DEFAULT_PERSIST_CALENDAR
            )
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # One Store per config entry. Created unconditionally so the
        # purge service can wipe an archive that exists from a previous
        # run where persistence was enabled, even if it's currently off.
        self._store = Store(
            self.hass,
            _STORAGE_VERSION,
            f"evisitor_calendar_{self.coordinator.entry.entry_id}",
        )
        if self._persist_enabled:
            data = await self._store.async_load() or {}
            self._archive = data.get("events", {}) or {}
        # Expose ourselves to the coordinator so the purge service can
        # find us without going through the entity registry.
        self.coordinator.calendar_entity = self
        # Sync the archive after each coordinator refresh.
        self.async_on_remove(
            self.coordinator.async_add_listener(self._sync_archive)
        )
        # First refresh already ran during config-entry setup, before
        # this entity was added -- the listener above only fires on
        # *future* updates. Prime the archive once with the current
        # snapshot so we don't have to wait for the next poll.
        self._sync_archive()

    async def async_will_remove_from_hass(self) -> None:
        if (
            self.coordinator is not None
            and getattr(self.coordinator, "calendar_entity", None) is self
        ):
            self.coordinator.calendar_entity = None
        await super().async_will_remove_from_hass()

    @callback
    def _sync_archive(self) -> None:
        """Upsert the latest snapshot of stays into the on-disk archive.

        No-op when persistence is disabled. Never deletes archive
        entries -- that's the purge service's job. A stay disappearing
        from eVisitor (cancellation, archival) is the entire reason the
        archive exists; we don't want to forget it just because the
        next poll didn't return it.
        """
        if not self._persist_enabled or self._store is None:
            return
        stays = (self.coordinator.data or {}).get("all_stays") or []
        changed = False
        for stay in stays:
            uid = str(stay.get("ID") or "")
            if not uid:
                continue
            start = stay.get("TimeStayFrom") or stay.get("StayFrom")
            end = stay.get("TimeEstimatedStayUntil") or stay.get(
                "ForeseenStayUntil"
            )
            if not start or not end:
                continue
            new_entry = {
                "summary": stay.get("SurnameAndName") or "Guest",
                "start": start,
                "end": end,
                "location": stay.get("FacilityName"),
            }
            if self._archive.get(uid) != new_entry:
                self._archive[uid] = new_entry
                changed = True
        if changed:
            # Store.async_save is debounced; safe to call frequently.
            self.hass.async_create_task(
                self._store.async_save({"events": self._archive})
            )

    async def async_purge_archive(self) -> None:
        """Wipe the on-disk archive AND the in-memory copy.

        Called by the `evisitor.purge_calendar_history` service. Runs
        regardless of whether persistence is currently enabled so that
        an archive left over from a previous "persistence on" period
        can always be erased.
        """
        self._archive = {}
        if self._store is not None:
            await self._store.async_save({"events": {}})
        # Tell HA to re-render anyone observing us.
        self.async_write_ha_state()

    # --- CalendarEntity API ------------------------------------------------

    @property
    def event(self) -> CalendarEvent | None:
        events = self._all_events()
        if not events:
            return None
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
        # Live data wins: any prijava the coordinator's last refresh saw
        # is rendered from that snapshot directly. The archive only
        # contributes entries whose uid is NOT in the live snapshot --
        # i.e. previously-seen prijave that have aged out of eVisitor
        # since (cancelled, archived).
        live_stays = (self.coordinator.data or {}).get("all_stays") or []
        live_uids: set[str] = set()
        events: list[CalendarEvent] = []

        for stay in live_stays:
            uid_str = str(stay.get("ID") or "")
            if uid_str:
                live_uids.add(uid_str)
            start = _dt_or_none(stay.get("TimeStayFrom") or stay.get("StayFrom"))
            end = _dt_or_none(
                stay.get("TimeEstimatedStayUntil") or stay.get("ForeseenStayUntil")
            )
            if start is None or end is None or end <= start:
                continue
            events.append(
                CalendarEvent(
                    summary=stay.get("SurnameAndName") or "Guest",
                    start=start,
                    end=end,
                    description=None,
                    location=stay.get("FacilityName"),
                    uid=uid_str or None,
                )
            )

        # Archive supplements: only show events with uids the live data
        # doesn't already have. Skipping this branch entirely when the
        # in-memory archive is empty (the common path with persistence
        # off) keeps the no-op cheap.
        if self._archive:
            for uid, entry in self._archive.items():
                if uid in live_uids:
                    continue
                start = _dt_or_none(entry.get("start"))
                end = _dt_or_none(entry.get("end"))
                if start is None or end is None or end <= start:
                    continue
                events.append(
                    CalendarEvent(
                        summary=entry.get("summary") or "Guest",
                        start=start,
                        end=end,
                        description=None,
                        location=entry.get("location"),
                        uid=uid,
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
