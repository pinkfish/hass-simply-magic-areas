"""Fan controls for magic areas."""

import logging
from datetime import UTC, datetime
from typing import Any

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
)
from homeassistant.util import slugify

from .base.entities import MagicEntity
from .base.magic import ControlType, MagicArea
from .config.area_state import AreaState
from .config.entity_names import EntityNames
from .const import (
    ATTR_HUMIDITY_DELTA,
    ATTR_HUMIDITY_START_TS,
    ATTR_LAST_UPDATE_FROM_ENTITY,
    ATTR_MANUAL_CONTROL,
    ATTR_MANUAL_TIMEOUT_TS,
    ATTR_REFERENCE_HUMIDITY,
    CONF_FEATURE_HUMIDITY,
    CONF_HUMIDITY_DEADBAND,
    CONF_HUMIDITY_DELTA_SHUTOFF,
    CONF_HUMIDITY_DELTA_THRESHOLD,
    CONF_HUMIDITY_MAX_FAN_TIME,
    CONF_HUMIDITY_PRESENCE_GATE_TIME,
    CONF_HUMIDITY_TREND_DOWN_CUT_OFF,
    CONF_HUMIDITY_TREND_UP_CUT_OFF,
    CONF_HUMIDITY_ZERO_WAIT_TIME,
    CONF_MANUAL_TIMEOUT,
    CONF_REFERENCE_HUMIDITY_SENSOR,
    DATA_AREA_OBJECT,
    DEFAULT_HUMIDITY_DEADBAND,
    DEFAULT_HUMIDITY_DELTA_SHUTOFF,
    DEFAULT_HUMIDITY_DELTA_THRESHOLD,
    DEFAULT_HUMIDITY_MAX_FAN_TIME,
    DEFAULT_HUMIDITY_PRESENCE_GATE_TIME,
    DEFAULT_HUMIDITY_TREND_DOWN_CUT_OFF,
    DEFAULT_HUMIDITY_TREND_UP_CUT_OFF,
    DEFAULT_HUMIDITY_ZERO_WAIT_TIME,
    DEFAULT_MANUAL_TIMEOUT,
    DOMAIN,
    META_AREA_INTERIOR,
    MODULE_DATA,
)
from .util import call_service_non_blocking

_LOGGER = logging.getLogger(__name__)
ATTR_FANS: str = "fans"
ATTR_HUMIDITY_ON: str = "humidity_on"
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

        self._controled_by_entity = True
        self._last_occupied_ts: float | None = None

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
            if ATTR_HUMIDITY_ON in last_state.attributes:
                self._attr_extra_state_attributes[ATTR_HUMIDITY_ON] = (
                    last_state.attributes[ATTR_HUMIDITY_ON]
                )
            if ATTR_HUMIDITY_START_TS in last_state.attributes:
                self._attr_extra_state_attributes[ATTR_HUMIDITY_START_TS] = (
                    last_state.attributes[ATTR_HUMIDITY_START_TS]
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
            self._attr_extra_state_attributes.get(ATTR_HUMIDITY_ON, False),
            self.is_on,
        )

        # Manual control, ignore all changes
        if to_state == AreaState.AREA_STATE_MANUAL:
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
            self._turn_on_fan()
        elif self.is_on and not self._attr_extra_state_attributes.get(
            ATTR_HUMIDITY_ON, False
        ):
            self._turn_off_fan()
        else:
            _LOGGER.debug("Ignoring state change")

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
        fans_on: bool = False
        humidity_trend = self.hass.states.get(
            self.area.simply_magic_entity_id(
                SENSOR_DOMAIN,
                EntityNames.HUMIDITY_STATISTICS,
            )
        )
        if (
            humidity_trend is None
            or humidity_trend.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        ):
            return

        humidity_feature_config = self.area.feature_config(CONF_FEATURE_HUMIDITY)
        trend_val = float(humidity_trend.state)
        now_ts = datetime.now(UTC).timestamp()

        # Compute differential baseline if available
        area_humidity = self._get_area_humidity()
        ref_humidity = self._get_reference_humidity()
        humidity_delta: float | None = None
        if area_humidity is not None and ref_humidity is not None:
            humidity_delta = area_humidity - ref_humidity
            self._attr_extra_state_attributes[ATTR_HUMIDITY_DELTA] = round(
                humidity_delta, 1
            )
            self._attr_extra_state_attributes[ATTR_REFERENCE_HUMIDITY] = round(
                ref_humidity, 1
            )
        else:
            self._attr_extra_state_attributes.pop(ATTR_HUMIDITY_DELTA, None)
            self._attr_extra_state_attributes.pop(ATTR_REFERENCE_HUMIDITY, None)

        # 1. Deadband Flatline Detection (replaces strict != 0.0)
        deadband = humidity_feature_config.get(
            CONF_HUMIDITY_DEADBAND, DEFAULT_HUMIDITY_DEADBAND
        )
        if abs(trend_val) > deadband:
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
            if zero_time > wait_time:
                _LOGGER.debug(
                    "%s: Humidity rate %s within deadband %s for %ss (> %ss), turning off",
                    self.name,
                    trend_val,
                    deadband,
                    zero_time,
                    wait_time,
                )
                self._attr_extra_state_attributes[ATTR_HUMIDITY_ON] = False

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
                self._attr_extra_state_attributes[ATTR_HUMIDITY_ON] = False
                max_runtime_exceeded = True

        # 3. Reference Delta Shutoff Check
        delta_shutoff = humidity_feature_config.get(
            CONF_HUMIDITY_DELTA_SHUTOFF, DEFAULT_HUMIDITY_DELTA_SHUTOFF
        )
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
            self._attr_extra_state_attributes[ATTR_HUMIDITY_ON] = False

        # 4. Falling Trend Check (Below DOWN_CUT_OFF)
        trend_down_cutoff = humidity_feature_config.get(
            CONF_HUMIDITY_TREND_DOWN_CUT_OFF, DEFAULT_HUMIDITY_TREND_DOWN_CUT_OFF
        )
        if trend_val < trend_down_cutoff:
            self._attr_extra_state_attributes[ATTR_HUMIDITY_ON] = False

        # 5. Rising Trend / Activation Check
        trend_up_cutoff = humidity_feature_config.get(
            CONF_HUMIDITY_TREND_UP_CUT_OFF, DEFAULT_HUMIDITY_TREND_UP_CUT_OFF
        )
        if trend_val >= trend_up_cutoff and not max_runtime_exceeded:
            can_activate = True

            # Differential check: require delta >= threshold if reference baseline is available
            delta_threshold = humidity_feature_config.get(
                CONF_HUMIDITY_DELTA_THRESHOLD, DEFAULT_HUMIDITY_DELTA_THRESHOLD
            )
            if humidity_delta is not None and humidity_delta < delta_threshold:
                _LOGGER.debug(
                    "%s: Humidity rising (%s) but delta (%s%%) < threshold (%s%%), rejecting activation",
                    self.name,
                    trend_val,
                    humidity_delta,
                    delta_threshold,
                )
                can_activate = False

            # Presence gating check: require recent occupancy if gate window is configured
            gate_time = humidity_feature_config.get(
                CONF_HUMIDITY_PRESENCE_GATE_TIME, DEFAULT_HUMIDITY_PRESENCE_GATE_TIME
            )
            if gate_time > 0 and self._last_occupied_ts is not None:
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
                self._attr_extra_state_attributes[ATTR_HUMIDITY_ON] = True

        # Determine whether fan should be on based on humidity_on and down trend
        if self._attr_extra_state_attributes.get(ATTR_HUMIDITY_ON, False):
            if trend_val > trend_down_cutoff:
                fans_on = True
            else:
                self._attr_extra_state_attributes[ATTR_HUMIDITY_ON] = False
        else:
            self._attr_extra_state_attributes.pop(ATTR_HUMIDITY_START_TS, None)

        _LOGGER.debug(
            "Fans on %s (humidity_on: %s)",
            fans_on,
            self._attr_extra_state_attributes.get(ATTR_HUMIDITY_ON, False),
        )
        if fans_on:
            self._turn_on_fan()
        elif self.area.state != AreaState.AREA_STATE_EXTENDED:
            self._turn_off_fan()

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

        # Skip non ON/OFF state changes
        if old_state.state not in [
            STATE_ON,
            STATE_OFF,
        ] or new_state.state not in [
            STATE_ON,
            STATE_OFF,
        ]:
            return

        if self.last_update_from_entity:
            self.last_update_from_entity = False
            return

        manual_timeout = self.area.config.get(
            CONF_MANUAL_TIMEOUT, DEFAULT_MANUAL_TIMEOUT
        )
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

        _LOGGER.debug("%s: Turning on fans", self.name)
        self.last_update_from_entity = True
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
        _LOGGER.debug("%s: Turning fan off", self.name)
        self.last_update_from_entity = True
        service_data = {ATTR_ENTITY_ID: self.entity_id}
        call_service_non_blocking(
            self.hass, FAN_DOMAIN, SERVICE_TURN_OFF, service_data
        )

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
