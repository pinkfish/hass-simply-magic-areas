"""Fan controls for magic areas."""

from datetime import UTC, datetime
import logging

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.components.group.fan import FanGroup
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity_registry import async_get as async_get_er
from homeassistant.helpers.event import async_track_state_change_event, call_later
from homeassistant.util import slugify

from .base.entities import MagicEntity
from .base.magic import ControlType, MagicArea
from .config.area_state import AreaState
from .config.entity_names import EntityNames
from .const import (
    ATTR_LAST_UPDATE_FROM_ENTITY,
    CONF_MANUAL_TIMEOUT,
    DATA_AREA_OBJECT,
    DEFAULT_MANUAL_TIMEOUT,
    DOMAIN,
    MODULE_DATA,
)

_LOGGER = logging.getLogger(__name__)
ATTR_HUMIDITY_UP: str = "humidity_up"
ATTR_FANS: str = "fans"
ATTR_MANUAL_CONTROL: str = "manual_control"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Area config entry."""

    area: MagicArea = hass.data[MODULE_DATA][config_entry.entry_id][DATA_AREA_OBJECT]

    existing_fan_entities: list[str] = []
    if DOMAIN + FAN_DOMAIN in area.entities:
        existing_fan_entities = [
            e["entity_id"] for e in area.entities[DOMAIN + FAN_DOMAIN]
        ]
    # Check if there are any fans
    if not area.has_entities(FAN_DOMAIN):
        _LOGGER.debug("%s: No fans for area (%s) ", area.name, FAN_DOMAIN)
        _cleanup_fan_entities(area.hass, [], existing_fan_entities)
        return
    if not area.is_control_enabled(ControlType.Fan):
        _LOGGER.info("%s: Fans disabled for area (%s) ", area.name, FAN_DOMAIN)
        _cleanup_fan_entities(area.hass, [], existing_fan_entities)
        return
    if area.is_meta():
        _LOGGER.debug("%s: No fan controls for meta area")
        _cleanup_fan_entities(area.hass, [], existing_fan_entities)
        return

    fan_groups = []

    # Find fans
    fan_entities = [e[ATTR_ENTITY_ID] for e in area.entities[FAN_DOMAIN]]

    # Create the ones with no entity automatically plus ones with an entity set
    fan_group_object = AreaFanGroup(area, fan_entities)
    fan_groups.append(fan_group_object)

    # Create all groups
    async_add_entities(fan_groups)
    group_ids = [e.entity_id for e in fan_groups]
    _cleanup_fan_entities(area.hass, group_ids, existing_fan_entities)


def _cleanup_fan_entities(
    hass: HomeAssistant, new_ids: list[str], old_ids: list[str]
) -> None:
    entity_registry = async_get_er(hass)
    for ent_id in old_ids:
        if ent_id in new_ids:
            continue
        _LOGGER.warning("Deleting old entity %s", ent_id)
        entity_registry.async_remove(ent_id)


class AreaFanGroup(MagicEntity, FanGroup):
    """The fan group to control the area fans specifically.

    There is one fan group created that will mutate with the different
    sets of fans to control for the various states.  The state will
    always reflect the current state of the system and fans entities in
    that state.
    """

    def __init__(self, area: MagicArea, entities: list[str]) -> None:
        """Init the fan group for the area."""
        MagicEntity.__init__(self, area=area, domain=FAN_DOMAIN, translation_key="fan")
        FanGroup.__init__(
            self,
            entities=entities,
            name="",
            unique_id=f"light_{slugify(area.name)}",
        )

        delattr(self, "_attr_name")
        self._icon: str = "mdi:fan-auto"
        self._manual_timeout_cb: CALLBACK_TYPE | None = None

        self._controled_by_entity = True

        # Add static attributes
        self.last_update_from_entity: bool = False
        self._attr_extra_state_attributes[ATTR_FANS] = self._entity_ids
        self._attr_extra_state_attributes[ATTR_LAST_UPDATE_FROM_ENTITY] = False

    @property
    def icon(self) -> str:
        """Return the icon to be used for this entity."""
        return self._icon

    async def async_added_to_hass(self) -> None:
        """Run when this is added into hass."""
        # Get last state
        last_state = await self.async_get_last_state()

        if last_state:
            _LOGGER.debug(
                "%s restored [state=%s]",
                self.name,
                last_state.state,
            )
            self._attr_is_on = last_state.state == STATE_ON

            if ATTR_LAST_UPDATE_FROM_ENTITY in last_state.attributes:
                self.last_update_from_entity = last_state.attributes[
                    ATTR_LAST_UPDATE_FROM_ENTITY
                ]
                self._attr_extra_state_attributes[ATTR_LAST_UPDATE_FROM_ENTITY] = (
                    self.last_update_from_entity
                )
        else:
            self._attr_is_on = False

        self.schedule_update_ha_state()

        # Setup state change listeners
        await self._setup_listeners()

        await super().async_added_to_hass()

    async def _setup_listeners(self, _=None) -> None:
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [self.entity_id],
                self._update_group_state,
            )
        )
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [
                    self.area.simply_magic_entity_id(SENSOR_DOMAIN, EntityNames.STATE),
                    self.area.simply_magic_entity_id(
                        SWITCH_DOMAIN, EntityNames.FAN_CONTROL
                    ),
                ],
                self._area_state_change,
            )
        )
        # If the trend entities exist, listen to them
        trend_up = self.area.simply_magic_entity_id(
            BINARY_SENSOR_DOMAIN, EntityNames.HUMIDITY_OCCUPIED
        )
        trend_down = self.area.simply_magic_entity_id(
            BINARY_SENSOR_DOMAIN,
            EntityNames.HUMIDITY_EMPTY,
        )
        if (
            self.hass.states.get(trend_up) is not None
            and self.hass.states.get(trend_down) is not None
        ):
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [trend_up], self._trend_up_state_change
                )
            )
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [trend_down], self._trend_down_state_change
                )
            )

    ### State Change Handling
    def _area_state_change(self, event: Event[EventStateChangedData]) -> None:
        if event.data["old_state"] is None or event.data["new_state"] is None:
            return

        from_state = event.data["old_state"].state
        to_state = event.data["new_state"].state

        _LOGGER.debug(
            "%s: Fan group New state: %s / Last state %s (Humidity %s On: %s)",
            self.name,
            to_state,
            from_state,
            self._attr_extra_state_attributes.get(ATTR_HUMIDITY_UP, False),
            self.is_on,
        )

        # Manual control, ignore all changes
        if to_state == AreaState.AREA_STATE_MANUAL:
            return

        # For fans we only worry about the extended state.
        if to_state == AreaState.AREA_STATE_EXTENDED:
            self._turn_on_fan()
        elif self.is_on and not self._attr_extra_state_attributes.get(
            ATTR_HUMIDITY_UP, False
        ):
            self._turn_off_fan()
        else:
            _LOGGER.debug("Ignoring state change")

    def _trend_down_state_change(self, event: Event[EventStateChangedData]):
        if event.data["old_state"] is None or event.data["new_state"] is None:
            return
        to_state = event.data["new_state"].state
        from_state = event.data["old_state"].state
        _LOGGER.debug(
            "%s: Trend down New state: %s / Last state %s",
            self.name,
            to_state,
            from_state,
        )
        if to_state == STATE_ON and from_state != STATE_ON:
            # We have stuff going down.
            self._attr_extra_state_attributes[ATTR_HUMIDITY_UP] = False
            self._turn_off_fan()

    def _trend_up_state_change(self, event: Event[EventStateChangedData]):
        if event.event_type != "state_changed":
            return
        if event.data["old_state"] is None or event.data["new_state"] is None:
            return
        to_state = event.data["new_state"].state
        from_state = event.data["old_state"].state
        _LOGGER.debug(
            "%s: Trend up New state: %s / Last state %s",
            self.name,
            to_state,
            from_state,
        )
        if to_state == STATE_ON and from_state != STATE_ON:
            # We have stuff going up.
            self._attr_extra_state_attributes[ATTR_HUMIDITY_UP] = True
            self._turn_on_fan()

    def _update_group_state(self, event: Event[EventStateChangedData]) -> None:
        if self.area.state == AreaState.AREA_STATE_CLEAR:
            self._reset_control(datetime.now(UTC))
        else:
            # Skip non ON/OFF state changes
            if not event.data["old_state"] or event.data["old_state"].state not in [
                STATE_ON,
                STATE_OFF,
            ]:
                return
            if not event.data["new_state"] or event.data["new_state"].state not in [
                STATE_ON,
                STATE_OFF,
            ]:
                return
            manual_timeout = self.area.config.get(
                CONF_MANUAL_TIMEOUT, DEFAULT_MANUAL_TIMEOUT
            )
            if (
                "restored" in event.data["old_state"].attributes
                and event.data["old_state"].attributes["restored"]
            ):
                # On state restored, also setup the timeout callback.
                if not self._is_controlled_by_this_entity():
                    if self._manual_timeout_cb is not None:
                        self._manual_timeout_cb()
                    self._manual_timeout_cb = call_later(
                        self.hass, manual_timeout, self._reset_manual_timeout
                    )
                return
            if self.last_update_from_entity:
                self.last_update_from_entity = False
                return
            self._set_controlled_by_this_entity(False)
            if self._manual_timeout_cb is not None:
                self._manual_timeout_cb()
            self._manual_timeout_cb = call_later(
                self.hass, manual_timeout, self._reset_manual_timeout
            )

    def _reset_manual_timeout(self, dt: datetime) -> None:
        self._set_controlled_by_this_entity(True)
        self._manual_timeout_cb = None

    ####  Fan Handling
    def _turn_on_fan(self) -> None:
        """Turn on the fan group."""

        if not self.area.is_control_enabled(ControlType.System):
            return

        _LOGGER.debug("%s: Turning on fans", self.name)
        self.last_update_from_entity = True
        service_data = {
            ATTR_ENTITY_ID: self.entity_id,
        }
        self.hass.services.call(FAN_DOMAIN, SERVICE_TURN_ON, service_data)

    def _turn_off_fan(self) -> None:
        """Turn off the fan group."""
        if not self.is_on:
            _LOGGER.debug("%s: Fan already off", self.name)
            return

        if not self.area.is_control_enabled(ControlType.System):
            return
        _LOGGER.debug("%s: Turning fan off", self.name)
        self.last_update_from_entity = True
        service_data = {ATTR_ENTITY_ID: self.entity_id}
        self.hass.services.call(FAN_DOMAIN, SERVICE_TURN_OFF, service_data)

    #### Control Release
    def _is_controlled_by_this_entity(self) -> bool:
        return self._attr_extra_state_attributes.get(ATTR_MANUAL_CONTROL, True)

    def _set_controlled_by_this_entity(self, enabled: bool) -> None:
        self._attr_extra_state_attributes.get(ATTR_MANUAL_CONTROL, enabled)

    def _reset_control(self, time: datetime) -> None:
        self._set_controlled_by_this_entity(True)
        self.schedule_update_ha_state()
        _LOGGER.debug("%s: Control Reset", self.name)
