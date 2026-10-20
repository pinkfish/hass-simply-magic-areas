"""Sensor entity that tracks the contextual lighting profile for an area."""

import logging
from datetime import UTC, datetime

from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
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
    ATTR_DARK_LUX_FLOOR,
    ATTR_HISTORICAL_DAYLIGHT_LUX,
    ATTR_IS_WINDOWLESS,
    CONF_BRIGHT_LUX_HYSTERESIS,
    CONF_BRIGHT_LUX_THRESHOLD,
    CONF_DARK_LUX_FLOOR,
    CONF_DAYLIGHT_LUX_LEVEL,
    CONF_FEATURE_ADVANCED_LIGHT_GROUPS,
    CONF_LEARN_DAYLIGHT_LUX,
    CONF_REFERENCE_BRIGHT_AREA,
    CONF_REFERENCE_ILLUMINANCE_SENSOR,
    DATA_AREA_OBJECT,
    DEFAULT_BRIGHT_LUX_HYSTERESIS,
    DEFAULT_BRIGHT_LUX_THRESHOLD,
    DEFAULT_DARK_LUX_FLOOR,
    DEFAULT_DAYLIGHT_LUX_LEVEL,
    DEFAULT_LEARN_DAYLIGHT_LUX,
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

        self._historical_daylight_lux: float | None = None
        self._is_windowless: bool = False
        self._daylight_dark_samples: int = 0

    async def async_added_to_hass(self) -> None:
        """Call when entity about to be added to hass."""
        await super().async_added_to_hass()
        await self._restore_state()
        await self._setup_listeners()

        self._update_historical_daylight()

        # Perform initial profile evaluation
        initial_profile = self._calculate_lighting_profile()
        self._attr_native_value = initial_profile
        self.area.lighting_profile = initial_profile
        self._update_extra_state_attributes()
        self.schedule_update_ha_state()

        _LOGGER.debug(
            "%s: Lighting profile sensor initialized [profile=%s, historical_daylight=%s, windowless=%s]",
            self.name,
            initial_profile,
            self._historical_daylight_lux,
            self._is_windowless,
        )

    async def _restore_state(self) -> None:
        """Restore previous state on startup."""
        self._historical_daylight_lux = None
        self._is_windowless = False
        self._daylight_dark_samples = 0

        last_state = await self.async_get_last_state()
        if last_state:
            if last_state.state in self._attr_options:
                restored = LightingProfile(last_state.state)
                self._attr_native_value = restored
                self.area.lighting_profile = restored
            attrs = last_state.attributes
            if (
                ATTR_HISTORICAL_DAYLIGHT_LUX in attrs
                and attrs[ATTR_HISTORICAL_DAYLIGHT_LUX] is not None
            ):
                try:
                    self._historical_daylight_lux = float(
                        attrs[ATTR_HISTORICAL_DAYLIGHT_LUX]
                    )
                    self.area.historical_daylight_lux = self._historical_daylight_lux
                except (ValueError, TypeError):
                    pass
            if ATTR_IS_WINDOWLESS in attrs:
                self._is_windowless = bool(attrs[ATTR_IS_WINDOWLESS])
                self.area.is_windowless = self._is_windowless
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

        # 2. Area light group (to detect when lights turn off and sample natural daylight)
        local_light_group = self.area.simply_magic_entity_id(
            LIGHT_DOMAIN, EntityNames.LIGHT
        )
        tracked_entities.append(local_light_group)

        # 3. Solar tracking entity
        tracked_entities.append("sun.sun")

        # 4. Reference area entities (illuminance and profile)
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

        # 5. Reference illuminance sensor
        ref_sensor = self.area.config.get(
            CONF_REFERENCE_ILLUMINANCE_SENSOR,
            self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS).get(
                CONF_REFERENCE_ILLUMINANCE_SENSOR
            ),
        )
        if ref_sensor:
            tracked_entities.append(ref_sensor)

        # 6. Binary entities: bright_entity, sleep_entity, accented_entity
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

    def _update_historical_daylight(self) -> None:
        """Autonomously learn the room's daylight baseline from ambient lux when lights are off."""
        learn_enabled = self.area.config.get(
            CONF_LEARN_DAYLIGHT_LUX,
            self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS).get(
                CONF_LEARN_DAYLIGHT_LUX, DEFAULT_LEARN_DAYLIGHT_LUX
            ),
        )
        if not learn_enabled:
            return

        # 1. Artificial lights must be OFF so they do not contaminate the reading
        light_entity_id = self.area.simply_magic_entity_id(
            LIGHT_DOMAIN, EntityNames.LIGHT
        )
        light_state = self.hass.states.get(light_entity_id)
        if light_state is not None and light_state.state == STATE_ON:
            return

        # 2. Daytime detection: sun is above horizon or solar elevation > 0
        sun_state = self.hass.states.get("sun.sun")
        if sun_state is not None:
            elevation = sun_state.attributes.get("elevation", 0)
            is_daytime = sun_state.state == "above_horizon" or elevation > 0
        else:
            # Fallback if sun component is not present: check daytime hours (08:00 - 18:00 UTC)
            now_hour = datetime.now(UTC).hour
            is_daytime = 8 <= now_hour <= 18

        if not is_daytime:
            return

        # 3. Read current local illuminance
        lux = self.area.get_effective_illuminance()
        if lux is None or lux < 0:
            return

        dark_floor = self._get_dark_lux_floor()

        if lux <= dark_floor:
            # Consistent daytime readings at or below floor signify a windowless space
            self._daylight_dark_samples = (
                getattr(self, "_daylight_dark_samples", 0) + 1
            )
            if self._daylight_dark_samples >= 3:
                self._is_windowless = True
                self.area.is_windowless = True
        else:
            self._is_windowless = False
            self.area.is_windowless = False
            self._daylight_dark_samples = 0

            # Update historical daylight baseline
            if (
                self._historical_daylight_lux is None
                or self._historical_daylight_lux <= dark_floor
            ):
                self._historical_daylight_lux = float(lux)
            else:
                if lux > self._historical_daylight_lux:
                    # Upward adaptation towards peak daylight
                    self._historical_daylight_lux = (
                        0.25 * float(lux) + 0.75 * self._historical_daylight_lux
                    )
                else:
                    # Slow seasonal downward tracking
                    self._historical_daylight_lux = max(
                        dark_floor + 5.0,
                        0.02 * float(lux) + 0.98 * self._historical_daylight_lux,
                    )

            self.area.historical_daylight_lux = self._historical_daylight_lux

    def _update_extra_state_attributes(self) -> None:
        """Update sensor attributes with floor, daylight baseline, and windowless status."""
        dark_floor = self._get_dark_lux_floor()
        lux = self.area.get_effective_illuminance()
        self._attr_extra_state_attributes = {
            ATTR_DARK_LUX_FLOOR: dark_floor,
            ATTR_HISTORICAL_DAYLIGHT_LUX: (
                round(self._historical_daylight_lux, 1)
                if self._historical_daylight_lux is not None
                else None
            ),
            ATTR_IS_WINDOWLESS: self._is_windowless,
            "effective_illuminance": lux,
        }

    def _context_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle state changes in tracked context entities."""
        if event.data.get("new_state") is None:
            return

        self._update_historical_daylight()

        new_profile = self._calculate_lighting_profile()
        profile_changed = new_profile != self._attr_native_value
        if profile_changed:
            _LOGGER.debug(
                "%s: Lighting profile changed from %s to %s",
                self.name,
                self._attr_native_value,
                new_profile,
            )
            self._attr_native_value = new_profile
            self.area.lighting_profile = new_profile

        self._update_extra_state_attributes()
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

    def _get_dark_lux_floor(self) -> int:
        return int(
            self.area.config.get(
                CONF_DARK_LUX_FLOOR,
                self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS).get(
                    CONF_DARK_LUX_FLOOR, DEFAULT_DARK_LUX_FLOOR
                ),
            )
        )

    def _is_bright_condition_met(self) -> bool:
        """Evaluate if bright condition is met via Direct Lux, Reference Area/Sensor, or binary entity."""
        dark_floor = self._get_dark_lux_floor()
        lux = self.area.get_effective_illuminance()

        # Dark Floor Invariant: If current lux is at or below the dark floor, or space is windowless,
        # it is physically dark and can NEVER resolve to the BRIGHT profile.
        if lux is not None and lux <= dark_floor:
            return False
        if getattr(self, "_is_windowless", False) or getattr(
            self.area, "is_windowless", False
        ):
            return False

        threshold = self._get_bright_lux_threshold()
        hysteresis = self._get_bright_lux_hysteresis()

        # If threshold is unconfigured (0), adapt to manual daylight level or learned historical daylight
        effective_threshold = threshold
        if effective_threshold <= 0:
            manual_daylight = int(
                self.area.config.get(
                    CONF_DAYLIGHT_LUX_LEVEL,
                    self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS).get(
                        CONF_DAYLIGHT_LUX_LEVEL, DEFAULT_DAYLIGHT_LUX_LEVEL
                    ),
                )
            )
            if manual_daylight > 0:
                effective_threshold = manual_daylight
            else:
                learned = getattr(self, "_historical_daylight_lux", None) or getattr(
                    self.area, "historical_daylight_lux", None
                )
                if learned and learned > (dark_floor + 5.0):
                    effective_threshold = int(learned)

        # Tier 1 & Tier 2 numeric lux check
        if effective_threshold > 0 and lux is not None:
            is_currently_bright = (
                getattr(self.area, "lighting_profile", None)
                == LightingProfile.BRIGHT
            )
            check_threshold = (
                (effective_threshold - hysteresis)
                if is_currently_bright
                else effective_threshold
            )
            return lux >= check_threshold

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
