"""Lookup (codetable) helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable

from .encoding import Filter

if TYPE_CHECKING:
    from .client import EVisitorClient


# Friendly name -> resource path. Names match the codetable wiki page.
#
# This list is curated for the **obveznik** role (regular hosts/landlords
# registering tourists). Lookups that are reserved for the turistička
# zajednica / HTZ admin role -- and therefore return
# ``You are not authorized for action 'Read'`` for an obveznik account --
# are intentionally **not** exposed here:
#   - FacilityCategoryLookup   - DistanceTypeLookup
#   - FacilityCodeLookup       - ServiceTypeLookup
#   - OpeningBasisLookup       - LegalPersonTypeLookup
#   - PaymentPayoutTypeLookup
# All of those concern facility/obveznik registration and cash-desk admin
# flows that an obveznik does not perform via the public Web API.
LOOKUP_PATHS: dict[str, str] = {
    # Tourist check-in / check-out flows -- everything the obveznik needs
    # to populate a CheckInTourist payload.
    "country": "Htz/CountryLookup/",
    "document_type": "Htz/DocumentTtypeLookup/",
    "settlement": "Htz/SettlementLookup/",
    "visa_type": "Htz/VisaTypeLookup/",
    "border_crossing_hr": "Htz/BorderCrossingHRlookup/",
    "tt_payment_category": "Htz/TTPaymentCategoryLookup/",
    "tt_payment_category_for_facility": "Htz/TTPaymentCategoryLookup2/",
    "arrival_organisation": "Htz/ArrivalOrganisationLookup/",
    "offered_service_type": "Htz/OfferedServiceTypeLookup/",
    # Per-facility info readable by the obveznik for the obveznik's own
    # facilities and accommodation units.
    "facility_subcategory": "Htz/FacilitySubcategoryLookup/",
    "facility_tourist_check_in": "Htz/FacilityTouristCheckInLookup/",
    "accommodation_unit_facility_type": "Htz/AccommodationUnitFacilityType/",
    "accommodation_unit_type_per_subtype": "Htz/AccommodationUnitTypePerSubtype/",
    "settlement_zone": "Htz/SettlementZoneLookup/",
    "distance": "Htz/DistanceLookup/",
    # Tourist agency directory (used when ArrivalOrganisation == "Agencijski").
    "tourist_agency": "Htz/TouristAgencyBrowse/",
    # Cash desk listing for an obveznik who pays via blagajna.
    "cash_desk": "Htz/CashDeskLookup/",
}


class Lookups:
    """Read-only helpers around the various ``*Lookup`` browses."""

    def __init__(self, client: "EVisitorClient") -> None:
        self._client = client

    @property
    def known(self) -> dict[str, str]:
        """Mapping of friendly lookup name -> REST path."""
        return dict(LOOKUP_PATHS)

    async def fetch(
        self,
        name: str,
        *,
        filters: Iterable[Filter | dict[str, Any]] | None = None,
        sort: str | None = None,
        page: int | None = None,
        psize: int | None = None,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Fetch records for the named lookup.

        ``active_only`` adds a ``Active=true`` filter when the resource
        exposes that attribute (most lookups do; a few like
        ``FacilityTouristCheckInLookup`` do not -- pass
        ``active_only=False`` for those).

        ``page``/``psize`` are not sent unless explicitly provided. The
        eVisitor server rejects ``psize`` without ``page`` and rejects
        ``psize`` larger than the total record count, so for small lookups
        the safest call is to ask for everything (the default).
        """
        try:
            path = LOOKUP_PATHS[name]
        except KeyError as err:
            raise KeyError(
                f"Unknown lookup {name!r}. "
                f"Known: {sorted(LOOKUP_PATHS)}"
            ) from err

        all_filters: list[Filter | dict[str, Any]] = []
        if active_only:
            all_filters.append(Filter("Active", "equal", True))
        if filters:
            all_filters.extend(filters)

        params: dict[str, Any] = {}
        if page is not None:
            params["page"] = page
        if psize is not None:
            params["psize"] = psize
        if sort is not None:
            params["sort"] = sort

        result = await self._client.get(
            path,
            params=params or None,
            filters=all_filters or None,
        )
        if isinstance(result, dict) and "Records" in result:
            return result["Records"]
        return result if isinstance(result, list) else []

    # Convenience wrappers used by the HA integration later. Each returns the
    # already-extracted Records list for ergonomics.

    async def countries(self, **kwargs: Any) -> list[dict[str, Any]]:
        return await self.fetch("country", **kwargs)

    async def document_types(self, **kwargs: Any) -> list[dict[str, Any]]:
        return await self.fetch("document_type", **kwargs)

    async def arrival_organisations(self, **kwargs: Any) -> list[dict[str, Any]]:
        return await self.fetch("arrival_organisation", **kwargs)

    async def tt_payment_categories(self, **kwargs: Any) -> list[dict[str, Any]]:
        return await self.fetch("tt_payment_category", **kwargs)

    async def offered_service_types(self, **kwargs: Any) -> list[dict[str, Any]]:
        return await self.fetch("offered_service_type", **kwargs)

    async def border_crossings(self, **kwargs: Any) -> list[dict[str, Any]]:
        return await self.fetch("border_crossing_hr", **kwargs)

    async def settlements(self, **kwargs: Any) -> list[dict[str, Any]]:
        return await self.fetch("settlement", **kwargs)


__all__ = ["LOOKUP_PATHS", "Lookups"]
