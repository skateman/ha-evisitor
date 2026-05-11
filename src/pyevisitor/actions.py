"""High-level wrappers for documented e-Visitor actions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import (
    CancelCheckInRequest,
    CancelCheckOutRequest,
    CheckInRequest,
    CheckOutRequest,
)

if TYPE_CHECKING:
    from .client import EVisitorClient


class Actions:
    """High-level wrappers around documented e-Visitor actions."""

    def __init__(self, client: "EVisitorClient") -> None:
        self._client = client

    async def check_in_tourist(self, request: CheckInRequest) -> Any:
        """Call ``Htz/CheckInTourist``.

        Pass an existing :attr:`CheckInRequest.id` to edit an active
        check-in; per the docs you must send all fields, not just the
        changed ones.

        Returns whatever the API returns (typically the new check-in's
        ID for new check-ins, an empty body for edits).
        """
        return await self._client.post(
            "Htz/CheckInTourist/", json_body=request.to_payload()
        )

    async def check_out_tourist(self, request: CheckOutRequest) -> Any:
        """Call ``Htz/CheckOutTourist``."""
        return await self._client.post(
            "Htz/CheckOutTourist/", json_body=request.to_payload()
        )

    async def cancel_tourist_check_in(
        self, request: CancelCheckInRequest
    ) -> Any:
        """Call ``Htz/CancelTouristCheckIn``."""
        return await self._client.post(
            "Htz/CancelTouristCheckIn/", json_body=request.to_payload()
        )

    async def cancel_tourist_check_out(
        self, request: CancelCheckOutRequest
    ) -> Any:
        """Call ``Htz/CancelTouristCheckOut``."""
        return await self._client.post(
            "Htz/CancelTouristCheckOut/", json_body=request.to_payload()
        )


__all__ = ["Actions"]
