"""Validate the shipped blueprints load via HA's own loader.

We use the blueprint module's own ``BLUEPRINT_SCHEMA`` rather than the
automation ``PLATFORM_SCHEMA``: the latter validates the *substituted*
form (with real values), not the template form (which still contains
``Input()`` placeholders for ``!input`` references). Catching every
issue beyond YAML well-formedness + input declaration is a job for the
hassfest tooling.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from homeassistant.components.automation.config import async_validate_config_item
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.core import HomeAssistant
from homeassistant.util import yaml as ha_yaml

BLUEPRINT_DIR = (
    Path(__file__).resolve().parents[2]
    / "custom_components"
    / "evisitor"
    / "blueprints"
    / "automation"
    / "evisitor"
)


@pytest.mark.parametrize(
    "filename,expected_inputs",
    [
        (
            "auto_check_in.yaml",
            {
                "person",
                "mode",
                "presence_debounce_minutes",
                "stay_days",
                "check_out_time",
                "platform",
                "notify_service",
                "telegram_chat_id",
                "notification_title",
                "notification_message",
                "check_in_action_label",
                "skip_action_label",
                "notification_timeout_minutes",
            },
        ),
        (
            "auto_check_out.yaml",
            {
                "person",
                "mode",
                "grace_minutes",
                "only_during_window",
                "window_start",
                "window_end",
                "platform",
                "notify_service",
                "telegram_chat_id",
                "notification_title",
                "notification_message",
                "check_out_action_label",
                "keep_in_action_label",
                "notification_timeout_minutes",
            },
        ),
        (
            "nightly_sliding_extender.yaml",
            {
                "person",
                "schedule",
                "stay_days",
                "only_if_home",
            },
        ),
    ],
)
def test_blueprint_inputs_match_expected_set(
    filename: str, expected_inputs: set[str]
) -> None:
    path = BLUEPRINT_DIR / filename
    raw = ha_yaml.load_yaml(str(path))
    blueprint = Blueprint(
        raw,
        expected_domain="automation",
        path=str(path),
        schema=BLUEPRINT_SCHEMA,
    )
    assert set((blueprint.inputs or {}).keys()) == expected_inputs


@pytest.mark.parametrize(
    "filename,inputs",
    [
        # Regression for the bug where an empty `notify_service` default
        # (the Telegram path) trips HA's static service-name validator
        # because `service: !input notify_service` substitutes literally
        # to `service: ""` even when the companion branch is dead.
        (
            "auto_check_in.yaml",
            {
                "person": ["person.demo_user"],
                "platform": "telegram",
                "telegram_chat_id": "123456",
                "presence_debounce_minutes": 15,
                "stay_days": 2,
                "notification_timeout_minutes": 120,
            },
        ),
        (
            "auto_check_out.yaml",
            {
                "person": ["person.demo_user"],
                "platform": "telegram",
                "telegram_chat_id": "123456",
                "notification_timeout_minutes": 60,
            },
        ),
    ],
)
async def test_blueprint_substitutes_and_validates_with_telegram_defaults(
    hass: HomeAssistant, filename: str, inputs: dict
) -> None:
    """Instantiate each blueprint with platform=telegram and
    ``notify_service`` left at its empty default, then run the
    substituted YAML through the automation config validator. This
    is the exact path that fails when ``service: !input notify_service``
    inlines to ``service: ""`` at parse time."""
    path = BLUEPRINT_DIR / filename
    raw = ha_yaml.load_yaml(str(path))
    blueprint = Blueprint(
        raw,
        expected_domain="automation",
        path=str(path),
        schema=BLUEPRINT_SCHEMA,
    )

    blueprint_inputs = BlueprintInputs(
        blueprint,
        {
            "use_blueprint": {
                "path": filename,
                "input": inputs,
            },
            "alias": "regression-test",
        },
    )
    blueprint_inputs.validate()
    substituted = blueprint_inputs.async_substitute()

    # If the companion branch's `service:` field still inlined to ""
    # this call raises with the user-reported "Service does not match
    # format <domain>.<name>" error.
    validated = await async_validate_config_item(hass, "regression-test", substituted)
    assert validated is not None
