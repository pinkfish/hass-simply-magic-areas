"""Sensor entity that tracks the contextual lighting profile for an area."""

import logging
from contextlib import suppress
from datetime import UTC, datetime

from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import Event, EventStateChangedData, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from ..config.area_state import AreaState, LightingProfile
from ..config.entity_names import EntityNames
from ..const import (
    ATTR_DARK_LUX_FLOOR,
    ATTR_HISTORICAL_DAYLIGHT_LUX,
    ATTR_IS_WINDOWLESS,
    CONF_AUTO_NIGHT_SLEEP,
    CONF_AUTO_SLEEP_AFTER_HOUR,
    CONF_AUTO_SLEEP_END_HOUR,
    CONF_BRIGHT_LUX_HYSTERESIS,
    CONF_BRIGHT_LUX_THRESHOLD,
    CONF_CHILD_AREAS,
    CONF_DARK_LUX_FLOOR,
    CONF_DAYLIGHT_LUX_LEVEL,
    CONF_FEATURE_ADVANCED_LIGHT_GROUPS,
    CONF_LEARN_DAYLIGHT_LUX,
    CONF_REFERENCE_BRIGHT_AREA,
    CONF_REFERENCE_ILLUMINANCE_SENSOR,
    CONF_SLEEP_MODE,
    DATA_AREA_OBJECT,
    DEFAULT_AUTO_NIGHT_SLEEP,
    DEFAULT_AUTO_SLEEP_AFTER_HOUR,
    DEFAULT_AUTO_SLEEP_END_HOUR,
    DEFAULT_BRIGHT_LUX_HYSTERESIS,
    DEFAULT_BRIGHT_LUX_THRESHOLD,
    DEFAULT_DARK_LUX_FLOOR,
    DEFAULT_DAYLIGHT_LUX_LEVEL,
    DEFAULT_LEARN_DAYLIGHT_LUX,
    DEFAULT_SLEEP_MODE,
    INVALID_STATES,
    META_AREA_BEDROOMS,
    META_AREA_EXTERIOR,
    META_AREA_GLOBAL,
    META_AREA_INTERIOR,
    MODULE_DATA,
)
from .entities import MagicEntity
from .magic import ControlType, MagicArea

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
        self._auto_sleep_latched: bool = False
        self._manual_sleep_mode: bool = bool(
            getattr(area, "sleep_mode_enabled", False) is True
            or (
                isinstance(getattr(area, "config", None), dict)
                and area.config.get(CONF_SLEEP_MODE, DEFAULT_SLEEP_MODE) is True
            )
        )
        self._local_sleep_override_off: bool = False
        self._was_nighttime_when_sleep_active: bool = False
        self._bedroom_light_was_on: bool = False
        self._bedroom_light_turned_on_then_off: bool = False
        self._morning_wakeup_disabled_sleep: bool = False

    async def async_added_to_hass(self) -> None:
        """Call when entity about to be added to hass."""
        await super().async_added_to_hass()
        self.area._lighting_profile_sensor = self  # type: ignore[attr-defined]
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

    async def async_will_remove_from_hass(self) -> None:
        """Clean up reference when removed from hass."""
        if getattr(self.area, "_lighting_profile_sensor", None) is self:
            self.area._lighting_profile_sensor = None  # type: ignore[attr-defined]
        await super().async_will_remove_from_hass()

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

        # 2. Area light group and member/child lights (to detect when lights turn on/off)
        local_light_group = self.area.simply_magic_entity_id(
            LIGHT_DOMAIN, EntityNames.LIGHT
        )
        tracked_entities.append(local_light_group)
        tracked_entities.extend(self._get_bedroom_light_entities())

        # 3. Solar tracking entity and local/house state sensors
        tracked_entities.append("sun.sun")
        tracked_entities.append(
            self.area.simply_magic_entity_id(SENSOR_DOMAIN, EntityNames.STATE)
        )
        for meta_slug in (
            META_AREA_INTERIOR.lower(),
            META_AREA_GLOBAL.lower(),
            META_AREA_BEDROOMS.lower(),
        ):
            tracked_entities.append(
                self.area.simply_magic_entity_id(
                    SENSOR_DOMAIN, EntityNames.STATE, area_name=meta_slug
                )
            )

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

        # 7. Parent meta-area lighting profile and illuminance sensors
        if not self.area.is_meta():
            for parent in self.area.get_parent_meta_areas():
                tracked_entities.append(
                    self.area.simply_magic_entity_id(
                        SENSOR_DOMAIN,
                        EntityNames.LIGHTING_PROFILE,
                        area_name=parent.slug,
                    )
                )
                tracked_entities.append(
                    self.area.simply_magic_entity_id(
                        SENSOR_DOMAIN,
                        EntityNames.ILLUMINANCE,
                        area_name=parent.slug,
                    )
                )
                tracked_entities.append(
                    self.area.simply_magic_entity_id(
                        SWITCH_DOMAIN,
                        EntityNames.SLEEP_MODE,
                        area_name=parent.slug,
                    )
                )

        # 8. Manual sleep mode switches (local, meta-area, reference, and parent suite)
        local_sleep_switch, inherited_sleep_switches = (
            self._get_applicable_sleep_switch_entities()
        )
        tracked_entities.append(local_sleep_switch)
        tracked_entities.extend(inherited_sleep_switches)
        for meta_slug in (
            META_AREA_INTERIOR.lower(),
            META_AREA_GLOBAL.lower(),
            META_AREA_BEDROOMS.lower(),
        ):
            tracked_entities.append(
                self.area.simply_magic_entity_id(
                    SWITCH_DOMAIN, EntityNames.SLEEP_MODE, area_name=meta_slug
                )
            )
        if ref_area:
            tracked_entities.append(
                self.area.simply_magic_entity_id(
                    SWITCH_DOMAIN, EntityNames.SLEEP_MODE, area_name=ref_area
                )
            )

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
        adv_conf = self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS)
        learn_enabled = adv_conf.get(
            CONF_LEARN_DAYLIGHT_LUX,
            self.area.config.get(CONF_LEARN_DAYLIGHT_LUX, DEFAULT_LEARN_DAYLIGHT_LUX),
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

    def _write_or_schedule_ha_state(self) -> None:
        """Write state immediately when on the event loop, or schedule thread-safely."""
        if (
            self.hass is not None
            and self.entity_id
            and getattr(self, "platform", None) is not None
        ):
            with suppress(RuntimeError):
                self.async_write_ha_state()
                return
        self.schedule_update_ha_state()

    def _get_applicable_sleep_switch_entities(self) -> tuple[str, list[str]]:
        """Return (local_sleep_switch_id, inherited_sleep_switch_ids) for this area."""
        local_switch = self.area.simply_magic_entity_id(
            SWITCH_DOMAIN, EntityNames.SLEEP_MODE
        )
        if (
            self.area.is_exterior()
            or str(getattr(self.area, "id", "")).lower() == META_AREA_EXTERIOR.lower()
        ):
            return local_switch, []

        raw_local_sleep_entity = (
            dict.get(self.area.config, "sleep_entity")
            if isinstance(getattr(self.area, "config", None), dict)
            else None
        )
        if isinstance(raw_local_sleep_entity, str) and raw_local_sleep_entity.lower() in (
            "none",
            "disabled",
        ):
            return local_switch, []

        inherited: list[str] = []
        ref_area = self.area.config.get(
            CONF_REFERENCE_BRIGHT_AREA,
            self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS).get(
                CONF_REFERENCE_BRIGHT_AREA
            ),
        )
        if ref_area:
            inherited.append(
                self.area.simply_magic_entity_id(
                    SWITCH_DOMAIN, EntityNames.SLEEP_MODE, area_name=ref_area
                )
            )

        my_keys = {
            str(getattr(self.area, "slug", "") or "").lower(),
            str(getattr(self.area, "id", "") or "").lower(),
            str(getattr(self.area, "name", "") or "").lower(),
        }
        for entry_data in self.hass.data.get(MODULE_DATA, {}).values():
            if not isinstance(entry_data, dict):
                continue
            other = entry_data.get(DATA_AREA_OBJECT)
            if other is None or other is self.area:
                continue
            other_children = (
                other.config.get(CONF_CHILD_AREAS)
                if isinstance(getattr(other, "config", None), dict)
                else None
            )
            if isinstance(other_children, (list, tuple, set)) and any(
                str(c).lower() in my_keys for c in other_children if c
            ):
                sw_id = other.simply_magic_entity_id(
                    SWITCH_DOMAIN, EntityNames.SLEEP_MODE
                )
                if sw_id and sw_id not in inherited:
                    inherited.append(sw_id)

        if self._is_bedroom_area():
            if not self.area.is_meta():
                bedrooms_sw = self.area.simply_magic_entity_id(
                    SWITCH_DOMAIN,
                    EntityNames.SLEEP_MODE,
                    area_name=META_AREA_BEDROOMS.lower(),
                )
                if bedrooms_sw not in inherited:
                    inherited.append(bedrooms_sw)
        elif not self.area.is_meta():
            for meta_slug in (
                META_AREA_INTERIOR.lower(),
                META_AREA_GLOBAL.lower(),
            ):
                meta_sw = self.area.simply_magic_entity_id(
                    SWITCH_DOMAIN, EntityNames.SLEEP_MODE, area_name=meta_slug
                )
                if meta_sw not in inherited:
                    inherited.append(meta_sw)

        return local_switch, inherited

    def set_manual_sleep_mode(self, enabled: bool) -> None:
        """Manually enable or disable sleep mode for this area."""
        enabled_bool = bool(enabled)
        self._manual_sleep_mode = enabled_bool
        self.area.sleep_mode_enabled = enabled_bool
        if isinstance(getattr(self.area, "config", None), dict):
            self.area.config[CONF_SLEEP_MODE] = enabled_bool

        local_switch, inherited_switches = self._get_applicable_sleep_switch_entities()
        if enabled_bool:
            self._local_sleep_override_off = False
            self._morning_wakeup_disabled_sleep = False
            self._was_nighttime_when_sleep_active = self._is_after_10pm_nighttime()
        else:
            self._auto_sleep_latched = False
            self._was_nighttime_when_sleep_active = False
            self._bedroom_light_turned_on_then_off = False
            self._bedroom_light_was_on = self._is_any_bedroom_light_on()
            if self._is_after_10pm_nighttime():
                self._morning_wakeup_disabled_sleep = True
            if any(
                (st := self.hass.states.get(sw)) is not None and st.state == STATE_ON
                for sw in inherited_switches
            ):
                self._local_sleep_override_off = True

        sleep_switch = getattr(self.area, "_sleep_mode_switch", None)
        if sleep_switch is not None and hasattr(sleep_switch, "set_synced_state"):
            sleep_switch.set_synced_state(enabled_bool)
        else:
            st = self.hass.states.get(local_switch)
            target_state = STATE_ON if enabled_bool else STATE_OFF
            if st is not None and st.state != target_state:
                self.hass.states.async_set(local_switch, target_state)

        new_profile = self._calculate_lighting_profile()
        self._attr_native_value = new_profile
        self.area.lighting_profile = new_profile
        self._update_extra_state_attributes()
        self._write_or_schedule_ha_state()

    def _is_manual_sleep_mode_active(self) -> bool:
        """Return True if manual sleep mode is active for this area or its parent area."""
        if (
            self.area.is_exterior()
            or str(getattr(self.area, "id", "")).lower() == META_AREA_EXTERIOR.lower()
        ):
            return False

        if self._manual_sleep_mode or getattr(self.area, "sleep_mode_enabled", False) is True:
            return True
        if (
            isinstance(getattr(self.area, "config", None), dict)
            and self.area.config.get(CONF_SLEEP_MODE) is True
        ):
            return True

        local_switch, inherited_switches = self._get_applicable_sleep_switch_entities()
        local_st = self.hass.states.get(local_switch)
        if (
            local_st is not None
            and local_st.state == STATE_ON
            and getattr(self.area, "_sleep_mode_switch", None) is None
        ):
            return True

        if self._local_sleep_override_off:
            return False

        for sw_id in inherited_switches:
            st = self.hass.states.get(sw_id)
            if st is not None and st.state == STATE_ON:
                # Check if this switch belongs to a loaded parent area that only auto-latched sleep
                parent_auto_only = False
                for entry_data in self.hass.data.get(MODULE_DATA, {}).values():
                    if not isinstance(entry_data, dict):
                        continue
                    other = entry_data.get(DATA_AREA_OBJECT)
                    if other is not None and other.simply_magic_entity_id(
                        SWITCH_DOMAIN, EntityNames.SLEEP_MODE
                    ) == sw_id:
                        other_sensor = getattr(other, "_lighting_profile_sensor", None)
                        if (
                            other_sensor is not None
                            and not getattr(other_sensor, "_manual_sleep_mode", False)
                            and not getattr(other, "sleep_mode_enabled", False)
                        ):
                            parent_auto_only = True
                        break
                if not parent_auto_only:
                    return True

        return False

    @callback
    def _context_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle state changes in tracked context entities."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        if (
            self.area.state == AreaState.AREA_STATE_MANUAL
            or (
                hasattr(self.area, "is_control_enabled")
                and not self.area.is_control_enabled(ControlType.System)
            )
        ):
            return
        old_state = event.data.get("old_state")
        entity_id = str(event.data.get("entity_id", ""))
        if (
            entity_id.startswith(f"{SENSOR_DOMAIN}.simply_magic_areas_state_")
            and old_state is not None
            and old_state.state == new_state.state
        ):
            return

        if entity_id.startswith(f"{SWITCH_DOMAIN}."):
            local_sleep_switch, inherited_sleep_switches = (
                self._get_applicable_sleep_switch_entities()
            )
            if entity_id == local_sleep_switch:
                if old_state is not None and old_state.state == new_state.state:
                    return
                sleep_switch = getattr(self.area, "_sleep_mode_switch", None)
                expected = getattr(sleep_switch, "_expected_synced_state", None)
                if expected is not None and expected == (new_state.state == STATE_ON):
                    sleep_switch._expected_synced_state = None
                    return
                if new_state.state == STATE_ON:
                    if hasattr(self.area, "set_sleep_mode"):
                        self.area.set_sleep_mode(True, propagate=True)
                    else:
                        self.set_manual_sleep_mode(True)
                    return
                if (
                    new_state.state == STATE_OFF
                    and old_state is not None
                    and old_state.state == STATE_ON
                ):
                    if hasattr(self.area, "set_sleep_mode"):
                        self.area.set_sleep_mode(False, propagate=True)
                    else:
                        self.set_manual_sleep_mode(False)
                    return
                return

            if entity_id in inherited_sleep_switches:
                if old_state is not None and old_state.state == new_state.state:
                    return
                if new_state.state == STATE_ON:
                    self._local_sleep_override_off = False
                    self.set_manual_sleep_mode(True)
                    return
                if (
                    new_state.state == STATE_OFF
                    and old_state is not None
                    and old_state.state == STATE_ON
                ):
                    self.set_manual_sleep_mode(False)
                    return
                return

        if entity_id.startswith(f"{LIGHT_DOMAIN}.") and entity_id in self._get_bedroom_light_entities():
            if old_state is not None and old_state.state == STATE_ON:
                self._bedroom_light_was_on = True
            if new_state.state == STATE_ON:
                self._bedroom_light_was_on = True
                self._bedroom_light_turned_on_then_off = False
            elif (
                new_state.state == STATE_OFF
                and self._bedroom_light_was_on
                and self._are_all_bedroom_lights_off()
            ):
                self._bedroom_light_turned_on_then_off = True

        self._update_historical_daylight()

        new_profile = self._calculate_lighting_profile()
        profile_changed = new_profile != self._attr_native_value
        if profile_changed:
            _LOGGER.debug(
                "%s: Lighting profile changed from %s to %s",
                self.area.name,
                self._attr_native_value,
                new_profile,
            )
            self._attr_native_value = new_profile
            self.area.lighting_profile = new_profile

        self._update_extra_state_attributes()
        self._write_or_schedule_ha_state()

    def refresh_profile(self) -> None:
        """Recalculate and update the lighting profile when area or house state changes."""
        if (
            self.area.state == AreaState.AREA_STATE_MANUAL
            or (
                hasattr(self.area, "is_control_enabled")
                and not self.area.is_control_enabled(ControlType.System)
            )
        ):
            return
        new_profile = self._calculate_lighting_profile()
        if new_profile != self._attr_native_value:
            _LOGGER.debug(
                "%s: Lighting profile refreshed from %s to %s",
                self.area.name,
                self._attr_native_value,
                new_profile,
            )
            self._attr_native_value = new_profile
            self.area.lighting_profile = new_profile
            self._update_extra_state_attributes()
            self._write_or_schedule_ha_state()

    def disable_morning_sleep(self) -> None:
        """Disable automatic and manual night sleep mode when the morning routine triggers."""
        local_switch, inherited_switches = self._get_applicable_sleep_switch_entities()
        for sw_id in [local_switch, *inherited_switches]:
            st = self.hass.states.get(sw_id)
            if st is not None and st.state == STATE_ON:
                self.hass.states.async_set(sw_id, STATE_OFF)
        self._morning_wakeup_disabled_sleep = True
        self._auto_sleep_latched = False
        self._manual_sleep_mode = False
        self._was_nighttime_when_sleep_active = False
        self._local_sleep_override_off = True
        self.area.sleep_mode_enabled = False
        if isinstance(getattr(self.area, "config", None), dict):
            self.area.config[CONF_SLEEP_MODE] = False
        sleep_switch = getattr(self.area, "_sleep_mode_switch", None)
        if sleep_switch is not None and hasattr(sleep_switch, "set_synced_state"):
            sleep_switch.set_synced_state(False)
        self._bedroom_light_turned_on_then_off = False
        self._bedroom_light_was_on = self._is_any_bedroom_light_on()
        self.refresh_profile()

    def _is_after_10pm_nighttime(self) -> bool:
        """Return True if automatic night sleep is enabled and current local time is after 10pm (or before morning end hour) at night."""
        if (
            self.area.is_exterior()
            or self.area.id.lower() == META_AREA_EXTERIOR.lower()
        ):
            return False

        raw_sleep_entity = self.area.config.get("sleep_entity")
        if isinstance(raw_sleep_entity, str) and raw_sleep_entity.lower() in (
            "none",
            "disabled",
        ):
            return False

        adv_conf = self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS)
        auto_sleep_enabled = adv_conf.get(
            CONF_AUTO_NIGHT_SLEEP,
            self.area.config.get(CONF_AUTO_NIGHT_SLEEP, DEFAULT_AUTO_NIGHT_SLEEP),
        )
        if not auto_sleep_enabled:
            return False

        raw_explicit_hour = getattr(self.area, "_current_local_hour", None)
        explicit_hour = (
            int(raw_explicit_hour)
            if isinstance(raw_explicit_hour, (int, float))
            and not isinstance(raw_explicit_hour, bool)
            else None
        )
        sun_state = self.hass.states.get("sun.sun")
        if sun_state is None and explicit_hour is None:
            return False

        if sun_state is not None and explicit_hour is None:
            elevation = sun_state.attributes.get("elevation", 0)
            try:
                elev_val = float(elevation)
            except (ValueError, TypeError):
                elev_val = 0.0
            if sun_state.state == "above_horizon" and elev_val > 0:
                return False

        if explicit_hour is not None:
            current_hour = int(explicit_hour)
        elif (
            sun_state is not None
            and sun_state.attributes.get("local_hour") is not None
        ):
            current_hour = int(sun_state.attributes["local_hour"])
        else:
            current_hour = dt_util.now().hour

        start_hour = int(
            adv_conf.get(
                CONF_AUTO_SLEEP_AFTER_HOUR,
                self.area.config.get(
                    CONF_AUTO_SLEEP_AFTER_HOUR, DEFAULT_AUTO_SLEEP_AFTER_HOUR
                ),
            )
        )
        end_hour = int(
            adv_conf.get(
                CONF_AUTO_SLEEP_END_HOUR,
                self.area.config.get(
                    CONF_AUTO_SLEEP_END_HOUR, DEFAULT_AUTO_SLEEP_END_HOUR
                ),
            )
        )

        if start_hour > end_hour:
            return current_hour >= start_hour or current_hour < end_hour
        return start_hour <= current_hour < end_hour

    def _is_bedroom_area(self) -> bool:
        """Return True if this area is a bedroom, ensuite, or bedroom meta-area."""
        is_bed_fn = getattr(self.area, "is_bedroom_or_ensuite", None)
        is_bed_meta_fn = getattr(self.area, "is_bedroom_meta", None)
        if callable(is_bed_fn) and is_bed_fn() is True:
            return True
        if callable(is_bed_meta_fn) and is_bed_meta_fn() is True:
            return True
        return bool(
            isinstance(getattr(self.area, "config", None), dict)
            and self.area.config.get(CONF_CHILD_AREAS)
        )

    def _get_bedroom_light_entities(self) -> list[str]:
        """Return all light entity IDs belonging to this area and any tied child areas."""
        light_ids: list[str] = []
        local_light_group = self.area.simply_magic_entity_id(
            LIGHT_DOMAIN, EntityNames.LIGHT
        )
        if local_light_group:
            light_ids.append(local_light_group)

        entities_dict = getattr(self.area, "entities", None)
        if isinstance(entities_dict, dict):
            for ent in entities_dict.get(LIGHT_DOMAIN, []):
                ent_id = ent.get(ATTR_ENTITY_ID) if isinstance(ent, dict) else None
                if ent_id and ent_id not in light_ids:
                    light_ids.append(ent_id)

        child_slugs: list[str] = []
        is_meta_fn = getattr(self.area, "is_meta", None)
        get_children_fn = getattr(self.area, "get_child_areas", None)
        if (
            callable(is_meta_fn)
            and is_meta_fn() is True
            and callable(get_children_fn)
        ):
            children = get_children_fn()
            if isinstance(children, list):
                child_slugs.extend(str(c) for c in children if c)
        elif isinstance(getattr(self.area, "config", None), dict):
            configured_children = self.area.config.get(CONF_CHILD_AREAS, [])
            if isinstance(configured_children, list):
                child_slugs.extend(str(c) for c in configured_children if c)

        for child_slug in child_slugs:
            child_lg = self.area.simply_magic_entity_id(
                LIGHT_DOMAIN, EntityNames.LIGHT, area_name=child_slug
            )
            if child_lg and child_lg not in light_ids:
                light_ids.append(child_lg)
            for entry_data in self.hass.data.get(MODULE_DATA, {}).values():
                if not isinstance(entry_data, dict):
                    continue
                child_area = entry_data.get(DATA_AREA_OBJECT)
                if child_area is not None and (
                    getattr(child_area, "slug", None) == child_slug
                    or getattr(child_area, "id", None) == child_slug
                    or str(getattr(child_area, "name", "")).lower()
                    == child_slug.lower()
                ):
                    child_ents = getattr(child_area, "entities", None)
                    if isinstance(child_ents, dict):
                        for ent in child_ents.get(LIGHT_DOMAIN, []):
                            ent_id = (
                                ent.get(ATTR_ENTITY_ID)
                                if isinstance(ent, dict)
                                else None
                            )
                            if ent_id and ent_id not in light_ids:
                                light_ids.append(ent_id)
        return light_ids

    def _is_any_bedroom_light_on(self) -> bool:
        """Return True if at least one tracked light in this area/suite is currently ON."""
        for eid in self._get_bedroom_light_entities():
            st = self.hass.states.get(eid)
            if st is not None and st.state == STATE_ON:
                return True
        return False

    def _are_all_bedroom_lights_off(self) -> bool:
        """Return True if at least one light state is tracked and all tracked lights are OFF."""
        valid_states = [
            self.hass.states.get(eid)
            for eid in self._get_bedroom_light_entities()
            if self.hass.states.get(eid) is not None
            and self.hass.states.get(eid).state not in INVALID_STATES  # type: ignore[union-attr]
        ]
        if not valid_states:
            return False
        return all(st.state == STATE_OFF for st in valid_states if st is not None)

    def _update_bedroom_light_cycle(self) -> None:
        """Track whether bedroom lights have been turned ON and then OFF."""
        if self._is_any_bedroom_light_on():
            self._bedroom_light_was_on = True
            self._bedroom_light_turned_on_then_off = False
        elif self._bedroom_light_was_on and self._are_all_bedroom_lights_off():
            self._bedroom_light_turned_on_then_off = True

    def _is_room_or_house_unoccupied(self) -> bool:
        """Return True if either this room or the main house is currently unoccupied."""
        unoccupied_states = {
            AreaState.AREA_STATE_CLEAR,
            AreaState.AREA_STATE_EXTENDED,
            "clear",
            "extended",
        }

        # 1. Check if this room itself is unoccupied
        if getattr(self.area, "state", None) in unoccupied_states:
            return True
        local_state_entity = self.area.simply_magic_entity_id(
            SENSOR_DOMAIN, EntityNames.STATE
        )
        local_state_obj = self.hass.states.get(local_state_entity)
        if local_state_obj is not None and local_state_obj.state in unoccupied_states:
            return True

        # 2. Check if the main house is unoccupied (via Interior / Global meta-area state sensors)
        for meta_slug in (META_AREA_INTERIOR.lower(), META_AREA_GLOBAL.lower()):
            meta_state_id = self.area.simply_magic_entity_id(
                SENSOR_DOMAIN, EntityNames.STATE, area_name=meta_slug
            )
            meta_state_obj = self.hass.states.get(meta_state_id)
            if meta_state_obj is not None and meta_state_obj.state in unoccupied_states:
                return True

        # 3. Check loaded main house area objects directly
        main_house_areas = []
        for entry_data in self.hass.data.get(MODULE_DATA, {}).values():
            if not isinstance(entry_data, dict):
                continue
            other_area = entry_data.get(DATA_AREA_OBJECT)
            if other_area is not None and other_area.is_main_house_area():
                main_house_areas.append(other_area)
        return bool(
            main_house_areas
            and all(
                getattr(a, "state", None) in unoccupied_states
                for a in main_house_areas
            )
        )

    def _calculate_lighting_profile(self) -> LightingProfile:
        """Calculate the active lighting profile based on priority: Accent > Sleep > Bright > Standard."""
        self._update_bedroom_light_cycle()

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
        is_nighttime = self._is_after_10pm_nighttime()
        if not is_nighttime and self._was_nighttime_when_sleep_active:
            # Morning arrived after nighttime sleep: reset manual and auto night sleep
            self._was_nighttime_when_sleep_active = False
            self._manual_sleep_mode = False
            self._local_sleep_override_off = False
            self.area.sleep_mode_enabled = False
            if isinstance(getattr(self.area, "config", None), dict):
                self.area.config[CONF_SLEEP_MODE] = False
            local_sw, inherited_sws = self._get_applicable_sleep_switch_entities()
            for sw_id in [local_sw, *inherited_sws]:
                st = self.hass.states.get(sw_id)
                if st is not None and st.state == STATE_ON:
                    self.hass.states.async_set(sw_id, STATE_OFF)
            sleep_switch = getattr(self.area, "_sleep_mode_switch", None)
            if sleep_switch is not None and hasattr(sleep_switch, "set_synced_state"):
                sleep_switch.set_synced_state(False)

        if self._is_manual_sleep_mode_active():
            if is_nighttime:
                self._was_nighttime_when_sleep_active = True
            return LightingProfile.SLEEP

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
        elif (
            not sleep_entity
            and "sleep_entity" not in self.area.config
            and not self.area.is_meta()
            and not self._local_sleep_override_off
        ):
            for parent in self.area.get_parent_meta_areas():
                # Bedrooms only inherit sleep profile from a bedroom meta-area or explicit parent sleep_entity
                if (
                    self._is_bedroom_area()
                    and not getattr(parent, "is_bedroom_meta", lambda: False)()
                    and not parent.config.get("sleep_entity")
                ):
                    continue
                if getattr(parent, "lighting_profile", None) == LightingProfile.SLEEP:
                    return LightingProfile.SLEEP
                parent_profile_sensor = self.area.simply_magic_entity_id(
                    SENSOR_DOMAIN,
                    EntityNames.LIGHTING_PROFILE,
                    area_name=parent.slug,
                )
                state_obj = self.hass.states.get(parent_profile_sensor)
                if state_obj and state_obj.state == LightingProfile.SLEEP:
                    return LightingProfile.SLEEP

        # Automatic night sleep mode:
        # - In bedrooms: only set after the light is turned ON and then OFF at night (after 10pm).
        # - In main house areas: triggered when the house or room is unoccupied at night (after 10pm).
        # - Disabled when morning routine (sustained morning activity or morning hour / sunrise) occurs.
        if is_nighttime:
            if not self._morning_wakeup_disabled_sleep:
                if self._is_bedroom_area():
                    if self._bedroom_light_turned_on_then_off:
                        self._auto_sleep_latched = True
                elif self._is_room_or_house_unoccupied():
                    self._auto_sleep_latched = True
                if self._auto_sleep_latched:
                    self._was_nighttime_when_sleep_active = True
                    sleep_switch = getattr(self.area, "_sleep_mode_switch", None)
                    if sleep_switch is not None and hasattr(
                        sleep_switch, "set_synced_state"
                    ):
                        sleep_switch.set_synced_state(True)
                    return LightingProfile.SLEEP
        else:
            self._morning_wakeup_disabled_sleep = False
            self._auto_sleep_latched = False
            self._bedroom_light_turned_on_then_off = False
            self._bedroom_light_was_on = self._is_any_bedroom_light_on()

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
        adv_conf = self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS)
        if CONF_DARK_LUX_FLOOR in adv_conf:
            return int(adv_conf[CONF_DARK_LUX_FLOOR])
        return int(self.area.config.get(CONF_DARK_LUX_FLOOR, DEFAULT_DARK_LUX_FLOOR))

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
            adv_conf = self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS)
            if CONF_DAYLIGHT_LUX_LEVEL in adv_conf:
                manual_daylight = int(adv_conf[CONF_DAYLIGHT_LUX_LEVEL])
            else:
                manual_daylight = int(
                    self.area.config.get(
                        CONF_DAYLIGHT_LUX_LEVEL, DEFAULT_DAYLIGHT_LUX_LEVEL
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
            sun_state = self.hass.states.get("sun.sun")
            if sun_state is not None:
                if sun_state.state == "below_horizon":
                    return False
                if sun_state.state != "above_horizon":
                    elev = sun_state.attributes.get("elevation")
                    if elev is not None:
                        with suppress(ValueError, TypeError):
                            if float(elev) <= 0:
                                return False

            is_currently_bright = (
                getattr(self.area, "lighting_profile", None)
                == LightingProfile.BRIGHT
            )
            adv_conf = self.area.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS)
            has_ref_lux_sensor = bool(
                self.area.config.get(CONF_REFERENCE_ILLUMINANCE_SENSOR)
                or adv_conf.get(CONF_REFERENCE_ILLUMINANCE_SENSOR)
            )
            if (
                not is_currently_bright
                and not has_ref_lux_sensor
                and self._is_any_bedroom_light_on()
            ):
                return False

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

        # Tier 4 parent meta-area bright profile fallback when no local lux or reference area
        if (
            lux is None
            and not ref_area
            and "bright_entity" not in self.area.config
            and not self.area.is_meta()
        ):
            for parent in self.area.get_parent_meta_areas():
                if getattr(parent, "lighting_profile", None) == LightingProfile.BRIGHT:
                    return True
                parent_profile_sensor = self.area.simply_magic_entity_id(
                    SENSOR_DOMAIN,
                    EntityNames.LIGHTING_PROFILE,
                    area_name=parent.slug,
                )
                state_obj = self.hass.states.get(parent_profile_sensor)
                if state_obj and state_obj.state == LightingProfile.BRIGHT:
                    return True

        return False
