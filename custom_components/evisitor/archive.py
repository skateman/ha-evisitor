"""Persistent calendar archive for the eVisitor integration.

The archive is a tiny on-disk store, one file per config entry, that
mirrors the subset of past prijave the calendar entity needs to render
historical events even after a Home Assistant restart (and as a defence
against future eVisitor changes that might trim
``ListOfTouristsExtended``).

Stored shape::

    {
      "schema_version": 1,
      "events": {
          "<prijava-uid>": {
              "summary": "Surname Name",
              "start":   "2026-05-19T14:00:00+02:00",
              "end":     "2026-05-21T10:00:00+02:00",
              "location": "Facility Name"
          },
          ...
      }
    }

Only the four fields the calendar UI already renders are persisted; no
DOB, document number, address, citizenship, telephone, e-mail, etc.

The archive is *append-mostly*: stays land in it once they're marked
``CheckedOutTourist=True`` in eVisitor's ``ListOfTouristsExtended`` and
stay there. The only path that removes an entry is the cancellation
sweep (a uid that turns up in ``TouristCancelledBrowse`` is dropped, so
the rare case of a checked-out prijava being cancelled afterwards
doesn't leave a void event behind).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Mapping

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

ARCHIVE_SCHEMA_VERSION = 1
ARCHIVE_STORE_KEY_PREFIX = "evisitor_archive_"
# Legacy file written by the (now yanked) v0.4.0 build. Different key,
# so the new archive can't collide with it; we just want to delete the
# old file on first load to keep .storage tidy.
_LEGACY_V040_STORE_KEY_PREFIX = "evisitor_calendar_"


def _store_key(entry_id: str) -> str:
    return f"{ARCHIVE_STORE_KEY_PREFIX}{entry_id}"


def _legacy_store_key(entry_id: str) -> str:
    return f"{_LEGACY_V040_STORE_KEY_PREFIX}{entry_id}"


class EvisitorArchive:
    """In-memory + on-disk view of the persistent calendar archive.

    Atomicity is provided by HA's :class:`Store`; we just batch writes
    via the ``_dirty`` flag so a single poll cycle results in at most one
    save call.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._store: Store = Store(hass, ARCHIVE_SCHEMA_VERSION, _store_key(entry_id))
        self._events: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self._loaded = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_load(self) -> None:
        """Load the archive from disk; create an empty one if absent.

        Also cleans up the legacy v0.4.0 ``evisitor_calendar_<entry>``
        file if it exists, so storage doesn't accumulate dead state.
        """
        if self._loaded:
            return
        await self._delete_legacy_residue()
        data = await self._store.async_load()
        if isinstance(data, Mapping):
            events = data.get("events")
            if isinstance(events, Mapping):
                # Defensive copy + skip malformed entries quietly.
                for uid, event in events.items():
                    if isinstance(uid, str) and isinstance(event, Mapping):
                        self._events[uid] = dict(event)
        self._loaded = True

    async def async_save(self) -> None:
        """Persist the archive to disk if dirty."""
        if not self._dirty:
            return
        await self._store.async_save(self._payload())
        self._dirty = False

    async def _delete_legacy_residue(self) -> None:
        """Remove the v0.4.0 calendar Store file if present.

        v0.4.0 wrote to ``.storage/evisitor_calendar_<entry_id>`` with a
        different shape; the v0.4.1 release reverted persistence
        entirely. v0.5.0 introduces a fresh archive under a new key, so
        the old file is now orphaned -- delete it best-effort.
        """
        legacy = Store(
            self._hass, ARCHIVE_SCHEMA_VERSION, _legacy_store_key(self._entry_id)
        )
        try:
            existing = await legacy.async_load()
        except Exception:  # pragma: no cover - best-effort cleanup
            _LOGGER.debug(
                "Could not read legacy v0.4.0 calendar store for %s",
                self._entry_id,
                exc_info=True,
            )
            return
        if existing is None:
            return
        try:
            await legacy.async_remove()
        except Exception:  # pragma: no cover - best-effort cleanup
            _LOGGER.debug(
                "Could not delete legacy v0.4.0 calendar store for %s",
                self._entry_id,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Accessors / mutators (in-memory; async_save flushes to disk)
    # ------------------------------------------------------------------

    def has(self, uid: str) -> bool:
        return uid in self._events

    def get(self, uid: str) -> dict[str, Any] | None:
        event = self._events.get(uid)
        return dict(event) if event is not None else None

    def upsert(self, uid: str, event: Mapping[str, Any]) -> bool:
        """Insert/update ``uid``'s event. Returns True iff anything changed."""
        normalised = _normalise_event(event)
        if self._events.get(uid) == normalised:
            return False
        self._events[uid] = normalised
        self._dirty = True
        return True

    def discard(self, uid: str) -> bool:
        """Remove ``uid``. Returns True iff something was removed."""
        if uid in self._events:
            del self._events[uid]
            self._dirty = True
            return True
        return False

    def purge(self) -> bool:
        """Drop every archived event. Returns True iff anything was removed."""
        if not self._events:
            return False
        self._events.clear()
        self._dirty = True
        return True

    def uids(self) -> set[str]:
        return set(self._events)

    def items(self) -> list[tuple[str, dict[str, Any]]]:
        """Snapshot copy of ``(uid, event)`` pairs for iteration."""
        return [(uid, dict(event)) for uid, event in self._events.items()]

    def events_in_range(
        self, start: datetime, end: datetime
    ) -> list[tuple[str, dict[str, Any]]]:
        """Return ``[(uid, event_dict)]`` for events overlapping ``[start, end]``."""
        out: list[tuple[str, dict[str, Any]]] = []
        for uid, event in self._events.items():
            ev_start = _parse_iso(event.get("start"))
            ev_end = _parse_iso(event.get("end"))
            if ev_start is None or ev_end is None:
                continue
            if ev_end < start or ev_start > end:
                continue
            out.append((uid, dict(event)))
        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @property
    def dirty(self) -> bool:
        return self._dirty

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": ARCHIVE_SCHEMA_VERSION,
            "events": dict(self._events),
        }


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


_EVENT_FIELDS = ("summary", "start", "end", "location")


def _normalise_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Project ``event`` down to the four persisted fields only.

    Anything else (DOB, document, address...) silently dropped -- that's
    the privacy guarantee.
    """
    out: dict[str, Any] = {}
    for key in _EVENT_FIELDS:
        value = event.get(key)
        if isinstance(value, datetime):
            value = value.isoformat()
        out[key] = value
    return out


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def event_from_stay(stay: Mapping[str, Any]) -> dict[str, Any] | None:
    """Build the persisted-event dict from an eVisitor stay record.

    Returns ``None`` for stays without usable start/end dates.
    """
    from pyevisitor.encoding import from_dotnet_date

    def _dt(value: Any) -> datetime | None:
        if not value or not isinstance(value, str):
            return None
        try:
            return from_dotnet_date(value)
        except (ValueError, TypeError):
            return None

    start = _dt(stay.get("TimeStayFrom") or stay.get("StayFrom"))
    end = _dt(stay.get("TimeEstimatedStayUntil") or stay.get("ForeseenStayUntil"))
    if start is None or end is None or end <= start:
        return None
    return {
        "summary": stay.get("SurnameAndName") or "Guest",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "location": stay.get("FacilityName"),
    }


__all__ = [
    "ARCHIVE_SCHEMA_VERSION",
    "ARCHIVE_STORE_KEY_PREFIX",
    "EvisitorArchive",
    "event_from_stay",
]
