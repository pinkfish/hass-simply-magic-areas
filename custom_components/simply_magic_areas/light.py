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
    EVENT_STATE_CHANGED,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_HOME,
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
    ATTR_DARK_LUX_FLOOR,
    ATTR_DARKNESS_RATIO,
    ATTR_EXTERIOR_LIGHTING_ACTIVE,
    ATTR_EXTERIOR_TIMER_TS,
    ATTR_EXTERIOR_TRIGGER_REASON,
    ATTR_HISTORICAL_DAYLIGHT_LUX,
    ATTR_IS_WINDOWLESS,
    ATTR_LAST_UPDATE_FROM_ENTITY,
    ATTR_MANUAL_CONTROL,
    ATTR_MANUAL_TIMEOUT_TS,
    CAMERA_DOMAIN,
    CONF_DARK_LUX_FLOOR,
    CONF_DAYLIGHT_LUX_LEVEL,
    CONF_EXTERIOR_ARRIVAL_TRIGGER,
    CONF_EXTERIOR_LIGHTING_TIMEOUT,
    CONF_EXTERIOR_SUNSET_TRIGGER,
    CONF_FEATURE_ADVANCED_LIGHT_GROUPS,
    CONF_LEARN_DAYLIGHT_LUX,
    CONF_LEARN_LIGHTING_PREFERENCES,
    CONF_MANUAL_TIMEOUT,
    CONF_MAX_BRIGHTNESS_LEVEL,
    CONF_MIN_BRIGHTNESS_LEVEL,
    DATA_AREA_OBJECT,
    DEFAULT_DARK_LUX_FLOOR,
    DEFAULT_EXTERIOR_ARRIVAL_TRIGGER,
    DEFAULT_EXTERIOR_LIGHTING_TIMEOUT,
    DEFAULT_EXTERIOR_SUNSET_TRIGGER,
    DEFAULT_LEARN_LIGHTING_PREFERENCES,
    DEFAULT_MANUAL_TIMEOUT,
    DEFAULT_MAX_BRIGHTNESS_LEVEL,
    DEFAULT_MIN_BRIGHTNESS_LEVEL,
    DOMAIN,
    META_AREA_EXTERIOR,
    MODULE_DATA,
)
from .ml.lighting_learner import OnlineBayesianLightingLearner
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

    @property
    def is_exterior(self) -> bool:
        """Return True if this area is an exterior area or exterior meta-area."""
        return self.area.is_exterior() or (
            self.area.is_meta() and self.area.id.lower() == META_AREA_EXTERIOR.lower()
        )

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

        # Exterior lighting state
        self._exterior_timer_cb: CALLBACK_TYPE | None = None
        self._exterior_timer_ts: float | None = None
        self._exterior_trigger_reason: str | None = None

        # Add static attributes
        self.last_update_from_entity: bool = False
        self._attr_extra_state_attributes["lights"] = self._entity_ids
        self._attr_extra_state_attributes[ATTR_LAST_UPDATE_FROM_ENTITY] = False

        self._lighting_learner: OnlineBayesianLightingLearner = (
            OnlineBayesianLightingLearner(area.hass, area.slug)
        )
        self._attr_extra_state_attributes.update(
            self._lighting_learner.get_attributes()
        )

        if self.is_exterior:
            self._attr_extra_state_attributes[ATTR_EXTERIOR_LIGHTING_ACTIVE] = False
            self._attr_extra_state_attributes[ATTR_EXTERIOR_TIMER_TS] = None
            self._attr_extra_state_attributes[ATTR_EXTERIOR_TRIGGER_REASON] = None

    async def async_added_to_hass(self) -> None:
        """Run when this is added into hass."""
        await self._lighting_learner.async_load()
        self._attr_extra_state_attributes.update(
            self._lighting_learner.get_attributes()
        )

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

            if self.is_exterior and ATTR_EXTERIOR_LIGHTING_ACTIVE in last_state.attributes:
                ext_active = bool(last_state.attributes[ATTR_EXTERIOR_LIGHTING_ACTIVE])
                target_ts = last_state.attributes.get(ATTR_EXTERIOR_TIMER_TS)
                trigger_reason = last_state.attributes.get(ATTR_EXTERIOR_TRIGGER_REASON)
                now_ts = datetime.now(UTC).timestamp()
                if ext_active and target_ts is not None and self._is_nighttime():
                    try:
                        remaining = float(target_ts) - now_ts
                        if remaining > 0:
                            self._exterior_timer_ts = float(target_ts)
                            self._exterior_trigger_reason = (
                                str(trigger_reason) if trigger_reason else "restored"
                            )
                            self._attr_extra_state_attributes[
                                ATTR_EXTERIOR_LIGHTING_ACTIVE
                            ] = True
                            self._attr_extra_state_attributes[
                                ATTR_EXTERIOR_TIMER_TS
                            ] = self._exterior_timer_ts
                            self._attr_extra_state_attributes[
                                ATTR_EXTERIOR_TRIGGER_REASON
                            ] = self._exterior_trigger_reason
                            self._exterior_timer_cb = async_call_later(
                                self.hass, remaining, self._exterior_timer_expired
                            )
                        else:
                            self._clear_exterior_lighting()
                    except (ValueError, TypeError):
                        self._clear_exterior_lighting()
                else:
                    self._clear_exterior_lighting()
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

        if self.is_exterior:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    ["sun.sun"],
                    self._sun_state_change,
                )
            )
            cam_entities = [
                e[ATTR_ENTITY_ID]
                for e in self.area.entities.get(CAMERA_DOMAIN, [])
            ]
            if cam_entities:
                self.async_on_remove(
                    async_track_state_change_event(
                        self.hass,
                        cam_entities,
                        self._camera_state_change,
                    )
                )
            self.async_on_remove(
                self.hass.bus.async_listen(
                    EVENT_STATE_CHANGED,
                    self._handle_global_state_change,
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

        occupied_states = {
            AreaState.AREA_STATE_OCCUPIED,
            AreaState.AREA_STATE_BRIGHT,
            AreaState.AREA_STATE_SLEEP,
            AreaState.AREA_STATE_ACCENTED,
            AreaState.AREA_STATE_EXTENDED,
        }

        was_occupied = from_state in occupied_states
        now_occupied = to_state in occupied_states
        occupancy_changed = (was_occupied != now_occupied) or (
            from_state == AreaState.AREA_STATE_MANUAL
        )

        if to_state == AreaState.AREA_STATE_MANUAL:
            _LOGGER.debug("%s: In manual mode, leaving lights as is", self.name)
            return

        if occupancy_changed:
            if not self._is_controlled_by_this_entity():
                _LOGGER.info(
                    "%s: Occupancy state changed from %s to %s; resetting manual override",
                    self.name,
                    from_state,
                    to_state,
                )
                self._reset_control()
        elif not self._is_controlled_by_this_entity():
            _LOGGER.debug(
                "%s: In manual override and occupancy status did not change (%s -> %s); holding state",
                self.name,
                from_state,
                to_state,
            )
            return

        if to_state == AreaState.AREA_STATE_CLEAR:
            if self.is_exterior:
                if (
                    from_state in occupied_states
                    and self._attr_extra_state_attributes.get(
                        ATTR_EXTERIOR_LIGHTING_ACTIVE, False
                    )
                    and self._is_nighttime()
                ):
                    _LOGGER.debug(
                        "%s: Movement ceased in exterior area at night; restarting 2-hour countdown",
                        self.name,
                    )
                    self._trigger_exterior_lighting(reason="motion")
                    return
                if not self._is_nighttime():
                    self._clear_exterior_lighting()
                    self._turn_off_light()
                    return
                if self._attr_extra_state_attributes.get(
                    ATTR_EXTERIOR_LIGHTING_ACTIVE, False
                ):
                    return
            self._turn_off_light()
            return

        if to_state in (
            AreaState.AREA_STATE_OCCUPIED,
            AreaState.AREA_STATE_BRIGHT,
            AreaState.AREA_STATE_SLEEP,
            AreaState.AREA_STATE_ACCENTED,
        ):
            if self.is_exterior:
                if not self._is_nighttime():
                    _LOGGER.debug(
                        "%s: Exterior area occupied during daytime; lights suppressed",
                        self.name,
                    )
                    return
                self._trigger_exterior_lighting(reason="motion")
                return

            conf = self._get_active_profile_config()
            if conf is not None:
                self._turn_on_light(conf, force=True)
            return

        if to_state == AreaState.AREA_STATE_EXTENDED:
            if self.is_exterior:
                if not self._is_nighttime():
                    self._turn_off_light()
                    return
                self._trigger_exterior_lighting(reason="motion")
                return

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

        if self.is_exterior and not self._is_nighttime():
            return

        occupied_states = {
            AreaState.AREA_STATE_OCCUPIED,
            AreaState.AREA_STATE_BRIGHT,
            AreaState.AREA_STATE_SLEEP,
            AreaState.AREA_STATE_ACCENTED,
        }

        # Profile change only updates lights when area is occupied
        if self.area.state in occupied_states:
            if not self._is_controlled_by_this_entity():
                _LOGGER.debug(
                    "%s: In manual override; ignoring profile change from %s to %s",
                    self.name,
                    old_profile,
                    new_profile,
                )
                return
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

        # Check for user manual brightness adjustment while light remains ON
        if (
            old_state.state == STATE_ON
            and new_state.state == STATE_ON
            and old_state.attributes.get(ATTR_BRIGHTNESS)
            != new_state.attributes.get(ATTR_BRIGHTNESS)
        ):
            if self.last_update_from_entity:
                self.last_update_from_entity = False
                return

            new_brightness = new_state.attributes.get(ATTR_BRIGHTNESS)
            if new_brightness is not None and isinstance(new_brightness, (int, float)):
                lux = self._get_illuminance()
                profile = getattr(
                    self.area, "lighting_profile", LightingProfile.STANDARD
                )
                sun_state = self.hass.states.get("sun.sun")
                solar_elevation = None
                if sun_state and "elevation" in sun_state.attributes:
                    try:
                        solar_elevation = float(sun_state.attributes["elevation"])
                    except (ValueError, TypeError):
                        pass

                _LOGGER.info(
                    "%s: Manual brightness adjustment to %s (lux=%s); learning preference",
                    self.name,
                    new_brightness,
                    lux,
                )
                self._lighting_learner.learn_preference(
                    lux=lux,
                    profile=profile,
                    solar_elevation=solar_elevation,
                    target_brightness=int(new_brightness),
                )
                self._attr_extra_state_attributes.update(
                    self._lighting_learner.get_attributes()
                )
                self._set_controlled_by_this_entity(False)
                self.schedule_update_ha_state()
            return

        # Skip non ON/OFF state changes or attribute-only updates
        if old_state.state == new_state.state:
            return
        if old_state.state not in [
            STATE_ON,
            STATE_OFF,
        ] or new_state.state not in [
            STATE_ON,
            STATE_OFF,
        ]:
            return

        self._attr_is_on = new_state.state == STATE_ON

        # If this update was initiated by internal automation, consume the flag and return
        if self.last_update_from_entity:
            self.last_update_from_entity = False
            return

        # External manual interaction (physical switch, UI, external integration)
        _LOGGER.debug(
            "%s: Light turned %s externally; entering manual override until occupied state change",
            self.name,
            new_state.state,
        )

        if (
            self.is_exterior
            and new_state.state == STATE_OFF
        ):
            self._clear_exterior_lighting()

        self._set_controlled_by_this_entity(False)
        if self._manual_timeout_cb is not None:
            self._manual_timeout_cb()
            self._manual_timeout_cb = None
        self.schedule_update_ha_state()

    def _reset_manual_timeout(self, now: datetime | None = None) -> None:
        if self._manual_timeout_cb is not None:
            self._manual_timeout_cb = None
        self._set_controlled_by_this_entity(True)
        self.schedule_update_ha_state()

    ####  Light Handling
    def _turn_on_light(self, conf: StateConfigData, force: bool = False) -> None:
        """Turn on the light group."""

        if not force and not self._is_controlled_by_this_entity():
            _LOGGER.debug(
                "%s: Light in manual override, aborting automatic turn on",
                self.name,
            )
            return

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

        # Determine effective floor and daylight levels
        adv_conf = self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS)
        dark_lux_floor = float(
            getattr(self.area, "dark_lux_floor", None)
            or adv_conf.get(CONF_DARK_LUX_FLOOR)
            or self.area.config.get(CONF_DARK_LUX_FLOOR, DEFAULT_DARK_LUX_FLOOR)
        )
        is_windowless = bool(getattr(self.area, "is_windowless", False))
        historical_daylight = getattr(self.area, "historical_daylight_lux", None)

        # Check for windowless room or absolute dark floor guarantee
        if is_windowless or luminesence <= dark_lux_floor:
            darkness_ratio = 1.0
            _LOGGER.debug(
                "%s: Ambient lux %s is at or below dark floor %s (or windowless: %s) -> full brightness %s",
                self.name,
                luminesence,
                dark_lux_floor,
                is_windowless,
                brightness,
            )
        else:
            manual_daylight = float(
                adv_conf.get(CONF_DAYLIGHT_LUX_LEVEL)
                or self.area.config.get(CONF_DAYLIGHT_LUX_LEVEL, 0)
            )
            learn_daylight = bool(
                adv_conf.get(CONF_LEARN_DAYLIGHT_LUX)
                or self.area.config.get(CONF_LEARN_DAYLIGHT_LUX, False)
            )
            if manual_daylight > (dark_lux_floor + 5.0):
                lower_lux = dark_lux_floor
                upper_lux = manual_daylight
            elif (
                learn_daylight
                and historical_daylight is not None
                and historical_daylight > (dark_lux_floor + 5.0)
            ):
                # Autonomously learned daylight baseline for this specific room's sensor
                lower_lux = dark_lux_floor
                upper_lux = float(historical_daylight)
            else:
                # Configured / fallback thresholds
                lower_lux = float(
                    self.area.config.get(
                        CONF_MIN_BRIGHTNESS_LEVEL, DEFAULT_MIN_BRIGHTNESS_LEVEL
                    )
                )
                upper_lux = float(
                    self.area.config.get(
                        CONF_MAX_BRIGHTNESS_LEVEL, DEFAULT_MAX_BRIGHTNESS_LEVEL
                    )
                )

            if luminesence >= upper_lux:
                darkness_ratio = 0.0
                brightness = 0
                _LOGGER.debug(
                    "%s: Ambient lux %s exceeds daylight level %s -> brightness 0",
                    self.name,
                    luminesence,
                    upper_lux,
                )
            elif luminesence > lower_lux:
                darkness_ratio = (upper_lux - luminesence) / (upper_lux - lower_lux)
                brightness = int(brightness * darkness_ratio)
                _LOGGER.debug(
                    "%s: Proportional darkness compensation: lux=%s, range=[%s, %s], ratio=%.2f -> brightness %s",
                    self.name,
                    luminesence,
                    lower_lux,
                    upper_lux,
                    darkness_ratio,
                    brightness,
                )
            else:
                darkness_ratio = 1.0

        self._attr_extra_state_attributes.update(
            {
                ATTR_LAST_ON_ILLUMINANCE: luminesence,
                ATTR_DARK_LUX_FLOOR: dark_lux_floor,
                ATTR_DARKNESS_RATIO: round(darkness_ratio, 2),
                ATTR_IS_WINDOWLESS: is_windowless,
            }
        )
        if historical_daylight is not None:
            self._attr_extra_state_attributes[ATTR_HISTORICAL_DAYLIGHT_LUX] = round(
                float(historical_daylight), 1
            )

        # Apply local ML Bayesian preference learning if enabled and samples exist
        learn_preferences = bool(
            adv_conf.get(
                CONF_LEARN_LIGHTING_PREFERENCES,
                DEFAULT_LEARN_LIGHTING_PREFERENCES,
            )
        )
        if (
            learn_preferences
            and self._lighting_learner
            and self._lighting_learner.sample_count > 0
            and brightness > 0
        ):
            sun_state = self.hass.states.get("sun.sun")
            solar_elevation = None
            if sun_state and "elevation" in sun_state.attributes:
                try:
                    solar_elevation = float(sun_state.attributes["elevation"])
                except (ValueError, TypeError):
                    pass
            ml_brightness = self._lighting_learner.predict_brightness(
                lux=luminesence,
                profile=getattr(
                    self.area, "lighting_profile", LightingProfile.STANDARD
                ),
                solar_elevation=solar_elevation,
                default_brightness=brightness,
            )
            if ml_brightness > 0:
                _LOGGER.debug(
                    "%s: ML adaptive lighting adjusted brightness from %s to %s (samples=%s, conf=%.2f)",
                    self.name,
                    brightness,
                    ml_brightness,
                    self._lighting_learner.sample_count,
                    self._lighting_learner.confidence,
                )
                brightness = ml_brightness

        if self._lighting_learner:
            self._attr_extra_state_attributes.update(
                self._lighting_learner.get_attributes()
            )

        if not self.area.is_control_enabled(ControlType.System):
            return

        if brightness == 0:
            _LOGGER.debug("%s: Brightness is 0", self.name)
            self._turn_off_light(force=True)
            return

        _LOGGER.debug("Turning on lights")
        self.last_update_from_entity = True
        self._attr_is_on = True
        self.schedule_update_ha_state()
        service_data = {
            ATTR_ENTITY_ID: self.entity_id,
            ATTR_BRIGHTNESS: brightness,
        }
        call_service_non_blocking(
            self.hass, LIGHT_DOMAIN, SERVICE_TURN_ON, service_data
        )

        return

    def _turn_off_light(self, force: bool = False) -> None:
        """Turn off the light group."""
        if not force and not self._is_controlled_by_this_entity():
            _LOGGER.debug(
                "%s: Light in manual override, aborting automatic turn off",
                self.name,
            )
            return

        occupied_states = {
            AreaState.AREA_STATE_OCCUPIED,
            AreaState.AREA_STATE_BRIGHT,
            AreaState.AREA_STATE_SLEEP,
            AreaState.AREA_STATE_ACCENTED,
        }
        # Safety check: Never turn off lights if the area is currently in an occupied state (unless forced)
        if not force and self.area.state in occupied_states:
            _LOGGER.debug(
                "%s: Area is in occupied state %s, aborting turn off",
                self.name,
                self.area.state,
            )
            return

        # Exterior safety check: Never turn off if exterior timer is active unless forced
        if not force and self.is_exterior and self._attr_extra_state_attributes.get(
            ATTR_EXTERIOR_LIGHTING_ACTIVE, False
        ):
            _LOGGER.debug(
                "%s: Exterior lighting timer is active, aborting turn off",
                self.name,
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

    #### Exterior Nighttime Lighting Control
    def _is_nighttime(self) -> bool:
        """Return True if it is currently nighttime (sun below horizon or elevation <= 0)."""
        sun_state = self.hass.states.get("sun.sun")
        if sun_state is not None:
            if sun_state.state == "below_horizon":
                return True
            if sun_state.state == "above_horizon":
                return False
            elevation = sun_state.attributes.get("elevation")
            if elevation is not None:
                try:
                    return float(elevation) <= 0
                except (ValueError, TypeError):
                    pass
        return False

    def _get_exterior_timeout(self) -> float:
        """Get the configured or default exterior lighting timeout in seconds."""
        adv_conf = self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS)
        val = (
            adv_conf.get(CONF_EXTERIOR_LIGHTING_TIMEOUT)
            or self.area.config.get(CONF_EXTERIOR_LIGHTING_TIMEOUT)
            or DEFAULT_EXTERIOR_LIGHTING_TIMEOUT
        )
        try:
            return float(val)
        except (ValueError, TypeError):
            return float(DEFAULT_EXTERIOR_LIGHTING_TIMEOUT)

    def _is_exterior_sunset_trigger_enabled(self) -> bool:
        """Check if exterior sunset trigger is enabled."""
        adv_conf = self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS)
        return bool(
            adv_conf.get(
                CONF_EXTERIOR_SUNSET_TRIGGER,
                self.area.config.get(
                    CONF_EXTERIOR_SUNSET_TRIGGER, DEFAULT_EXTERIOR_SUNSET_TRIGGER
                ),
            )
        )

    def _is_exterior_arrival_trigger_enabled(self) -> bool:
        """Check if exterior arrival trigger is enabled."""
        adv_conf = self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS)
        return bool(
            adv_conf.get(
                CONF_EXTERIOR_ARRIVAL_TRIGGER,
                self.area.config.get(
                    CONF_EXTERIOR_ARRIVAL_TRIGGER, DEFAULT_EXTERIOR_ARRIVAL_TRIGGER
                ),
            )
        )

    def _trigger_exterior_lighting(self, reason: str) -> None:
        """Trigger exterior nighttime lighting and arm the 2-hour stay-on timer."""
        if not self.is_exterior or not self._is_nighttime():
            return

        if self._exterior_timer_cb is not None:
            self._exterior_timer_cb()
            self._exterior_timer_cb = None

        timeout = self._get_exterior_timeout()
        now_ts = datetime.now(UTC).timestamp()
        self._exterior_timer_ts = now_ts + timeout
        self._exterior_trigger_reason = reason

        self._attr_extra_state_attributes[ATTR_EXTERIOR_LIGHTING_ACTIVE] = True
        self._attr_extra_state_attributes[ATTR_EXTERIOR_TIMER_TS] = (
            self._exterior_timer_ts
        )
        self._attr_extra_state_attributes[ATTR_EXTERIOR_TRIGGER_REASON] = (
            self._exterior_trigger_reason
        )

        self._exterior_timer_cb = async_call_later(
            self.hass, timeout, self._exterior_timer_expired
        )

        conf = self._get_active_profile_config()
        if conf is not None:
            self._turn_on_light(conf, force=(not self.is_on))
        else:
            if not self.is_on:
                self.last_update_from_entity = True
                self._attr_is_on = True
                service_data = {
                    ATTR_ENTITY_ID: self.entity_id,
                    ATTR_BRIGHTNESS: 255,
                }
                call_service_non_blocking(
                    self.hass, LIGHT_DOMAIN, SERVICE_TURN_ON, service_data
                )

        self.schedule_update_ha_state()

    def _exterior_timer_expired(self, now: datetime | None = None) -> None:
        """Handle expiration of the exterior stay-on timer."""
        self._exterior_timer_cb = None
        if not self.is_exterior:
            return

        if self._has_active_motion() and self._is_nighttime():
            _LOGGER.info(
                "%s: Exterior timer expired but motion is still active at night; restarting timer",
                self.name,
            )
            self._trigger_exterior_lighting(reason="motion")
            return

        _LOGGER.info(
            "%s: Exterior timer expired with no active motion; turning off lights",
            self.name,
        )
        self._clear_exterior_lighting()
        self._turn_off_light(force=True)

    def _clear_exterior_lighting(self) -> None:
        """Clear exterior timer and reset extra state attributes."""
        if self._exterior_timer_cb is not None:
            self._exterior_timer_cb()
            self._exterior_timer_cb = None
        self._exterior_timer_ts = None
        self._exterior_trigger_reason = None
        if self.is_exterior:
            self._attr_extra_state_attributes[ATTR_EXTERIOR_LIGHTING_ACTIVE] = False
            self._attr_extra_state_attributes[ATTR_EXTERIOR_TIMER_TS] = None
            self._attr_extra_state_attributes[ATTR_EXTERIOR_TRIGGER_REASON] = None
            self.schedule_update_ha_state()

    def _has_active_motion(self) -> bool:
        """Check if motion or occupancy is currently detected in this exterior area."""
        occupied_states = {
            AreaState.AREA_STATE_OCCUPIED,
            AreaState.AREA_STATE_BRIGHT,
            AreaState.AREA_STATE_SLEEP,
            AreaState.AREA_STATE_ACCENTED,
        }
        if self.area.state in occupied_states:
            return True

        # Check camera entities
        cam_entities = [
            e[ATTR_ENTITY_ID]
            for e in self.area.entities.get(CAMERA_DOMAIN, [])
        ]
        for cam_id in cam_entities:
            state = self.hass.states.get(cam_id)
            if state is not None:
                if state.state in ("recording", "streaming", "motion", STATE_ON):
                    return True
                if (
                    state.attributes.get("motion_detected") is True
                    or state.attributes.get("person_detected") is True
                ):
                    return True

        return False

    def _sun_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle sun state changes for sunset activation and sunrise deactivation."""
        if not self.is_exterior:
            return
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None:
            return

        # Sunset transition: below_horizon
        if new_state.state == "below_horizon":
            if old_state is not None and old_state.state == "below_horizon":
                return
            if not self._is_exterior_sunset_trigger_enabled():
                return
            _LOGGER.info("%s: Sunset detected, triggering exterior lighting", self.name)
            self._trigger_exterior_lighting(reason="sunset")

        # Sunrise transition: above_horizon
        elif new_state.state == "above_horizon":
            if old_state is not None and old_state.state == "above_horizon":
                return
            _LOGGER.info("%s: Sunrise detected, clearing exterior lighting", self.name)
            self._clear_exterior_lighting()
            self._turn_off_light(force=True)

    def _person_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle resident person state changes for arrival at night."""
        if not self.is_exterior:
            return
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if old_state is None or new_state is None:
            return
        if old_state.state == new_state.state:
            return

        # Resident arrival: person transitions from non-home to home
        if new_state.state == STATE_HOME and old_state.state != STATE_HOME:
            if not self._is_exterior_arrival_trigger_enabled():
                return
            if not self._is_nighttime():
                _LOGGER.debug(
                    "%s: Person %s arrived home during daytime; exterior lighting suppressed",
                    self.name,
                    event.data.get("entity_id"),
                )
                return
            _LOGGER.info(
                "%s: Person %s arrived home at night, triggering exterior lighting",
                self.name,
                event.data.get("entity_id"),
            )
            self._trigger_exterior_lighting(reason="arrival")

    def _camera_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle camera state changes or attribute updates for motion/person detection."""
        if not self.is_exterior:
            return
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        is_motion = new_state.state in (
            "recording",
            "streaming",
            "motion",
            STATE_ON,
        ) or (
            new_state.attributes.get("motion_detected") is True
            or new_state.attributes.get("person_detected") is True
        )

        if is_motion:
            if not self._is_nighttime():
                _LOGGER.debug(
                    "%s: Camera %s detected motion during daytime; exterior lighting suppressed",
                    self.name,
                    event.data.get("entity_id"),
                )
                return
            _LOGGER.info(
                "%s: Camera %s detected motion at night, restarting exterior lighting clock",
                self.name,
                event.data.get("entity_id"),
            )
            self._trigger_exterior_lighting(reason="motion")

    def _handle_global_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle global state changed events for person and camera entities."""
        if not self.is_exterior:
            return
        entity_id = event.data.get("entity_id", "")
        if entity_id.startswith("person."):
            self._person_state_change(event)
        elif entity_id.startswith("camera."):
            cam_entities = [
                e[ATTR_ENTITY_ID]
                for e in self.area.entities.get(CAMERA_DOMAIN, [])
            ]
            if entity_id in cam_entities:
                self._camera_state_change(event)
