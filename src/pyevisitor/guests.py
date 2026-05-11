"""Deduplicated view over historical guests.

The eVisitor API exposes one row per stay (``ListOfTouristsExtended``).
For *repeat* check-ins we want one row per unique person (name + date
of birth) with **all** their stays preserved verbatim, so callers can
pick the latest stay's full record and use it to pre-fill a new
``CheckInTourist`` payload.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any

from .encoding import from_dotnet_date

if TYPE_CHECKING:
    from .client import EVisitorClient


# Trailing ``" (40)"`` etc. that the API appends to SurnameAndName as the
# guest's current age.
_AGE_SUFFIX_RE = re.compile(r"\s*\(\d+\)\s*$")
# DD.MM.YYYY at the start of DatePlaceOfBirth.
_BIRTH_DATE_RE = re.compile(r"^\s*(\d{2})\.(\d{2})\.(\d{4})")

_MIN_DT = datetime.min.replace(tzinfo=timezone.utc)


def _strip_age(name: str | None) -> str:
    if not name:
        return ""
    return _AGE_SUFFIX_RE.sub("", name).strip()


def _normalize_name(name: str) -> str:
    """Casefold + Unicode-normalize so dedup ignores accents and case."""
    if not name:
        return ""
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", folded).casefold().strip()


def _parse_birth(value: str | None) -> date | None:
    """Extract the ``DD.MM.YYYY`` date out of ``DatePlaceOfBirth``."""
    if not value:
        return None
    match = _BIRTH_DATE_RE.match(value)
    if not match:
        return None
    day, month, year = (int(g) for g in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _arrival(record: dict[str, Any]) -> datetime | None:
    raw = record.get("DateTimeOfArrival")
    if not raw or not isinstance(raw, str):
        return None
    try:
        return from_dotnet_date(raw)
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True)
class Guest:
    """A unique person aggregated from one-or-more historical stays.

    ``stays`` contains every raw ``ListOfTouristsExtended`` record the
    API returned for this person, sorted newest-first. Every API field
    is preserved verbatim; ``name`` and ``date_of_birth`` are the parsed
    dedup keys plus a convenience.
    """

    name: str
    date_of_birth: date | None
    stays: tuple[dict[str, Any], ...]

    @property
    def visit_count(self) -> int:
        return len(self.stays)

    @property
    def latest(self) -> dict[str, Any]:
        """Most recent raw stay -- the natural source for re-check-in."""
        return self.stays[0]


class Guests:
    """High-level helper around historical guest data."""

    def __init__(self, client: "EVisitorClient") -> None:
        self._client = client

    async def stays(self) -> list[dict[str, Any]]:
        """Return the raw flat list of every historical check-in.

        One entry per ``ListOfTouristsExtended`` row -- the lowest-level
        guest data the public API exposes. Cancelled prijave are
        excluded by the server.
        """
        result = await self._client.browses.list_tourists(extended=True)
        return list((result or {}).get("Records") or [])

    async def unique(self) -> list[Guest]:
        """Return every unique guest, deduplicated by name + birth date.

        Built on top of :meth:`stays`. Within each :class:`Guest`, every
        stay's full raw record is preserved unchanged in
        :attr:`Guest.stays` (sorted newest first), so ``guest.latest``
        is ready to feed back into a new ``CheckInTourist`` payload for
        a returning guest. The returned list is sorted by most-recent
        stay first.
        """
        records = await self.stays()
        return _dedupe_records(records)


def _dedupe_records(records: list[dict[str, Any]]) -> list[Guest]:
    """Group ``ListOfTouristsExtended`` rows into :class:`Guest` records."""
    buckets: dict[tuple[str, date | None], list[dict[str, Any]]] = {}
    display_name: dict[tuple[str, date | None], str] = {}
    dob_for: dict[tuple[str, date | None], date | None] = {}

    for record in records:
        name = _strip_age(record.get("SurnameAndName"))
        dob = _parse_birth(record.get("DatePlaceOfBirth"))
        key = (_normalize_name(name), dob)
        buckets.setdefault(key, []).append(record)
        display_name.setdefault(key, name)
        dob_for[key] = dob

    guests: list[Guest] = []
    for key, stays in buckets.items():
        stays_sorted = sorted(
            stays,
            key=lambda r: (
                _arrival(r) is not None,
                _arrival(r) or _MIN_DT,
            ),
            reverse=True,
        )
        guests.append(
            Guest(
                name=display_name[key],
                date_of_birth=dob_for[key],
                stays=tuple(stays_sorted),
            )
        )

    guests.sort(
        key=lambda g: (
            _arrival(g.latest) is not None,
            _arrival(g.latest) or _MIN_DT,
        ),
        reverse=True,
    )
    return guests


__all__ = ["Guest", "Guests"]
