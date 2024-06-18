"""Select control for magic areas, tracks the state as an enum."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import logging

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_ENTITY_ID, STATE_ON
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)

from ..config.area_state import AreaState
from ..config.entity_names import EntityNames
from ..const import (
    ATTR_ACTIVE_AREAS,
    ATTR_ACTIVE_SENSORS,
    ATTR_AREAS,
    ATTR_CLEAR_TIMEOUT,
    ATTR_EXTENDED_TIMEOUT,
    ATTR_LAST_ACTIVE_SENSORS,
    ATTR_PRESENCE_SENSORS,
    ATTR_STATE,
    ATTR_TYPE,
    CONF_CLEAR_TIMEOUT,
    CONF_EXTENDED_TIMEOUT,
    CONF_FEATURE_ADVANCED_LIGHT_GROUPS,
    CONF_ON_STATES,
    CONF_PRESENCE_DEVICE_PLATFORMS,
    CONF_PRESENCE_SENSOR_DEVICE_CLASS,
    CONF_TYPE,
    CONF_UPDATE_INTERVAL,
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


class AreaStateSensor(MagicEntity, SensorEntity):
    """Create an area presence select entity that tracks the current occupied state."""

    def __init__(self, area: MagicArea) -> None:
        """Initialize the area presence select."""

        SensorEntity.__init__(self)
        MagicEntity.__init__(
            self, area=area, domain=SENSOR_DOMAIN, translation_key="state"
        )

        self._attr_options = list(AreaState)
        self._attr_native_value = AreaState.AREA_STATE_CLEAR
        self._attr_extra_state_attributes = {}
        self._attr_device_class = SensorDeviceClass.ENUM

        self._last_off_time: datetime = datetime.now(UTC) - timedelta(days=2)  # type: ignore  # noqa: PGH003
        self._clear_timeout_callback: Callable[[], None] | None = None
        self._extended_timeout_callback: Callable[[], None] | None = None
        self._sensors: list[str] = []
        self._mode: str = "one"

    async def async_added_to_hass(self) -> None:
        """Call to add the system to hass."""
        _LOGGER.info("%s: added to hass", self.area.name)
        await super().async_added_to_hass()
        await self._restore_state()
        await self._load_attributes()
        self._load_presence_sensors()

        # Setup the listeners
        await self._setup_listeners()

        _LOGGER.debug(  # type: ignore  # noqa: PGH003
            "%s: Select initialized %s %s %s",
            self.unique_id,
            self.entity_id,
            self.name,
            self.translation_key,
        )  # type: ignore  # noqa: PGH003
        self.async_on_remove(self._cleanup_timers)

    async def _restore_state(self) -> None:
        """Restore the state of the select entity on initialize."""
        last_state = await self.async_get_last_state()

        self.schedule_update_ha_state()
        if last_state is None:
            _LOGGER.debug("%s: New select created", self.name)  # type: ignore  # noqa: PGH003
            self._attr_native_value = AreaState.AREA_STATE_CLEAR
        else:
            _LOGGER.debug(  # type: ignore  # noqa: PGH003
                "%s: Select restored [state=%s]",
                self.name,
                last_state.state,
            )  # type: ignore  # noqa: PGH003
            self.area.state = AreaState(last_state.state)
            self._attr_native_value = last_state.state
            self._attr_extra_state_attributes = dict(last_state.attributes)  # type: ignore  # noqa: PGH003
        self.hass.loop.call_soon_threadsafe(self._update_state, datetime.now(UTC))

    async def _setup_listeners(self) -> None:
        _LOGGER.debug("%s: Called '_setup_listeners'", self.name)  # type: ignore  # noqa: PGH003
        if not self.hass.is_running:
            _LOGGER.debug("%s: Cancelled '_setup_listeners'", self.name)  # type: ignore  # noqa: PGH003
            return

        # Track presence sensor
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._sensors, self._sensor_state_change
            )
        )

        # Track humidity sensors
        trend_up = self.area.simply_magic_entity_id(
            BINARY_SENSOR_DOMAIN, EntityNames.HUMIDITY_OCCUPIED
        )
        trend_down = self.area.simply_magic_entity_id(
            BINARY_SENSOR_DOMAIN, EntityNames.HUMIDITY_EMPTY
        )
        if (
            self.hass.states.get(trend_up) is not None
            and self.hass.states.get(trend_down) is not None
        ):
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [trend_up, trend_down], self._humidity_sensor_change
                )
            )

        # Track secondary states
        for state in self.area.all_state_configs():
            conf = self.area.all_state_configs()[state]

            if not conf.entity:
                continue

            _LOGGER.debug("%s: State entity tracking: %s", self.name, conf.entity)  # type: ignore  # noqa: PGH003

            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, conf.entity, self._group_entity_state_change
                )
            )

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
            }
        )

    def _update_attributes(self) -> None:
        self._attr_extra_state_attributes[ATTR_STATE] = self.area.state
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

        _LOGGER.debug("Sensor state %s", occupied_state)  # type: ignore  # noqa: PGH003

        seconds_since_last_change: int = int(
            (datetime.now(UTC) - self._last_off_time).total_seconds()
        )

        clear_timeout: int = self._get_clear_timeout()
        extended_timeout: int = self._get_extended_timeout() + clear_timeout
        if not occupied_state:
            if (
                not self._is_on_clear_timeout()
                and seconds_since_last_change < clear_timeout
            ):
                self._set_clear_timeout(clear_timeout - seconds_since_last_change)
            if seconds_since_last_change >= clear_timeout:
                if seconds_since_last_change >= extended_timeout:
                    self._remove_extended_timeout()
                    return AreaState.AREA_STATE_CLEAR
                _LOGGER.debug("%s: Clearing the imput, state extended", self.area.slug)  # type: ignore  # noqa: PGH003
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
            self._remove_clear_timeout()
            self._remove_extended_timeout()

        # If it is not occupied, then set the override state or leave as just occupied.
        new_state = AreaState.AREA_STATE_OCCUPIED

        for state in self.area.all_state_configs():
            conf = self.area.all_state_configs()[state]
            if conf.entity is None:
                continue

            entity = self.hass.states.get(conf.entity)

            if entity is None:
                continue

            if entity.state.lower() == conf.entity_state_on:  # type: ignore  # noqa: PGH003
                _LOGGER.debug(  # type: ignore  # noqa: PGH003
                    "%s: Secondary state: %s is at %s, adding %s",
                    self.area.name,
                    conf.entity,
                    conf.entity_state_on,
                    conf.for_state,
                )
                new_state = conf.for_state

        return new_state

    @callback
    def _update_state(self, extra: datetime) -> None:
        last_state = self.area.state
        new_state = self.get_current_area_state()

        if last_state == new_state:
            self._update_attributes()
            return

        # Calculate what's new
        _LOGGER.debug(  # type: ignore  # noqa: PGH003
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

        if to_state in INVALID_STATES:
            _LOGGER.debug(
                "%s: sensor '%s' has invalid state %s",
                self.area.name,
                entity_id,
                to_state,
            )
            return None

        self.hass.loop.call_soon_threadsafe(self._update_state, datetime.now(UTC))

    ###       Clearing

    async def _async_update_state(self, timeout: int) -> None:
        await asyncio.sleep(timeout)
        self._update_state()

    def _get_clear_timeout(self) -> int:
        return int(self.area.config.get(CONF_CLEAR_TIMEOUT, 60))

    def _set_clear_timeout(self, timeout: int) -> None:
        if self._clear_timeout_callback:
            self._remove_clear_timeout()

        _LOGGER.debug("%s: Scheduling clear in %s seconds", self.area.name, timeout)  # type: ignore  # noqa: PGH003
        self._attr_extra_state_attributes["clear"] = True
        self._clear_timeout_callback = async_call_later(
            self.hass,
            timeout,
            self._update_state,
        )

    def _remove_clear_timeout(self) -> None:
        if not self._clear_timeout_callback:
            return

        _LOGGER.debug(  # type: ignore  # noqa: PGH003
            "%s: Clearing timeout",
            self.area.name,
        )

        self._attr_extra_state_attributes["clear"] = False
        self._clear_timeout_callback()
        self._clear_timeout_callback = None

    def _is_on_clear_timeout(self) -> bool:
        return self._clear_timeout_callback is not None

    @callback
    def _cleanup_timers(self) -> None:
        self._remove_clear_timeout()
        self._remove_extended_timeout()

    ###       Extended

    def _get_extended_timeout(self) -> int:
        return int(self.area.config.get(CONF_EXTENDED_TIMEOUT, 60))

    def _set_extended_timeout(self, timeout: int) -> None:
        if self._extended_timeout_callback:
            self._remove_extended_timeout()

        _LOGGER.info("%s: Scheduling extended in %s seconds", self.area.name, timeout)  # type: ignore  # noqa: PGH003
        self._attr_extra_state_attributes["extended"] = True
        self._extended_timeout_callback = async_call_later(
            self.hass,
            timeout,
            self._update_state,
        )

    def _remove_extended_timeout(self) -> None:
        if not self._extended_timeout_callback:
            return

        self._attr_extra_state_attributes["extended"] = False
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

        if to_state in INVALID_STATES:
            _LOGGER.debug(
                "%s: sensor '%s' has invalid state %s",
                self.name,
                entity_id,
                to_state,
            )
            return

        if to_state and to_state not in self.area.feature_config(
            CONF_FEATURE_ADVANCED_LIGHT_GROUPS
        ).get(CONF_ON_STATES, DEFAULT_ON_STATES):
            _LOGGER.debug(
                "Setting last non-normal time %s %s",
                event.data["old_state"],
                event.data["new_state"],
            )
            self._last_off_time = datetime.now(UTC)  # Update last_off_time
            # Clear the timeout
            self._remove_clear_timeout()

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

        # Track the up/down trend.
        trend_up = self.hass.states.get(
            self.area.simply_magic_entity_id(
                BINARY_SENSOR_DOMAIN, EntityNames.HUMIDITY_OCCUPIED
            )
        )
        trend_down = self.hass.states.get(
            self.area.simply_magic_entity_id(
                BINARY_SENSOR_DOMAIN, EntityNames.HUMIDITY_EMPTY
            )
        )
        if trend_up is not None and trend_down is not None:
            up_state = (
                trend_up.state == STATE_ON
                or self._attr_extra_state_attributes.get(ATTR_HUMIDITY_ON, False)
            )
            if up_state:
                self._attr_extra_state_attributes[ATTR_HUMIDITY_ON] = True
                if trend_down.state != STATE_ON:
                    active_sensors.append(trend_up.entity_id)
            # Make the last off time stay until this is not on any more.
            if trend_down.state == STATE_ON:
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
