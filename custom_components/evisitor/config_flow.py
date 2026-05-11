"""Config + options flow for the eVisitor integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from pyevisitor import (
    EVisitorAuthError,
    EVisitorClient,
    EVisitorConfig,
    EVisitorError,
    Environment,
)

from .const import (
    CONF_API_KEY,
    CONF_ENVIRONMENT,
    CONF_FACILITY_CODE,
    CONF_PERSON_MAP,
    CONF_SETTINGS,
    DEFAULT_CHECK_OUT_TIME,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_STAY_DURATION_HOURS,
    DOMAIN,
    KEY_CHECK_IN_ID_SEED,
    SETTING_CHECK_OUT_TIME,
    SETTING_SCAN_INTERVAL_MINUTES,
    SETTING_STAY_DURATION_HOURS,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Initial setup flow: credentials -> facility pick
# ---------------------------------------------------------------------------


class EVisitorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Initial setup flow for an eVisitor account + facility."""

    VERSION = 1

    def __init__(self) -> None:
        self._credentials: dict[str, Any] = {}
        self._facilities: list[dict[str, Any]] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                facilities = await self._login_and_list_facilities(user_input)
            except EVisitorAuthError as err:
                _LOGGER.warning("Auth failed: %s", err)
                errors["base"] = "invalid_auth"
            except EVisitorError as err:
                _LOGGER.warning("Connection failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                if not facilities:
                    errors["base"] = "no_facilities"
                else:
                    self._credentials = user_input
                    self._facilities = facilities
                    return await self.async_step_facility()

        schema = vol.Schema(
            {
                vol.Required(CONF_ENVIRONMENT, default="production"): vol.In(
                    ["production", "test"]
                ),
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_API_KEY, default=""): str,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    async def async_step_facility(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            data = {**self._credentials, CONF_FACILITY_CODE: user_input[CONF_FACILITY_CODE]}
            unique_id = f"{data[CONF_USERNAME]}::{data[CONF_FACILITY_CODE]}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            facility = next(
                (f for f in self._facilities if f["Code"] == data[CONF_FACILITY_CODE]),
                None,
            )
            title = facility["Name"] if facility else f"eVisitor {data[CONF_FACILITY_CODE]}"
            return self.async_create_entry(
                title=title,
                data=data,
                options={CONF_PERSON_MAP: {}},
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_FACILITY_CODE): vol.In(
                    {f["Code"]: f"{f['Code']} – {f['Name']}" for f in self._facilities}
                )
            }
        )
        return self.async_show_form(step_id="facility", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return EVisitorOptionsFlow(entry)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _login_and_list_facilities(
        self, user_input: dict[str, Any]
    ) -> list[dict[str, Any]]:
        config = EVisitorConfig(
            username=user_input[CONF_USERNAME],
            password=user_input[CONF_PASSWORD],
            environment=Environment.parse(user_input[CONF_ENVIRONMENT]),
            api_key=(user_input.get(CONF_API_KEY) or "") or None,
        )
        # Build the TLS context off the event loop -- see coordinator
        # for the full rationale.
        ssl_context = None
        if config.relax_tls:
            from pyevisitor.client import _build_relaxed_ssl_context

            ssl_context = await self.hass.async_add_executor_job(
                _build_relaxed_ssl_context
            )
        client = EVisitorClient(config, ssl_context=ssl_context)
        try:
            await client.login()
            res = await client.browses.list_facilities()
            return list((res or {}).get("Records") or [])
        finally:
            try:
                if client.authenticated:
                    await client.logout()
            except EVisitorError:
                pass
            await client.close()


# ---------------------------------------------------------------------------
# Options flow: manage person -> guest mappings
# ---------------------------------------------------------------------------


class EVisitorOptionsFlow(OptionsFlow):
    """Add / remove person mappings without storing PII in HA storage."""

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry
        self._coordinator = None
        self._candidate_person: str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["settings", "add_person", "remove_person", "finish"],
        )

    # -- settings -------------------------------------------------------

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        existing = dict(self.entry.options.get(CONF_SETTINGS) or {})
        if user_input is not None:
            # Validate the HH:MM strings -- we tolerate trailing whitespace
            # but reject anything that doesn't parse, so a typo can't bork
            # the next coordinator load.
            try:
                _validate_hhmm(user_input[SETTING_CHECK_OUT_TIME])
            except ValueError:
                return self.async_show_form(
                    step_id="settings",
                    data_schema=_settings_schema(existing),
                    errors={"base": "invalid_time_format"},
                )
            return self.async_create_entry(
                title="",
                data={**self.entry.options, CONF_SETTINGS: dict(user_input)},
            )

        return self.async_show_form(
            step_id="settings",
            data_schema=_settings_schema(existing),
        )

    # -- add ------------------------------------------------------------

    async def async_step_add_person(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        coord = self._coord()
        if coord is None or not coord.data:
            return self.async_abort(reason="coordinator_not_ready")

        existing = set(coord.person_map.keys())
        if user_input is not None:
            self._candidate_person = user_input["person"]
            return await self.async_step_pick_guest()

        # Person selector via the entity selector restricted to person domain.
        schema = vol.Schema(
            {
                vol.Required("person"): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="person", multiple=False)
                )
            }
        )
        return self.async_show_form(
            step_id="add_person",
            data_schema=schema,
            description_placeholders={
                "already_mapped": ", ".join(sorted(existing)) or "none",
            },
        )

    async def async_step_pick_guest(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        coord = self._coord()
        if coord is None:
            return self.async_abort(reason="coordinator_not_ready")
        guests = coord.data.get("unique_guests") or []
        if not guests:
            return self.async_abort(reason="no_past_guests")

        guest_choices: dict[str, str] = {}
        for g in guests:
            seed = g.latest.get("ID")
            if not seed:
                continue
            dob = g.date_of_birth.isoformat() if g.date_of_birth else "?"
            label = f"{g.name} · {dob} · {g.visit_count} visits"
            guest_choices[str(seed)] = label

        if user_input is not None:
            person_map = dict(coord.person_map)
            person_map[self._candidate_person or ""] = {
                KEY_CHECK_IN_ID_SEED: user_input["seed"],
            }
            return self.async_create_entry(
                title="",
                data={**self.entry.options, CONF_PERSON_MAP: person_map},
            )

        schema = vol.Schema(
            {vol.Required("seed"): vol.In(guest_choices)}
        )
        return self.async_show_form(
            step_id="pick_guest",
            data_schema=schema,
            description_placeholders={"person": self._candidate_person or "?"},
        )

    # -- remove ----------------------------------------------------------

    async def async_step_remove_person(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        coord = self._coord()
        if coord is None:
            return self.async_abort(reason="coordinator_not_ready")
        if not coord.person_map:
            return self.async_abort(reason="nothing_to_remove")

        if user_input is not None:
            person_map = dict(coord.person_map)
            person_map.pop(user_input["person"], None)
            return self.async_create_entry(
                title="",
                data={**self.entry.options, CONF_PERSON_MAP: person_map},
            )

        schema = vol.Schema(
            {vol.Required("person"): vol.In(sorted(coord.person_map.keys()))}
        )
        return self.async_show_form(step_id="remove_person", data_schema=schema)

    # -- finish ---------------------------------------------------------

    async def async_step_finish(
        self, _user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_create_entry(title="", data=dict(self.entry.options))

    # -- helpers --------------------------------------------------------

    def _coord(self):
        if self._coordinator is None:
            self._coordinator = self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id)
        return self._coordinator


# ---------------------------------------------------------------------------
# Module helpers (settings step)
# ---------------------------------------------------------------------------


def _settings_schema(existing: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(
                SETTING_SCAN_INTERVAL_MINUTES,
                default=existing.get(
                    SETTING_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES
                ),
            ): vol.All(int, vol.Range(min=1, max=1440)),
            vol.Optional(
                SETTING_STAY_DURATION_HOURS,
                default=existing.get(
                    SETTING_STAY_DURATION_HOURS, DEFAULT_STAY_DURATION_HOURS
                ),
            ): vol.All(int, vol.Range(min=1, max=720)),
            vol.Optional(
                SETTING_CHECK_OUT_TIME,
                default=existing.get(
                    SETTING_CHECK_OUT_TIME, DEFAULT_CHECK_OUT_TIME
                ),
            ): str,
        }
    )


def _validate_hhmm(value: str) -> tuple[int, int]:
    """Parse and validate a ``HH:MM`` string. Raises ``ValueError`` on bad input."""
    parts = (value or "").strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"expected HH:MM, got {value!r}")
    hh, mm = int(parts[0]), int(parts[1])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"hour/minute out of range in {value!r}")
    return hh, mm
