"""Switches for magic areas."""

import logging

from homeassistant.components.switch import (
    DOMAIN as SWITCH_DOMAIN,
    SwitchDeviceClass,
    SwitchEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base.entities import MagicEntity
from .base.magic import MagicArea
from .const import (
    DATA_AREA_OBJECT,
    ICON_LIGHT_CONTROL,
    ICON_MANUAL_OVERRIDE,
    MODULE_DATA,
    EntityNames,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Area config entry."""

    area: MagicArea = hass.data[MODULE_DATA][config_entry.entry_id][DATA_AREA_OBJECT]

    async_add_entities(
        [AreaLightControlSwitch(area), AreaLightsManualOverrideActiveSwitch(area)]
    )


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


class AreaLightsManualOverrideActiveSwitch(SwitchBase):
    """Keeps track of a manual override was enabled due to switch change."""

    def __init__(self, area: MagicArea) -> None:
        """Initialize the area manual override switch."""

        super().__init__(area, translation_key=EntityNames.MANUAL_OVERRIDE)
        self._attr_is_on = False

    @property
    def icon(self):
        """Return the icon to be used for this entity."""
        return ICON_MANUAL_OVERRIDE
