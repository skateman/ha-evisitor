"""Constants for the eVisitor integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "evisitor"

# --- Config / options keys --------------------------------------------------

CONF_ENVIRONMENT = "environment"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_API_KEY = "api_key"
CONF_FACILITY_CODE = "facility_code"
CONF_PERSON_MAP = "person_map"
CONF_SETTINGS = "settings"

# Per-person mapping payload key -- holds the *seed* check_in_id used to find
# the matching guest in client.guests.unique(). Not the live prijava ID.
KEY_CHECK_IN_ID_SEED = "check_in_id_seed"

# Settings sub-keys (all stored under CONF_SETTINGS in options).
SETTING_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"
SETTING_STAY_DURATION_HOURS = "default_stay_duration_hours"
SETTING_CHECK_OUT_TIME = "default_check_out_time"

# --- Defaults (used when the corresponding setting is unset) ---------------

DEFAULT_SCAN_INTERVAL_MINUTES = 5
DEFAULT_STAY_DURATION_HOURS = 48
DEFAULT_CHECK_OUT_TIME = "10:00"

# Convenience timedeltas built from the defaults above; not used as
# settings storage, just as fallbacks where a timedelta is wanted.
DEFAULT_SCAN_INTERVAL = timedelta(minutes=DEFAULT_SCAN_INTERVAL_MINUTES)
DEFAULT_STAY_DURATION = timedelta(hours=DEFAULT_STAY_DURATION_HOURS)
LOOKUPS_REFRESH_INTERVAL = timedelta(hours=12)

# --- Service names ----------------------------------------------------------

SERVICE_CHECK_IN_PERSON = "check_in_person"
SERVICE_CHECK_OUT_PERSON = "check_out_person"
SERVICE_CANCEL_CHECK_IN = "cancel_check_in"
SERVICE_EXTEND_STAY = "extend_stay"
SERVICE_PURGE_CALENDAR_ARCHIVE = "purge_calendar_archive"
SERVICE_REBUILD_CALENDAR_ARCHIVE = "rebuild_calendar_archive"

# --- Event names ------------------------------------------------------------

EVENT_CHECK_IN_SUCCEEDED = "evisitor_check_in_succeeded"
EVENT_CHECK_IN_FAILED = "evisitor_check_in_failed"
EVENT_CHECK_OUT_SUCCEEDED = "evisitor_check_out_succeeded"
EVENT_CHECK_OUT_FAILED = "evisitor_check_out_failed"
EVENT_EXTEND_SUCCEEDED = "evisitor_extend_succeeded"
EVENT_EXTEND_FAILED = "evisitor_extend_failed"

# --- Misc -------------------------------------------------------------------

PLATFORMS = ("binary_sensor", "sensor", "calendar")
