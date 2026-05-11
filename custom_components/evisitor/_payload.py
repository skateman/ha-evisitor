"""Build CheckInTourist payloads from a stored mapping + a live Guest.

Storage model: the persisted ``PersonOptions`` carries **only** the
``check_in_id_seed`` (an opaque UUID pointing at any one of the
guest's past stays). Everything else -- direct identifiers (name,
DOB, document number, addresses) and lookup codes (document type,
ISO citizenship etc.) -- is derived at check-in time from the live
:class:`pyevisitor.Guest` and the integration's cached eVisitor
lookups.

This module is the seam where the two are stitched together. Pure
functions, no I/O, no Home Assistant runtime dependencies, easy to
unit-test.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from pyevisitor import CheckInRequest, Guest

# Local copies of the few constants this module needs so it stays
# free of any non-stdlib relative imports.
_DEFAULT_STAY_DURATION = timedelta(days=2)
_DEFAULT_CHECK_OUT_TIME = "10:00"

# Defaults baked in for fields the past-stay data does not carry.
_DEFAULT_ARRIVAL_ORGANISATION = "I"  # Osobno
_DEFAULT_OFFERED_SERVICE_TYPE = "Noćenje"
_DEFAULT_TT_PAYMENT_CATEGORY = "18"  # "Vlasnici kuće za odmor i članovi njegove obitelji"
                                     # — owner-family exemption. Appropriate fallback for
                                     # this integration: HA `person.*` entities track
                                     # household members, who in this use case are the
                                     # owner's family. Friends-of-owner (code 16) or
                                     # paying tourists (code 14) are out of scope for
                                     # auto check-ins via HA presence.
_FALLBACK_DOCUMENT_TYPE = "027"  # "Osobna iskaznica (strana)"
_FALLBACK_COUNTRY = "HRV"

# eVisitor ``DatePlaceOfBirth`` looks like
# ``"DD.MM.YYYY (NN) <city or "" > <country>"``.
_BIRTH_PREFIX_RE = re.compile(r"^\s*\d{2}\.\d{2}\.\d{4}\s*\(\d+\)\s*")
_GENDER_NORMALIZER = {
    "muški": "muški",
    "muski": "muški",
    "ženski": "ženski",
    "zenski": "ženski",
    "muski (m)": "muški",
    "zenski (z)": "ženski",
}


@dataclass(frozen=True)
class PersonOptions:
    """Persisted configuration for a single mapped HA person.

    The only thing we keep across restarts is a ``check_in_id_seed``
    -- the GUID of one of the guest's past stays. The integration
    finds the matching :class:`pyevisitor.Guest` by walking
    ``client.guests.unique()`` and looking for a stay whose ``ID``
    equals the seed.

    All direct identifiers (name, DOB, document number, address) and
    lookup codes (document type, ISO citizenship etc.) are derived
    from the live ``Guest`` snapshot at check-in time via
    :func:`build_check_in_request`.
    """

    check_in_id_seed: str


@dataclass(frozen=True)
class StayWindow:
    """Concrete stay timing -- arrival and departure datetimes (local)."""

    stay_from: datetime
    foreseen_stay_until: datetime

    @classmethod
    def default_from_now(
        cls,
        *,
        now: datetime,
        stay_duration: timedelta | None = None,
        check_out_time: str | None = None,
    ) -> "StayWindow":
        """Build the default 48 h window (or whatever duration) starting at ``now``.

        Caller passes ``now`` so the module stays independent of HA's
        ``dt_util``. ``stay_duration`` and ``check_out_time`` override
        the module-level defaults; pass them through from
        coordinator settings to honour user overrides.
        """
        duration = stay_duration if stay_duration is not None else _DEFAULT_STAY_DURATION
        cot = check_out_time if check_out_time is not None else _DEFAULT_CHECK_OUT_TIME
        hh, mm = (int(part) for part in cot.split(":", 1))
        until = (now + duration).replace(
            hour=hh, minute=mm, second=0, microsecond=0
        )
        return cls(stay_from=now.replace(microsecond=0), foreseen_stay_until=until)


def build_check_in_request(
    guest: Guest,
    person_options: PersonOptions,
    *,
    facility_code: str,
    stay_window: StayWindow,
    lookup_cache: dict[str, list[dict[str, Any]]],
    check_in_id: str | None = None,
) -> CheckInRequest:
    """Build a CheckInTourist payload from a live Guest snapshot.

    Direct identifiers come from ``guest.latest``; lookup codes are
    resolved from ``lookup_cache`` (the ``country``, ``document_type``,
    ``tt_payment_category`` keys are the ones consulted).

    ``check_in_id`` is set when *editing* an existing prijava; for new
    check-ins leave it ``None`` and a fresh ``uuid4`` is allocated
    eagerly so the caller can read ``request.id`` immediately.
    """
    latest = guest.latest

    surname, name = _split_surname_and_name(latest.get("SurnameAndName"))
    document_number = _split_document_number(latest.get("TravelDocumentTypeNumber"))
    city_of_birth = _city_of_birth(latest.get("DatePlaceOfBirth"))
    address_parts = _split_address(latest.get("Address"))

    countries = lookup_cache.get("country") or []
    citizenship = (
        _country_code_by_citizenship(latest.get("Citizenship"), countries)
        or _FALLBACK_COUNTRY
    )
    country_of_birth = (
        _country_code_by_national(
            _country_label_at_end(latest.get("DatePlaceOfBirth")), countries
        )
        or citizenship
    )
    country_of_residence = (
        _country_code_by_national(
            _country_label_at_end(latest.get("Address")), countries
        )
        or citizenship
    )
    document_type = (
        _document_type_code(
            latest.get("TravelDocumentTypeNumber"),
            lookup_cache.get("document_type") or [],
        )
        or _FALLBACK_DOCUMENT_TYPE
    )
    tt_payment_category = (
        _payment_category_code(
            latest.get("Note"), lookup_cache.get("tt_payment_category") or []
        )
        or _DEFAULT_TT_PAYMENT_CATEGORY
    )

    request = CheckInRequest(
        id=check_in_id or str(uuid.uuid4()),
        facility=facility_code,
        stay_from=stay_window.stay_from.date(),
        foreseen_stay_until=stay_window.foreseen_stay_until.date(),
        time_stay_from=stay_window.stay_from.time().replace(microsecond=0),
        time_estimated_stay_until=stay_window.foreseen_stay_until.time().replace(
            microsecond=0
        ),
        document_type=document_type,
        document_number=document_number or "",
        tourist_name=name,
        tourist_surname=surname,
        gender=_normalize_gender(latest.get("Gender")),
        date_of_birth=guest.date_of_birth or date(1900, 1, 1),
        citizenship=citizenship,
        country_of_birth=country_of_birth,
        city_of_birth=city_of_birth,
        country_of_residence=country_of_residence,
        city_of_residence=address_parts.city,
        residence_address=address_parts.street,
        arrival_organisation=_DEFAULT_ARRIVAL_ORGANISATION,
        offered_service_type=_DEFAULT_OFFERED_SERVICE_TYPE,
        tt_payment_category=tt_payment_category,
    )
    return request


# ---------------------------------------------------------------------------
# Pure parsing helpers (extensively unit tested)
# ---------------------------------------------------------------------------


def _country_code_by_citizenship(
    label: str | None, countries: list[dict[str, Any]]
) -> str | None:
    """``"Slovačke Republike"`` -> ``"SVK"`` via ``NameCitizenships``."""
    if not label:
        return None
    target = label.strip()
    for row in countries:
        if (row.get("NameCitizenships") or "").strip() == target:
            return row.get("CodeThreeLetters")
    return None


def _country_code_by_national(
    label: str | None, countries: list[dict[str, Any]]
) -> str | None:
    """``"Slovačka Republika"`` -> ``"SVK"`` via ``NameNational``."""
    if not label:
        return None
    target = label.strip()
    for row in countries:
        if (row.get("NameNational") or "").strip() == target:
            return row.get("CodeThreeLetters")
        if (row.get("NameNationalAlternative") or "").strip() == target:
            return row.get("CodeThreeLetters")
    return None


def _country_label_at_end(value: str | None) -> str | None:
    """Trailing 1- or 2-token country label of a ``DatePlaceOfBirth``-like
    or ``Address``-like string.

    Heuristic: the country label is the last 1 or 2 whitespace-delimited
    tokens of the body (after stripping the leading date prefix on
    ``DatePlaceOfBirth``).
    """
    if not value:
        return None
    body = _BIRTH_PREFIX_RE.sub("", value).strip()
    if not body:
        return None
    tokens = body.split()
    if not tokens:
        return None
    if len(tokens) >= 2 and tokens[-1] == "Republika":
        return " ".join(tokens[-2:])
    return tokens[-1]


def _document_type_code(
    travel_doc: str | None, doc_types: list[dict[str, Any]]
) -> str | None:
    """``"Osobna iskaznica (strana) XX111111"`` -> ``"027"``.

    Matches the longest ``Name`` from the document_type lookup that
    appears as a prefix of ``travel_doc``.
    """
    if not travel_doc:
        return None
    text = travel_doc.strip()
    best_name = ""
    best_code: str | None = None
    for row in doc_types:
        name = (row.get("Name") or "").strip()
        if name and text.startswith(name) and len(name) > len(best_name):
            best_name = name
            best_code = row.get("Code") or row.get("CodeMI")
    return best_code


def _payment_category_code(
    note: str | None, categories: list[dict[str, Any]]
) -> str | None:
    """Match ``Note`` (e.g. ``"Vlasnici kuće za odmor i članovi ..."``)
    against ``tt_payment_category.Name`` to recover the ``Code``."""
    if not note:
        return None
    target = note.strip()
    for row in categories:
        if (row.get("Name") or "").strip() == target:
            return row.get("Code")
    return None


def _split_surname_and_name(value: str | None) -> tuple[str, str]:
    """eVisitor's ``SurnameAndName`` is ``"Surname GivenNames..."``."""
    if not value:
        return ("", "")
    parts = value.strip().split()
    if not parts:
        return ("", "")
    if len(parts) == 1:
        return (parts[0], parts[0])
    surname = parts[0]
    given = " ".join(parts[1:])
    return (surname, given)


def _split_document_number(value: str | None) -> str | None:
    """``"Osobna iskaznica (strana) XX111111"`` -> ``"XX111111"``."""
    if not value:
        return None
    parts = value.rsplit(" ", 1)
    if len(parts) != 2:
        return None
    return parts[1] or None


def _city_of_birth(value: str | None) -> str | None:
    """Strip the ``DD.MM.YYYY (NN)`` prefix and the trailing country token.

    ``"15.01.1985 (40) Bratislava Slovačka Republika"`` ->
    ``"Bratislava"``.

    ``"15.01.1985 (40)  Slovačka Republika"`` -> ``None`` (city absent).
    """
    if not value:
        return None
    body = _BIRTH_PREFIX_RE.sub("", value).strip()
    if not body:
        return None
    tokens = body.split()
    # Heuristic: the trailing 1-2 tokens form the country label; drop them.
    # If only 1-2 tokens remain we have no city info.
    if len(tokens) <= 2:
        return None
    return " ".join(tokens[:-2])


@dataclass(frozen=True)
class _AddressParts:
    street: str | None
    city: str | None


def _split_address(value: str | None) -> _AddressParts:
    """``"Hlavná 12 Trnava Slovačka Republika"`` ->
    ``street="Hlavná 12", city="Trnava"``.

    Heuristic: strip the trailing 1-2 country tokens, then the next
    last token is the city, the rest is the street.
    """
    if not value:
        return _AddressParts(None, None)
    tokens = value.strip().split()
    if len(tokens) <= 2:
        return _AddressParts(None, None)
    # Country is 1-2 trailing tokens. Most country labels are 1 word ("Mađarska")
    # but "Slovačka Republika" is 2; we strip 2 if the second-to-last token is
    # capitalised and looks like the country. Pragmatic guess: strip trailing 2,
    # if that leaves nothing, strip 1.
    head = tokens[:-2]
    if not head:
        head = tokens[:-1]
    if not head:
        return _AddressParts(None, None)
    if len(head) == 1:
        return _AddressParts(street=None, city=head[0])
    city = head[-1]
    street = " ".join(head[:-1])
    return _AddressParts(street=street, city=city)


def _normalize_gender(value: str | None) -> str:
    if not value:
        return "muški"
    key = value.strip().lower()
    return _GENDER_NORMALIZER.get(key, value.strip().lower())
