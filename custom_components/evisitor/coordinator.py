"""Data coordinator for the eVisitor integration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from pyevisitor import (
    CancelCheckInRequest,
    CheckOutRequest,
    EVisitorClient,
    EVisitorConfig,
    EVisitorError,
    Environment,
    Guest,
)
from pyevisitor.encoding import from_dotnet_date

from ._payload import (
    PersonOptions,
    StayWindow,
    build_check_in_request,
)
from .const import (
    CONF_API_KEY,
    CONF_ENVIRONMENT,
    CONF_FACILITY_CODE,
    CONF_PASSWORD,
    CONF_PERSON_MAP,
    CONF_SETTINGS,
    CONF_USERNAME,
    DEFAULT_CHECK_OUT_TIME,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_STAY_DURATION_HOURS,
    DOMAIN,
    KEY_CHECK_IN_ID_SEED,
    LOOKUPS_REFRESH_INTERVAL,
    SETTING_CHECK_OUT_TIME,
    SETTING_SCAN_INTERVAL_MINUTES,
    SETTING_STAY_DURATION_HOURS,
)

_LOGGER = logging.getLogger(__name__)


class EVisitorCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Owns the eVisitor client + caches relevant snapshots in memory."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        # Compute the coordinator scan interval up front from settings
        # so we can hand it to the base class.
        settings = entry.options.get(CONF_SETTINGS) or {}
        scan_interval = timedelta(
            minutes=int(
                settings.get(
                    SETTING_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES
                )
            )
        )
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}.{entry.entry_id}",
            update_interval=scan_interval,
        )
        self.entry = entry
        # Snapshot the options as currently persisted; used by the
        # update listener in __init__.py to tell pure seed-refresh
        # writes (which we make ourselves and don't need a reload)
        # apart from real config changes.
        self.previous_options: dict[str, Any] = dict(entry.options)
        self._client: EVisitorClient | None = None
        self._lookups_loaded_at: datetime | None = None
        self._lookup_cache: dict[str, list[dict[str, Any]]] = {}
        self._facility_id: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _async_setup(self) -> None:
        """Build the client and log in."""
        config = EVisitorConfig(
            username=self.entry.data[CONF_USERNAME],
            password=self.entry.data[CONF_PASSWORD],
            environment=Environment.parse(self.entry.data[CONF_ENVIRONMENT]),
            api_key=self.entry.data.get(CONF_API_KEY) or None,
        )
        # The TLS context construction reads CA bundles from disk; do it
        # off the event loop so HA doesn't log a "Detected blocking call"
        # warning. The library accepts a pre-built context.
        ssl_context = None
        if config.relax_tls:
            from pyevisitor.client import _build_relaxed_ssl_context

            ssl_context = await self.hass.async_add_executor_job(
                _build_relaxed_ssl_context
            )
        self._client = EVisitorClient(config, ssl_context=ssl_context)
        await self._client.login()

    async def async_close(self) -> None:
        if self._client is not None:
            try:
                if self._client.authenticated:
                    await self._client.logout()
            except EVisitorError:
                _LOGGER.debug("Best-effort logout failed", exc_info=True)
            await self._client.close()
            self._client = None

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def client(self) -> EVisitorClient:
        if self._client is None:
            raise RuntimeError("eVisitor client not yet initialised")
        return self._client

    @property
    def facility_code(self) -> str:
        return self.entry.data[CONF_FACILITY_CODE]

    @property
    def lookup_cache(self) -> dict[str, list[dict[str, Any]]]:
        return self._lookup_cache

    @property
    def settings(self) -> dict[str, Any]:
        """Live view of the integration's user-tunable settings."""
        return dict(self.entry.options.get(CONF_SETTINGS) or {})

    @property
    def stay_duration(self) -> timedelta:
        hours = int(
            self.settings.get(SETTING_STAY_DURATION_HOURS, DEFAULT_STAY_DURATION_HOURS)
        )
        return timedelta(hours=hours)

    @property
    def check_out_time_str(self) -> str:
        return str(
            self.settings.get(SETTING_CHECK_OUT_TIME, DEFAULT_CHECK_OUT_TIME)
        )

    @property
    def person_map(self) -> dict[str, dict[str, Any]]:
        return dict(self.entry.options.get(CONF_PERSON_MAP) or {})

    def person_options(self, person_entity_id: str) -> PersonOptions | None:
        info = self.person_map.get(person_entity_id)
        if not info:
            return None
        # Accept both legacy entries (dict with codes) and the new minimal
        # form ({ "check_in_id_seed": "..." }) for forward compatibility.
        seed = info.get("check_in_id_seed") if isinstance(info, dict) else None
        if not seed:
            _LOGGER.warning(
                "Person mapping for %s has no check_in_id_seed: %s",
                person_entity_id,
                info,
            )
            return None
        return PersonOptions(check_in_id_seed=seed)

    # ------------------------------------------------------------------
    # Periodic refresh
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        if self._client is None:
            await self._async_setup()
        try:
            await self._maybe_refresh_lookups()
            await self._resolve_facility_id()
            stays = await self.client.guests.stays()
            unique_guests = await self.client.guests.unique()
        except EVisitorError as err:
            raise UpdateFailed(str(err)) from err

        active = [s for s in stays if not s.get("CheckedOutTourist")]
        return {
            "facility_id": self._facility_id,
            "active_stays": active,
            "all_stays": stays,
            "unique_guests": unique_guests,
        }

    async def _maybe_refresh_lookups(self) -> None:
        now = dt_util.utcnow()
        if (
            self._lookups_loaded_at is not None
            and (now - self._lookups_loaded_at) < LOOKUPS_REFRESH_INTERVAL
        ):
            return
        self._lookup_cache = {
            "country": await self.client.lookups.countries(),
            "document_type": await self.client.lookups.document_types(),
            "arrival_organisation": await self.client.lookups.arrival_organisations(),
            "tt_payment_category": await self.client.lookups.tt_payment_categories(),
            "offered_service_type": await self.client.lookups.offered_service_types(),
        }
        self._lookups_loaded_at = now

    async def _resolve_facility_id(self) -> None:
        if self._facility_id is not None:
            return
        fac = await self.client.browses.get_facility_by_code(self.facility_code)
        if fac:
            self._facility_id = fac["ID"]

    # ------------------------------------------------------------------
    # Lookups <-> Guest plumbing
    # ------------------------------------------------------------------

    def find_guest_by_seed(self, seed: str | None) -> Guest | None:
        if not seed:
            return None
        guests: list[Guest] = (self.data or {}).get("unique_guests") or []
        seed_lower = seed.lower()
        for g in guests:
            for stay in g.stays:
                if str(stay.get("ID", "")).lower() == seed_lower:
                    return g
        return None

    def find_active_stay_for_person(
        self, person_entity_id: str
    ) -> dict[str, Any] | None:
        opts = self.person_options(person_entity_id)
        if opts is None:
            return None
        guest = self.find_guest_by_seed(opts.check_in_id_seed)
        if guest is None:
            return None
        active_list = (self.data or {}).get("active_stays") or []
        for stay in active_list:
            if (
                stay.get("SurnameAndName") == guest.latest.get("SurnameAndName")
                and stay.get("DatePlaceOfBirth") == guest.latest.get("DatePlaceOfBirth")
            ):
                return stay
        return None

    async def _recover_guest_after_cancel(self, seed: str | None) -> Guest | None:
        """Resolve a guest whose seeded prijava was cancelled out-of-band.

        Looks the seed up in ``TouristCancelledBrowse`` (cancelled prijave
        are intentionally absent from ``ListOfTouristsExtended``); if
        found, matches its ``(SurnameAndName, DatePlaceOfBirth)`` against
        the current ``unique()`` snapshot. Returns the matching live
        :class:`Guest` if any, otherwise ``None``.
        """
        if not seed:
            return None
        try:
            cancelled = await self.client.browses.get_cancelled_tourist_by_id(seed)
        except EVisitorError:
            _LOGGER.debug(
                "TouristCancelledBrowse lookup failed for seed %s",
                seed,
                exc_info=True,
            )
            return None
        if not cancelled:
            return None

        identity_name = cancelled.get("Tourist") or cancelled.get("SurnameAndName")
        identity_dpb = cancelled.get("DatePlaceOfBirth")
        if not identity_name and not identity_dpb:
            return None

        for guest in (self.data or {}).get("unique_guests") or []:
            latest = guest.latest
            if (
                identity_name
                and latest.get("SurnameAndName") == identity_name
                and (
                    not identity_dpb
                    or latest.get("DatePlaceOfBirth") == identity_dpb
                )
            ):
                return guest
            if (
                identity_dpb
                and latest.get("DatePlaceOfBirth") == identity_dpb
                and not identity_name
            ):
                return guest
        return None

    # ------------------------------------------------------------------
    # Service entrypoints
    # ------------------------------------------------------------------

    async def check_in_person(
        self,
        person_entity_id: str,
        *,
        foreseen_stay_until: datetime | None = None,
        stay_from: datetime | None = None,
    ) -> str:
        opts = self.person_options(person_entity_id)
        if opts is None:
            raise EVisitorError(f"No eVisitor mapping for {person_entity_id!r}")
        guest = self.find_guest_by_seed(opts.check_in_id_seed)
        if guest is None:
            await self.async_refresh()
            guest = self.find_guest_by_seed(opts.check_in_id_seed)
        if guest is None:
            # Seed isn't in unique() -- maybe the prijava we last created
            # was manually cancelled in the eVisitor web UI. Try to recover
            # by looking the seed up in TouristCancelledBrowse and matching
            # by identity against the current unique() snapshot.
            guest = await self._recover_guest_after_cancel(opts.check_in_id_seed)
            if guest is not None:
                _LOGGER.info(
                    "Recovered guest for %s via TouristCancelledBrowse fallback "
                    "(seed %s was cancelled out-of-band); self-healing the seed.",
                    person_entity_id,
                    opts.check_in_id_seed,
                )
                # Re-seed to a still-valid stay so future lookups skip the fallback.
                fresh_seed = str(guest.latest.get("ID") or "")
                if fresh_seed:
                    self._refresh_seed(person_entity_id, fresh_seed)
                    opts = PersonOptions(check_in_id_seed=fresh_seed)
        if guest is None:
            raise EVisitorError(
                f"Mapped seed for {person_entity_id} is not in any "
                f"current or cancelled prijava -- please re-map the person."
            )

        # Arrival timestamp -- caller may backdate it (e.g. an auto-checkin
        # blueprint passing the trigger's last_changed instead of "now",
        # so the registered StayFrom matches when the guest actually got
        # home rather than when presence-debounce fired). eVisitor's
        # server allows StayFrom up to ~14 days in the past (the per-account
        # `AllowedNumberOfDaysToCheckInCheckOut` parameter) -- well outside
        # any realistic debounce window.
        arrival = (stay_from or dt_util.now()).replace(microsecond=0)
        window = (
            StayWindow(
                stay_from=arrival,
                foreseen_stay_until=foreseen_stay_until,
            )
            if foreseen_stay_until is not None
            else StayWindow.default_from_now(
                now=arrival,
                stay_duration=self.stay_duration,
                check_out_time=self.check_out_time_str,
            )
        )
        request = build_check_in_request(
            guest,
            opts,
            facility_code=self.facility_code,
            stay_window=window,
            lookup_cache=self._lookup_cache,
        )
        await self.client.actions.check_in_tourist(request)
        # Refresh the persisted seed so the mapping survives eVisitor
        # archiving/purging older prijave. The update listener in
        # __init__.py recognises pure seed refreshes and skips the
        # entry reload that ``async_update_entry`` would normally
        # trigger.
        if request.id:
            self._refresh_seed(person_entity_id, request.id)
        await self.async_refresh()
        return request.id  # type: ignore[return-value]

    async def check_out_person(
        self,
        person_entity_id: str,
        *,
        check_out_at: datetime | None = None,
    ) -> str:
        active = self.find_active_stay_for_person(person_entity_id)
        if active is None:
            raise EVisitorError(f"No active prijava for {person_entity_id!r}")
        moment = check_out_at or dt_util.now()
        request = CheckOutRequest(
            id=str(active["ID"]),
            check_out_date=moment.date(),
            check_out_time=moment.time().replace(microsecond=0),
        )
        await self.client.actions.check_out_tourist(request)
        await self.async_refresh()
        return request.id

    async def cancel_check_in_person(
        self,
        person_entity_id: str,
        *,
        reason: str | None = None,
    ) -> str:
        active = self.find_active_stay_for_person(person_entity_id)
        if active is None:
            raise EVisitorError(f"No active prijava for {person_entity_id!r}")
        await self.client.actions.cancel_tourist_check_in(
            CancelCheckInRequest(id=str(active["ID"]), reason=reason)
        )
        await self.async_refresh()
        return str(active["ID"])

    async def extend_stay(
        self,
        person_entity_id: str,
        *,
        foreseen_stay_until: datetime | None = None,
        stay_days: int | None = None,
    ) -> str:
        """Edit the active prijava: same ID, full payload, new ForeseenStayUntil.

        ``foreseen_stay_until`` and ``stay_days`` are alternatives:
        - ``foreseen_stay_until`` (datetime) is used verbatim;
        - ``stay_days`` (int) computes
          ``today_local_midnight + stay_days days`` at the integration's
          ``check_out_time_str``;
        - if both are unset, falls back to the integration's default
          stay-duration setting (today + ``default_stay_duration`` at
          ``check_out_time``).
        """
        opts = self.person_options(person_entity_id)
        active = self.find_active_stay_for_person(person_entity_id)
        if opts is None or active is None:
            raise EVisitorError(f"No active prijava for {person_entity_id!r}")
        guest = self.find_guest_by_seed(opts.check_in_id_seed)
        if guest is None:
            raise EVisitorError(f"Cannot resolve guest for {person_entity_id!r}")

        if foreseen_stay_until is None:
            base = _today_local_midnight()
            if stay_days is not None:
                duration = timedelta(days=int(stay_days))
            else:
                duration = self.stay_duration
            target = base + duration
            hh, mm = (int(p) for p in self.check_out_time_str.split(":", 1))
            foreseen_stay_until = target.replace(
                hour=hh, minute=mm, second=0, microsecond=0
            )

        # Extension keeps the original StayFrom + TimeStayFrom.
        original_from = (
            from_dotnet_date(active["StayFrom"])
            if active.get("StayFrom")
            else dt_util.now()
        )
        window = StayWindow(
            stay_from=original_from,
            foreseen_stay_until=foreseen_stay_until,
        )
        request = build_check_in_request(
            guest,
            opts,
            facility_code=self.facility_code,
            stay_window=window,
            lookup_cache=self._lookup_cache,
            check_in_id=str(active["ID"]),
        )
        await self.client.actions.check_in_tourist(request)
        await self.async_refresh()
        return request.id  # type: ignore[return-value]

    def _refresh_seed(self, person_entity_id: str, new_seed: str) -> None:
        """Persist a fresh ``check_in_id_seed`` for the mapped person.

        Goes through ``async_update_entry`` so HA persists the change.
        The integration's update listener (in ``__init__.py``)
        recognises pure seed-refresh writes via the
        ``previous_options`` snapshot and skips the entry reload.
        """
        person_map = dict(self.person_map)
        existing = dict(person_map.get(person_entity_id) or {})
        if existing.get(KEY_CHECK_IN_ID_SEED) == new_seed:
            return  # no-op, e.g. extend on the same prijava
        existing[KEY_CHECK_IN_ID_SEED] = new_seed
        person_map[person_entity_id] = existing
        new_options = {**self.entry.options, CONF_PERSON_MAP: person_map}
        self.hass.config_entries.async_update_entry(
            self.entry, options=new_options
        )
        # Sync the snapshot ourselves so the update listener sees
        # equality on the next compare even if it races us.
        self.previous_options = dict(new_options)



def _today_local_midnight() -> datetime:
    now = dt_util.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)
