"""Sensor entity that tracks the contextual lighting profile for an area."""

import logging

from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.const import STATE_ON
from homeassistant.core import Event, EventStateChangedData
from homeassistant.helpers.event import async_track_state_change_event

from ..config.area_state import AreaState, LightingProfile
from ..config.entity_names import EntityNames
from ..const import (
    CONF_BRIGHT_LUX_HYSTERESIS,
    CONF_BRIGHT_LUX_THRESHOLD,
    CONF_FEATURE_ADVANCED_LIGHT_GROUPS,
    CONF_REFERENCE_BRIGHT_AREA,
    CONF_REFERENCE_ILLUMINANCE_SENSOR,
    DATA_AREA_OBJECT,
    DEFAULT_BRIGHT_LUX_HYSTERESIS,
    DEFAULT_BRIGHT_LUX_THRESHOLD,
    MODULE_DATA,
)
from .entities import MagicEntity
from .magic import MagicArea

_LOGGER = logging.getLogger(__name__)


class AreaLightingProfileSensor(MagicEntity, SensorEntity):
    """Sensor that evaluates and tracks the active lighting profile for a Magic Area."""

    def __init__(self, area: MagicArea) -> None:
        """Initialize the area lighting profile sensor."""
        SensorEntity.__init__(self)
        MagicEntity.__init__(
            self,
            area=area,
            domain=SENSOR_DOMAIN,
            translation_key=EntityNames.LIGHTING_PROFILE,
        )

        self._attr_options = [p.value for p in LightingProfile]
        self._attr_native_value = LightingProfile.STANDARD
        self._attr_device_class = SensorDeviceClass.ENUM
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self) -> None:
        """Call when entity about to be added to hass."""
        await super().async_added_to_hass()
        await self._restore_state()
        await self._setup_listeners()

        # Perform initial profile evaluation
        initial_profile = self._calculate_lighting_profile()
        self._attr_native_value = initial_profile
        self.area.lighting_profile = initial_profile
        self.schedule_update_ha_state()

        _LOGGER.debug(
            "%s: Lighting profile sensor initialized [profile=%s]",
            self.name,
            initial_profile,
        )

    async def _restore_state(self) -> None:
        """Restore previous state on startup."""
        last_state = await self.async_get_last_state()
        if last_state and last_state.state in self._attr_options:
            restored = LightingProfile(last_state.state)
            self._attr_native_value = restored
            self.area.lighting_profile = restored
        else:
            self._attr_native_value = LightingProfile.STANDARD
            self.area.lighting_profile = LightingProfile.STANDARD

    async def _setup_listeners(self) -> None:
        """Set up state change listeners for environmental and context entities."""
        tracked_entities: list[str] = []

        # 1. Local illuminance sensor
        local_lux_sensor = self.area.simply_magic_entity_id(
            SENSOR_DOMAIN, EntityNames.ILLUMINANCE
        )
        tracked_entities.append(local_lux_sensor)

        # 2. Reference area entities (illuminance and profile)
        ref_area = self.area.config.get(
            CONF_REFERENCE_BRIGHT_AREA,
            self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS).get(
                CONF_REFERENCE_BRIGHT_AREA
            ),
        )
        if ref_area:
            ref_lux_sensor = self.area.simply_magic_entity_id(
                SENSOR_DOMAIN, EntityNames.ILLUMINANCE, area_name=ref_area
            )
            ref_profile_sensor = self.area.simply_magic_entity_id(
                SENSOR_DOMAIN, EntityNames.LIGHTING_PROFILE, area_name=ref_area
            )
            ref_state_sensor = self.area.simply_magic_entity_id(
                SENSOR_DOMAIN, EntityNames.STATE, area_name=ref_area
            )
            tracked_entities.extend(
                [ref_lux_sensor, ref_profile_sensor, ref_state_sensor]
            )

        # 3. Reference illuminance sensor
        ref_sensor = self.area.config.get(
            CONF_REFERENCE_ILLUMINANCE_SENSOR,
            self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS).get(
                CONF_REFERENCE_ILLUMINANCE_SENSOR
            ),
        )
        if ref_sensor:
            tracked_entities.append(ref_sensor)

        # 4. Binary entities: bright_entity, sleep_entity, accented_entity
        bright_conf = self.area.all_state_configs().get(AreaState.AREA_STATE_BRIGHT)
        bright_entity = (
            bright_conf.entity if bright_conf else self.area.config.get("bright_entity")
        )
        if bright_entity:
            tracked_entities.append(bright_entity)

        sleep_conf = self.area.all_state_configs().get(AreaState.AREA_STATE_SLEEP)
        sleep_entity = (
            sleep_conf.entity if sleep_conf else self.area.config.get("sleep_entity")
        )
        if sleep_entity:
            tracked_entities.append(sleep_entity)

        accented_conf = self.area.all_state_configs().get(AreaState.AREA_STATE_ACCENTED)
        accented_entity = (
            accented_conf.entity
            if accented_conf
            else self.area.config.get("accented_entity")
        )
        if accented_entity:
            tracked_entities.append(accented_entity)

        # Filter unique non-empty entities
        unique_tracked = list(dict.fromkeys([e for e in tracked_entities if e]))
        if unique_tracked:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    unique_tracked,
                    self._context_state_change,
                )
            )

    def _context_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle state changes in tracked context entities."""
        if event.data.get("new_state") is None:
            return

        new_profile = self._calculate_lighting_profile()
        if new_profile != self._attr_native_value:
            _LOGGER.debug(
                "%s: Lighting profile changed from %s to %s",
                self.name,
                self._attr_native_value,
                new_profile,
            )
            self._attr_native_value = new_profile
            self.area.lighting_profile = new_profile
            self.schedule_update_ha_state()

    def _calculate_lighting_profile(self) -> LightingProfile:
        """Calculate the active lighting profile based on priority: Accent > Sleep > Bright > Standard."""
        # 1. Accent Profile
        accented_conf = self.area.all_state_configs().get(AreaState.AREA_STATE_ACCENTED)
        accented_entity = (
            accented_conf.entity
            if accented_conf
            else self.area.config.get("accented_entity")
        )
        if accented_entity:
            entity_state = self.hass.states.get(accented_entity)
            check_state = (
                accented_conf.entity_state_on if accented_conf else STATE_ON
            )
            if entity_state and entity_state.state.lower() == check_state.lower():
                return LightingProfile.ACCENTED

        # 2. Sleep Profile
        sleep_conf = self.area.all_state_configs().get(AreaState.AREA_STATE_SLEEP)
        sleep_entity = (
            sleep_conf.entity if sleep_conf else self.area.config.get("sleep_entity")
        )
        if sleep_entity:
            entity_state = self.hass.states.get(sleep_entity)
            check_state = sleep_conf.entity_state_on if sleep_conf else STATE_ON
            if entity_state and entity_state.state.lower() == check_state.lower():
                return LightingProfile.SLEEP

        # Reference area sleep check (e.g. Master Bedroom sleep applies to Ensuite)
        ref_area = self.area.config.get(
            CONF_REFERENCE_BRIGHT_AREA,
            self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS).get(
                CONF_REFERENCE_BRIGHT_AREA
            ),
        )
        if ref_area:
            if MODULE_DATA in self.hass.data:
                for entry in self.hass.data[MODULE_DATA].values():
                    area = entry.get(DATA_AREA_OBJECT)
                    if (
                        area
                        and (
                            area.id == ref_area
                            or area.slug == ref_area
                            or area.name == ref_area
                        )
                        and getattr(area, "lighting_profile", None)
                        == LightingProfile.SLEEP
                    ):
                        return LightingProfile.SLEEP
            ref_profile_sensor = self.area.simply_magic_entity_id(
                SENSOR_DOMAIN, EntityNames.LIGHTING_PROFILE, area_name=ref_area
            )
            state_obj = self.hass.states.get(ref_profile_sensor)
            if state_obj and state_obj.state == LightingProfile.SLEEP:
                return LightingProfile.SLEEP
            ref_state_sensor = self.area.simply_magic_entity_id(
                SENSOR_DOMAIN, EntityNames.STATE, area_name=ref_area
            )
            state_obj = self.hass.states.get(ref_state_sensor)
            if state_obj and state_obj.state == AreaState.AREA_STATE_SLEEP:
                return LightingProfile.SLEEP

        # 3. Bright Profile
        if self._is_bright_condition_met():
            return LightingProfile.BRIGHT

        # 4. Standard Profile
        return LightingProfile.STANDARD

    def _get_bright_lux_threshold(self) -> int:
        return int(
            self.area.config.get(
                CONF_BRIGHT_LUX_THRESHOLD,
                self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS).get(
                    CONF_BRIGHT_LUX_THRESHOLD, DEFAULT_BRIGHT_LUX_THRESHOLD
                ),
            )
        )

    def _get_bright_lux_hysteresis(self) -> int:
        return int(
            self.area.config.get(
                CONF_BRIGHT_LUX_HYSTERESIS,
                self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS).get(
                    CONF_BRIGHT_LUX_HYSTERESIS, DEFAULT_BRIGHT_LUX_HYSTERESIS
                ),
            )
        )

    def _is_bright_condition_met(self) -> bool:
        """Evaluate if bright condition is met via Direct Lux, Reference Area/Sensor, or binary entity."""
        threshold = self._get_bright_lux_threshold()
        hysteresis = self._get_bright_lux_hysteresis()

        # Tier 1 & Tier 2 numeric lux check
        if threshold > 0:
            lux = self.area.get_effective_illuminance()
            if lux is not None:
                is_currently_bright = (
                    getattr(self.area, "lighting_profile", None)
                    == LightingProfile.BRIGHT
                )
                effective_threshold = (
                    (threshold - hysteresis) if is_currently_bright else threshold
                )
                return lux >= effective_threshold

        # Tier 2 reference area state check
        ref_area = self.area.config.get(
            CONF_REFERENCE_BRIGHT_AREA,
            self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS).get(
                CONF_REFERENCE_BRIGHT_AREA
            ),
        )
        if ref_area:
            if MODULE_DATA in self.hass.data:
                for entry in self.hass.data[MODULE_DATA].values():
                    area = entry.get(DATA_AREA_OBJECT)
                    if (
                        area
                        and (
                            area.id == ref_area
                            or area.slug == ref_area
                            or area.name == ref_area
                        )
                        and getattr(area, "lighting_profile", None)
                        == LightingProfile.BRIGHT
                    ):
                        return True
            ref_profile_sensor = self.area.simply_magic_entity_id(
                SENSOR_DOMAIN, EntityNames.LIGHTING_PROFILE, area_name=ref_area
            )
            state_obj = self.hass.states.get(ref_profile_sensor)
            if state_obj and state_obj.state == LightingProfile.BRIGHT:
                return True
            ref_state_entity = self.area.simply_magic_entity_id(
                SENSOR_DOMAIN, EntityNames.STATE, area_name=ref_area
            )
            state_obj = self.hass.states.get(ref_state_entity)
            if state_obj and state_obj.state in (
                AreaState.AREA_STATE_BRIGHT,
                LightingProfile.BRIGHT,
            ):
                return True

        # Tier 3 binary bright_entity check
        bright_conf = self.area.all_state_configs().get(AreaState.AREA_STATE_BRIGHT)
        if bright_conf and bright_conf.entity:
            entity = self.hass.states.get(bright_conf.entity)
            if entity and entity.state.lower() == bright_conf.entity_state_on.lower():
                return True
        else:
            bright_entity = self.area.config.get("bright_entity")
            if bright_entity:
                entity = self.hass.states.get(bright_entity)
                check_state = self.area.feature_config(
                    CONF_FEATURE_ADVANCED_LIGHT_GROUPS
                ).get("bright_state_check", STATE_ON)
                if entity and entity.state.lower() == check_state.lower():
                    return True

        return False
