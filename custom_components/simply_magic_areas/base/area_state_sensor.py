"""Select control for magic areas, tracks the state as an enum."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_ENTITY_ID,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, EventStateChangedData, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)

from ..config.area_state import AreaState, LightingProfile
from ..config.entity_names import EntityNames
from ..const import (
    ATTR_ACTIVE_AREAS,
    ATTR_ACTIVE_SENSORS,
    ATTR_AREAS,
    ATTR_CLEAR_TIMEOUT,
    ATTR_CLEAR_TIMEOUT_TS,
    ATTR_EXTENDED_TIMEOUT,
    ATTR_EXTENDED_TIMEOUT_TS,
    ATTR_LAST_ACTIVE_SENSORS,
    ATTR_LAST_OFF_TIME,
    ATTR_LIGHTING_PROFILE,
    ATTR_PRESENCE_SENSORS,
    ATTR_STATE,
    ATTR_TYPE,
    CONF_CLEAR_TIMEOUT,
    CONF_EXTENDED_TIMEOUT,
    CONF_FEATURE_ADVANCED_LIGHT_GROUPS,
    CONF_FEATURE_HUMIDITY,
    CONF_HUMIDITY_DEADBAND,
    CONF_HUMIDITY_DELTA_SHUTOFF,
    CONF_HUMIDITY_DELTA_THRESHOLD,
    CONF_HUMIDITY_PRESENCE_GATE_TIME,
    CONF_HUMIDITY_TREND_DOWN_CUT_OFF,
    CONF_HUMIDITY_TREND_UP_CUT_OFF,
    CONF_HUMIDITY_ZERO_WAIT_TIME,
    CONF_MQTT_ROOM_PRESENCE,
    CONF_ON_STATES,
    CONF_PRESENCE_DEVICE_PLATFORMS,
    CONF_PRESENCE_SENSOR_DEVICE_CLASS,
    CONF_REFERENCE_HUMIDITY_SENSOR,
    CONF_TYPE,
    CONF_UPDATE_INTERVAL,
    DEFAULT_HUMIDITY_DEADBAND,
    DEFAULT_HUMIDITY_DELTA_SHUTOFF,
    DEFAULT_HUMIDITY_DELTA_THRESHOLD,
    DEFAULT_HUMIDITY_PRESENCE_GATE_TIME,
    DEFAULT_HUMIDITY_TREND_DOWN_CUT_OFF,
    DEFAULT_HUMIDITY_TREND_UP_CUT_OFF,
    DEFAULT_HUMIDITY_ZERO_WAIT_TIME,
    DEFAULT_MQTT_ROOM_PRESENCE,
    DEFAULT_ON_STATES,
    DEFAULT_PRESENCE_DEVICE_PLATFORMS,
    DEFAULT_PRESENCE_DEVICE_SENSOR_CLASS,
    DEFAULT_UPDATE_INTERVAL,
    INVALID_STATES,
)
from .entities import MagicEntity
from .magic import ControlType, MagicArea

_LOGGER = logging.getLogger(__name__)

ATTR_HUMIDITY_ON = "humidity_on"
ATTR_HUMIDITY_ZERO_TS = "humidity_zero_ts"


class AreaStateSensor(MagicEntity, SensorEntity):
    """Create an area presence select entity that tracks the current occupied state."""

    def __init__(self, area: MagicArea) -> None:
        """Initialize the area presence select."""

        SensorEntity.__init__(self)
        MagicEntity.__init__(
            self, area=area, domain=SENSOR_DOMAIN, translation_key="state"
        )

        self._attr_options = [
            AreaState.AREA_STATE_CLEAR,
            AreaState.AREA_STATE_OCCUPIED,
            AreaState.AREA_STATE_EXTENDED,
            AreaState.AREA_STATE_MANUAL,
        ]
        self._attr_native_value = AreaState.AREA_STATE_CLEAR
        self._attr_extra_state_attributes = {}
        self._attr_device_class = SensorDeviceClass.ENUM

        self._last_off_time: datetime = datetime.now(UTC) - timedelta(days=2)  # type: ignore
        self._clear_timeout_callback: Callable[[], None] | None = None
        self._extended_timeout_callback: Callable[[], None] | None = None
        self._sensors: list[str] = []
        self._mqqt_room_sensors: list[str] = []
        self._mode: str = "one"
        self._sensor_changed_in_extended: bool = False

    async def async_added_to_hass(self) -> None:
        """Call to add the system to hass."""
        _LOGGER.info("%s: added to hass", self.area.name)
        await super().async_added_to_hass()
        await self._restore_state()
        await self._load_attributes()
        self._load_presence_sensors()

        # Setup the listeners
        await self._setup_listeners()

        _LOGGER.debug(  # type: ignore
            "%s: Select initialized %s %s %s",
            self.unique_id,
            self.entity_id,
            self.name,
            self.translation_key,
        )  # type: ignore
        self.async_on_remove(self._cleanup_timers)

    async def _restore_state(self) -> None:
        """Restore the state of the select entity on initialize."""
        last_state = await self.async_get_last_state()

        self.schedule_update_ha_state()
        if last_state is None:
            _LOGGER.debug("%s: New select created", self.name)  # type: ignore
            self._attr_native_value = AreaState.AREA_STATE_CLEAR
            self.area.state = AreaState.AREA_STATE_CLEAR
        else:
            _LOGGER.debug(  # type: ignore
                "%s: Select restored [state=%s]",
                self.name,
                last_state.state,
            )  # type: ignore
            if last_state.state in ("bright", "sleep", "accented"):
                restored_state = AreaState.AREA_STATE_OCCUPIED
            elif last_state.state in self._attr_options:
                restored_state = AreaState(last_state.state)
            else:
                restored_state = AreaState.AREA_STATE_CLEAR

            self.area.state = restored_state
            self._attr_native_value = restored_state
            self._attr_extra_state_attributes = dict(last_state.attributes)  # type: ignore

            # Persistent timer restoration
            if (
                ATTR_LAST_OFF_TIME in last_state.attributes
                and last_state.attributes[ATTR_LAST_OFF_TIME] is not None
            ):
                try:
                    self._last_off_time = datetime.fromtimestamp(
                        float(last_state.attributes[ATTR_LAST_OFF_TIME]), UTC
                    )
                except (ValueError, TypeError):
                    pass

            now_ts = datetime.now(UTC).timestamp()

            # Restore persistent extended timeout
            if self.area.state == AreaState.AREA_STATE_EXTENDED:
                target_ts = last_state.attributes.get(ATTR_EXTENDED_TIMEOUT_TS)
                if target_ts is not None:
                    try:
                        remaining = int(float(target_ts) - now_ts)
                        if remaining > 0:
                            self._set_extended_timeout(remaining)
                        else:
                            self._remove_extended_timeout()
                    except (ValueError, TypeError):
                        pass

            # Restore persistent clear timeout
            elif last_state.attributes.get("clear"):
                target_ts = last_state.attributes.get(ATTR_CLEAR_TIMEOUT_TS)
                if target_ts is not None:
                    try:
                        remaining = int(float(target_ts) - now_ts)
                        if remaining > 0:
                            self._set_clear_timeout(remaining)
                        else:
                            self._remove_clear_timeout()
                    except (ValueError, TypeError):
                        pass

        self.hass.loop.call_soon_threadsafe(self._update_state, datetime.now(UTC))

    async def _setup_listeners(self) -> None:
        _LOGGER.debug("%s: Called '_setup_listeners'", self.name)  # type: ignore
        if not self.hass.is_running:
            _LOGGER.debug("%s: Cancelled '_setup_listeners'", self.name)  # type: ignore
            return

        # Track presence sensor
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._sensors, self._sensor_state_change
            )
        )

        # Track room presence.
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._mqqt_room_sensors, self._group_entity_state_change
            )
        )

        # Track humidity trend sensor
        humidity_trend = self.area.simply_magic_entity_id(
            SENSOR_DOMAIN, EntityNames.HUMIDITY_STATISTICS
        )
        if self.hass.states.get(humidity_trend) is not None:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [humidity_trend], self._humidity_sensor_change
                )
            )

        # Track system control switch
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [
                    self.area.simply_magic_entity_id(
                        SWITCH_DOMAIN, EntityNames.SYSTEM_CONTROL
                    )
                ],
                self._group_entity_state_change,
            )
        )

        # Track lighting profile changes to update attribute
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [
                    self.area.simply_magic_entity_id(
                        SENSOR_DOMAIN, EntityNames.LIGHTING_PROFILE
                    )
                ],
                self._lighting_profile_changed,
            )
        )

        # Timed self update
        delta = timedelta(
            seconds=self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS).get(
                CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
            )
        )
        self.async_on_remove(
            async_track_time_interval(self.hass, self._update_state, delta)
        )

    def _load_presence_sensors(self) -> None:
        if self.area.is_meta():
            # MetaAreas track their children
            child_areas: list[str] = self.area.get_child_areas()
            for child_area in child_areas:
                entity_id = f"{SENSOR_DOMAIN}.simply_magic_area_state_{child_area}"
                self._sensors.append(entity_id)
            return

        valid_presence_platforms = self.area.feature_config(
            CONF_FEATURE_ADVANCED_LIGHT_GROUPS
        ).get(CONF_PRESENCE_DEVICE_PLATFORMS, DEFAULT_PRESENCE_DEVICE_PLATFORMS)

        for component, entities in self.area.entities.items():
            if component == "mqtt_room" + SENSOR_DOMAIN:
                # Handle the mqtt_room entities
                self._mqqt_room_sensors.extend(
                    [entity[ATTR_ENTITY_ID] for entity in entities]
                )
                continue

            if component not in valid_presence_platforms:
                continue

            for entity in entities:
                if not entity:
                    continue

                if component == BINARY_SENSOR_DOMAIN:
                    if ATTR_DEVICE_CLASS not in entity:
                        continue

                    if entity[ATTR_DEVICE_CLASS] not in self.area.feature_config(
                        CONF_FEATURE_ADVANCED_LIGHT_GROUPS
                    ).get(
                        CONF_PRESENCE_SENSOR_DEVICE_CLASS,
                        DEFAULT_PRESENCE_DEVICE_SENSOR_CLASS,
                    ):
                        continue

                self._sensors.append(entity[ATTR_ENTITY_ID])

    async def _load_attributes(self) -> None:
        # Set attributes
        if not self.area.is_meta():
            self._attr_extra_state_attributes[ATTR_STATE] = self.area.state
        else:
            self._attr_extra_state_attributes.update(
                {
                    ATTR_AREAS: self.area.get_child_areas(),
                    ATTR_ACTIVE_AREAS: self.area.get_active_areas(),
                }
            )

        # Add common attributes
        self._attr_extra_state_attributes.update(
            {
                ATTR_ACTIVE_SENSORS: [],
                ATTR_LAST_ACTIVE_SENSORS: [],
                ATTR_PRESENCE_SENSORS: self._sensors,
                ATTR_TYPE: self.area.config.get(CONF_TYPE),
                ATTR_LIGHTING_PROFILE: getattr(
                    self.area, "lighting_profile", LightingProfile.STANDARD
                ),
            }
        )

    def _update_attributes(self) -> None:
        self._attr_extra_state_attributes[ATTR_STATE] = self.area.state
        self._attr_extra_state_attributes[ATTR_LIGHTING_PROFILE] = getattr(
            self.area, "lighting_profile", LightingProfile.STANDARD
        )
        self._attr_extra_state_attributes[ATTR_CLEAR_TIMEOUT] = (
            self._get_clear_timeout()
        )
        self._attr_extra_state_attributes[ATTR_EXTENDED_TIMEOUT] = (
            self._get_extended_timeout()
        )

        if self.area.is_meta():
            self._attr_extra_state_attributes[ATTR_ACTIVE_AREAS] = (
                self.area.get_active_areas()
            )

    ####
    ####     State Change Handling
    def get_current_area_state(self) -> AreaState:
        """Get the current state for the area based on the various entities and controls."""
        # If it is in manual mode, set the state to manual.
        if not self.area.is_control_enabled(ControlType.System):
            return AreaState.AREA_STATE_MANUAL

        # Get Main occupancy state
        occupied_state = self._get_sensors_state()

        _LOGGER.debug("Sensor state %s", occupied_state)  # type: ignore

        seconds_since_last_change: int = int(
            (datetime.now(UTC) - self._last_off_time).total_seconds()
        )

        clear_timeout: int = self._get_clear_timeout()
        extended_timeout: int = self._get_extended_timeout() + clear_timeout
        if not occupied_state:
            # If the area was in extended mode and a sensor changed, move back to occupied
            if self._sensor_changed_in_extended:
                self._sensor_changed_in_extended = False
                self._remove_extended_timeout()
                self._remove_clear_timeout()
                self._set_clear_timeout(clear_timeout)
            # If the area was already clear, non-occupied sensor state keeps it clear
            elif self.area.state == AreaState.AREA_STATE_CLEAR:
                self._remove_clear_timeout()
                self._remove_extended_timeout()
                return AreaState.AREA_STATE_CLEAR

            # If the area was in extended mode, check if extended timeout has elapsed
            elif self.area.state == AreaState.AREA_STATE_EXTENDED:
                if seconds_since_last_change >= extended_timeout:
                    self._remove_extended_timeout()
                    return AreaState.AREA_STATE_CLEAR
                if not self._is_on_extended_timeout():
                    remaining = extended_timeout - seconds_since_last_change
                    if remaining > 0:
                        self._set_extended_timeout(remaining)
                return AreaState.AREA_STATE_EXTENDED

            # If area was previously occupied: start or continue clear_timeout countdown
            if (
                not self._is_on_clear_timeout()
                and seconds_since_last_change < clear_timeout
            ):
                self._set_clear_timeout(clear_timeout - seconds_since_last_change)
            if seconds_since_last_change >= clear_timeout:
                if seconds_since_last_change >= extended_timeout:
                    self._remove_extended_timeout()
                    return AreaState.AREA_STATE_CLEAR
                _LOGGER.debug("%s: Clearing the imput, state extended", self.area.slug)  # type: ignore
                self._remove_clear_timeout()
                if (
                    not self._is_on_extended_timeout()
                    and seconds_since_last_change < extended_timeout
                ):
                    self._set_extended_timeout(
                        extended_timeout - seconds_since_last_change
                    )
                return AreaState.AREA_STATE_EXTENDED
        else:
            self._sensor_changed_in_extended = False
            self._remove_clear_timeout()
            self._remove_extended_timeout()

        # Pure occupancy state
        return AreaState.AREA_STATE_OCCUPIED

    @callback
    def _validate_state_consistency(self) -> None:
        """Validate area state consistency and schedule clear timeout if needed.

        Catches edge cases where:
        1. Area is stuck occupied due to a race condition or sensor unavailability
        2. Clear timeout was lost or expired during Home Assistant shutdown
        """
        if self.area.state in (AreaState.AREA_STATE_CLEAR, AreaState.AREA_STATE_MANUAL):
            return

        has_active_sensors = self._get_sensors_state()
        has_timeout_pending = (
            self._is_on_clear_timeout() or self._is_on_extended_timeout()
        )

        if not has_active_sensors and not has_timeout_pending:
            _LOGGER.warning(
                "%s: State inconsistency detected - occupied with no active sensors "
                "and no timeout scheduled. Scheduling clear timeout.",
                self.area.name,
            )
            clear_timeout = self._get_clear_timeout()
            seconds_since_off = int(
                (datetime.now(UTC) - self._last_off_time).total_seconds()
            )
            if seconds_since_off >= clear_timeout:
                self.area.state = AreaState.AREA_STATE_CLEAR
                self._attr_native_value = AreaState.AREA_STATE_CLEAR
                self._update_attributes()
                self.schedule_update_ha_state()
            else:
                self._set_clear_timeout(clear_timeout - seconds_since_off)

    @callback
    def _update_state(self, extra: datetime | None = None) -> None:
        last_state = self.area.state
        new_state = self.get_current_area_state()

        if last_state == new_state:
            self._update_attributes()
            self._validate_state_consistency()
            return

        # Calculate what's new
        _LOGGER.debug(  # type: ignore
            "%s: Current state: %s, last state: %s",
            self.name,
            new_state,
            last_state,
        )

        # Update the state so the on/off works correctly.
        self.area.state = new_state
        self._attr_native_value = new_state

        self._update_attributes()
        self.schedule_update_ha_state()

        _LOGGER.debug(
            "Reporting state change for %s (new state: %s/last state: %s)",
            self.area.name,
            new_state,
            last_state,
        )

        self._validate_state_consistency()

    def _lighting_profile_changed(self, event: Event[EventStateChangedData]) -> None:
        if event.data.get("new_state") is None:
            return
        self._attr_extra_state_attributes[ATTR_LIGHTING_PROFILE] = (
            event.data["new_state"].state
        )
        self.schedule_update_ha_state()

    def _group_entity_state_change(self, event: Event[EventStateChangedData]) -> None:
        if event.event_type != "state_changed":
            return
        if event.data["new_state"] is None:
            return

        to_state: str = str(event.data["new_state"].state)
        entity_id: str = str(event.data["entity_id"])

        _LOGGER.debug(
            "%s: Secondary state change: entity '%s' changed to %s",
            self.area.name,
            entity_id,
            to_state,
        )

        old_state = event.data.get("old_state")
        old_state_val = old_state.state if old_state is not None else None

        if to_state in INVALID_STATES:
            _LOGGER.debug(
                "%s: sensor '%s' has invalid state %s",
                self.area.name,
                entity_id,
                to_state,
            )
            return

        if (
            self.area.state == AreaState.AREA_STATE_EXTENDED
            and entity_id in self._mqqt_room_sensors
            and old_state_val != to_state
        ):
            _LOGGER.info(
                "%s: Room presence '%s' changed from %s to %s while in extended state; returning to occupied",
                self.area.name,
                entity_id,
                old_state_val,
                to_state,
            )
            self._sensor_changed_in_extended = True
            self._last_off_time = datetime.now(UTC)
            self._remove_extended_timeout()
            self._remove_clear_timeout()

        self.hass.loop.call_soon_threadsafe(self._update_state, datetime.now(UTC))

    ###       Clearing

    def _get_clear_timeout(self) -> int:
        return int(self.area.config.get(CONF_CLEAR_TIMEOUT, 60))

    def _set_clear_timeout(self, timeout: int) -> None:
        if self._clear_timeout_callback:
            self._remove_clear_timeout()

        _LOGGER.debug("%s: Scheduling clear in %s seconds", self.area.name, timeout)  # type: ignore
        self._attr_extra_state_attributes["clear"] = True
        self._attr_extra_state_attributes[ATTR_CLEAR_TIMEOUT_TS] = (
            datetime.now(UTC) + timedelta(seconds=timeout)
        ).timestamp()
        self._attr_extra_state_attributes[ATTR_LAST_OFF_TIME] = (
            self._last_off_time.timestamp()
        )
        self._clear_timeout_callback = async_call_later(
            self.hass,
            timeout,
            self._update_state,
        )

    def _remove_clear_timeout(self) -> None:
        if not self._clear_timeout_callback:
            return

        _LOGGER.debug(  # type: ignore
            "%s: Clearing timeout",
            self.area.name,
        )

        self._attr_extra_state_attributes["clear"] = False
        self._attr_extra_state_attributes.pop(ATTR_CLEAR_TIMEOUT_TS, None)
        self._clear_timeout_callback()
        self._clear_timeout_callback = None

    def _is_on_clear_timeout(self) -> bool:
        return self._clear_timeout_callback is not None

    @callback
    def _cleanup_timers(self) -> None:
        self._sensor_changed_in_extended = False
        self._remove_clear_timeout()
        self._remove_extended_timeout()
        self._attr_extra_state_attributes.pop("clear", None)
        self._attr_extra_state_attributes.pop("extended", None)
        self._attr_extra_state_attributes.pop(ATTR_CLEAR_TIMEOUT_TS, None)
        self._attr_extra_state_attributes.pop(ATTR_EXTENDED_TIMEOUT_TS, None)
        self._attr_extra_state_attributes.pop(ATTR_LAST_OFF_TIME, None)

    ###       Extended

    def _get_extended_timeout(self) -> int:
        return int(self.area.config.get(CONF_EXTENDED_TIMEOUT, 60))

    def _set_extended_timeout(self, timeout: int) -> None:
        if self._extended_timeout_callback:
            self._remove_extended_timeout()

        _LOGGER.info("%s: Scheduling extended in %s seconds", self.area.name, timeout)  # type: ignore
        self._attr_extra_state_attributes["extended"] = True
        self._attr_extra_state_attributes[ATTR_EXTENDED_TIMEOUT_TS] = (
            datetime.now(UTC) + timedelta(seconds=timeout)
        ).timestamp()
        self._attr_extra_state_attributes[ATTR_LAST_OFF_TIME] = (
            self._last_off_time.timestamp()
        )
        self._extended_timeout_callback = async_call_later(
            self.hass,
            timeout,
            self._update_state,
        )

    def _remove_extended_timeout(self) -> None:
        if not self._extended_timeout_callback:
            return

        self._attr_extra_state_attributes["extended"] = False
        self._attr_extra_state_attributes.pop(ATTR_EXTENDED_TIMEOUT_TS, None)
        self._extended_timeout_callback()
        self._extended_timeout_callback = None

    def _is_on_extended_timeout(self) -> bool:
        return self._extended_timeout_callback is not None

    #### Sensor controls.

    def _humidity_sensor_change(self, event: Event[EventStateChangedData]) -> None:
        if event.data["new_state"] is None:
            return
        to_state = event.data["new_state"].state
        entity_id = event.data["entity_id"]
        if to_state in INVALID_STATES:
            _LOGGER.debug(
                "%s: sensor '%s' has invalid state %s",
                self.name,
                entity_id,
                to_state,
            )
            return
        self.hass.loop.call_soon_threadsafe(self._update_state, datetime.now(UTC))

    def _sensor_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Actions when the sensor state has changed."""
        if event.data["new_state"] is None:
            return
        to_state = event.data["new_state"].state
        entity_id = event.data["entity_id"]

        _LOGGER.debug(
            "%s: sensor '%s' changed to {%s}",
            self.name,
            entity_id,
            to_state,
        )

        old_state = event.data.get("old_state")
        old_state_val = old_state.state if old_state is not None else None

        if to_state in INVALID_STATES:
            _LOGGER.debug(
                "%s: sensor '%s' has invalid state %s",
                self.name,
                entity_id,
                to_state,
            )
            return

        if (
            self.area.state == AreaState.AREA_STATE_EXTENDED
            and old_state_val != to_state
        ):
            _LOGGER.info(
                "%s: Sensor '%s' changed from %s to %s while in extended state; returning to occupied",
                self.name,
                entity_id,
                old_state_val,
                to_state,
            )
            self._sensor_changed_in_extended = True
            self._last_off_time = datetime.now(UTC)
            self._remove_extended_timeout()
            self._remove_clear_timeout()

        if to_state and to_state not in self.area.feature_config(
            CONF_FEATURE_ADVANCED_LIGHT_GROUPS
        ).get(CONF_ON_STATES, DEFAULT_ON_STATES):
            _LOGGER.debug(
                "Setting last non-normal time %s %s",
                event.data["old_state"],
                event.data["new_state"],
            )
            if self.area.state == AreaState.AREA_STATE_OCCUPIED:
                self._last_off_time = datetime.now(UTC)  # Update last_off_time
        else:
            # Presence sensor turned ON: cancel any pending clear or extended timeouts
            self._remove_clear_timeout()
            self._remove_extended_timeout()

        self.hass.loop.call_soon_threadsafe(self._update_state, datetime.now(UTC))

    def _get_sensors_state(self) -> bool:
        """Get the current state of the sensor."""
        valid_states = (
            [STATE_ON]
            if self.area.is_meta()
            else self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS).get(
                CONF_ON_STATES, DEFAULT_ON_STATES
            )
        )

        _LOGGER.debug(
            "[Area: %s] Updating state. (Valid states: %s)",
            self.area.slug,
            valid_states,
        )

        if valid_states is None:
            valid_states = [STATE_ON]

        active_sensors: list[str] = []
        active_areas: set[str] = set()

        # Loop over all entities and check their state
        for sensor in self._sensors:
            try:
                entity = self.hass.states.get(sensor)

                if not entity:
                    _LOGGER.info(
                        "[Area: %s] Could not get sensor state: %s entity not found, skipping",
                        self.area.slug,
                        sensor,
                    )
                    continue

                _LOGGER.debug(
                    "[Area: %s] Sensor %s state: %s",
                    self.area.slug,
                    sensor,
                    entity.state,
                )

                # Skip unavailable entities
                if entity.state in INVALID_STATES:
                    _LOGGER.debug(
                        "[Area: %s] Sensor '%s' is unavailable, skipping",
                        self.area.slug,
                        sensor,
                    )
                    continue

                if entity.state in valid_states:
                    _LOGGER.debug(
                        "[Area: %s] Valid presence sensor found: %s",
                        self.area.slug,
                        sensor,
                    )
                    active_sensors.append(sensor)

            except Exception as e:  # noqa: BLE001
                _LOGGER.error(
                    "[%s] Error getting entity state for '%s': %s",
                    self.area.slug,
                    sensor,
                    str(e),
                )

        # Track the mqtt room stuff.
        if self.area.config.get(CONF_MQTT_ROOM_PRESENCE, DEFAULT_MQTT_ROOM_PRESENCE):
            for mqtt_room_sensor in self._mqqt_room_sensors:
                try:
                    entity = self.hass.states.get(mqtt_room_sensor)
                    if entity and entity.state == self.area.name.lower():
                        active_sensors.append(mqtt_room_sensor)
                except Exception as e:  # noqa: BLE001
                    _LOGGER.error(  # type: ignore
                        "[%s] Error getting entity state for '%s': %s",
                        self.area.slug,
                        mqtt_room_sensor,
                        str(e),
                    )

        # Track the up/down trend.
        humidity_trend = self.hass.states.get(
            self.area.simply_magic_entity_id(
                SENSOR_DOMAIN,
                EntityNames.HUMIDITY_STATISTICS,
            )
        )
        if (
            humidity_trend is not None
            and humidity_trend.state != STATE_UNAVAILABLE
            and humidity_trend.state != STATE_UNKNOWN
        ):
            humidity_feature_config = self.area.feature_config(CONF_FEATURE_HUMIDITY)
            deadband = humidity_feature_config.get(
                CONF_HUMIDITY_DEADBAND, DEFAULT_HUMIDITY_DEADBAND
            )
            trend_val = float(humidity_trend.state)
            if abs(trend_val) > deadband:
                self._attr_extra_state_attributes[ATTR_HUMIDITY_ZERO_TS] = None
            elif self._attr_extra_state_attributes.get(ATTR_HUMIDITY_ZERO_TS) is None:
                self._attr_extra_state_attributes[ATTR_HUMIDITY_ZERO_TS] = datetime.now(
                    UTC
                ).timestamp()
            if self._attr_extra_state_attributes[ATTR_HUMIDITY_ZERO_TS] is not None:
                zero_time: int = int(
                    (
                        datetime.now(UTC)
                        - datetime.fromtimestamp(
                            int(
                                self._attr_extra_state_attributes[ATTR_HUMIDITY_ZERO_TS]
                            ),
                            UTC,
                        )
                    ).total_seconds()
                )
                if zero_time > humidity_feature_config.get(
                    CONF_HUMIDITY_ZERO_WAIT_TIME, DEFAULT_HUMIDITY_ZERO_WAIT_TIME
                ):
                    self._attr_extra_state_attributes[ATTR_HUMIDITY_ON] = False

            # Calculate humidity delta if reference sensor is configured
            humidity_delta: float | None = None
            ref_sensor = humidity_feature_config.get(CONF_REFERENCE_HUMIDITY_SENSOR)
            if ref_sensor:
                ref_state = self.hass.states.get(ref_sensor)
                area_humidity_id = self.area.simply_magic_entity_id(
                    SENSOR_DOMAIN, SensorDeviceClass.HUMIDITY
                )
                area_state = self.hass.states.get(area_humidity_id)
                if (
                    ref_state
                    and ref_state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
                    and area_state
                    and area_state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
                ):
                    try:
                        ref_val = (
                            float(ref_state.attributes["current_humidity"])
                            if "current_humidity" in ref_state.attributes
                            and ref_state.attributes["current_humidity"] is not None
                            else float(ref_state.state)
                        )
                        area_val = float(area_state.state)
                        humidity_delta = area_val - ref_val
                    except (ValueError, TypeError):
                        pass

            # Delta shutoff check
            delta_shutoff = humidity_feature_config.get(
                CONF_HUMIDITY_DELTA_SHUTOFF, DEFAULT_HUMIDITY_DELTA_SHUTOFF
            )
            if (
                humidity_delta is not None
                and self._attr_extra_state_attributes.get(ATTR_HUMIDITY_ON, False)
                and humidity_delta <= delta_shutoff
            ):
                self._attr_extra_state_attributes[ATTR_HUMIDITY_ON] = False

            # Work out if it is trending up.
            trending_up = float(humidity_trend.state) >= humidity_feature_config.get(
                CONF_HUMIDITY_TREND_UP_CUT_OFF, DEFAULT_HUMIDITY_TREND_UP_CUT_OFF
            )
            if trending_up:
                can_activate = True
                delta_threshold = humidity_feature_config.get(
                    CONF_HUMIDITY_DELTA_THRESHOLD, DEFAULT_HUMIDITY_DELTA_THRESHOLD
                )
                if humidity_delta is not None and humidity_delta < delta_threshold:
                    can_activate = False

                gate_time = humidity_feature_config.get(
                    CONF_HUMIDITY_PRESENCE_GATE_TIME,
                    DEFAULT_HUMIDITY_PRESENCE_GATE_TIME,
                )
                if (
                    gate_time > 0
                    and self.state == AreaState.AREA_STATE_CLEAR
                    and self._last_off_time is not None
                ):
                    time_since_clear = (
                        datetime.now(UTC) - self._last_off_time
                    ).total_seconds()
                    if time_since_clear > gate_time:
                        can_activate = False

                if can_activate:
                    self._attr_extra_state_attributes[ATTR_HUMIDITY_ON] = True

            if self._attr_extra_state_attributes.get(ATTR_HUMIDITY_ON, False):
                if float(humidity_trend.state) > humidity_feature_config.get(
                    CONF_HUMIDITY_TREND_DOWN_CUT_OFF,
                    DEFAULT_HUMIDITY_TREND_DOWN_CUT_OFF,
                ):
                    active_sensors.append(humidity_trend.entity_id)
                else:
                    self._attr_extra_state_attributes[ATTR_HUMIDITY_ON] = False

            # Make the last off time stay until this is not on any more.
            if float(humidity_trend.state) < humidity_feature_config.get(
                CONF_HUMIDITY_TREND_DOWN_CUT_OFF,
                DEFAULT_HUMIDITY_TREND_DOWN_CUT_OFF,
            ):
                self._attr_extra_state_attributes[ATTR_HUMIDITY_ON] = False
                self._last_off_time = datetime.now(UTC)

        self._attr_extra_state_attributes["active_sensors"] = active_sensors

        # Make a copy that doesn't gets cleared out, for debugging
        if active_sensors:
            self._attr_extra_state_attributes["last_active_sensors"] = active_sensors

        _LOGGER.debug(
            "[Area: %s] Active sensors: %s",
            self.area.slug,
            active_sensors,
        )

        if self.area.is_meta():
            active_areas = set(self.area.get_active_areas())
            _LOGGER.debug(
                "[Area: %s] Active areas: %s",
                self.area.slug,
                active_areas,
            )
            self._attr_extra_state_attributes[ATTR_ACTIVE_AREAS] = active_areas

        if self._mode == "all":
            return len(active_sensors) == len(self._sensors)
        return len(active_sensors) > 0
