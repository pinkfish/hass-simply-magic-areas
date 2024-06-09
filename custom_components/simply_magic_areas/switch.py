"""Switches for magic areas."""

import logging

from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.components.switch import (
    DOMAIN as SWITCH_DOMAIN,
    SwitchDeviceClass,
    SwitchEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base.entities import MagicEntity
from .base.magic import MagicArea
from .config.entity_names import EntityNames
from .const import (
    DATA_AREA_OBJECT,
    DOMAIN,
    ICON_FAN_CONTROL,
    ICON_LIGHT_CONTROL,
    MODULE_DATA,
)
from .util import cleanup_magic_entities

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Area config entry."""

    area: MagicArea = hass.data[MODULE_DATA][config_entry.entry_id][DATA_AREA_OBJECT]

    existing_switch_entities: list[str] = []
    if DOMAIN + SWITCH_DOMAIN in area.entities:
        existing_switch_entities = [
            e[ATTR_ENTITY_ID] for e in area.entities[DOMAIN + SWITCH_DOMAIN]
        ]

    switches: list[Entity] = []
    if area.has_entities(FAN_DOMAIN):
        switches.append(AreaFanControlSwitch(area))
    switches.append(AreaLightControlSwitch(area))
    cleanup_magic_entities(hass, switches, existing_switch_entities)
    async_add_entities(switches)


class SwitchBase(MagicEntity, SwitchEntity):
    """The base class for all the switches."""

    def __init__(self, area: MagicArea, translation_key: str) -> None:
        """Initialize the base switch bits, basic just a mixin for the two types."""
        MagicEntity.__init__(
            self, area=area, domain=SWITCH_DOMAIN, translation_key=translation_key
        )
        SwitchEntity.__init__(self)
        self._attr_device_class = SwitchDeviceClass.SWITCH
        self._attr_should_poll = False
        self._attr_is_on = False

    async def async_added_to_hass(self) -> None:
        """Call when entity about to be added to hass."""
        await super().async_added_to_hass()
        self.async_write_ha_state()
        self.schedule_update_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on the switch."""
        self._attr_is_on = True
        self.schedule_update_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off the switch."""
        self._attr_is_on = False
        self.schedule_update_ha_state()


class AreaLightControlSwitch(SwitchBase):
    """Controls if the system is running and watching state."""

    def __init__(self, area: MagicArea) -> None:
        """Initialize the area light control switch."""

        super().__init__(area, translation_key=EntityNames.LIGHT_CONTROL)
        self._attr_is_on = True

    @property
    def icon(self):
        """Return the icon to be used for this entity."""
        return ICON_LIGHT_CONTROL


class AreaFanControlSwitch(SwitchBase):
    """Controls if the system is running and watching state."""

    def __init__(self, area: MagicArea) -> None:
        """Initialize the area fan control switch."""

        super().__init__(area, translation_key=EntityNames.FAN_CONTROL)
        self._attr_is_on = True

    @property
    def icon(self):
        """Return the icon to be used for this entity."""
        return ICON_FAN_CONTROL
