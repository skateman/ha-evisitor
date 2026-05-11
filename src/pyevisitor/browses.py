"""High-level wrappers for documented e-Visitor browse resources."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable

from .encoding import Filter

if TYPE_CHECKING:
    from .client import EVisitorClient


class Browses:
    """Helpers for the ``Browse`` resources used by tourist/facility lists."""

    def __init__(self, client: "EVisitorClient") -> None:
        self._client = client

    async def browse(
        self,
        path: str,
        *,
        filters: Iterable[Filter | dict[str, Any]] | None = None,
        sort: str | None = None,
        page: int | None = None,
        psize: int | None = None,
        with_total_count: bool = False,
    ) -> Any:
        """Generic GET against any browse/entity ``path``.

        ``page``/``psize`` are only sent when explicitly provided. The
        eVisitor server rejects ``psize`` without ``page`` and rejects
        ``psize`` greater than the total available records, so leaving
        them ``None`` returns the full set, which is usually what you
        want for small queries.

        When ``with_total_count`` is True the call goes to
        ``<path>/RecordsAndTotalCount`` and the response includes
        ``TotalCount`` alongside ``Records``.
        """
        target = path.rstrip("/")
        if with_total_count:
            target = f"{target}/RecordsAndTotalCount"
        else:
            target = f"{target}/"

        params: dict[str, Any] = {}
        if page is not None:
            params["page"] = page
        if psize is not None:
            params["psize"] = psize
        if sort is not None:
            params["sort"] = sort

        return await self._client.get(
            target, params=params or None, filters=filters
        )

    async def total_count(
        self,
        path: str,
        *,
        filters: Iterable[Filter | dict[str, Any]] | None = None,
    ) -> Any:
        """Call ``<path>/TotalCount``."""
        target = f"{path.rstrip('/')}/TotalCount"
        return await self._client.get(target, filters=filters)

    async def list_tourists(
        self,
        *,
        filters: Iterable[Filter | dict[str, Any]] | None = None,
        sort: str | None = None,
        page: int | None = None,
        psize: int | None = None,
        extended: bool = False,
        with_total_count: bool = False,
    ) -> Any:
        """List tourists via ``ListOfTourists`` (or ``ListOfTouristsExtended``).

        Cancelled check-ins are excluded by the API itself (see docs).
        Pass ``page`` and ``psize`` together to paginate; leaving both
        ``None`` returns every record (safest with the eVisitor server's
        strict pagination rules).
        """
        path = "Htz/ListOfTouristsExtended" if extended else "Htz/ListOfTourists"
        return await self.browse(
            path,
            filters=filters,
            sort=sort,
            page=page,
            psize=psize,
            with_total_count=with_total_count,
        )

    async def list_facilities(
        self,
        *,
        filters: Iterable[Filter | dict[str, Any]] | None = None,
        sort: str | None = None,
        page: int | None = None,
        psize: int | None = None,
        with_total_count: bool = False,
    ) -> Any:
        """List facilities via ``FacilityBrowse``.

        Pagination params are off by default to avoid the eVisitor server
        rejecting requests where ``psize`` exceeds the total record count.
        """
        return await self.browse(
            "Htz/FacilityBrowse",
            filters=filters,
            sort=sort,
            page=page,
            psize=psize,
            with_total_count=with_total_count,
        )

    async def list_cancelled_tourists(
        self,
        *,
        filters: Iterable[Filter | dict[str, Any]] | None = None,
        sort: str | None = None,
        page: int | None = None,
        psize: int | None = None,
        with_total_count: bool = False,
    ) -> Any:
        """List cancelled (poništene) prijave via ``TouristCancelledBrowse``.

        Cancelled prijave are intentionally excluded from
        ``ListOfTourists`` / ``ListOfTouristsExtended`` per the upstream
        wiki, so this is the only public way to look them up after a
        ``CancelTouristCheckIn`` call. Filter by ``ID`` to retrieve a
        specific cancelled check-in.
        """
        return await self.browse(
            "Htz/TouristCancelledBrowse",
            filters=filters,
            sort=sort,
            page=page,
            psize=psize,
            with_total_count=with_total_count,
        )

    async def get_cancelled_tourist_by_id(
        self, check_in_id: str
    ) -> dict[str, Any] | None:
        """Convenience: fetch one cancelled prijava by its check-in GUID."""
        result = await self.list_cancelled_tourists(
            filters=[Filter("ID", "equal", check_in_id)],
        )
        records = (result or {}).get("Records") or []
        return records[0] if records else None

    async def get_facility_by_code(self, code: str) -> dict[str, Any] | None:
        """Convenience wrapper: fetch a single facility by its eVisitor Code."""
        result = await self.list_facilities(
            filters=[Filter("Code", "equal", code)],
        )
        records = (result or {}).get("Records") or []
        return records[0] if records else None


__all__ = ["Browses"]
