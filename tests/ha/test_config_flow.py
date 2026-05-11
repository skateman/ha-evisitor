"""Tests for the credentials → facility config flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import data_entry_flow
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant

from custom_components.evisitor.const import (
    CONF_API_KEY,
    CONF_ENVIRONMENT,
    CONF_FACILITY_CODE,
    DOMAIN,
)


async def test_user_step_then_facility_creates_entry(
    hass: HomeAssistant, fake_client_factory
) -> None:
    # Patch BOTH the config_flow's client (used to validate creds + list
    # facilities) and the coordinator's client (used by HA to immediately
    # set up the freshly-created entry).
    with patch(
        "custom_components.evisitor.config_flow.EVisitorClient",
        side_effect=lambda config, **_kw: fake_client_factory(),
    ), patch(
        "custom_components.evisitor.coordinator.EVisitorClient",
        side_effect=lambda config, **_kw: fake_client_factory(),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["type"] is data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_ENVIRONMENT: "production",
                "username": "u",
                "password": "p",
                CONF_API_KEY: "",
            },
        )
        assert result["type"] is data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "facility"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_FACILITY_CODE: "0000001"}
        )
        await hass.async_block_till_done()
        assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["title"] == "Test House"
        assert result["data"][CONF_FACILITY_CODE] == "0000001"


async def test_user_step_invalid_auth_shows_error(hass: HomeAssistant) -> None:
    from pyevisitor import EVisitorAuthError

    with patch(
        "custom_components.evisitor.config_flow.EVisitorClient",
        side_effect=lambda config, **_kw: _raising_client(EVisitorAuthError("bad creds")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_ENVIRONMENT: "production",
                "username": "u",
                "password": "p",
                CONF_API_KEY: "",
            },
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


# ---------------------------------------------------------------------------
# Options flow: integration settings step
# ---------------------------------------------------------------------------


async def test_settings_step_persists_values(
    hass: HomeAssistant, fake_client_factory
) -> None:
    """Submitting the settings form writes them under options.settings."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.evisitor.const import (
        CONF_FACILITY_CODE,
        CONF_PASSWORD,
        CONF_PERSON_MAP,
        CONF_SETTINGS,
        CONF_USERNAME,
        SETTING_CHECK_OUT_TIME,
        SETTING_SCAN_INTERVAL_MINUTES,
        SETTING_STAY_DURATION_HOURS,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENVIRONMENT: "production",
            CONF_USERNAME: "u",
            CONF_PASSWORD: "p",
            CONF_API_KEY: "",
            CONF_FACILITY_CODE: "0000001",
        },
        options={CONF_PERSON_MAP: {}},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.evisitor.coordinator.EVisitorClient",
        side_effect=lambda cfg, **_kw: fake_client_factory(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Open options flow → menu → "settings".
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is data_entry_flow.FlowResultType.MENU
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "settings"}
        )
        assert result["type"] is data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "settings"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                SETTING_SCAN_INTERVAL_MINUTES: 10,
                SETTING_STAY_DURATION_HOURS: 72,
                SETTING_CHECK_OUT_TIME: "11:30",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    saved = entry.options[CONF_SETTINGS]
    assert saved[SETTING_SCAN_INTERVAL_MINUTES] == 10
    assert saved[SETTING_STAY_DURATION_HOURS] == 72
    assert saved[SETTING_CHECK_OUT_TIME] == "11:30"


async def test_settings_step_rejects_bad_time_format(
    hass: HomeAssistant, fake_client_factory
) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.evisitor.const import (
        CONF_FACILITY_CODE,
        CONF_PASSWORD,
        CONF_PERSON_MAP,
        CONF_USERNAME,
        SETTING_CHECK_OUT_TIME,
        SETTING_SCAN_INTERVAL_MINUTES,
        SETTING_STAY_DURATION_HOURS,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENVIRONMENT: "production",
            CONF_USERNAME: "u",
            CONF_PASSWORD: "p",
            CONF_API_KEY: "",
            CONF_FACILITY_CODE: "0000001",
        },
        options={CONF_PERSON_MAP: {}},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.evisitor.coordinator.EVisitorClient",
        side_effect=lambda cfg, **_kw: fake_client_factory(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "settings"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                SETTING_SCAN_INTERVAL_MINUTES: 10,
                SETTING_STAY_DURATION_HOURS: 72,
                SETTING_CHECK_OUT_TIME: "not-a-time",
            },
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_time_format"}


def _raising_client(exc):
    """Build a synchronous fake EVisitorClient whose login() raises."""
    from unittest.mock import AsyncMock, MagicMock

    client = MagicMock(name="EVisitorClient")
    client.authenticated = False
    client.login = AsyncMock(side_effect=exc)
    client.logout = AsyncMock()
    client.close = AsyncMock()
    return client
