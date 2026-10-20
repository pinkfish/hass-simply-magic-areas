"""Light controls for magic areas."""

import logging
from datetime import UTC, datetime

from homeassistant.components.group.light import LightGroup
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    LightEntityDescription,
)
from homeassistant.components.light import (
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
)
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
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
)

from .base.entities import MagicEntity
from .base.magic import ControlType, MagicArea, StateConfigData
from .config.area_state import AreaState, LightingProfile
from .config.entity_names import EntityNames
from .const import (
    ATTR_LAST_UPDATE_FROM_ENTITY,
    ATTR_MANUAL_CONTROL,
    ATTR_MANUAL_TIMEOUT_TS,
    CONF_MANUAL_TIMEOUT,
    CONF_MAX_BRIGHTNESS_LEVEL,
    CONF_MIN_BRIGHTNESS_LEVEL,
    DATA_AREA_OBJECT,
    DEFAULT_MANUAL_TIMEOUT,
    DEFAULT_MAX_BRIGHTNESS_LEVEL,
    DEFAULT_MIN_BRIGHTNESS_LEVEL,
    DOMAIN,
    MODULE_DATA,
)
from .util import call_service_non_blocking

_LOGGER = logging.getLogger(__name__)
ATTR_LAST_ON_ILLUMINANCE: str = "last_on_illuminance"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Area config entry."""
    area: MagicArea = hass.data[MODULE_DATA][config_entry.entry_id][DATA_AREA_OBJECT]
    existing_light_entities: list[str] = []
    if DOMAIN + LIGHT_DOMAIN in area.entities:
        existing_light_entities = [
            e[ATTR_ENTITY_ID] for e in area.entities[DOMAIN + LIGHT_DOMAIN]
        ]
    # Check if there are any lights
    if not area.has_entities(LIGHT_DOMAIN):
        _LOGGER.debug("No %s entities for area %s ", LIGHT_DOMAIN, area.name)
        _cleanup_light_entities(area.hass, [], existing_light_entities)
        return
    if not area.is_control_enabled(ControlType.Light):
        _LOGGER.info("%s: Lights disabled for area (%s) ", area.name, LIGHT_DOMAIN)
        _cleanup_light_entities(area.hass, [], existing_light_entities)
        return

    light_groups: list[AreaLightGroup] = []

    # Create light groups
    light_entities = [e[ATTR_ENTITY_ID] for e in area.entities[LIGHT_DOMAIN]]
    if area.is_meta():
        # light_groups.append(MagicLightGroup(area, light_entities))
        pass
    else:
        # Create the ones with no entity automatically plus ones with an entity set
        light_group_object = AreaLightGroup(area, light_entities)
        light_groups.append(light_group_object)

    # Create all groups
    async_add_entities(light_groups)
    group_ids: list[str] = [e.entity_id for e in light_groups]
    _cleanup_light_entities(area.hass, group_ids, existing_light_entities)


def _cleanup_light_entities(
    hass: HomeAssistant, new_ids: list[str], old_ids: list[str]
) -> None:
    entity_registry = async_get_er(hass)
    for ent_id in old_ids:
        if ent_id in new_ids:
            continue
        _LOGGER.warning("Deleting old entity %s", ent_id)
        entity_registry.async_remove(ent_id)


class AreaLightGroup(MagicEntity, LightGroup):
    """The light group to control the area lights specifically.

    There is one light group created that will mutate with the different
    sets of lights to control for the various states.  The state will
    always reflect the current state of the system and lights entities in
    that state.
    """

    def __init__(self, area: MagicArea, entities: list[str]) -> None:
        """Init the light group for the area."""
        MagicEntity.__init__(self, area, domain=LIGHT_DOMAIN, translation_key="light")
        LightGroup.__init__(
            self,
            name=None,
            entity_ids=entities,
            unique_id=self._attr_unique_id,
            mode=False,
        )

        self.entity_description = LightEntityDescription(
            key="light",
            name=f"{self.area.name} Lights (Simply Magic Areas)",
            icon="mdi:ceiling-light",
            device_class=LIGHT_DOMAIN,
        )

        delattr(self, "_attr_name")
        self._manual_timeout_cb: CALLBACK_TYPE | None = None
        self._attr_icon: str = "mdi:ceiling-light"
        self._last_turn_off_ts: float = 0.0

        # Add static attributes
        self.last_update_from_entity: bool = False
        self._attr_extra_state_attributes["lights"] = self._entity_ids
        self._attr_extra_state_attributes[ATTR_LAST_UPDATE_FROM_ENTITY] = False

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
                self.last_update_from_entity = bool(
                    last_state.attributes[ATTR_LAST_UPDATE_FROM_ENTITY]
                )
                self._attr_extra_state_attributes[ATTR_LAST_UPDATE_FROM_ENTITY] = (
                    self.last_update_from_entity
                )

            if ATTR_MANUAL_CONTROL in last_state.attributes:
                is_manual = bool(last_state.attributes[ATTR_MANUAL_CONTROL])
                if is_manual:
                    self._attr_extra_state_attributes[ATTR_MANUAL_CONTROL] = True
                    target_ts = last_state.attributes.get(ATTR_MANUAL_TIMEOUT_TS)
                    now_ts = datetime.now(UTC).timestamp()
                    if target_ts is not None:
                        try:
                            remaining = float(target_ts) - now_ts
                            if remaining > 0:
                                self._attr_extra_state_attributes[
                                    ATTR_MANUAL_TIMEOUT_TS
                                ] = float(target_ts)
                                self._manual_timeout_cb = async_call_later(
                                    self.hass, remaining, self._reset_manual_timeout
                                )
                            else:
                                self._reset_manual_timeout()
                        except (ValueError, TypeError):
                            self._reset_manual_timeout()
                    else:
                        manual_timeout = self.area.config.get(
                            CONF_MANUAL_TIMEOUT, DEFAULT_MANUAL_TIMEOUT
                        )
                        self._attr_extra_state_attributes[ATTR_MANUAL_TIMEOUT_TS] = (
                            now_ts + manual_timeout
                        )
                        self._manual_timeout_cb = async_call_later(
                            self.hass, manual_timeout, self._reset_manual_timeout
                        )
        else:
            self._attr_is_on = False

        self.schedule_update_ha_state()

        # Setup state change listeners
        await self._setup_listeners()

        await super().async_added_to_hass()

    async def _setup_listeners(self) -> None:
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
                        SWITCH_DOMAIN, EntityNames.LIGHT_CONTROL
                    ),
                ],
                self._area_state_change,
            )
        )
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [
                    self.area.simply_magic_entity_id(
                        SENSOR_DOMAIN, EntityNames.LIGHTING_PROFILE
                    )
                ],
                self._profile_change,
            )
        )

    ### State Change Handling
    def _area_state_change(self, event: Event[EventStateChangedData]) -> None:
        if event.event_type != "state_changed":
            return
        if event.data["old_state"] is None or event.data["new_state"] is None:
            return

        from_state = event.data["old_state"].state
        if event.data["new_state"].state not in AreaState:
            _LOGGER.debug(
                "Light group (invalid to) %s. New state: %s / Last state %s",
                self.name,
                event.data["new_state"].state,
                from_state,
            )
            return
        to_state = AreaState(event.data["new_state"].state)
        from_state = (
            AreaState(event.data["old_state"].state)
            if event.data["old_state"].state in AreaState
            else None
        )

        _LOGGER.debug(
            "Light group %s. New state: %s / Last state %s",
            self.name,
            to_state,
            from_state,
        )

        if to_state == AreaState.AREA_STATE_CLEAR:
            self._turn_off_light()
            return

        if to_state == AreaState.AREA_STATE_MANUAL:
            _LOGGER.debug("%s: In manual mode, leaving lights as is", self.name)
            return

        if to_state in (
            AreaState.AREA_STATE_OCCUPIED,
            AreaState.AREA_STATE_BRIGHT,
            AreaState.AREA_STATE_SLEEP,
            AreaState.AREA_STATE_ACCENTED,
        ):
            conf = self._get_active_profile_config()
            if conf is not None:
                self._turn_on_light(conf, force=True)
            return

        if to_state == AreaState.AREA_STATE_EXTENDED:
            if self.area.has_configured_state(to_state):
                conf = self.area.state_config(to_state)
                if conf is not None:
                    self._turn_on_light(conf)
            else:
                self._turn_off_light()
            return

    def _get_active_profile_config(self) -> StateConfigData | None:
        """Resolve the active lighting profile configuration."""
        profile = getattr(self.area, "lighting_profile", LightingProfile.STANDARD)
        if self.area.has_configured_state(profile):
            return self.area.state_config(profile)
        if self.area.has_configured_state(AreaState.AREA_STATE_OCCUPIED):
            return self.area.state_config(AreaState.AREA_STATE_OCCUPIED)
        return None

    def _profile_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle changes to the area's lighting profile."""
        if event.data.get("new_state") is None:
            return
        new_profile = event.data["new_state"].state
        old_profile = (
            event.data["old_state"].state
            if event.data.get("old_state") is not None
            else None
        )
        if new_profile == old_profile:
            return

        _LOGGER.debug(
            "%s: Lighting profile changed from %s to %s",
            self.name,
            old_profile,
            new_profile,
        )

        occupied_states = {
            AreaState.AREA_STATE_OCCUPIED,
            AreaState.AREA_STATE_BRIGHT,
            AreaState.AREA_STATE_SLEEP,
            AreaState.AREA_STATE_ACCENTED,
        }

        # Profile change only updates lights when area is occupied
        if self.area.state in occupied_states:
            if self.is_on:
                conf = self._get_active_profile_config()
                if conf is not None:
                    self._turn_on_light(conf)
            else:
                _LOGGER.debug(
                    "%s: Lights are off; maintaining state on profile change from %s to %s",
                    self.name,
                    old_profile,
                    new_profile,
                )

    def _update_group_state(self, event: Event[EventStateChangedData]) -> None:
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if old_state is None or new_state is None:
            return

        if old_state.attributes.get("restored"):
            # On state restored, if already scheduled from async_added_to_hass, do not rearm.
            if not self._is_controlled_by_this_entity() and self._manual_timeout_cb is None:
                manual_timeout = self.area.config.get(
                    CONF_MANUAL_TIMEOUT, DEFAULT_MANUAL_TIMEOUT
                )
                now_ts = datetime.now(UTC).timestamp()
                self._attr_extra_state_attributes[ATTR_MANUAL_TIMEOUT_TS] = (
                    now_ts + manual_timeout
                )
                self._manual_timeout_cb = async_call_later(
                    self.hass, manual_timeout, self._reset_manual_timeout
                )
            return

        occupied_states = {
            AreaState.AREA_STATE_OCCUPIED,
            AreaState.AREA_STATE_BRIGHT,
            AreaState.AREA_STATE_SLEEP,
            AreaState.AREA_STATE_ACCENTED,
        }

        # If light group turned off while the area is occupied and not in manual override,
        # recover from any racing turn-off by immediately re-asserting turn on.
        if (
            new_state.state == STATE_OFF
            and self.area.state in occupied_states
            and not self._attr_extra_state_attributes.get(ATTR_MANUAL_CONTROL, False)
        ):
            conf = self._get_active_profile_config()
            if conf is not None and conf.dim_level > 0:
                _LOGGER.info(
                    "%s: Light turned off while area is %s, re-asserting turn on",
                    self.name,
                    self.area.state,
                )
                self._turn_on_light(conf, force=True)
                return

        if self.area.state != AreaState.AREA_STATE_CLEAR:
            self._reset_control()
        else:
            # Skip non ON/OFF state changes
            if old_state.state not in [
                STATE_ON,
                STATE_OFF,
            ]:
                return
            if new_state.state not in [
                STATE_ON,
                STATE_OFF,
            ]:
                return
            manual_timeout = self.area.config.get(
                CONF_MANUAL_TIMEOUT, DEFAULT_MANUAL_TIMEOUT
            )
            if self.last_update_from_entity:
                self.last_update_from_entity = False
                return
            self._set_controlled_by_this_entity(False)
            if self._manual_timeout_cb is not None:
                self._manual_timeout_cb()
            now_ts = datetime.now(UTC).timestamp()
            self._attr_extra_state_attributes[ATTR_MANUAL_TIMEOUT_TS] = (
                now_ts + manual_timeout
            )
            self._manual_timeout_cb = async_call_later(
                self.hass, manual_timeout, self._reset_manual_timeout
            )

    def _reset_manual_timeout(self, now: datetime | None = None) -> None:
        if self._manual_timeout_cb is not None:
            self._manual_timeout_cb = None
        self._set_controlled_by_this_entity(True)
        self.schedule_update_ha_state()

    ####  Light Handling
    def _turn_on_light(self, conf: StateConfigData, force: bool = False) -> None:
        """Turn on the light group."""

        self._entity_ids = conf.lights
        self.async_update_group_state()
        _LOGGER.debug(
            "Update light group %s %s %s %s %s",
            self.is_on,
            self.brightness,
            self.area.entities.keys(),
            conf.lights,
            self.area.entities[LIGHT_DOMAIN],
        )

        brightness = int(conf.dim_level * 255 / 100)
        now_ts = datetime.now(UTC).timestamp()
        recent_turn_off = (now_ts - getattr(self, "_last_turn_off_ts", 0.0)) < 3.0
        if not force and not recent_turn_off and self.is_on and self.brightness == brightness:
            _LOGGER.debug("%s: Already on at %s", self.name, brightness)
            return

        luminesence = self._get_illuminance()
        min_brightness = self.area.config.get(
            CONF_MIN_BRIGHTNESS_LEVEL, DEFAULT_MIN_BRIGHTNESS_LEVEL
        )
        _LOGGER.debug(
            "%s: Checking brightness to %s %s,  %s, %s -- %s",
            self.name,
            self.is_on,
            luminesence,
            min_brightness,
            self.area.config.get(
                CONF_MAX_BRIGHTNESS_LEVEL, DEFAULT_MAX_BRIGHTNESS_LEVEL
            ),
            self._attr_extra_state_attributes.get(ATTR_LAST_ON_ILLUMINANCE, 0),
        )
        if luminesence > min_brightness:
            max_brightness = self.area.config.get(
                CONF_MAX_BRIGHTNESS_LEVEL, DEFAULT_MAX_BRIGHTNESS_LEVEL
            )
            if luminesence > max_brightness:
                brightness = 0
            else:
                diff = luminesence - min_brightness
                brightness = int(
                    brightness * (1.0 - diff / (max_brightness - min_brightness))
                )
                _LOGGER.debug(
                    "%s: Updating brightness to %s 1.0 - %s / %s",
                    self.name,
                    brightness,
                    diff,
                    (max_brightness - min_brightness),
                )
        if not self.area.is_control_enabled(ControlType.System):
            return

        if brightness == 0:
            _LOGGER.debug("%s: Brightness is 0", self.name)
            self._turn_off_light()
            return

        _LOGGER.debug("Turning on lights")
        self.last_update_from_entity = True
        self._attr_is_on = True
        service_data = {
            ATTR_ENTITY_ID: self.entity_id,
            ATTR_BRIGHTNESS: brightness,
        }
        call_service_non_blocking(
            self.hass, LIGHT_DOMAIN, SERVICE_TURN_ON, service_data
        )

        return

    def _turn_off_light(self) -> None:
        """Turn off the light group."""
        occupied_states = {
            AreaState.AREA_STATE_OCCUPIED,
            AreaState.AREA_STATE_BRIGHT,
            AreaState.AREA_STATE_SLEEP,
            AreaState.AREA_STATE_ACCENTED,
        }
        # Safety check: Never turn off lights if the area is currently in an occupied state
        if self.area.state in occupied_states:
            _LOGGER.debug(
                "%s: Area is in occupied state %s, aborting turn off",
                self.name,
                self.area.state,
            )
            return

        if not self.is_on:
            _LOGGER.debug("%s: Light already off", self.name)
            return

        if not self.area.is_control_enabled(ControlType.System):
            return

        self._last_turn_off_ts = datetime.now(UTC).timestamp()
        self.last_update_from_entity = True
        self._attr_is_on = False
        service_data = {ATTR_ENTITY_ID: self.entity_id}
        # await self.async_turn_off()
        call_service_non_blocking(
            self.hass, LIGHT_DOMAIN, SERVICE_TURN_OFF, service_data
        )

        return

    def _get_illuminance(self) -> float:
        if self.is_on:
            if ATTR_LAST_ON_ILLUMINANCE in self._attr_extra_state_attributes:
                return (
                    self._attr_extra_state_attributes[ATTR_LAST_ON_ILLUMINANCE] or 0.0
                )
            return 0.0

        val = self.area.get_effective_illuminance()
        if val is None:
            self._attr_extra_state_attributes[ATTR_LAST_ON_ILLUMINANCE] = 0.0
            return 0.0
        self._attr_extra_state_attributes[ATTR_LAST_ON_ILLUMINANCE] = val
        return val

    #### Control Release
    def _is_controlled_by_this_entity(self) -> bool:
        return not self._attr_extra_state_attributes.get(ATTR_MANUAL_CONTROL, False)

    def _set_controlled_by_this_entity(self, enabled: bool) -> None:
        if enabled:
            self._attr_extra_state_attributes.pop(ATTR_MANUAL_CONTROL, None)
            self._attr_extra_state_attributes.pop(ATTR_MANUAL_TIMEOUT_TS, None)
        else:
            self._attr_extra_state_attributes[ATTR_MANUAL_CONTROL] = True

    def _reset_control(self) -> None:
        if self._manual_timeout_cb is not None:
            self._manual_timeout_cb()
            self._manual_timeout_cb = None
        self._set_controlled_by_this_entity(True)
        self.schedule_update_ha_state()
