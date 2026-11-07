"""Light controls for magic areas."""

import logging
from datetime import UTC, datetime

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
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
    ATTR_DEVICE_CLASS,
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
    State,
)
from homeassistant.helpers.device_registry import async_get as async_get_dr
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
    ATTR_ARRIVAL_PREWARM_ACTIVE,
    ATTR_ARRIVAL_PREWARM_REASON,
    ATTR_ARRIVAL_PREWARM_TS,
    ATTR_DARK_LUX_FLOOR,
    ATTR_DARKNESS_RATIO,
    ATTR_EXTERIOR_LIGHTING_ACTIVE,
    ATTR_EXTERIOR_TIMER_TS,
    ATTR_EXTERIOR_TRIGGER_REASON,
    ATTR_HAS_PRESENCE_SENSORS,
    ATTR_HISTORICAL_DAYLIGHT_LUX,
    ATTR_IS_WINDOWLESS,
    ATTR_LAST_UPDATE_FROM_ENTITY,
    ATTR_MANUAL_CONTROL,
    ATTR_MANUAL_TIMEOUT_TS,
    ATTR_PRESENCE_SENSORS,
    ATTR_SLEEP_WAKEUP_BOOST,
    ATTR_SWITCH_DEFAULT_DIM_ENTITIES,
    ATTR_SWITCH_DEFAULT_DIM_LEVEL,
    BINARY_SENSOR_DOMAIN,
    CAMERA_DOMAIN,
    CONF_ALL_AWAY_SWEEP,
    CONF_ARRIVAL_PREWARM,
    CONF_ARRIVAL_PREWARM_TIMEOUT,
    CONF_DARK_LUX_FLOOR,
    CONF_DAYLIGHT_LUX_LEVEL,
    CONF_EXTENDED_WARNING_DIM,
    CONF_EXTERIOR_ARRIVAL_TRIGGER,
    CONF_EXTERIOR_LIGHTING_TIMEOUT,
    CONF_EXTERIOR_SUNSET_TRIGGER,
    CONF_FEATURE_ADVANCED_LIGHT_GROUPS,
    CONF_LEARN_DAYLIGHT_LUX,
    CONF_LEARN_LIGHTING_PREFERENCES,
    CONF_MANUAL_TIMEOUT,
    CONF_MAX_BRIGHTNESS_LEVEL,
    CONF_MIN_BRIGHTNESS_LEVEL,
    CONF_SLEEP_WAKEUP_DELAY,
    DATA_AREA_OBJECT,
    DEFAULT_ACTUATOR_RETRY_DELAYS,
    DEFAULT_ALL_AWAY_SWEEP,
    DEFAULT_ARRIVAL_PREWARM_TIMEOUT,
    DEFAULT_DARK_LUX_FLOOR,
    DEFAULT_EXTENDED_WARNING_DIM,
    DEFAULT_EXTERIOR_ARRIVAL_TRIGGER,
    DEFAULT_EXTERIOR_LIGHTING_TIMEOUT,
    DEFAULT_EXTERIOR_SUNSET_TRIGGER,
    DEFAULT_LEARN_LIGHTING_PREFERENCES,
    DEFAULT_MANUAL_OFF_UNOCCUPIED_DELAY,
    DEFAULT_MANUAL_TIMEOUT,
    DEFAULT_MAX_BRIGHTNESS_LEVEL,
    DEFAULT_MIN_BRIGHTNESS_LEVEL,
    DEFAULT_SLEEP_WAKEUP_DELAY,
    DOMAIN,
    ENTRY_AREA_KEYWORDS,
    INVALID_STATES,
    LIVING_AREA_KEYWORDS,
    META_AREA_EXTERIOR,
    MODULE_DATA,
    bright_lights,
    extended_lights,
    occupied_lights,
    sleep_lights,
)
from .ml.lighting_learner import OnlineBayesianLightingLearner
from .util import call_service_non_blocking

_LOGGER = logging.getLogger(__name__)
ATTR_LAST_ON_ILLUMINANCE: str = "last_on_illuminance"
NUMBER_DOMAIN: str = "number"
SERVICE_SET_VALUE: str = "set_value"

SWITCH_DEFAULT_DIM_KEYWORDS: tuple[str, ...] = (
    "default_level",
    "default_brightness",
    "default_dim",
    "default_on_level",
    "default_turn_on",
    "custom_brightness",
    "forced_brightness",
    "forced_switch_on_brightness",
    "preset_light_level",
    "preset_level",
    "single_tap_turn_on_brightness",
    "turn_on_brightness",
    "local_start_level",
    "start_up_current_level",
    "startup_current_level",
    "on_level",
)

SWITCH_DEFAULT_DIM_EXCLUSIONS: tuple[str, ...] = (
    "ramp",
    "speed",
    "duration",
    "time",
    "rate",
    "min_",
    "max_",
    "minimum",
    "maximum",
    "led_indicator",
    "notification",
    "auto_off",
    "auto_turn_off",
    "double_tap",
)


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
        self._manual_off_unoccupied_timer_cb: CALLBACK_TYPE | None = None
        self._manual_brightness_override: bool = False
        self._attr_icon: str = "mdi:ceiling-light"
        self._last_turn_off_ts: float = 0.0
        self._retry_timer_cb: CALLBACK_TYPE | None = None
        self._retry_attempt: int = 0
        self._expected_state: str | None = None
        self._expected_brightness: int | None = None
        self._command_in_flight_until: float = 0.0

        # Exterior lighting state
        self._exterior_timer_cb: CALLBACK_TYPE | None = None
        self._exterior_timer_ts: float | None = None
        self._exterior_trigger_reason: str | None = None

        # Interior arrival pre-warming and sustained sleep wake-up state
        self._arrival_prewarm_timer_cb: CALLBACK_TYPE | None = None
        self._arrival_prewarm_ts: float | None = None
        self._arrival_prewarm_reason: str | None = None
        self._sleep_wakeup_timer_cb: CALLBACK_TYPE | None = None
        self._sleep_wakeup_boost_active: bool = False

        # Add static attributes
        self._all_lights: list[str] = list(entities)
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

        self._sync_switch_default_dim_level()

        await super().async_added_to_hass()

    async def _setup_listeners(self) -> None:
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [self.entity_id],
                self._update_group_state,
            )
        )
        local_room_lights = self._get_local_room_lights()
        if len(local_room_lights) > 1:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    local_room_lights,
                    self._member_light_state_change,
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

        active_occupied_states = {
            AreaState.AREA_STATE_OCCUPIED,
            AreaState.AREA_STATE_BRIGHT,
            AreaState.AREA_STATE_SLEEP,
            AreaState.AREA_STATE_ACCENTED,
        }
        occupied_states = active_occupied_states | {AreaState.AREA_STATE_EXTENDED}

        is_manually_off = (
            not self._is_controlled_by_this_entity() and not self.is_on
        )
        reentered_from_extended = (
            from_state == AreaState.AREA_STATE_EXTENDED
            and to_state in active_occupied_states
        )
        left_occupied_while_manually_off = (
            is_manually_off
            and to_state in (AreaState.AREA_STATE_EXTENDED, AreaState.AREA_STATE_CLEAR)
        )

        was_occupied = from_state in occupied_states
        now_occupied = to_state in occupied_states
        occupancy_changed = (
            (was_occupied != now_occupied)
            or (from_state == AreaState.AREA_STATE_MANUAL)
            or reentered_from_extended
            or left_occupied_while_manually_off
        )

        if to_state == AreaState.AREA_STATE_MANUAL:
            self._cancel_sleep_wakeup_timer()
            _LOGGER.debug("%s: In manual mode, leaving lights as is", self.name)
            return

        if not self.is_exterior and not self._has_presence_sensors():
            if from_state == AreaState.AREA_STATE_MANUAL:
                self._reset_control()
                if self.is_on or self._has_any_working_light_on():
                    conf = self._get_active_profile_config()
                    if conf is not None:
                        self._turn_on_light(conf)
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
            self._cancel_sleep_wakeup_timer()
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
            if self._attr_extra_state_attributes.get(
                ATTR_ARRIVAL_PREWARM_ACTIVE, False
            ):
                _LOGGER.debug(
                    "%s: Arrival pre-warm active; keeping entry lights on while waiting for entry",
                    self.name,
                )
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

            if self._attr_extra_state_attributes.get(
                ATTR_ARRIVAL_PREWARM_ACTIVE, False
            ):
                self._clear_arrival_prewarm()

            conf = self._get_active_profile_config()
            if conf is not None:
                self._turn_on_light(conf, force=True)
            self._schedule_sleep_wakeup_if_needed()
            return

        if to_state == AreaState.AREA_STATE_EXTENDED:
            self._cancel_sleep_wakeup_timer()
            if self.is_exterior:
                if not self._is_nighttime():
                    self._turn_off_light()
                    return
                self._trigger_exterior_lighting(reason="motion")
                return

            if self.area.has_configured_state(to_state):
                conf = self.area.state_config(to_state)
                adv_conf = self.area.feature_config(
                    CONF_FEATURE_ADVANCED_LIGHT_GROUPS
                )
                ext_dim_key = extended_lights.state_dim_level()
                has_explicit_ext_dim = (
                    ext_dim_key in adv_conf or ext_dim_key in self.area.config
                )
                if conf is not None and (conf.dim_level > 0 or has_explicit_ext_dim):
                    self._turn_on_light(conf)
                elif self.is_on or self._has_any_working_light_on():
                    warning_pct = int(
                        adv_conf.get(
                            CONF_EXTENDED_WARNING_DIM,
                            self.area.config.get(
                                CONF_EXTENDED_WARNING_DIM,
                                DEFAULT_EXTENDED_WARNING_DIM,
                            ),
                        )
                    )
                    if warning_pct > 0:
                        active_conf = self._get_active_profile_config()
                        base_dim = (
                            active_conf.dim_level
                            if active_conf is not None and active_conf.dim_level > 0
                            else occupied_lights.default_dim_level
                        )
                        target_dim = max(
                            10, min(100, round(base_dim * warning_pct / 100))
                        )
                        lights_list = (
                            list(conf.lights)
                            if conf is not None and conf.lights
                            else list(self._entity_ids)
                        )
                        warning_conf = StateConfigData(
                            name="extended_warning",
                            entity=None,
                            entity_state_on=STATE_ON,
                            dim_level=target_dim,
                            for_state=AreaState.AREA_STATE_EXTENDED,
                            icon=extended_lights.icon,
                            control_entity=self.area.simply_magic_entity_id(
                                SWITCH_DOMAIN, EntityNames.LIGHT_CONTROL
                            ),
                            lights=lights_list,
                        )
                        self._turn_on_light(warning_conf, dim_only=True)
                    else:
                        self._turn_off_light()
            else:
                self._turn_off_light()
            return

    def _has_presence_sensors(self) -> bool:
        """Return True if this room has presence or contact sensors configured."""
        state_sensor_id = self.area.simply_magic_entity_id(
            SENSOR_DOMAIN, EntityNames.STATE
        )
        state_sensor = self.hass.states.get(state_sensor_id)
        if state_sensor is not None:
            if ATTR_HAS_PRESENCE_SENSORS in state_sensor.attributes:
                return bool(state_sensor.attributes[ATTR_HAS_PRESENCE_SENSORS])
            if ATTR_PRESENCE_SENSORS in state_sensor.attributes:
                sensors = state_sensor.attributes[ATTR_PRESENCE_SENSORS]
                if isinstance(sensors, (list, tuple, set)):
                    return len(sensors) > 0
            if state_sensor.state in (
                AreaState.AREA_STATE_OCCUPIED,
                AreaState.AREA_STATE_EXTENDED,
            ):
                return True
        if self.area.state in (
            AreaState.AREA_STATE_OCCUPIED,
            AreaState.AREA_STATE_EXTENDED,
        ):
            return True
        return self.area.has_configured_presence_sensors()

    def _is_switch_default_dim_entity(self, entity_id: str) -> bool:
        """Return True if entity_id is a switch/dimmer default turn-on brightness number entity."""
        if not entity_id.startswith(f"{NUMBER_DOMAIN}."):
            return False
        object_id = entity_id.split(".", 1)[1].lower()
        st = self.hass.states.get(entity_id)
        friendly = (
            str(st.attributes.get("friendly_name", "")).lower().replace(" ", "_")
            if st is not None
            else ""
        )
        combined = f"{object_id} {friendly}"
        if any(excl in combined for excl in SWITCH_DEFAULT_DIM_EXCLUSIONS):
            return False
        return any(kw in combined for kw in SWITCH_DEFAULT_DIM_KEYWORDS)

    def _get_switch_default_dim_entities(self) -> list[str]:
        """Discover switch default turn-on dim level number.* entities for this room's lights."""
        matched: list[str] = []
        area_lights = [
            e[ATTR_ENTITY_ID]
            for e in self.area.entities.get(LIGHT_DOMAIN, [])
            if ATTR_ENTITY_ID in e
        ] or list(self._entity_ids)

        # 1. Explicit number entities loaded into the area
        for ent in self.area.entities.get(NUMBER_DOMAIN, []):
            ent_id = ent.get(ATTR_ENTITY_ID) if isinstance(ent, dict) else None
            if (
                ent_id
                and self._is_switch_default_dim_entity(ent_id)
                and ent_id not in matched
            ):
                matched.append(ent_id)

        # 2. Entity Registry device/area siblings of the room's lights
        try:
            er = async_get_er(self.hass)
            if er is not None and hasattr(er, "entities"):
                light_device_ids: set[str] = set()
                for light_id in area_lights:
                    entry = er.async_get(light_id)
                    if entry is not None and getattr(entry, "device_id", None):
                        light_device_ids.add(entry.device_id)
                for entry in er.entities.values():
                    if getattr(entry, "disabled_by", None) is not None:
                        continue
                    ent_id = getattr(entry, "entity_id", "")
                    if not ent_id.startswith(f"{NUMBER_DOMAIN}."):
                        continue
                    if (
                        (
                            getattr(entry, "device_id", None) in light_device_ids
                            or getattr(entry, "area_id", None) == self.area.id
                        )
                        and self._is_switch_default_dim_entity(ent_id)
                        and ent_id not in matched
                    ):
                        matched.append(ent_id)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Unable to inspect entity registry for switch dim entities: %s", err)

        # 3. State machine number.* entities matching light object_ids or area slug
        prefixes: list[str] = []
        if getattr(self.area, "slug", None):
            prefixes.append(self.area.slug.lower())
        for light_id in area_lights:
            if "." in light_id:
                obj_id = light_id.split(".", 1)[1].lower()
                prefixes.append(obj_id)
                for suffix in ("_light", "_lights", "_dimmer", "_switch"):
                    if obj_id.endswith(suffix) and len(obj_id[: -len(suffix)]) >= 4:
                        prefixes.append(obj_id[: -len(suffix)])

        for num_id in self.hass.states.async_entity_ids(NUMBER_DOMAIN):
            if num_id in matched:
                continue
            num_obj = num_id.split(".", 1)[1].lower()
            if any(pref in num_obj for pref in prefixes) and self._is_switch_default_dim_entity(
                num_id
            ):
                matched.append(num_id)

        return matched

    def _sync_switch_default_dim_level(self, profile: str | None = None) -> None:
        """Set default turn-on dim level on smart switches in the room for the active profile."""
        active_profile = profile or getattr(
            self.area, "lighting_profile", LightingProfile.STANDARD
        )
        if active_profile in (LightingProfile.SLEEP, AreaState.AREA_STATE_SLEEP):
            conf = self._get_profile_config_for(LightingProfile.SLEEP)
            raw_pct = (
                conf.dim_level
                if conf is not None
                else sleep_lights.default_dim_level
            )
            target_pct = max(
                1,
                min(
                    100,
                    int(raw_pct) if raw_pct > 0 else sleep_lights.default_dim_level,
                ),
            )
        elif active_profile in (
            LightingProfile.ACCENTED,
            AreaState.AREA_STATE_ACCENTED,
        ):
            conf = self._get_profile_config_for(LightingProfile.ACCENTED)
            raw_pct = conf.dim_level if conf is not None else 100
            target_pct = max(1, min(100, int(raw_pct) if raw_pct > 0 else 100))
        else:
            conf = (
                self.area.state_config(AreaState.AREA_STATE_OCCUPIED)
                if self.area.has_configured_state(AreaState.AREA_STATE_OCCUPIED)
                else None
            )
            raw_pct = (
                conf.dim_level
                if conf is not None
                else occupied_lights.default_dim_level
            )
            target_pct = max(
                1,
                min(
                    100,
                    int(raw_pct)
                    if raw_pct > 0
                    else occupied_lights.default_dim_level,
                ),
            )

        dim_entities = self._get_switch_default_dim_entities()
        if not dim_entities:
            return

        self._attr_extra_state_attributes[ATTR_SWITCH_DEFAULT_DIM_ENTITIES] = (
            dim_entities
        )
        self._attr_extra_state_attributes[ATTR_SWITCH_DEFAULT_DIM_LEVEL] = target_pct

        if not self.area.is_control_enabled(ControlType.System):
            return

        for num_id in dim_entities:
            st = self.hass.states.get(num_id)
            if st is not None and st.state in INVALID_STATES:
                continue
            try:
                min_val = float(st.attributes.get("min", 0)) if st is not None else 0.0
            except (ValueError, TypeError):
                min_val = 0.0
            try:
                max_val = (
                    float(st.attributes.get("max", 100)) if st is not None else 100.0
                )
            except (ValueError, TypeError):
                max_val = 100.0

            if max_val == 99:
                scaled = max(int(min_val), min(99, round(target_pct * 99 / 100)))
            elif max_val > 100:
                scaled = max(
                    int(min_val),
                    min(int(max_val), round(target_pct * max_val / 100)),
                )
            else:
                scaled = max(int(min_val), min(int(max_val), round(target_pct)))

            if st is not None:
                try:
                    if abs(float(st.state) - float(scaled)) < 0.5:
                        continue
                except (ValueError, TypeError):
                    pass

            call_service_non_blocking(
                self.hass,
                NUMBER_DOMAIN,
                SERVICE_SET_VALUE,
                {ATTR_ENTITY_ID: num_id, "value": scaled},
            )

    def _get_profile_config_for(
        self, profile: str, dim_only: bool = False
    ) -> StateConfigData | None:
        """Resolve lighting profile configuration for a specific profile."""
        enforce_non_zero_dim = dim_only or not self._has_presence_sensors()
        occ_conf = (
            self.area.state_config(AreaState.AREA_STATE_OCCUPIED)
            if self.area.has_configured_state(AreaState.AREA_STATE_OCCUPIED)
            else None
        )
        if self.area.has_configured_state(profile):
            conf = self.area.state_config(profile)
            if (
                conf is not None
                and enforce_non_zero_dim
                and conf.dim_level <= 0
            ):
                fallback_dim = (
                    occ_conf.dim_level
                    if occ_conf and occ_conf.dim_level > 0
                    else occupied_lights.default_dim_level
                )
                return StateConfigData(
                    name=conf.name,
                    entity=conf.entity,
                    entity_state_on=conf.entity_state_on,
                    dim_level=fallback_dim,
                    for_state=conf.for_state,
                    icon=conf.icon,
                    control_entity=conf.control_entity,
                    lights=list(conf.lights),
                )
            return conf

        default_lights = (
            list(occ_conf.lights)
            if occ_conf
            else (
                [
                    e[ATTR_ENTITY_ID]
                    for e in self.area.entities.get(LIGHT_DOMAIN, [])
                    if ATTR_ENTITY_ID in e
                ]
                or list(self._entity_ids)
            )
        )
        if profile in (
            LightingProfile.SLEEP,
            AreaState.AREA_STATE_SLEEP,
            LightingProfile.BRIGHT,
            AreaState.AREA_STATE_BRIGHT,
        ) and default_lights:
            lg = (
                sleep_lights
                if profile in (LightingProfile.SLEEP, AreaState.AREA_STATE_SLEEP)
                else bright_lights
            )
            adv_conf = self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS)
            lights = (
                adv_conf.get(lg.advanced_lights_to_control(), default_lights)
                or default_lights
            )
            dim_level = int(
                adv_conf.get(
                    lg.state_dim_level(),
                    self.area.config.get(lg.state_dim_level(), lg.default_dim_level),
                )
            )
            if enforce_non_zero_dim and dim_level <= 0:
                dim_level = (
                    occ_conf.dim_level
                    if occ_conf and occ_conf.dim_level > 0
                    else occupied_lights.default_dim_level
                )
            return StateConfigData(
                name=lg.name,
                entity=None,
                entity_state_on=adv_conf.get(lg.advanced_state_check(), "on"),
                dim_level=dim_level,
                for_state=lg.enable_state,
                icon=lg.icon,
                control_entity=self.area.simply_magic_entity_id(
                    SWITCH_DOMAIN, EntityNames.LIGHT_CONTROL
                ),
                lights=list(lights),
            )

        if occ_conf is not None:
            return occ_conf
        return None

    def _get_active_profile_config(self) -> StateConfigData | None:
        """Resolve the active lighting profile configuration."""
        profile = getattr(self.area, "lighting_profile", LightingProfile.STANDARD)
        if self.area.has_configured_state(profile):
            return self.area.state_config(profile)
        if not self._has_presence_sensors():
            return self._get_profile_config_for(profile)
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

        # Pre-stage smart switch default turn-on dim levels across all rooms
        self._sync_switch_default_dim_level(new_profile)
        if new_profile not in (LightingProfile.SLEEP, AreaState.AREA_STATE_SLEEP):
            self._cancel_sleep_wakeup_timer()
        else:
            self._schedule_sleep_wakeup_if_needed()

        if self.is_exterior and not self._is_nighttime():
            return

        occupied_states = {
            AreaState.AREA_STATE_OCCUPIED,
            AreaState.AREA_STATE_BRIGHT,
            AreaState.AREA_STATE_SLEEP,
            AreaState.AREA_STATE_ACCENTED,
        }

        if (
            self.area.state == AreaState.AREA_STATE_MANUAL
            or not self.area.is_control_enabled(ControlType.System)
        ):
            return

        if getattr(self, "_manual_brightness_override", False):
            _LOGGER.debug(
                "%s: In manual brightness override; ignoring profile change from %s to %s",
                self.name,
                old_profile,
                new_profile,
            )
            return

        is_currently_on = bool(self.is_on or self._has_any_working_light_on())

        # If light is manually ON (in a sensor or sensorless room) or room has no sensors,
        # only adjust the dim level without changing the ON/OFF state.
        if not self._is_controlled_by_this_entity() or not self._has_presence_sensors():
            if is_currently_on:
                conf = self._get_profile_config_for(new_profile, dim_only=True)
                if conf is not None:
                    self._turn_on_light(conf, force=True, dim_only=True)
            else:
                _LOGGER.debug(
                    "%s: Lights are off; maintaining manual off state on profile change from %s to %s",
                    self.name,
                    old_profile,
                    new_profile,
                )
            return

        # Normal automatic occupancy in a sensor-equipped room
        if self.area.state in occupied_states:
            if is_currently_on:
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

        now_ts = datetime.now(UTC).timestamp()
        in_command_grace = now_ts <= getattr(self, "_command_in_flight_until", 0.0)
        if (now_ts - getattr(self, "_last_member_manual_ts", 0.0)) < 0.5:
            self._attr_is_on = new_state.state == STATE_ON
            return

        # Check for user manual brightness adjustment while light remains ON
        if (
            old_state.state == STATE_ON
            and new_state.state == STATE_ON
            and old_state.attributes.get(ATTR_BRIGHTNESS)
            != new_state.attributes.get(ATTR_BRIGHTNESS)
        ):
            new_brightness = new_state.attributes.get(ATTR_BRIGHTNESS)
            if self.last_update_from_entity or (
                in_command_grace
                and getattr(self, "_expected_state", None) == STATE_ON
                and new_brightness is not None
                and getattr(self, "_expected_brightness", None) is not None
                and abs(int(new_brightness) - int(self._expected_brightness)) <= 3
            ):
                self.last_update_from_entity = False
                return

            if new_brightness is not None and isinstance(new_brightness, (int, float)):
                self._cancel_retry_timer()
                self._command_in_flight_until = 0.0
                self._record_manual_brightness_preference(int(new_brightness))
                self._manual_brightness_override = True
                self._set_controlled_by_this_entity(False)
                self._sync_room_lights(
                    STATE_ON,
                    target_brightness=int(new_brightness),
                    source_entity_id=self.entity_id,
                )
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

        # If this update was initiated by internal automation (or delayed Z-Wave report
        # matching the in-flight command), consume the flag and return
        if self.last_update_from_entity or (
            in_command_grace
            and new_state.state == getattr(self, "_expected_state", None)
        ):
            self.last_update_from_entity = False
            return

        # External manual interaction (physical switch, UI, external integration)
        self._cancel_retry_timer()
        self._expected_state = None
        self._command_in_flight_until = 0.0
        self._manual_brightness_override = False

        if not self._has_presence_sensors() and new_state.state == STATE_OFF:
            if self.is_exterior:
                self._clear_exterior_lighting()
            self._reset_control()
            self._sync_room_lights(STATE_OFF, source_entity_id=self.entity_id)
            self.schedule_update_ha_state()
            return

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

        if new_state.state == STATE_OFF:
            self._sync_room_lights(STATE_OFF, source_entity_id=self.entity_id)
            self._schedule_manual_off_unoccupied_clear_if_needed()
            self.schedule_update_ha_state()
            return

        self._cancel_manual_off_unoccupied_timer()

        # When a switch is manually turned ON in any room (sensor or sensorless),
        # still adjust the dim level if the room is in sleep/accented mode without changing ON/OFF state
        if (
            old_state.state == STATE_OFF
            and new_state.state == STATE_ON
            and self.area.state != AreaState.AREA_STATE_MANUAL
            and self.area.is_control_enabled(ControlType.System)
        ):
            active_profile = getattr(
                self.area, "lighting_profile", LightingProfile.STANDARD
            )
            if active_profile in (
                LightingProfile.SLEEP,
                AreaState.AREA_STATE_SLEEP,
                LightingProfile.ACCENTED,
                AreaState.AREA_STATE_ACCENTED,
            ):
                conf = self._get_profile_config_for(active_profile, dim_only=True)
                if conf is not None and 0 < conf.dim_level < 100:
                    target_brightness = int(conf.dim_level * 255 / 100)
                    current_brightness = new_state.attributes.get(ATTR_BRIGHTNESS)
                    if (
                        current_brightness is None
                        or abs(int(current_brightness) - target_brightness) > 5
                    ):
                        _LOGGER.debug(
                            "%s: Light switched ON manually during %s mode; adjusting dim level to %s%%",
                            self.name,
                            active_profile,
                            conf.dim_level,
                        )
                        self._turn_on_light(conf, force=True, dim_only=True)
                        return
            self._sync_room_lights(
                STATE_ON,
                target_brightness=new_state.attributes.get(ATTR_BRIGHTNESS),
                source_entity_id=self.entity_id,
            )

        self.schedule_update_ha_state()

    def _record_manual_brightness_preference(self, target_brightness: int) -> None:
        """Record a manual brightness preference sample in the local Bayesian learner."""
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
            target_brightness,
            lux,
        )
        self._lighting_learner.learn_preference(
            lux=lux,
            profile=profile,
            solar_elevation=solar_elevation,
            target_brightness=int(target_brightness),
        )
        self._attr_extra_state_attributes.update(
            self._lighting_learner.get_attributes()
        )

    def _get_local_room_lights(self) -> list[str]:
        """Return light entity_ids strictly belonging to this single room (never across rooms or meta-areas)."""
        if self.area.is_meta():
            return []
        candidates = (
            list(self._all_lights) if self._all_lights else list(self._entity_ids)
        )
        entity_registry = async_get_er(self.hass)
        device_registry = async_get_dr(self.hass)
        local_lights: list[str] = []
        for ent_id in candidates:
            if ent_id == self.entity_id or ent_id.startswith(
                f"{LIGHT_DOMAIN}.simply_magic_areas_"
            ):
                continue
            st = self.hass.states.get(ent_id)
            if st is not None and isinstance(
                st.attributes.get(ATTR_ENTITY_ID), (list, tuple)
            ):
                continue
            reg_entry = entity_registry.async_get(ent_id)
            if reg_entry is not None:
                ent_area_id = reg_entry.area_id
                if not ent_area_id and reg_entry.device_id:
                    dev_entry = device_registry.async_get(reg_entry.device_id)
                    if dev_entry is not None:
                        ent_area_id = dev_entry.area_id
                if ent_area_id and ent_area_id not in (
                    self.area.id,
                    self.area.slug,
                    self.area.name,
                ):
                    continue
            if ent_id not in local_lights:
                local_lights.append(ent_id)
        return local_lights

    def _member_light_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle manual changes on individual member lights and synchronize all lights in the room."""
        if self.area.is_meta():
            return
        entity_id = event.data.get("entity_id")
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if not entity_id or old_state is None or new_state is None:
            return
        if entity_id not in self._get_local_room_lights():
            return
        if old_state.attributes.get("restored"):
            return
        if old_state.state not in (STATE_ON, STATE_OFF) or new_state.state not in (
            STATE_ON,
            STATE_OFF,
        ):
            return
        if (
            not self.area.is_control_enabled(ControlType.System)
            or self.area.state == AreaState.AREA_STATE_MANUAL
        ):
            return

        now_ts = datetime.now(UTC).timestamp()
        in_command_grace = now_ts <= getattr(self, "_command_in_flight_until", 0.0)
        expected_state = getattr(self, "_expected_state", None)
        expected_brightness = getattr(self, "_expected_brightness", None)

        # 1. Member light brightness changed while remaining ON
        if old_state.state == STATE_ON and new_state.state == STATE_ON:
            old_b = old_state.attributes.get(ATTR_BRIGHTNESS)
            new_b = new_state.attributes.get(ATTR_BRIGHTNESS)
            if old_b == new_b or new_b is None or not isinstance(new_b, (int, float)):
                return
            if (
                (self.last_update_from_entity or in_command_grace)
                and (expected_state is None or expected_state == STATE_ON)
                and (
                    expected_brightness is None
                    or abs(int(new_b) - int(expected_brightness)) <= 5
                )
            ):
                return

            self._last_member_manual_ts = now_ts
            self._cancel_retry_timer()
            self._command_in_flight_until = 0.0
            self._record_manual_brightness_preference(int(new_b))
            self._manual_brightness_override = True
            self._set_controlled_by_this_entity(False)
            self._sync_room_lights(
                STATE_ON,
                target_brightness=int(new_b),
                source_entity_id=entity_id,
            )
            self.schedule_update_ha_state()
            return

        # 2. Member light turned ON or OFF
        if (self.last_update_from_entity or in_command_grace) and (
            expected_state is None or new_state.state == expected_state
        ):
            return

        self._last_member_manual_ts = now_ts
        self._cancel_retry_timer()
        self._expected_state = None
        self._command_in_flight_until = 0.0
        self._manual_brightness_override = False
        self._attr_is_on = new_state.state == STATE_ON

        if new_state.state == STATE_OFF:
            if not self._has_presence_sensors():
                if self.is_exterior:
                    self._clear_exterior_lighting()
                self._reset_control()
            else:
                if self.is_exterior:
                    self._clear_exterior_lighting()
                self._set_controlled_by_this_entity(False)
                if self._manual_timeout_cb is not None:
                    self._manual_timeout_cb()
                    self._manual_timeout_cb = None
                self._schedule_manual_off_unoccupied_clear_if_needed()
            self._sync_room_lights(STATE_OFF, source_entity_id=entity_id)
            self.schedule_update_ha_state()
            return

        # Member light turned ON manually
        self._cancel_manual_off_unoccupied_timer()
        self._set_controlled_by_this_entity(False)
        if self._manual_timeout_cb is not None:
            self._manual_timeout_cb()
            self._manual_timeout_cb = None

        active_profile = getattr(
            self.area, "lighting_profile", LightingProfile.STANDARD
        )
        if active_profile in (
            LightingProfile.SLEEP,
            AreaState.AREA_STATE_SLEEP,
            LightingProfile.ACCENTED,
            AreaState.AREA_STATE_ACCENTED,
        ):
            conf = self._get_profile_config_for(active_profile, dim_only=True)
            if conf is not None and 0 < conf.dim_level < 100:
                target_brightness = int(conf.dim_level * 255 / 100)
                current_brightness = new_state.attributes.get(ATTR_BRIGHTNESS)
                if (
                    current_brightness is None
                    or abs(int(current_brightness) - target_brightness) > 5
                ):
                    _LOGGER.debug(
                        "%s: Member light %s switched ON manually during %s mode; syncing room dim level to %s%%",
                        self.name,
                        entity_id,
                        active_profile,
                        conf.dim_level,
                    )
                    self._turn_on_light(conf, force=True, dim_only=True)
                    return

        target_b = new_state.attributes.get(ATTR_BRIGHTNESS)
        if target_b is None:
            conf = self._get_active_profile_config()
            if conf is not None and conf.dim_level > 0:
                target_b = int(conf.dim_level * 255 / 100)
        self._sync_room_lights(
            STATE_ON,
            target_brightness=int(target_b) if target_b is not None else None,
            source_entity_id=entity_id,
        )
        self.schedule_update_ha_state()

    def _sync_room_lights(
        self,
        target_state: str,
        target_brightness: int | None = None,
        source_entity_id: str | None = None,
    ) -> None:
        """Synchronize all lights in this single room to match a manual state or brightness change."""
        if self.area.is_meta():
            return
        if not self.area.is_control_enabled(ControlType.System):
            return
        if self.area.state == AreaState.AREA_STATE_MANUAL:
            return

        room_lights = self._get_local_room_lights()
        if len(room_lights) <= 1:
            return
        if room_lights and self._entity_ids != room_lights:
            self._entity_ids = room_lights

        available = self._get_available_member_states()
        if target_state == STATE_OFF:
            out_of_sync = [
                ent_id for ent_id, st in available if st.state != STATE_OFF
            ]
            if not out_of_sync:
                return
            _LOGGER.debug(
                "%s: Synchronizing room lights %s to OFF after manual change on %s",
                self.name,
                out_of_sync,
                source_entity_id,
            )
            now_ts = datetime.now(UTC).timestamp()
            self._last_turn_off_ts = now_ts
            self.last_update_from_entity = True
            self._attr_is_on = False
            self._command_in_flight_until = now_ts + 2.5
            call_service_non_blocking(
                self.hass,
                LIGHT_DOMAIN,
                SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: self.entity_id},
            )
            self._schedule_actuator_retry(STATE_OFF, allow_manual=True)
            return

        if target_state == STATE_ON:
            out_of_sync = []
            for ent_id, st in available:
                if st.state != STATE_ON:
                    out_of_sync.append(ent_id)
                elif (
                    target_brightness is not None
                    and ent_id != source_entity_id
                ):
                    cur_b = st.attributes.get(ATTR_BRIGHTNESS)
                    if cur_b is None or abs(int(cur_b) - int(target_brightness)) > 3:
                        out_of_sync.append(ent_id)
            if not out_of_sync:
                return
            _LOGGER.debug(
                "%s: Synchronizing room lights %s to ON (brightness=%s) after manual change on %s",
                self.name,
                out_of_sync,
                target_brightness,
                source_entity_id,
            )
            now_ts = datetime.now(UTC).timestamp()
            self.last_update_from_entity = True
            self._attr_is_on = True
            if target_brightness is not None:
                self._attr_brightness = int(target_brightness)
            self._command_in_flight_until = now_ts + 2.5
            service_data: dict[str, object] = {ATTR_ENTITY_ID: self.entity_id}
            if target_brightness is not None:
                service_data[ATTR_BRIGHTNESS] = int(target_brightness)
            call_service_non_blocking(
                self.hass,
                LIGHT_DOMAIN,
                SERVICE_TURN_ON,
                service_data,
            )
            self._schedule_actuator_retry(
                STATE_ON, target_brightness, allow_manual=True
            )

    def _reset_manual_timeout(self, now: datetime | None = None) -> None:
        if self._manual_timeout_cb is not None:
            self._manual_timeout_cb = None
        self._set_controlled_by_this_entity(True)
        self.schedule_update_ha_state()

    ####  Light Handling
    def _get_available_member_states(self) -> list[tuple[str, State]]:
        """Return available (non-unavailable/unknown) member light states."""
        available: list[tuple[str, State]] = []
        for ent_id in self._entity_ids:
            st = self.hass.states.get(ent_id)
            if st is not None and st.state not in INVALID_STATES:
                available.append((ent_id, st))
        return available

    def _has_any_working_light_on(self) -> bool:
        """Return True if at least one available member light is ON."""
        available = self._get_available_member_states()
        if available:
            return any(st.state == STATE_ON for _, st in available)
        return bool(self.is_on)

    def _has_any_working_light_off(self) -> bool:
        """Return True if at least one available member light is OFF."""
        available = self._get_available_member_states()
        if available:
            return any(st.state == STATE_OFF for _, st in available)
        return not bool(self.is_on)

    def _are_all_working_lights_at_brightness(self, brightness: int) -> bool:
        """Return True if all available member lights are ON and at the target brightness."""
        available = self._get_available_member_states()
        if not available:
            return self.brightness == brightness
        for _ent_id, st in available:
            if st.state != STATE_ON:
                return False
            cur_b = st.attributes.get(ATTR_BRIGHTNESS)
            if cur_b is not None and abs(int(cur_b) - int(brightness)) > 3:
                return False
        return self.brightness == brightness

    def _get_unresponsive_lights(self, target_state: str) -> list[str]:
        """Return available member lights that have not reached target_state (and expected brightness)."""
        expected_b = getattr(self, "_expected_brightness", None)
        available = self._get_available_member_states()
        if available:
            unresponsive: list[str] = []
            for ent_id, st in available:
                if st.state != target_state or (
                    target_state == STATE_ON
                    and expected_b is not None
                    and st.attributes.get(ATTR_BRIGHTNESS) is not None
                    and abs(int(st.attributes[ATTR_BRIGHTNESS]) - int(expected_b)) > 5
                ):
                    unresponsive.append(ent_id)
            return unresponsive
        group_state = self.hass.states.get(self.entity_id)
        if (
            group_state is not None
            and group_state.state not in INVALID_STATES
            and group_state.state != target_state
        ):
            return [self.entity_id]
        return []

    def _cancel_retry_timer(self) -> None:
        """Cancel any pending actuator sliding backoff retry timer."""
        if getattr(self, "_retry_timer_cb", None) is not None:
            self._retry_timer_cb()
            self._retry_timer_cb = None
        self._retry_attempt = 0
        self._retry_allow_manual = False

    def _schedule_actuator_retry(
        self,
        target_state: str,
        brightness: int | None = None,
        allow_manual: bool = False,
    ) -> None:
        """Schedule sliding backoff retry to verify Z-Wave/physical lights responded."""
        self._cancel_retry_timer()
        self._expected_state = target_state
        self._expected_brightness = brightness
        self._retry_allow_manual = allow_manual
        if not DEFAULT_ACTUATOR_RETRY_DELAYS:
            return
        self._retry_timer_cb = async_call_later(
            self.hass,
            DEFAULT_ACTUATOR_RETRY_DELAYS[0],
            self._execute_actuator_retry,
        )

    def _execute_actuator_retry(self, _now: datetime | None = None) -> None:
        """Check if available member lights reached expected state; retry with backoff if not."""
        self._retry_timer_cb = None
        if not self.area.is_control_enabled(ControlType.System):
            return
        if not getattr(
            self, "_retry_allow_manual", False
        ) and not self._is_controlled_by_this_entity():
            return
        target_state = getattr(self, "_expected_state", None)
        if target_state not in (STATE_ON, STATE_OFF):
            return

        unresponsive = self._get_unresponsive_lights(target_state)
        if not unresponsive:
            self._retry_attempt = 0
            return

        self._retry_attempt += 1
        _LOGGER.warning(
            "%s: Lights %s did not respond to %s command; retrying (attempt %d/%d)",
            self.name,
            unresponsive,
            target_state,
            self._retry_attempt,
            len(DEFAULT_ACTUATOR_RETRY_DELAYS),
        )
        self.last_update_from_entity = True
        self._command_in_flight_until = datetime.now(UTC).timestamp() + 2.5
        if target_state == STATE_ON:
            self._attr_is_on = True
            service_data: dict[str, object] = {ATTR_ENTITY_ID: self.entity_id}
            if getattr(self, "_expected_brightness", None) is not None:
                service_data[ATTR_BRIGHTNESS] = self._expected_brightness
            call_service_non_blocking(
                self.hass, LIGHT_DOMAIN, SERVICE_TURN_ON, service_data
            )
        else:
            self._attr_is_on = False
            service_data = {ATTR_ENTITY_ID: self.entity_id}
            call_service_non_blocking(
                self.hass, LIGHT_DOMAIN, SERVICE_TURN_OFF, service_data
            )

        if self._retry_attempt < len(DEFAULT_ACTUATOR_RETRY_DELAYS):
            next_delay = DEFAULT_ACTUATOR_RETRY_DELAYS[self._retry_attempt]
            self._retry_timer_cb = async_call_later(
                self.hass,
                next_delay,
                self._execute_actuator_retry,
            )

    def _turn_on_light(
        self,
        conf: StateConfigData,
        force: bool = False,
        dim_only: bool = False,
        allow_sensorless: bool = False,
    ) -> None:
        """Turn on the light group."""

        if not force and not self._is_controlled_by_this_entity():
            _LOGGER.debug(
                "%s: Light in manual override, aborting automatic turn on",
                self.name,
            )
            return

        enforce_dim_only = dim_only or (
            not allow_sensorless
            and not self.is_exterior
            and not self._has_presence_sensors()
        )
        if (
            enforce_dim_only
            and not self.is_on
            and not self._has_any_working_light_on()
        ):
            _LOGGER.debug(
                "%s: Light is off and dim_only is active; preserving manual off state",
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
        all_working_on = not self._has_any_working_light_off()
        if (
            not force
            and not recent_turn_off
            and self.is_on
            and all_working_on
            and self._are_all_working_lights_at_brightness(brightness)
        ):
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

        if enforce_dim_only and brightness <= 0:
            brightness = max(1, int(conf.dim_level * 255 / 100) or 255)

        if brightness == 0:
            _LOGGER.debug("%s: Brightness is 0", self.name)
            self._turn_off_light(force=True)
            return

        _LOGGER.debug("Turning on lights")
        self.last_update_from_entity = True
        self._attr_is_on = True
        self._command_in_flight_until = datetime.now(UTC).timestamp() + 2.5
        self.schedule_update_ha_state()
        service_data = {
            ATTR_ENTITY_ID: self.entity_id,
            ATTR_BRIGHTNESS: brightness,
        }
        call_service_non_blocking(
            self.hass, LIGHT_DOMAIN, SERVICE_TURN_ON, service_data
        )
        self._schedule_actuator_retry(STATE_ON, brightness)

        return

    def _turn_off_light(
        self, force: bool = False, allow_sensorless: bool = False
    ) -> None:
        """Turn off the light group."""
        if (
            not allow_sensorless
            and not self.is_exterior
            and not self._has_presence_sensors()
        ):
            _LOGGER.debug(
                "%s: Sensorless room on/off state is manual; aborting automatic turn off",
                self.name,
            )
            return

        if not force and not self._is_controlled_by_this_entity():
            _LOGGER.debug(
                "%s: Light in manual override, aborting automatic turn off",
                self.name,
            )
            return

        if not force and self._attr_extra_state_attributes.get(
            ATTR_ARRIVAL_PREWARM_ACTIVE, False
        ):
            _LOGGER.debug(
                "%s: Arrival pre-warm timer is active, aborting turn off",
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

        if not self.is_on and not self._has_any_working_light_on():
            _LOGGER.debug("%s: Light already off", self.name)
            return

        if not self.area.is_control_enabled(ControlType.System):
            return

        now_ts = datetime.now(UTC).timestamp()
        self._last_turn_off_ts = now_ts
        self.last_update_from_entity = True
        self._attr_is_on = False
        self._command_in_flight_until = now_ts + 2.5
        service_data = {ATTR_ENTITY_ID: self.entity_id}
        # await self.async_turn_off()
        call_service_non_blocking(
            self.hass, LIGHT_DOMAIN, SERVICE_TURN_OFF, service_data
        )
        self._schedule_actuator_retry(STATE_OFF)

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
            self._manual_brightness_override = False
            self._attr_extra_state_attributes.pop(ATTR_MANUAL_CONTROL, None)
            self._attr_extra_state_attributes.pop(ATTR_MANUAL_TIMEOUT_TS, None)
        else:
            self._attr_extra_state_attributes[ATTR_MANUAL_CONTROL] = True

    def _reset_control(self) -> None:
        self._cancel_manual_off_unoccupied_timer()
        if self._manual_timeout_cb is not None:
            self._manual_timeout_cb()
            self._manual_timeout_cb = None
        self._set_controlled_by_this_entity(True)
        self.schedule_update_ha_state()

    def _cancel_manual_off_unoccupied_timer(self) -> None:
        """Cancel any pending unoccupied manual-off clear timer."""
        if self._manual_off_unoccupied_timer_cb is not None:
            self._manual_off_unoccupied_timer_cb()
            self._manual_off_unoccupied_timer_cb = None

    def _schedule_manual_off_unoccupied_clear_if_needed(self) -> None:
        """Quickly clear manual-off status if the space is already marked unoccupied."""
        self._cancel_manual_off_unoccupied_timer()
        if self.area.state in (
            AreaState.AREA_STATE_EXTENDED,
            AreaState.AREA_STATE_CLEAR,
        ):
            self._manual_off_unoccupied_timer_cb = async_call_later(
                self.hass,
                DEFAULT_MANUAL_OFF_UNOCCUPIED_DELAY,
                self._manual_off_unoccupied_timer_expired,
            )

    def _manual_off_unoccupied_timer_expired(
        self, _now: datetime | None = None
    ) -> None:
        """Clear manual-off override once the brief unoccupied settle delay expires."""
        self._manual_off_unoccupied_timer_cb = None
        if (
            not self._is_controlled_by_this_entity()
            and not self.is_on
            and self.area.state
            in (AreaState.AREA_STATE_EXTENDED, AreaState.AREA_STATE_CLEAR)
        ):
            _LOGGER.info(
                "%s: Space is unoccupied (%s) after manual turn-off; clearing manual override",
                self.name,
                self.area.state,
            )
            self._reset_control()

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
                self._command_in_flight_until = datetime.now(UTC).timestamp() + 2.5
                service_data = {
                    ATTR_ENTITY_ID: self.entity_id,
                    ATTR_BRIGHTNESS: 255,
                }
                call_service_non_blocking(
                    self.hass, LIGHT_DOMAIN, SERVICE_TURN_ON, service_data
                )
                self._schedule_actuator_retry(STATE_ON, 255)

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
        """Handle resident person state changes for arrival pre-warming and all-away departure."""
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if old_state is None or new_state is None:
            return
        if old_state.state == new_state.state:
            return

        # Resident arrival: person transitions from non-home to home
        if new_state.state == STATE_HOME and old_state.state != STATE_HOME:
            if self.is_exterior:
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
            elif self._is_entry_area():
                self._trigger_arrival_prewarm(reason="person_arrival")
            return

        # Resident departure: person transitions from home to away
        if (
            old_state.state == STATE_HOME
            and new_state.state != STATE_HOME
            and not self.is_exterior
            and self._are_all_persons_away()
        ):
            self._handle_all_away_departure()

    def _proximity_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle proximity sensor updates to pre-warm entry lighting before arrival."""
        if self.is_exterior or not self._is_entry_area():
            return
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in INVALID_STATES:
            return
        dir_of_travel = str(
            new_state.attributes.get("dir_of_travel", "")
        ).lower()
        if dir_of_travel != "towards":
            return
        try:
            distance = float(new_state.state)
        except (ValueError, TypeError):
            return
        if 0 < distance <= 1500 and self._is_interior_vacant():
            self._trigger_arrival_prewarm(reason="proximity_approaching")

    def _is_entry_area(self) -> bool:
        """Return True if this interior room is an entry/front room eligible for arrival pre-warming."""
        if self.is_exterior or self.area.is_meta():
            return False
        adv_conf = self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS)
        explicit = adv_conf.get(
            CONF_ARRIVAL_PREWARM,
            self.area.config.get(CONF_ARRIVAL_PREWARM, None),
        )
        if explicit is not None:
            return bool(explicit)

        slug = (getattr(self.area, "slug", "") or "").lower()
        name = (getattr(self.area, "name", "") or "").lower().replace(" ", "_")
        combined = f"{slug} {name}"
        if any(kw in combined for kw in ENTRY_AREA_KEYWORDS):
            return True

        for ent in self.area.entities.get(BINARY_SENSOR_DOMAIN, []):
            if not isinstance(ent, dict):
                continue
            ent_id = str(ent.get(ATTR_ENTITY_ID, "")).lower()
            st = self.hass.states.get(ent_id) if ent_id else None
            dev_cls = ent.get(ATTR_DEVICE_CLASS) or (
                st.attributes.get(ATTR_DEVICE_CLASS) if st is not None else None
            )
            if dev_cls == BinarySensorDeviceClass.GARAGE_DOOR:
                return True
            if dev_cls == BinarySensorDeviceClass.DOOR and any(
                kw in ent_id
                for kw in ("front", "entry", "foyer", "main", "mudroom", "garage")
            ):
                return True
        return False

    def _is_dark_for_arrival(self) -> bool:
        """Return True if it is nighttime or the room is dark enough to need arrival lighting."""
        if self._is_nighttime():
            return True
        sun_state = self.hass.states.get("sun.sun")
        adv_conf = self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS)
        dark_lux_floor = float(
            getattr(self.area, "dark_lux_floor", None)
            or adv_conf.get(CONF_DARK_LUX_FLOOR)
            or self.area.config.get(CONF_DARK_LUX_FLOOR, DEFAULT_DARK_LUX_FLOOR)
        )
        val = self.area.get_effective_illuminance()
        if val is not None:
            return float(val) <= dark_lux_floor
        return sun_state is None

    def _is_interior_vacant(self) -> bool:
        """Return True if all interior areas are currently clear."""
        interior_state = self.hass.states.get(
            "sensor.simply_magic_areas_state_interior"
        )
        if interior_state is not None:
            return interior_state.state == AreaState.AREA_STATE_CLEAR

        occupied_states = {
            AreaState.AREA_STATE_OCCUPIED,
            AreaState.AREA_STATE_BRIGHT,
            AreaState.AREA_STATE_SLEEP,
            AreaState.AREA_STATE_ACCENTED,
            AreaState.AREA_STATE_EXTENDED,
        }
        for entry_data in self.hass.data.get(MODULE_DATA, {}).values():
            if not isinstance(entry_data, dict):
                continue
            other_area = entry_data.get(DATA_AREA_OBJECT)
            if (
                other_area is not None
                and not other_area.is_meta()
                and not other_area.is_exterior()
                and getattr(other_area, "state", None) in occupied_states
            ):
                return False
        return True

    def _get_arrival_prewarm_timeout(self) -> float:
        """Return the arrival pre-warm timeout in seconds."""
        adv_conf = self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS)
        val = adv_conf.get(
            CONF_ARRIVAL_PREWARM_TIMEOUT,
            self.area.config.get(
                CONF_ARRIVAL_PREWARM_TIMEOUT, DEFAULT_ARRIVAL_PREWARM_TIMEOUT
            ),
        )
        try:
            return float(val)
        except (ValueError, TypeError):
            return float(DEFAULT_ARRIVAL_PREWARM_TIMEOUT)

    def _trigger_arrival_prewarm(self, reason: str) -> None:
        """Pre-warm interior entry/front-room lights when a resident arrives or approaches home at night."""
        if not self._is_entry_area() or not self._is_dark_for_arrival():
            return
        if (
            self.area.state == AreaState.AREA_STATE_MANUAL
            or not self.area.is_control_enabled(ControlType.System)
        ):
            return
        if not self._is_exterior_arrival_trigger_enabled():
            return

        if self._arrival_prewarm_timer_cb is not None:
            self._arrival_prewarm_timer_cb()
            self._arrival_prewarm_timer_cb = None

        timeout = self._get_arrival_prewarm_timeout()
        now_ts = datetime.now(UTC).timestamp()
        self._arrival_prewarm_ts = now_ts + timeout
        self._arrival_prewarm_reason = reason
        self._attr_extra_state_attributes[ATTR_ARRIVAL_PREWARM_ACTIVE] = True
        self._attr_extra_state_attributes[ATTR_ARRIVAL_PREWARM_TS] = (
            self._arrival_prewarm_ts
        )
        self._attr_extra_state_attributes[ATTR_ARRIVAL_PREWARM_REASON] = reason

        self._arrival_prewarm_timer_cb = async_call_later(
            self.hass, timeout, self._arrival_prewarm_expired
        )

        self._reset_control()
        active_profile = getattr(
            self.area, "lighting_profile", LightingProfile.STANDARD
        )
        conf = self._get_profile_config_for(active_profile, dim_only=True)
        if conf is not None:
            _LOGGER.info(
                "%s: Pre-warming entry lights for resident arrival (%s)",
                self.name,
                reason,
            )
            self._turn_on_light(conf, force=True, allow_sensorless=True)
        self.schedule_update_ha_state()

    def _clear_arrival_prewarm(self) -> None:
        """Cancel arrival pre-warm timer and clean up state attributes."""
        if self._arrival_prewarm_timer_cb is not None:
            self._arrival_prewarm_timer_cb()
            self._arrival_prewarm_timer_cb = None
        self._arrival_prewarm_ts = None
        self._arrival_prewarm_reason = None
        self._attr_extra_state_attributes.pop(ATTR_ARRIVAL_PREWARM_ACTIVE, None)
        self._attr_extra_state_attributes.pop(ATTR_ARRIVAL_PREWARM_TS, None)
        self._attr_extra_state_attributes.pop(ATTR_ARRIVAL_PREWARM_REASON, None)

    def _arrival_prewarm_expired(self, _now: datetime | None = None) -> None:
        """Handle expiration of the arrival pre-warm window if no occupancy followed."""
        self._arrival_prewarm_timer_cb = None
        self._clear_arrival_prewarm()
        if self.area.state == AreaState.AREA_STATE_CLEAR:
            self._turn_off_light(force=True, allow_sensorless=True)
        self.schedule_update_ha_state()

    def _is_all_away_sweep_enabled(self) -> bool:
        """Return True if all-away departure light sweep is enabled."""
        adv_conf = self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS)
        return bool(
            adv_conf.get(
                CONF_ALL_AWAY_SWEEP,
                self.area.config.get(CONF_ALL_AWAY_SWEEP, DEFAULT_ALL_AWAY_SWEEP),
            )
        )

    def _are_all_persons_away(self) -> bool:
        """Return True if at least one person entity exists and all valid person entities are away."""
        person_states = self.hass.states.async_all("person")
        valid_states = [
            st for st in person_states if st.state not in INVALID_STATES
        ]
        return bool(valid_states) and all(
            st.state != STATE_HOME for st in valid_states
        )

    def _handle_all_away_departure(self) -> None:
        """Sweep forgotten lights off and clear manual overrides when all residents leave home."""
        if self.is_exterior or not self._is_all_away_sweep_enabled():
            return
        if (
            self.area.state == AreaState.AREA_STATE_MANUAL
            or not self.area.is_control_enabled(ControlType.System)
        ):
            return
        self._clear_arrival_prewarm()
        self._cancel_sleep_wakeup_timer()
        self._reset_control()
        if (
            self.area.state == AreaState.AREA_STATE_CLEAR
            or not self._has_presence_sensors()
        ) and (self.is_on or self._has_any_working_light_on()):
            _LOGGER.info(
                "%s: All residents away; sweeping forgotten lights off",
                self.name,
            )
            self._turn_off_light(force=True, allow_sensorless=True)

    def _is_living_area(self) -> bool:
        """Return True if this room is a daytime/living area eligible for sustained sleep wake-up."""
        if self.is_exterior or self.area.is_meta():
            return False
        slug = (getattr(self.area, "slug", "") or "").lower()
        name = (getattr(self.area, "name", "") or "").lower().replace(" ", "_")
        return any(kw in f"{slug} {name}" for kw in LIVING_AREA_KEYWORDS)

    def _get_sleep_wakeup_delay(self) -> float:
        """Return the sustained-occupancy delay in seconds before boosting sleep brightness in living areas."""
        adv_conf = self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS)
        val = adv_conf.get(
            CONF_SLEEP_WAKEUP_DELAY,
            self.area.config.get(
                CONF_SLEEP_WAKEUP_DELAY, DEFAULT_SLEEP_WAKEUP_DELAY
            ),
        )
        try:
            return float(val)
        except (ValueError, TypeError):
            return float(DEFAULT_SLEEP_WAKEUP_DELAY)

    def _cancel_sleep_wakeup_timer(self) -> None:
        """Cancel any active sleep wake-up timer and reset boost flag."""
        if self._sleep_wakeup_timer_cb is not None:
            self._sleep_wakeup_timer_cb()
            self._sleep_wakeup_timer_cb = None
        self._sleep_wakeup_boost_active = False
        self._attr_extra_state_attributes.pop(ATTR_SLEEP_WAKEUP_BOOST, None)

    def _schedule_sleep_wakeup_if_needed(self) -> None:
        """Schedule a sustained-activity brightness boost when a living area is occupied during sleep mode."""
        if not self._is_living_area():
            return
        active_profile = getattr(
            self.area, "lighting_profile", LightingProfile.STANDARD
        )
        if (
            active_profile not in (LightingProfile.SLEEP, AreaState.AREA_STATE_SLEEP)
            or self.area.state != AreaState.AREA_STATE_OCCUPIED
        ):
            self._cancel_sleep_wakeup_timer()
            return
        delay = self._get_sleep_wakeup_delay()
        if delay <= 0:
            return
        if (
            self._sleep_wakeup_timer_cb is None
            and not self._sleep_wakeup_boost_active
        ):
            self._sleep_wakeup_timer_cb = async_call_later(
                self.hass, delay, self._sleep_wakeup_timer_expired
            )

    def _sleep_wakeup_timer_expired(self, _now: datetime | None = None) -> None:
        """Promote living area brightness from sleep dim to standard when occupied continuously."""
        self._sleep_wakeup_timer_cb = None
        if not self._is_living_area():
            return
        if (
            self.area.state != AreaState.AREA_STATE_OCCUPIED
            or not self.area.is_control_enabled(ControlType.System)
            or not self._is_controlled_by_this_entity()
        ):
            return
        active_profile = getattr(
            self.area, "lighting_profile", LightingProfile.STANDARD
        )
        if active_profile not in (
            LightingProfile.SLEEP,
            AreaState.AREA_STATE_SLEEP,
        ):
            return
        occ_conf = self._get_profile_config_for(
            LightingProfile.STANDARD, dim_only=True
        )
        if occ_conf is not None:
            self._sleep_wakeup_boost_active = True
            self._attr_extra_state_attributes[ATTR_SLEEP_WAKEUP_BOOST] = True
            _LOGGER.info(
                "%s: Sustained occupancy in living area during sleep mode; boosting lights to %s%%",
                self.name,
                occ_conf.dim_level,
            )
            self._turn_on_light(occ_conf, force=True, dim_only=True)
            self.schedule_update_ha_state()

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
        """Handle global state changed events for person, proximity, exterior arrival, and camera entities."""
        entity_id = event.data.get("entity_id", "")
        if entity_id.startswith("person."):
            self._person_state_change(event)
            return
        if entity_id.startswith("proximity."):
            self._proximity_state_change(event)
            return
        if (
            not self.is_exterior
            and entity_id.startswith(f"{LIGHT_DOMAIN}.")
            and entity_id != self.entity_id
            and self._is_entry_area()
        ):
            new_state = event.data.get("new_state")
            old_state = event.data.get("old_state")
            if (
                new_state is not None
                and new_state.attributes.get(ATTR_EXTERIOR_LIGHTING_ACTIVE) is True
                and (
                    old_state is None
                    or old_state.attributes.get(ATTR_EXTERIOR_LIGHTING_ACTIVE)
                    is not True
                )
                and new_state.attributes.get(ATTR_EXTERIOR_TRIGGER_REASON)
                in ("arrival", "motion")
                and self._is_interior_vacant()
            ):
                self._trigger_arrival_prewarm(reason="exterior_arrival")
            return
        if not self.is_exterior:
            return
        if entity_id.startswith("camera."):
            cam_entities = [
                e[ATTR_ENTITY_ID]
                for e in self.area.entities.get(CAMERA_DOMAIN, [])
            ]
            if entity_id in cam_entities:
                self._camera_state_change(event)

    async def async_will_remove_from_hass(self) -> None:
        """Clean up timers when removed from hass."""
        self._cancel_retry_timer()
        self._clear_arrival_prewarm()
        self._cancel_sleep_wakeup_timer()
        self._cancel_manual_off_unoccupied_timer()
        if self._manual_timeout_cb is not None:
            self._manual_timeout_cb()
            self._manual_timeout_cb = None
        if self._exterior_timer_cb is not None:
            self._exterior_timer_cb()
            self._exterior_timer_cb = None
        await super().async_will_remove_from_hass()
