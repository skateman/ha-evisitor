"""Typed payloads for e-Visitor actions.

These dataclasses model the documented request/response shapes for the
high-level actions (``CheckInTourist``, ``CheckOutTourist``, the two
``Cancel...`` actions). They handle the per-field date/time encoding the
API expects (``YYYYMMDD`` and ``HH:MM`` strings -- not the .NET JSON Date
format).
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from typing import Any

from .encoding import Filter, FilterOp


def _date_to_yyyymmdd(value: date | datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        value = value.date()
    return value.strftime("%Y%m%d")


def _time_to_hhmm(value: time | datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        value = value.time()
    return value.strftime("%H:%M")


def _strip_none(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if v is not None}


@dataclass
class CheckInRequest:
    """Payload for ``Htz/CheckInTourist``.

    Fields mirror the documented attribute names verbatim. A subset is
    technically optional (see docs); we leave defaults as ``None`` so
    only the fields you set are sent.

    The ``id`` field is the prijava GUID. The eVisitor server **rejects
    new check-ins without one** (``[[[Nije zadan ID.]]]``), so
    :meth:`to_payload` will auto-generate a fresh ``uuid4`` when one is
    not supplied and stash it back on this instance so the caller can
    retrieve it after POSTing for later cancellation/edit. Set
    :attr:`id` explicitly to **edit** an existing active check-in.
    """

    facility: str
    stay_from: date | datetime
    foreseen_stay_until: date | datetime
    time_stay_from: time | datetime
    time_estimated_stay_until: time | datetime
    document_type: str
    document_number: str
    tourist_name: str
    tourist_surname: str
    gender: str
    date_of_birth: date | datetime
    citizenship: str
    country_of_birth: str
    country_of_residence: str
    arrival_organisation: str
    offered_service_type: str
    tt_payment_category: str

    id: str | None = None
    tt_payer_id: str | None = None
    accommodation_unit_type: str | None = None
    tourist_agency: str | None = None
    city_of_birth: str | None = None
    city_of_residence: str | None = None
    residence_address: str | None = None
    is_tt_flat_rate_payment_vacation_home: bool | None = None
    tourist_middle_name: str | None = None
    tourist_email: str | None = None
    tourist_telephone: str | None = None
    border_crossing_hr: str | None = None
    passage_date: date | datetime | None = None

    extra: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        """Encode to the JSON payload the API expects.

        Side effect: if :attr:`id` is unset, a fresh ``uuid4`` is
        generated and assigned back to the instance, so the caller can
        read it after posting (e.g., to cancel the check-in later).
        """
        if not self.id:
            self.id = str(uuid.uuid4())
        payload: dict[str, Any] = {
            "ID": self.id,
            "TTPayerID": self.tt_payer_id,
            "AccommodationUnitType": self.accommodation_unit_type,
            "ArrivalOrganisation": self.arrival_organisation,
            "TouristAgency": self.tourist_agency,
            "Citizenship": self.citizenship,
            "CityOfBirth": self.city_of_birth,
            "CityOfResidence": self.city_of_residence,
            "CountryOfBirth": self.country_of_birth,
            "CountryOfResidence": self.country_of_residence,
            "DateOfBirth": _date_to_yyyymmdd(self.date_of_birth),
            "DocumentNumber": self.document_number,
            "DocumentType": self.document_type,
            "Facility": self.facility,
            "ForeseenStayUntil": _date_to_yyyymmdd(self.foreseen_stay_until),
            "Gender": self.gender,
            "IsTTFlatRatePaymentVacationHome": (
                self.is_tt_flat_rate_payment_vacation_home
            ),
            "OfferedServiceType": self.offered_service_type,
            "ResidenceAddress": self.residence_address,
            "StayFrom": _date_to_yyyymmdd(self.stay_from),
            "TimeEstimatedStayUntil": _time_to_hhmm(
                self.time_estimated_stay_until
            ),
            "TimeStayFrom": _time_to_hhmm(self.time_stay_from),
            "TouristEmail": self.tourist_email,
            "TouristMiddleName": self.tourist_middle_name,
            "TouristName": self.tourist_name,
            "TouristSurname": self.tourist_surname,
            "TouristTelephone": self.tourist_telephone,
            "TTPaymentCategory": self.tt_payment_category,
            "BorderCrossingHr": self.border_crossing_hr,
            "PassageDate": _date_to_yyyymmdd(self.passage_date),
        }
        payload.update(self.extra)
        return _strip_none(payload)


@dataclass
class CheckOutRequest:
    """Payload for ``Htz/CheckOutTourist``."""

    id: str
    check_out_date: date | datetime
    check_out_time: time | datetime
    tt_payer_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ID": self.id,
            "TTPayerID": self.tt_payer_id,
            "CheckOutDate": _date_to_yyyymmdd(self.check_out_date),
            "CheckOutTime": _time_to_hhmm(self.check_out_time),
        }
        payload.update(self.extra)
        return _strip_none(payload)


@dataclass
class CancelCheckInRequest:
    """Payload for ``Htz/CancelTouristCheckIn``."""

    id: str
    reason: str | None = None
    tt_payer_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ID": self.id,
            "TTPayerID": self.tt_payer_id,
            "Reason": self.reason,
        }
        payload.update(self.extra)
        return _strip_none(payload)


@dataclass
class CancelCheckOutRequest:
    """Payload for ``Htz/CancelTouristCheckOut``."""

    id: str
    reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ID": self.id,
            "Reason": self.reason,
        }
        payload.update(self.extra)
        return _strip_none(payload)


__all__ = [
    "CancelCheckInRequest",
    "CancelCheckOutRequest",
    "CheckInRequest",
    "CheckOutRequest",
    "Filter",
    "FilterOp",
    "asdict",
]
