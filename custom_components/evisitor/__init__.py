"""eVisitor integration entry points."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_PERSON_MAP, DOMAIN, KEY_CHECK_IN_ID_SEED, PLATFORMS
from .coordinator import EVisitorCoordinator
from .services import async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up an eVisitor config entry."""
    coordinator = EVisitorCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Services live globally; safe to call repeatedly -- registration is idempotent.
    async_register_services(hass)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an eVisitor config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    coordinator: EVisitorCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
    await coordinator.async_close()

    if not hass.data[DOMAIN]:
        async_unregister_services(hass)
        hass.data.pop(DOMAIN, None)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry on options changes -- except for pure seed refreshes.

    The integration auto-refreshes ``check_in_id_seed`` after every
    successful check-in (so the mapping survives eVisitor archiving old
    prijave). That mutation goes through ``async_update_entry``, which
    unconditionally fires this listener. Reloading the entry mid-check-in
    would tear down the coordinator and re-login -- expensive and
    pointless when the only thing that changed is the seed of an
    already-mapped person. We detect that case and skip the reload.
    """
    coordinator: EVisitorCoordinator | None = (
        hass.data.get(DOMAIN, {}).get(entry.entry_id)
    )
    if coordinator is not None and _only_seed_refreshes_changed(
        coordinator.previous_options, dict(entry.options)
    ):
        # The coordinator already reads ``self.entry.options`` lazily, so
        # the new seeds are visible without further work. Just snapshot
        # the new options so the next compare is correct.
        coordinator.previous_options = dict(entry.options)
        return
    await hass.config_entries.async_reload(entry.entry_id)


def _only_seed_refreshes_changed(
    old: dict, new: dict
) -> bool:
    """True iff the only delta is a seed change for already-mapped persons."""
    old_top = {k: v for k, v in old.items() if k != CONF_PERSON_MAP}
    new_top = {k: v for k, v in new.items() if k != CONF_PERSON_MAP}
    if old_top != new_top:
        return False

    old_map = old.get(CONF_PERSON_MAP) or {}
    new_map = new.get(CONF_PERSON_MAP) or {}
    if set(old_map) != set(new_map):
        # Person added/removed -> need entity (de)registration via reload.
        return False

    for person, new_info in new_map.items():
        old_info = old_map.get(person) or {}
        # Only the seed key may differ; everything else must match.
        old_other = {k: v for k, v in old_info.items() if k != KEY_CHECK_IN_ID_SEED}
        new_other = {k: v for k, v in new_info.items() if k != KEY_CHECK_IN_ID_SEED}
        if old_other != new_other:
            return False
    return True
