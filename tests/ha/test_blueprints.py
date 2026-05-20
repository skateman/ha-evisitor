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

from homeassistant.components.blueprint.models import Blueprint
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
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
