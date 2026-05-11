"""sensor entity -- count of currently active prijave for the facility."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EVisitorCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coord: EVisitorCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EVisitorActiveCount(coord)])


class EVisitorActiveCount(CoordinatorEntity[EVisitorCoordinator], SensorEntity):
    """Number of currently checked-in tourists at the configured facility."""

    _attr_has_entity_name = True
    _attr_translation_key = "active_count"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "guests"

    def __init__(self, coord: EVisitorCoordinator) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{coord.entry.entry_id}::active_count"
        self._attr_name = "Active guests"

    @property
    def native_value(self) -> int:
        active = (self.coordinator.data or {}).get("active_stays") or []
        return len(active)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        active = (self.coordinator.data or {}).get("active_stays") or []
        return {
            "facility_code": self.coordinator.facility_code,
            "facility_id": (self.coordinator.data or {}).get("facility_id"),
        }
