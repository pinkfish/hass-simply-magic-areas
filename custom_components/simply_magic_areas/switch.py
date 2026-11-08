"""Switches for magic areas."""

import logging
from contextlib import suppress

from homeassistant.components.switch import (
    DOMAIN as SWITCH_DOMAIN,
)
from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base.entities import MagicEntity
from .base.magic import MagicArea
from .config.entity_names import EntityNames
from .const import (
    CONF_SLEEP_MODE,
    DATA_AREA_OBJECT,
    DEFAULT_SLEEP_MODE,
    DOMAIN,
    ICON_SLEEP_MODE,
    ICON_SYSTEM_CONTROL,
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

    switches: list[Entity] = [
        AreaSystemControlSwitch(area),
        AreaSleepModeSwitch(area),
    ]
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

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on the switch."""
        self._attr_is_on = True
        self.schedule_update_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off the switch."""
        self._attr_is_on = False
        self.schedule_update_ha_state()


class AreaSystemControlSwitch(SwitchBase):
    """Controls if the system is running and watching state."""

    def __init__(self, area: MagicArea) -> None:
        """Initialize the area light control switch."""

        super().__init__(area, translation_key=EntityNames.SYSTEM_CONTROL)
        self._attr_is_on = True
        self._expected_synced_state: bool | None = None

    async def async_added_to_hass(self) -> None:
        """Call when entity is added to hass."""
        self.area._system_control_switch = self  # type: ignore[attr-defined]
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in (STATE_ON, STATE_OFF):
            self._attr_is_on = last_state.state == STATE_ON
        self.area.system_control_enabled = bool(self._attr_is_on)
        await super().async_added_to_hass()
        if hasattr(self.area, "set_system_control"):
            self.area.set_system_control(bool(self._attr_is_on), propagate=False)

    async def async_will_remove_from_hass(self) -> None:
        """Clean up reference when removed from hass."""
        if getattr(self.area, "_system_control_switch", None) is self:
            self.area._system_control_switch = None  # type: ignore[attr-defined]
        await super().async_will_remove_from_hass()

    def _write_or_schedule_ha_state(self) -> None:
        """Write state immediately when on the event loop, or schedule thread-safely."""
        if (
            self.hass is not None
            and self.entity_id
            and getattr(self, "platform", None) is not None
        ):
            with suppress(RuntimeError):
                self.async_write_ha_state()
                return
        self.schedule_update_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on system control for this area."""
        self._expected_synced_state = True
        self._attr_is_on = True
        self.area.system_control_enabled = True
        self._write_or_schedule_ha_state()
        if hasattr(self.area, "set_system_control"):
            self.area.set_system_control(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off system control for this area."""
        self._expected_synced_state = False
        self._attr_is_on = False
        self.area.system_control_enabled = False
        self._write_or_schedule_ha_state()
        if hasattr(self.area, "set_system_control"):
            self.area.set_system_control(False)

    def set_synced_state(self, is_on: bool) -> None:
        """Update the switch state from internal system control changes without re-triggering propagation."""
        target = bool(is_on)
        if self._attr_is_on == target:
            return
        self._expected_synced_state = target
        self._attr_is_on = target
        if self.hass is not None and self.entity_id:
            self._write_or_schedule_ha_state()

    @property
    def icon(self):
        """Return the icon to be used for this entity."""
        return ICON_SYSTEM_CONTROL


class AreaSleepModeSwitch(SwitchBase):
    """Controls manual sleep mode for an area, bedroom area, or the house."""

    def __init__(self, area: MagicArea) -> None:
        """Initialize the area sleep mode switch."""
        super().__init__(area, translation_key=EntityNames.SLEEP_MODE)
        self._attr_is_on = bool(
            getattr(area, "sleep_mode_enabled", False)
            or area.config.get(CONF_SLEEP_MODE, DEFAULT_SLEEP_MODE)
        )
        self._expected_synced_state: bool | None = None

    async def async_added_to_hass(self) -> None:
        """Call when entity is added to hass."""
        self.area._sleep_mode_switch = self  # type: ignore[attr-defined]
        await super().async_added_to_hass()
        if self._attr_is_on and hasattr(self.area, "set_sleep_mode"):
            self.area.set_sleep_mode(True)

    async def async_will_remove_from_hass(self) -> None:
        """Clean up reference when removed from hass."""
        if getattr(self.area, "_sleep_mode_switch", None) is self:
            self.area._sleep_mode_switch = None  # type: ignore[attr-defined]
        await super().async_will_remove_from_hass()

    def _write_or_schedule_ha_state(self) -> None:
        """Write state immediately when on the event loop, or schedule thread-safely."""
        if (
            self.hass is not None
            and self.entity_id
            and getattr(self, "platform", None) is not None
        ):
            with suppress(RuntimeError):
                self.async_write_ha_state()
                return
        self.schedule_update_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on sleep mode for this area."""
        self._expected_synced_state = True
        self._attr_is_on = True
        self._write_or_schedule_ha_state()
        if hasattr(self.area, "set_sleep_mode"):
            self.area.set_sleep_mode(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off sleep mode for this area."""
        self._expected_synced_state = False
        self._attr_is_on = False
        self._write_or_schedule_ha_state()
        if hasattr(self.area, "set_sleep_mode"):
            self.area.set_sleep_mode(False)

    def set_synced_state(self, is_on: bool) -> None:
        """Update the switch state from internal sleep mode changes without re-triggering propagation."""
        target = bool(is_on)
        if self._attr_is_on == target:
            return
        self._expected_synced_state = target
        self._attr_is_on = target
        if self.hass is not None and self.entity_id:
            self._write_or_schedule_ha_state()

    @property
    def icon(self):
        """Return the icon to be used for this entity."""
        return ICON_SLEEP_MODE
