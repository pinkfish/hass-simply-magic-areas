"""Fan controls for magic areas."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.components.binary_sensor import (
    DOMAIN as BINARY_SENSOR_DOMAIN,
)
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
)
from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.components.group.fan import FanGroup
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
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
    async_track_time_interval,
)
from homeassistant.util import slugify

from .base.entities import MagicEntity
from .base.magic import ControlType, MagicArea
from .config.area_state import AreaState, LightingProfile
from .config.entity_names import EntityNames
from .const import (
    ATTR_DRYING_RATE_K,
    ATTR_ESTIMATED_DRY_TS,
    ATTR_HISTORICAL_HUMIDITY_BASELINE,
    ATTR_HISTORICAL_SHOWER_COUNT,
    ATTR_HISTORICAL_SHOWER_DELTA_PEAK,
    ATTR_HISTORICAL_SHOWER_RATE_PEAK,
    ATTR_HUMIDITY_DELTA,
    ATTR_HUMIDITY_ON,
    ATTR_HUMIDITY_START_TS,
    ATTR_LAST_UPDATE_FROM_ENTITY,
    ATTR_LEARNED_HUMIDITY_DELTA_SHUTOFF,
    ATTR_LEARNED_HUMIDITY_DELTA_THRESHOLD,
    ATTR_LEARNED_HUMIDITY_TREND_UP_CUTOFF,
    ATTR_MANUAL_CONTROL,
    ATTR_MANUAL_TIMEOUT_TS,
    ATTR_REFERENCE_HUMIDITY,
    ATTR_SHOWER_PROBABILITY,
    CONF_FAN_SUPPRESS_IN_SLEEP,
    CONF_FEATURE_ADVANCED_LIGHT_GROUPS,
    CONF_FEATURE_HUMIDITY,
    CONF_HUMIDITY_DEADBAND,
    CONF_HUMIDITY_DELTA_SHUTOFF,
    CONF_HUMIDITY_DELTA_THRESHOLD,
    CONF_HUMIDITY_MAX_FAN_TIME,
    CONF_HUMIDITY_PRESENCE_GATE_TIME,
    CONF_HUMIDITY_TREND_DOWN_CUT_OFF,
    CONF_HUMIDITY_TREND_UP_CUT_OFF,
    CONF_HUMIDITY_ZERO_WAIT_TIME,
    CONF_LEARN_SHOWER_HUMIDITY,
    CONF_MANUAL_TIMEOUT,
    CONF_REFERENCE_BRIGHT_AREA,
    CONF_REFERENCE_HUMIDITY_SENSOR,
    DATA_AREA_OBJECT,
    DEFAULT_FAN_SUPPRESS_IN_SLEEP,
    DEFAULT_HUMIDITY_DEADBAND,
    DEFAULT_HUMIDITY_DELTA_SHUTOFF,
    DEFAULT_HUMIDITY_DELTA_THRESHOLD,
    DEFAULT_HUMIDITY_MAX_FAN_TIME,
    DEFAULT_HUMIDITY_PRESENCE_GATE_TIME,
    DEFAULT_HUMIDITY_TREND_DOWN_CUT_OFF,
    DEFAULT_HUMIDITY_TREND_UP_CUT_OFF,
    DEFAULT_HUMIDITY_ZERO_WAIT_TIME,
    DEFAULT_LEARN_SHOWER_HUMIDITY,
    DEFAULT_MANUAL_TIMEOUT,
    DOMAIN,
    META_AREA_INTERIOR,
    MODULE_DATA,
)
from .ml import MoistureEvacuationModel, ShowerClassifier
from .util import call_service_non_blocking

_LOGGER = logging.getLogger(__name__)
ATTR_FANS: str = "fans"
ATTR_HUMIDITY_ZERO_TS: str = "humidity_zero_ts"


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
        self._watchdog_unsub: CALLBACK_TYPE | None = None
        self._last_shower_concluded_ts: float | None = None
        self._max_runtime_lockout_until: float | None = None

        self._controled_by_entity = True
        self._last_occupied_ts: float | None = None

        # Historical humidity learning attributes
        self._historical_baseline_humidity: float | None = None
        self._historical_shower_count: int = 0
        self._historical_shower_delta_peak: float = 15.0
        self._historical_shower_rate_peak: float = 0.033
        self._current_shower_rate_peak: float = 0.0
        self._current_shower_delta_peak: float = 0.0
        self._current_shower_start_baseline: float | None = None

        # Machine learning shower classifier and evacuation models
        self._shower_classifier = ShowerClassifier()
        self._evacuation_model = MoistureEvacuationModel()

        # Add static attributes
        self.last_update_from_entity: bool = False
        self._attr_extra_state_attributes[ATTR_FANS] = self._entity_ids
        self._attr_extra_state_attributes[ATTR_LAST_UPDATE_FROM_ENTITY] = False

    @property
    def is_on(self) -> bool:
        """Return true if fan is on."""
        group_is_on = super().is_on
        if group_is_on is not None:
            return group_is_on
        return self._attr_is_on or False

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
            if ATTR_HUMIDITY_ON in last_state.attributes:
                self._attr_extra_state_attributes[ATTR_HUMIDITY_ON] = (
                    last_state.attributes[ATTR_HUMIDITY_ON]
                )
            if ATTR_HUMIDITY_START_TS in last_state.attributes:
                self._attr_extra_state_attributes[ATTR_HUMIDITY_START_TS] = (
                    last_state.attributes[ATTR_HUMIDITY_START_TS]
                )
            if ATTR_HISTORICAL_HUMIDITY_BASELINE in last_state.attributes:
                try:
                    self._historical_baseline_humidity = float(
                        last_state.attributes[ATTR_HISTORICAL_HUMIDITY_BASELINE]
                    )
                except (ValueError, TypeError):
                    pass
            if ATTR_HISTORICAL_SHOWER_COUNT in last_state.attributes:
                try:
                    self._historical_shower_count = int(
                        last_state.attributes[ATTR_HISTORICAL_SHOWER_COUNT]
                    )
                except (ValueError, TypeError):
                    pass
            if ATTR_HISTORICAL_SHOWER_DELTA_PEAK in last_state.attributes:
                try:
                    self._historical_shower_delta_peak = float(
                        last_state.attributes[ATTR_HISTORICAL_SHOWER_DELTA_PEAK]
                    )
                except (ValueError, TypeError):
                    pass
            if ATTR_HISTORICAL_SHOWER_RATE_PEAK in last_state.attributes:
                try:
                    self._historical_shower_rate_peak = float(
                        last_state.attributes[ATTR_HISTORICAL_SHOWER_RATE_PEAK]
                    )
                except (ValueError, TypeError):
                    pass
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
        await self._setup_listeners("ignore")

        await super().async_added_to_hass()

    async def _setup_listeners(self, _: Any = None) -> None:
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
        trend_statistics = self.area.simply_magic_entity_id(
            SENSOR_DOMAIN,
            EntityNames.HUMIDITY_STATISTICS,
        )
        if self.hass.states.get(trend_statistics) is not None:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [trend_statistics], self._trend_state_change
                )
            )

        # Track configured reference humidity sensor if configured
        humidity_config = self.area.feature_config(CONF_FEATURE_HUMIDITY)
        ref_sensor = humidity_config.get(CONF_REFERENCE_HUMIDITY_SENSOR)
        if ref_sensor and self.hass.states.get(ref_sensor) is not None:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [ref_sensor], self._trend_state_change
                )
            )

        # Track temperature sensor for thermal flux shower classification
        temp_sensor = self.area.simply_magic_entity_id(
            SENSOR_DOMAIN, SensorDeviceClass.TEMPERATURE
        )
        if self.hass.states.get(temp_sensor) is not None:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [temp_sensor], self._temperature_state_change
                )
            )

        # Track local lighting profile sensor if configured
        local_profile_sensor = self.area.simply_magic_entity_id(
            SENSOR_DOMAIN, EntityNames.LIGHTING_PROFILE
        )
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [local_profile_sensor], self._sleep_state_change
            )
        )

        # Track sleep entity if configured
        sleep_conf = self.area.all_state_configs().get(AreaState.AREA_STATE_SLEEP)
        sleep_entity = (
            sleep_conf.entity if sleep_conf else self.area.config.get("sleep_entity")
        )
        if sleep_entity:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [sleep_entity], self._sleep_state_change
                )
            )

        # Track reference area state and profile if configured
        ref_area = self.area.config.get(
            CONF_REFERENCE_BRIGHT_AREA,
            self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS).get(
                CONF_REFERENCE_BRIGHT_AREA
            ),
        )
        if ref_area:
            ref_state_entity = self.area.simply_magic_entity_id(
                SENSOR_DOMAIN, EntityNames.STATE, area_name=ref_area
            )
            ref_profile_entity = self.area.simply_magic_entity_id(
                SENSOR_DOMAIN, EntityNames.LIGHTING_PROFILE, area_name=ref_area
            )
            entities_to_track = [
                e
                for e in [ref_state_entity, ref_profile_entity]
                if self.hass.states.get(e) is not None
            ]
            if entities_to_track:
                self.async_on_remove(
                    async_track_state_change_event(
                        self.hass, entities_to_track, self._sleep_state_change
                    )
                )

    ### State Change Handling
    def _area_state_change(self, event: Event[EventStateChangedData]) -> None:
        if event.data["old_state"] is None or event.data["new_state"] is None:
            return

        from_state = (
            AreaState(event.data["old_state"].state)
            if event.data["old_state"].state in AreaState
            else None
        )
        to_state = (
            AreaState(event.data["new_state"].state)
            if event.data["new_state"].state in AreaState
            else None
        )

        _LOGGER.debug(
            "%s: Fan group New state: %s / Last state %s (Humidity %s On: %s)",
            self.name,
            to_state,
            from_state,
            self._attr_extra_state_attributes.get(ATTR_HUMIDITY_ON, False),
            self.is_on,
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

        # Manual control, ignore all changes
        if to_state == AreaState.AREA_STATE_MANUAL:
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
                "%s: In manual override and occupancy status did not change (%s -> %s); holding fan state",
                self.name,
                from_state,
                to_state,
            )
            return

        if to_state in (AreaState.AREA_STATE_OCCUPIED, AreaState.AREA_STATE_EXTENDED):
            state_entity = self.hass.states.get(
                self.area.simply_magic_entity_id(SENSOR_DOMAIN, EntityNames.STATE)
            )
            if state_entity is None or not state_entity.attributes.get(
                ATTR_HUMIDITY_ON, False
            ):
                self._last_occupied_ts = datetime.now(UTC).timestamp()

        # For fans we only worry about the extended state.
        if to_state == AreaState.AREA_STATE_EXTENDED:
            if (
                self._is_fan_suppressed_in_sleep()
                and not self._is_emergency_moisture()
            ):
                _LOGGER.debug(
                    "%s: Sleep suppression active, suppressing fan in extended state",
                    self.name,
                )
                if self.is_on:
                    self._turn_off_fan()
            else:
                self._turn_on_fan()
        elif self.is_on and not (
            self._attr_extra_state_attributes.get(ATTR_HUMIDITY_ON, False)
            and not (
                self._is_fan_suppressed_in_sleep()
                and not self._is_emergency_moisture()
            )
        ):
            self._turn_off_fan()
        else:
            _LOGGER.debug("Ignoring state change")

    def _is_emergency_moisture(
        self,
        area_humidity: float | None = None,
        humidity_delta: float | None = None,
        shower_prob: float | None = None,
    ) -> bool:
        """Return True if moisture levels represent critical emergency (>=80%)."""
        if area_humidity is None:
            area_humidity = self._get_area_humidity()
        return bool(area_humidity is not None and area_humidity >= 80.0)

    def _is_fan_suppressed_in_sleep(self) -> bool:
        """Check if fan should be suppressed due to sleep mode."""
        if not self.area.config.get(
            CONF_FAN_SUPPRESS_IN_SLEEP, DEFAULT_FAN_SUPPRESS_IN_SLEEP
        ):
            return False

        # 1. Current area lighting profile is sleep, or area state is sleep
        if getattr(self.area, "lighting_profile", None) == LightingProfile.SLEEP:
            return True
        if self.area.state == AreaState.AREA_STATE_SLEEP:
            return True

        local_profile_entity = self.area.simply_magic_entity_id(
            SENSOR_DOMAIN, EntityNames.LIGHTING_PROFILE
        )
        state_obj = self.hass.states.get(local_profile_entity)
        if state_obj and state_obj.state == LightingProfile.SLEEP:
            return True

        sleep_conf = self.area.all_state_configs().get(AreaState.AREA_STATE_SLEEP)
        sleep_entity = (
            sleep_conf.entity if sleep_conf else self.area.config.get("sleep_entity")
        )
        if sleep_entity:
            entity_state = self.hass.states.get(sleep_entity)
            check_state = sleep_conf.entity_state_on if sleep_conf else STATE_ON
            if entity_state and entity_state.state.lower() == check_state.lower():
                return True

        # 2. Reference area sleep state (e.g. Master Bedroom for Ensuite)
        ref_area = self.area.config.get(
            CONF_REFERENCE_BRIGHT_AREA,
            self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS).get(
                CONF_REFERENCE_BRIGHT_AREA
            ),
        )
        if ref_area:
            if MODULE_DATA in self.hass.data:
                for entry in self.hass.data[MODULE_DATA].values():
                    area_obj = entry.get(DATA_AREA_OBJECT)
                    if (
                        area_obj
                        and (
                            area_obj.id == ref_area
                            or area_obj.slug == ref_area
                            or area_obj.name == ref_area
                        )
                        and (
                            getattr(area_obj, "lighting_profile", None)
                            == LightingProfile.SLEEP
                            or area_obj.state == AreaState.AREA_STATE_SLEEP
                        )
                    ):
                        return True
            ref_profile_entity = self.area.simply_magic_entity_id(
                SENSOR_DOMAIN, EntityNames.LIGHTING_PROFILE, area_name=ref_area
            )
            state_obj = self.hass.states.get(ref_profile_entity)
            if state_obj and state_obj.state == LightingProfile.SLEEP:
                return True
            ref_state_entity = self.area.simply_magic_entity_id(
                SENSOR_DOMAIN, EntityNames.STATE, area_name=ref_area
            )
            state_obj = self.hass.states.get(ref_state_entity)
            if state_obj and state_obj.state in (
                AreaState.AREA_STATE_SLEEP,
                LightingProfile.SLEEP,
            ):
                return True

        return False

    def _sleep_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle changes to sleep entities or reference area states."""
        if event.data.get("new_state") is None:
            return

        if (
            self._is_fan_suppressed_in_sleep()
            and not self._is_emergency_moisture()
            and self.is_on
        ):
            _LOGGER.debug(
                "%s: Sleep became active, suppressing running fan",
                self.name,
            )
            self._attr_extra_state_attributes[ATTR_HUMIDITY_ON] = False
            self._turn_off_fan()
        elif (
            not self._is_fan_suppressed_in_sleep()
            and self.area.state == AreaState.AREA_STATE_EXTENDED
            and not self.is_on
        ):
            _LOGGER.debug(
                "%s: Sleep cleared during extended state, turning fan on",
                self.name,
            )
            self._turn_on_fan()

    def _trend_state_change(self, event: Event[EventStateChangedData]):
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
        if to_state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self._humidity_fan_control()

    def _temperature_state_change(self, event: Event[EventStateChangedData]) -> None:
        if event.data["new_state"] is None:
            return
        state = event.data["new_state"].state
        if state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            try:
                temp_val = float(state)
                now_ts = datetime.now(UTC).timestamp()
                self._shower_classifier.update_temperature(temp_val, now_ts)
            except (ValueError, TypeError):
                pass

    def _get_area_temperature(self) -> float | None:
        sensor_id = self.area.simply_magic_entity_id(
            SENSOR_DOMAIN, SensorDeviceClass.TEMPERATURE
        )
        state = self.hass.states.get(sensor_id)
        if state and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            try:
                return float(state.state)
            except (ValueError, TypeError):
                pass
        return None

    def _is_door_closed(self) -> bool | None:
        if BINARY_SENSOR_DOMAIN in self.area.entities:
            for entity_meta in self.area.entities[BINARY_SENSOR_DOMAIN]:
                ent_id = entity_meta.get(ATTR_ENTITY_ID)
                if not ent_id:
                    continue
                state = self.hass.states.get(ent_id)
                if (
                    state
                    and state.attributes.get("device_class")
                    == BinarySensorDeviceClass.DOOR
                ):
                    return state.state == STATE_OFF
        return None

    def _get_area_humidity(self) -> float | None:
        sensor_id = self.area.simply_magic_entity_id(
            SENSOR_DOMAIN, SensorDeviceClass.HUMIDITY
        )
        state = self.hass.states.get(sensor_id)
        if state and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            try:
                return float(state.state)
            except (ValueError, TypeError):
                pass
        return None

    def _get_reference_humidity(self) -> float | None:
        humidity_feature_config = self.area.feature_config(CONF_FEATURE_HUMIDITY)
        ref_entity = humidity_feature_config.get(CONF_REFERENCE_HUMIDITY_SENSOR)
        if ref_entity:
            state = self.hass.states.get(ref_entity)
            if state and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                try:
                    if (
                        "current_humidity" in state.attributes
                        and state.attributes["current_humidity"] is not None
                    ):
                        return float(state.attributes["current_humidity"])
                    return float(state.state)
                except (ValueError, TypeError):
                    pass

        # Fallback to Interior meta-area humidity if available
        if MODULE_DATA in self.hass.data:
            for entry in self.hass.data[MODULE_DATA].values():
                area = entry.get(DATA_AREA_OBJECT)
                if area and area.is_meta() and area.id == META_AREA_INTERIOR.lower():
                    interior_sensor_id = area.simply_magic_entity_id(
                        SENSOR_DOMAIN, SensorDeviceClass.HUMIDITY
                    )
                    state = self.hass.states.get(interior_sensor_id)
                    if state and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                        try:
                            return float(state.state)
                        except (ValueError, TypeError):
                            pass
        return None

    def _humidity_fan_control(self) -> None:
        if not self._is_controlled_by_this_entity():
            _LOGGER.debug(
                "%s: Fan group in manual override; ignoring humidity control",
                self.name,
            )
            return

        fans_on: bool = False
        trend_entity = self.area.simply_magic_entity_id(
            SENSOR_DOMAIN,
            EntityNames.HUMIDITY_STATISTICS,
        )
        humidity_trend = self.hass.states.get(trend_entity)
        trend_val = 0.0
        if humidity_trend is not None and humidity_trend.state not in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        ):
            try:
                trend_val = float(humidity_trend.state)
            except (ValueError, TypeError):
                trend_val = 0.0

        humidity_feature_config = self.area.feature_config(CONF_FEATURE_HUMIDITY)
        now_ts = datetime.now(UTC).timestamp()

        learn_shower = humidity_feature_config.get(
            CONF_LEARN_SHOWER_HUMIDITY, DEFAULT_LEARN_SHOWER_HUMIDITY
        )

        # Determine effective thresholds (autonomous learned vs explicit override)
        user_trend_up = humidity_feature_config.get(
            CONF_HUMIDITY_TREND_UP_CUT_OFF, DEFAULT_HUMIDITY_TREND_UP_CUT_OFF
        )
        user_delta_thresh = humidity_feature_config.get(
            CONF_HUMIDITY_DELTA_THRESHOLD, DEFAULT_HUMIDITY_DELTA_THRESHOLD
        )
        user_delta_shutoff = humidity_feature_config.get(
            CONF_HUMIDITY_DELTA_SHUTOFF, DEFAULT_HUMIDITY_DELTA_SHUTOFF
        )

        trend_up_cutoff = user_trend_up
        delta_threshold = user_delta_thresh
        delta_shutoff = user_delta_shutoff

        if learn_shower and self._historical_shower_count > 0:
            if user_trend_up == DEFAULT_HUMIDITY_TREND_UP_CUT_OFF:
                trend_up_cutoff = max(
                    0.010, min(0.040, 0.40 * self._historical_shower_rate_peak)
                )
            if user_delta_thresh == DEFAULT_HUMIDITY_DELTA_THRESHOLD:
                delta_threshold = max(
                    4.0, min(12.0, 0.40 * self._historical_shower_delta_peak)
                )
            if user_delta_shutoff == DEFAULT_HUMIDITY_DELTA_SHUTOFF:
                delta_shutoff = max(1.5, min(4.0, 0.25 * delta_threshold))

        # Baseline & differential calculations
        area_humidity = self._get_area_humidity()
        ref_humidity = self._get_reference_humidity()

        # Update historical resting baseline when fan is not forced by humidity, room is idle, and not recently post-shower
        is_post_shower = (
            self._last_shower_concluded_ts is not None
            and (now_ts - self._last_shower_concluded_ts) <= 600
        )
        if (
            not self._attr_extra_state_attributes.get(ATTR_HUMIDITY_ON, False)
            and not is_post_shower
            and area_humidity is not None
            and abs(trend_val) < trend_up_cutoff
        ):
            if self._historical_baseline_humidity is None:
                self._historical_baseline_humidity = area_humidity
            else:
                self._historical_baseline_humidity = round(
                    0.95 * self._historical_baseline_humidity + 0.05 * area_humidity, 2
                )

        # Compute differential baseline if available
        humidity_delta: float | None = None
        if area_humidity is not None:
            if ref_humidity is not None:
                humidity_delta = area_humidity - ref_humidity
                self._attr_extra_state_attributes[ATTR_REFERENCE_HUMIDITY] = round(
                    ref_humidity, 1
                )
            elif (
                learn_shower
                and self._historical_baseline_humidity is not None
            ):
                humidity_delta = area_humidity - self._historical_baseline_humidity
                self._attr_extra_state_attributes[ATTR_REFERENCE_HUMIDITY] = round(
                    self._historical_baseline_humidity, 1
                )

            if humidity_delta is not None:
                self._attr_extra_state_attributes[ATTR_HUMIDITY_DELTA] = round(
                    humidity_delta, 1
                )
        if humidity_delta is None:
            self._attr_extra_state_attributes.pop(ATTR_HUMIDITY_DELTA, None)
            self._attr_extra_state_attributes.pop(ATTR_REFERENCE_HUMIDITY, None)

        # Track in-progress shower metrics
        if self._attr_extra_state_attributes.get(ATTR_HUMIDITY_ON, False):
            self._current_shower_rate_peak = max(
                self._current_shower_rate_peak, trend_val
            )
            if humidity_delta is not None:
                self._current_shower_delta_peak = max(
                    self._current_shower_delta_peak, humidity_delta
                )
            elif (
                area_humidity is not None
                and self._current_shower_start_baseline is not None
            ):
                self._current_shower_delta_peak = max(
                    self._current_shower_delta_peak,
                    max(0.0, area_humidity - self._current_shower_start_baseline),
                )

        # Multi-sensor shower classification
        area_temp = self._get_area_temperature()
        door_closed = self._is_door_closed()
        shower_prob = self._shower_classifier.compute_probability(
            trend=trend_val,
            ts=now_ts,
            area_humidity=area_humidity if area_humidity is not None else 50.0,
            ref_humidity=ref_humidity,
            area_temp=area_temp,
            door_closed=door_closed,
            historical_baseline=self._historical_baseline_humidity,
        )
        self._attr_extra_state_attributes[ATTR_SHOWER_PROBABILITY] = shower_prob

        event_concluded = False

        def _conclude_shower_event(is_max_runtime: bool = False) -> None:
            nonlocal event_concluded
            event_concluded = True
            self._last_shower_concluded_ts = now_ts
            if is_max_runtime:
                self._max_runtime_lockout_until = now_ts + 900  # 15 minutes lockout
            if not self._attr_extra_state_attributes.get(ATTR_HUMIDITY_ON, False):
                return
            self._attr_extra_state_attributes[ATTR_HUMIDITY_ON] = False
            self._evacuation_model.reset()
            self._attr_extra_state_attributes.pop(ATTR_DRYING_RATE_K, None)
            self._attr_extra_state_attributes.pop(ATTR_ESTIMATED_DRY_TS, None)
            if (
                self._current_shower_delta_peak >= 4.0
                or self._current_shower_rate_peak >= 0.010
            ):
                self._historical_shower_delta_peak = round(
                    0.75 * self._historical_shower_delta_peak
                    + 0.25 * max(self._current_shower_delta_peak, 4.0),
                    2,
                )
                self._historical_shower_rate_peak = round(
                    0.75 * self._historical_shower_rate_peak
                    + 0.25 * max(self._current_shower_rate_peak, 0.010),
                    4,
                )
                self._historical_shower_count += 1
            self._current_shower_rate_peak = 0.0
            self._current_shower_delta_peak = 0.0
            self._current_shower_start_baseline = None

        # 1. Deadband Flatline Detection
        start_baseline = (
            self._current_shower_start_baseline
            or self._historical_baseline_humidity
        )
        deadband = humidity_feature_config.get(
            CONF_HUMIDITY_DEADBAND, DEFAULT_HUMIDITY_DEADBAND
        )
        effective_deadband = max(0.003, deadband)
        if abs(trend_val) > effective_deadband:
            self._attr_extra_state_attributes[ATTR_HUMIDITY_ZERO_TS] = None
        elif self._attr_extra_state_attributes.get(ATTR_HUMIDITY_ZERO_TS) is None:
            self._attr_extra_state_attributes[ATTR_HUMIDITY_ZERO_TS] = now_ts

        if self._attr_extra_state_attributes.get(ATTR_HUMIDITY_ZERO_TS) is not None:
            zero_time = int(
                now_ts - float(self._attr_extra_state_attributes[ATTR_HUMIDITY_ZERO_TS])
            )
            wait_time = humidity_feature_config.get(
                CONF_HUMIDITY_ZERO_WAIT_TIME, DEFAULT_HUMIDITY_ZERO_WAIT_TIME
            )
            is_near_baseline = (
                start_baseline is not None
                and area_humidity is not None
                and area_humidity <= (start_baseline + 5.0)
            )
            effective_wait = min(300, wait_time) if is_near_baseline else wait_time
            if zero_time > effective_wait:
                _LOGGER.debug(
                    "%s: Humidity rate %s within deadband %s for %ss (> %ss), turning off",
                    self.name,
                    trend_val,
                    deadband,
                    zero_time,
                    effective_wait,
                )
                _conclude_shower_event()

        # 2. Maximum Fan Runtime Check (Safety Timeout)
        max_runtime_exceeded = False
        max_fan_time = humidity_feature_config.get(
            CONF_HUMIDITY_MAX_FAN_TIME, DEFAULT_HUMIDITY_MAX_FAN_TIME
        )
        start_ts = self._attr_extra_state_attributes.get(ATTR_HUMIDITY_START_TS)
        if (
            max_fan_time > 0
            and self._attr_extra_state_attributes.get(ATTR_HUMIDITY_ON, False)
            and start_ts is not None
        ):
            run_duration = now_ts - float(start_ts)
            if run_duration > max_fan_time:
                _LOGGER.warning(
                    "%s: Humidity fan max runtime (%ss) exceeded, shutting off",
                    self.name,
                    max_fan_time,
                )
                _conclude_shower_event(is_max_runtime=True)
                max_runtime_exceeded = True

        # 3. Reference Delta Shutoff Check
        if (
            humidity_delta is not None
            and self._attr_extra_state_attributes.get(ATTR_HUMIDITY_ON, False)
            and humidity_delta <= delta_shutoff
        ):
            _LOGGER.debug(
                "%s: Humidity delta (%s%%) returned to <= shutoff (%s%%), turning off",
                self.name,
                humidity_delta,
                delta_shutoff,
            )
            _conclude_shower_event()

        # 3b. Pre-Shower Baseline Return Check (Robust against inter-room calibration skew)
        if (
            start_baseline is not None
            and area_humidity is not None
            and self._attr_extra_state_attributes.get(ATTR_HUMIDITY_ON, False)
            and (humidity_delta is None or humidity_delta < delta_threshold)
            and area_humidity <= (start_baseline + max(2.0, delta_shutoff))
            and trend_val <= 0.002
        ):
            _LOGGER.debug(
                "%s: Humidity (%.1f%%) returned to pre-shower baseline (%.1f%% + shutoff %.1f%%), turning off",
                self.name,
                area_humidity,
                start_baseline,
                delta_shutoff,
            )
            _conclude_shower_event()

        # 4. Falling Trend Check (Standalone fallback when reference delta is unavailable)
        trend_down_cutoff = humidity_feature_config.get(
            CONF_HUMIDITY_TREND_DOWN_CUT_OFF, DEFAULT_HUMIDITY_TREND_DOWN_CUT_OFF
        )
        if humidity_delta is None and trend_val < trend_down_cutoff:
            _conclude_shower_event()

        # 4b. Dynamic Exponential Evacuation Trajectory Complete
        effective_baseline = (
            self._current_shower_start_baseline
            or self._historical_baseline_humidity
            or ref_humidity
        )
        if (
            learn_shower
            and self._attr_extra_state_attributes.get(ATTR_HUMIDITY_ON, False)
            and trend_val <= 0.0
            and area_humidity is not None
            and effective_baseline is not None
        ):
            fitted_k, est_dry_ts = self._evacuation_model.record_sample(
                current_humidity=area_humidity,
                baseline_humidity=effective_baseline,
                now_ts=now_ts,
                delta_shutoff=delta_shutoff,
            )
            if self._evacuation_model.drying_rate_k is not None:
                self._attr_extra_state_attributes[ATTR_DRYING_RATE_K] = (
                    self._evacuation_model.drying_rate_k
                )
            if self._evacuation_model.estimated_dry_ts is not None:
                self._attr_extra_state_attributes[ATTR_ESTIMATED_DRY_TS] = (
                    self._evacuation_model.estimated_dry_ts
                )

            if self._evacuation_model.is_evacuation_complete(
                current_humidity=area_humidity,
                baseline_humidity=effective_baseline,
                now_ts=now_ts,
                delta_shutoff=delta_shutoff,
            ):
                _LOGGER.info(
                    "%s: Exponential moisture evacuation complete (k=%s, dry_ts=%s), shutting off fan",
                    self.name,
                    fitted_k,
                    est_dry_ts,
                )
                _conclude_shower_event()

        # 5. Rising Trend / Activation Check
        activation_triggered = False
        in_runtime_lockout = (
            self._max_runtime_lockout_until is not None
            and now_ts < self._max_runtime_lockout_until
        )
        in_conclude_cooldown = (
            self._last_shower_concluded_ts is not None
            and (now_ts - self._last_shower_concluded_ts) < 300
        )

        gate_time = humidity_feature_config.get(
            CONF_HUMIDITY_PRESENCE_GATE_TIME, DEFAULT_HUMIDITY_PRESENCE_GATE_TIME
        )
        is_occupied_or_recent = (
            gate_time == 0
            or self.area.state
            in (AreaState.AREA_STATE_OCCUPIED, AreaState.AREA_STATE_EXTENDED)
            or self._last_occupied_ts is None
            or (now_ts - self._last_occupied_ts) <= max(300, gate_time)
        )

        if not max_runtime_exceeded and not event_concluded and not in_runtime_lockout:
            # Trigger A: Rapid rising trend (catches showers early)
            # Requires exceeding the resting shutoff noise floor (Delta H > delta_shutoff)
            # If explicit reference sensor is configured, respect differential threshold gating.
            if (
                trend_val >= trend_up_cutoff
                and (
                    (humidity_delta is None or humidity_delta > delta_shutoff)
                    and (
                        ref_humidity is None
                        or (
                            humidity_delta is not None
                            and humidity_delta >= delta_threshold
                        )
                    )
                )
                and not (
                    learn_shower and ref_humidity is not None and shower_prob < 0.20
                )
            ):
                activation_triggered = True

            # Trigger B: Sustained elevated differential (warm baths, gradual steam accumulation)
            # Guard against activating when humidity is flat/falling, recently concluded, or room is unoccupied
            if (
                not in_conclude_cooldown
                and trend_val >= -0.001
                and is_occupied_or_recent
                and humidity_delta is not None
                and humidity_delta >= delta_threshold
                and not (
                    learn_shower and ref_humidity is not None and shower_prob < 0.20
                )
            ):
                activation_triggered = True

            # Trigger C: High absolute limit failsafe
            if area_humidity is not None and area_humidity >= 80.0:
                activation_triggered = True

        if activation_triggered:
            can_activate = True

            # Presence gating check: require recent occupancy if gate window is configured
            if (
                gate_time > 0
                and self._last_occupied_ts is not None
            ):
                time_since_occupied = now_ts - self._last_occupied_ts
                if time_since_occupied > gate_time:
                    _LOGGER.debug(
                        "%s: Humidity rising but area last occupied %ss ago (> %ss), rejecting activation",
                        self.name,
                        time_since_occupied,
                        gate_time,
                    )
                    can_activate = False

            if can_activate:
                if not self._attr_extra_state_attributes.get(ATTR_HUMIDITY_ON, False):
                    self._attr_extra_state_attributes[ATTR_HUMIDITY_START_TS] = now_ts
                    self._current_shower_rate_peak = trend_val
                    self._current_shower_delta_peak = (
                        humidity_delta if humidity_delta is not None else 0.0
                    )
                    self._current_shower_start_baseline = (
                        self._historical_baseline_humidity or ref_humidity or area_humidity
                    )
                    self._evacuation_model.record_peak(
                        max(4.0, self._current_shower_delta_peak), now_ts
                    )
                else:
                    self._current_shower_rate_peak = max(
                        self._current_shower_rate_peak, trend_val
                    )
                    if humidity_delta is not None:
                        self._current_shower_delta_peak = max(
                            self._current_shower_delta_peak, humidity_delta
                        )
                self._attr_extra_state_attributes[ATTR_HUMIDITY_ON] = True

        # Determine whether fan should be on based on humidity_on
        if self._attr_extra_state_attributes.get(ATTR_HUMIDITY_ON, False):
            if humidity_delta is None and trend_val <= trend_down_cutoff:
                _conclude_shower_event()
            elif (
                self._is_fan_suppressed_in_sleep()
                and not self._is_emergency_moisture(
                    area_humidity=area_humidity,
                    humidity_delta=humidity_delta,
                    shower_prob=shower_prob,
                )
            ):
                _LOGGER.debug(
                    "%s: Sleep suppression active and moisture non-emergency; suppressing fan",
                    self.name,
                )
                self._attr_extra_state_attributes[ATTR_HUMIDITY_ON] = False
                self._attr_extra_state_attributes.pop(ATTR_HUMIDITY_START_TS, None)
                fans_on = False
            else:
                fans_on = True
        else:
            self._attr_extra_state_attributes.pop(ATTR_HUMIDITY_START_TS, None)

        # Expose historical and learned state attributes
        if self._historical_baseline_humidity is not None:
            self._attr_extra_state_attributes[ATTR_HISTORICAL_HUMIDITY_BASELINE] = (
                self._historical_baseline_humidity
            )
        self._attr_extra_state_attributes[ATTR_HISTORICAL_SHOWER_COUNT] = (
            self._historical_shower_count
        )
        self._attr_extra_state_attributes[ATTR_HISTORICAL_SHOWER_DELTA_PEAK] = (
            self._historical_shower_delta_peak
        )
        self._attr_extra_state_attributes[ATTR_HISTORICAL_SHOWER_RATE_PEAK] = (
            self._historical_shower_rate_peak
        )
        self._attr_extra_state_attributes[ATTR_LEARNED_HUMIDITY_DELTA_THRESHOLD] = (
            round(delta_threshold, 1)
        )
        self._attr_extra_state_attributes[ATTR_LEARNED_HUMIDITY_TREND_UP_CUTOFF] = (
            round(trend_up_cutoff, 4)
        )
        self._attr_extra_state_attributes[ATTR_LEARNED_HUMIDITY_DELTA_SHUTOFF] = (
            round(delta_shutoff, 1)
        )

        _LOGGER.debug(
            "Fans on %s (humidity_on: %s)",
            fans_on,
            self._attr_extra_state_attributes.get(ATTR_HUMIDITY_ON, False),
        )
        if fans_on:
            self._turn_on_fan()
        elif self.area.state == AreaState.AREA_STATE_EXTENDED:
            if (
                self._is_fan_suppressed_in_sleep()
                and not self._is_emergency_moisture(
                    area_humidity=area_humidity,
                    humidity_delta=humidity_delta,
                    shower_prob=shower_prob,
                )
            ):
                self._turn_off_fan()
            else:
                self._turn_on_fan()
        else:
            self._turn_off_fan()

        if fans_on or self._attr_extra_state_attributes.get(ATTR_HUMIDITY_ON, False):
            self._start_watchdog_timer()
        else:
            self._stop_watchdog_timer()

        self.schedule_update_ha_state()

    def _update_group_state(self, event: Event[EventStateChangedData]) -> None:
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
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

        if self.last_update_from_entity:
            self.last_update_from_entity = False
            return

        # External manual interaction (physical switch, UI, external integration)
        _LOGGER.debug(
            "%s: Fan turned %s externally; entering manual override until occupied state change",
            self.name,
            new_state.state,
        )

        self._set_controlled_by_this_entity(False)
        if self._manual_timeout_cb is not None:
            self._manual_timeout_cb()
            self._manual_timeout_cb = None
        self.schedule_update_ha_state()

    def _reset_manual_timeout(self, dt: datetime | None = None) -> None:
        if self._manual_timeout_cb is not None:
            self._manual_timeout_cb = None
        self._set_controlled_by_this_entity(True)
        self.schedule_update_ha_state()

    ####  Fan Handling
    def _turn_on_fan(self) -> None:
        """Turn on the fan group."""
        if self.is_on:
            _LOGGER.debug("%s: Fan already on", self.name)
            return

        if not self.area.is_control_enabled(ControlType.System):
            return

        if not self._is_controlled_by_this_entity():
            _LOGGER.debug("%s: Fan in manual override, ignoring automatic turn on", self.name)
            return

        _LOGGER.debug("%s: Turning on fans", self.name)
        self.last_update_from_entity = True
        self._attr_is_on = True
        self._start_watchdog_timer()
        service_data = {
            ATTR_ENTITY_ID: self.entity_id,
        }
        call_service_non_blocking(self.hass, FAN_DOMAIN, SERVICE_TURN_ON, service_data)

    def _turn_off_fan(self) -> None:
        """Turn off the fan group."""
        if not self.is_on:
            _LOGGER.debug("%s: Fan already off", self.name)
            return

        if not self.area.is_control_enabled(ControlType.System):
            return

        if not self._is_controlled_by_this_entity():
            _LOGGER.debug("%s: Fan in manual override, ignoring automatic turn off", self.name)
            return
        _LOGGER.debug("%s: Turning fan off", self.name)
        self.last_update_from_entity = True
        self._attr_is_on = False
        if not self._attr_extra_state_attributes.get(ATTR_HUMIDITY_ON, False):
            self._stop_watchdog_timer()
        service_data = {ATTR_ENTITY_ID: self.entity_id}
        call_service_non_blocking(
            self.hass, FAN_DOMAIN, SERVICE_TURN_OFF, service_data
        )

    def _start_watchdog_timer(self) -> None:
        """Start periodic watchdog timer while fan or humidity mode is active."""
        if self._watchdog_unsub is None:
            self._watchdog_unsub = async_track_time_interval(
                self.hass, self._watchdog_tick, timedelta(seconds=60)
            )

    def _stop_watchdog_timer(self) -> None:
        """Stop watchdog timer."""
        if self._watchdog_unsub is not None:
            self._watchdog_unsub()
            self._watchdog_unsub = None

    def _watchdog_tick(self, _now: datetime | None = None) -> None:
        """Periodic watchdog evaluation to guarantee timeouts when sensors flatline."""
        _LOGGER.debug("%s: Watchdog evaluating fan humidity control", self.name)
        self._humidity_fan_control()

    async def async_will_remove_from_hass(self) -> None:
        """Clean up timers when removed from hass."""
        self._stop_watchdog_timer()
        if self._manual_timeout_cb is not None:
            self._manual_timeout_cb()
            self._manual_timeout_cb = None
        await super().async_will_remove_from_hass()

    #### Control Release
    def _is_controlled_by_this_entity(self) -> bool:
        return not self._attr_extra_state_attributes.get(ATTR_MANUAL_CONTROL, False)

    def _set_controlled_by_this_entity(self, enabled: bool) -> None:
        if enabled:
            self._attr_extra_state_attributes.pop(ATTR_MANUAL_CONTROL, None)
            self._attr_extra_state_attributes.pop(ATTR_MANUAL_TIMEOUT_TS, None)
        else:
            self._attr_extra_state_attributes[ATTR_MANUAL_CONTROL] = True

    def _reset_control(self, time: datetime | None = None) -> None:
        if self._manual_timeout_cb is not None:
            self._manual_timeout_cb()
            self._manual_timeout_cb = None
        self._set_controlled_by_this_entity(True)
        self.schedule_update_ha_state()
        _LOGGER.debug("%s: Control Reset", self.name)
