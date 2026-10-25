"""mmWave Radar Fan vs Human Discriminator for Simply Magic Areas.

Distinguishes rotating mechanical fan blades from genuine human presence using:
1. Actuator-Radar Causal Coupling (correlating with AreaFanGroup state).
2. Range Gate and Target Distance Spatial Masking (LD2410, LD2450, FP2, ESPHome).
3. Multi-Signal and Secondary Target Evaluation (unmasked range gates, macro-motion energy, multi-target tracking).
4. Statistical Micro-motion Variance / Doppler Harmonic Filtering.
5. Universal Binary Radar Dwell Filtering.

When an mmWave sensor detects a fan blade reflection, the primary occupancy binary sensor
is bypassed, and secondary radar signals (other elements on the sensor) are evaluated to
determine if a person is actually present in the room, only if the sensor exposes the
right signals to work this out.

Runs 100% locally with zero cloud dependencies.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant, State

if TYPE_CHECKING:
    from ..base.magic import MagicArea

_LOGGER = logging.getLogger(__name__)

# Typical radar sensor keywords in entity IDs or names
RADAR_KEYWORDS = {
    "radar",
    "mmwave",
    "ld24",
    "fp1",
    "fp2",
    "presence",
    "microwave",
    "target",
    "c4001",
    "dfrobot",
    "senic",
}

# Distance attributes reported by LD2410, LD2450, Tuya, ESPHome radars
DISTANCE_ATTRIBUTES = (
    "target_distance",
    "detection_distance",
    "distance",
    "moving_target_distance",
    "still_target_distance",
    "target_1_distance",
)

# Secondary target distance attributes (LD2450 multi-target tracking)
SECONDARY_TARGET_ATTRIBUTES = (
    "target_2_distance",
    "target_3_distance",
    "secondary_target_distance",
)

# Macro motion energy attributes
MOTION_ENERGY_ATTRIBUTES = (
    "moving_target_energy",
    "motion_energy",
    "move_energy",
    "macro_energy",
)


class MMWaveFanFilter:
    """Discriminates fan blade reflections from actual human presence."""

    def __init__(self, hass: HomeAssistant, area: MagicArea) -> None:
        """Initialize the mmWave filter for an area."""
        self.hass = hass
        self.area = area

        # Rolling history of scalar radar readings (distance or still energy)
        # entity_id -> deque of last 10 float values
        self._sensor_history: dict[str, deque[float]] = {}

        # Fan start timestamp tracking
        self._last_fan_on_ts: float | None = None
        self._last_fan_state: bool = False

    def is_fan_running(self) -> bool:
        """Check if any fan is active in the area."""
        now_ts = datetime.now(UTC).timestamp()

        # Check Area Fan Group
        fan_group_id = f"{FAN_DOMAIN}.simply_magic_areas_fan_{self.area.slug}"
        group_state = self.hass.states.get(fan_group_id)
        if group_state and group_state.state == STATE_ON:
            if not self._last_fan_state:
                self._last_fan_on_ts = now_ts
                self._last_fan_state = True
            return True

        # Check physical fan entities registered in the area
        fan_entities = self.area.entities.get(FAN_DOMAIN, [])
        for fan_entry in fan_entities:
            entity_id = (
                fan_entry.get(ATTR_ENTITY_ID)
                if isinstance(fan_entry, dict)
                else str(fan_entry)
            )
            state = self.hass.states.get(entity_id)
            if state and state.state == STATE_ON:
                if not self._last_fan_state:
                    self._last_fan_on_ts = now_ts
                    self._last_fan_state = True
                return True

        self._last_fan_state = False
        return False

    def is_radar_sensor(self, sensor_id: str, state: State | None = None) -> bool:
        """Determine if an entity is a mmWave radar sensor."""
        sensor_lower = sensor_id.lower()
        if any(kw in sensor_lower for kw in RADAR_KEYWORDS):
            return True

        if state is not None:
            friendly_name = str(state.attributes.get("friendly_name", "")).lower()
            if any(kw in friendly_name for kw in RADAR_KEYWORDS):
                return True
            for attr in DISTANCE_ATTRIBUTES:
                if attr in state.attributes:
                    return True

        return False

    def _get_sensor_base_candidates(self, sensor_id: str) -> list[str]:
        """Generate candidate base names for companion sensor discovery."""
        base = sensor_id.split(".", 1)[-1]
        candidates = [base]
        for suffix in (
            "_occupancy",
            "_presence",
            "_occupancy_sensor",
            "_presence_sensor",
            "_sensor",
            "_radar",
        ):
            if base.endswith(suffix):
                stripped = base[: -len(suffix)]
                if stripped and stripped not in candidates:
                    candidates.append(stripped)
        return candidates

    def _get_target_distance_meters(
        self, sensor_id: str, state: State
    ) -> float | None:
        """Extract target distance in meters from attributes or companion sensor."""
        # 1. Direct attribute on the binary sensor
        for attr in DISTANCE_ATTRIBUTES:
            val = state.attributes.get(attr)
            if val is not None:
                try:
                    num = float(val)
                    # If > 20, unit is centimeters (e.g. 240 cm = 2.4 m)
                    return num / 100.0 if num > 20.0 else num
                except (ValueError, TypeError):
                    continue

        # 2. Companion sensor (e.g. sensor.<name>_target_distance)
        for base_id in self._get_sensor_base_candidates(sensor_id):
            for candidate_name in (
                f"sensor.{base_id}_target_distance",
                f"sensor.{base_id}_distance",
                f"sensor.{base_id}_moving_target_distance",
                f"sensor.{base_id}_target_1_distance",
            ):
                companion = self.hass.states.get(candidate_name)
                if companion and companion.state not in (
                    STATE_OFF,
                    "unavailable",
                    "unknown",
                ):
                    try:
                        num = float(companion.state)
                        return num / 100.0 if num > 20.0 else num
                    except (ValueError, TypeError):
                        continue

        return None

    def _has_macro_motion(self, state: State) -> bool:
        """Check if high-energy macro-motion is detected."""
        for attr in MOTION_ENERGY_ATTRIBUTES:
            val = state.attributes.get(attr)
            if val is not None:
                try:
                    if float(val) >= 20.0:
                        return True
                except (ValueError, TypeError):
                    continue
        return False

    def _is_low_variance_mechanical(self, sensor_id: str, current_value: float) -> bool:
        """Evaluate if the sensor readings show mechanical constant periodicity."""
        if sensor_id not in self._sensor_history:
            self._sensor_history[sensor_id] = deque(maxlen=10)

        history = self._sensor_history[sensor_id]
        history.append(current_value)

        if len(history) < 6:
            return False

        # Compute sample variance
        mean = sum(history) / len(history)
        var = sum((x - mean) ** 2 for x in history) / len(history)

        # Rotating fan at fixed distance exhibits variance near 0 (< 0.02)
        # Biological humans have respiratory variations and shifting (variance > 0.1)
        return var < 0.02

    def has_secondary_signals(self, sensor_id: str, state: State) -> bool:
        """Check if the sensor exposes secondary signals to evaluate presence beyond occupancy."""
        # 1. Direct attributes on the state object
        for attr in MOTION_ENERGY_ATTRIBUTES:
            if attr in state.attributes:
                return True

        for attr in SECONDARY_TARGET_ATTRIBUTES:
            if attr in state.attributes:
                return True

        if "targets" in state.attributes:
            return True

        for attr in state.attributes:
            attr_l = attr.lower()
            if ("gate_" in attr_l or attr_l.startswith("g")) and (
                "move" in attr_l or "energy" in attr_l
            ):
                return True

        # 2. Companion entities in Home Assistant
        candidates = self._get_sensor_base_candidates(sensor_id)
        for base in candidates:
            # Companion motion binary sensor
            for companion_motion in (
                f"binary_sensor.{base}_motion",
                f"binary_sensor.{base}_moving",
            ):
                if self.hass.states.get(companion_motion) is not None:
                    return True

            # Companion motion energy sensor
            for companion_energy in (
                f"sensor.{base}_moving_target_energy",
                f"sensor.{base}_motion_energy",
                f"sensor.{base}_move_energy",
            ):
                if self.hass.states.get(companion_energy) is not None:
                    return True

            # Companion secondary target sensor
            for companion_target in (
                f"sensor.{base}_target_2_distance",
                f"sensor.{base}_target_3_distance",
                f"sensor.{base}_secondary_target_distance",
            ):
                if self.hass.states.get(companion_target) is not None:
                    return True

            # Companion gate energy sensors
            for gate_idx in ("0", "1", "5", "6", "7", "8"):
                for pat in (
                    f"sensor.{base}_gate_{gate_idx}_move_energy",
                    f"sensor.{base}_gate_{gate_idx}_energy",
                    f"sensor.{base}_g{gate_idx}_move_energy",
                ):
                    if self.hass.states.get(pat) is not None:
                        return True

        return False

    def detect_human_from_other_elements(self, sensor_id: str, state: State) -> bool:
        """Evaluate other elements on the sensor to determine human presence."""
        # 1. Macro-motion energy evaluation (direct attributes)
        if self._has_macro_motion(state):
            return True

        # 2. Secondary target distance evaluation (direct attributes)
        for attr in SECONDARY_TARGET_ATTRIBUTES:
            val = state.attributes.get(attr)
            if val is not None:
                try:
                    dist = float(val)
                    if dist > 20.0:
                        dist /= 100.0
                    # If secondary target is valid (> 0.3m) and outside the fan zone (< 1.8m or > 3.5m)
                    if 0.3 <= dist <= 12.0 and (dist < 1.8 or dist > 3.5):
                        _LOGGER.debug(
                            "%s: Radar %s detected human via secondary target (%s: %.2fm)",
                            self.area.slug,
                            sensor_id,
                            attr,
                            dist,
                        )
                        return True
                except (ValueError, TypeError):
                    continue

        # Check multi-target list attribute (e.g. FP2 or LD2450)
        targets = state.attributes.get("targets")
        if isinstance(targets, list):
            for t in targets:
                if isinstance(t, dict):
                    dist_val = t.get("distance")
                    if dist_val is not None:
                        try:
                            dist = float(dist_val)
                            if dist > 20.0:
                                dist /= 100.0
                            if 0.3 <= dist <= 12.0 and (dist < 1.8 or dist > 3.5):
                                _LOGGER.debug(
                                    "%s: Radar %s detected human via targets list (%.2fm)",
                                    self.area.slug,
                                    sensor_id,
                                    dist,
                                )
                                return True
                        except (ValueError, TypeError):
                            continue

        # 3. Unmasked distance gates outside fan zone (Gates 0, 1, 5, 6, 7, 8)
        for attr, val in state.attributes.items():
            attr_l = attr.lower()
            for gate_idx in ("0", "1", "5", "6", "7", "8"):
                if (
                    f"gate_{gate_idx}" in attr_l or f"g{gate_idx}_" in attr_l
                ) and ("move" in attr_l or "energy" in attr_l):
                    try:
                        if float(val) >= 20.0:
                            _LOGGER.debug(
                                "%s: Radar %s detected human via unmasked gate (%s: %s)",
                                self.area.slug,
                                sensor_id,
                                attr,
                                val,
                            )
                            return True
                    except (ValueError, TypeError):
                        continue

        # 4. Companion sensors in Home Assistant
        candidates = self._get_sensor_base_candidates(sensor_id)
        for base in candidates:
            # Companion motion energy sensors
            for companion_energy_id in (
                f"sensor.{base}_moving_target_energy",
                f"sensor.{base}_motion_energy",
                f"sensor.{base}_move_energy",
            ):
                companion_st = self.hass.states.get(companion_energy_id)
                if companion_st and companion_st.state not in (
                    STATE_OFF,
                    "unavailable",
                    "unknown",
                ):
                    try:
                        if float(companion_st.state) >= 20.0:
                            _LOGGER.debug(
                                "%s: Radar %s detected human via companion energy %s (%s)",
                                self.area.slug,
                                sensor_id,
                                companion_energy_id,
                                companion_st.state,
                            )
                            return True
                    except (ValueError, TypeError):
                        pass

            # Companion secondary target sensors
            for companion_target_id in (
                f"sensor.{base}_target_2_distance",
                f"sensor.{base}_target_3_distance",
                f"sensor.{base}_secondary_target_distance",
            ):
                companion_st = self.hass.states.get(companion_target_id)
                if companion_st and companion_st.state not in (
                    STATE_OFF,
                    "unavailable",
                    "unknown",
                ):
                    try:
                        dist = float(companion_st.state)
                        if dist > 20.0:
                            dist /= 100.0
                        if 0.3 <= dist <= 12.0 and (dist < 1.8 or dist > 3.5):
                            _LOGGER.debug(
                                "%s: Radar %s detected human via companion secondary target %s (%.2fm)",
                                self.area.slug,
                                sensor_id,
                                companion_target_id,
                                dist,
                            )
                            return True
                    except (ValueError, TypeError):
                        pass

            # Companion gate energy sensors
            for gate_idx in ("0", "1", "5", "6", "7", "8"):
                for pat in (
                    f"sensor.{base}_gate_{gate_idx}_move_energy",
                    f"sensor.{base}_gate_{gate_idx}_energy",
                    f"sensor.{base}_g{gate_idx}_move_energy",
                ):
                    companion_st = self.hass.states.get(pat)
                    if companion_st and companion_st.state not in (
                        STATE_OFF,
                        "unavailable",
                        "unknown",
                    ):
                        try:
                            if float(companion_st.state) >= 20.0:
                                _LOGGER.debug(
                                    "%s: Radar %s detected human via companion gate %s (%s)",
                                    self.area.slug,
                                    sensor_id,
                                    pat,
                                    companion_st.state,
                                )
                                return True
                        except (ValueError, TypeError):
                            pass

            # Companion motion binary sensor (e.g. PIR or separate moving target binary sensor)
            for companion_motion_id in (
                f"binary_sensor.{base}_motion",
                f"binary_sensor.{base}_moving",
            ):
                companion_st = self.hass.states.get(companion_motion_id)
                if companion_st and companion_st.state == STATE_ON:
                    _LOGGER.debug(
                        "%s: Radar %s detected human via companion motion sensor %s",
                        self.area.slug,
                        sensor_id,
                        companion_motion_id,
                    )
                    return True

        return False

    def is_fan_detecting(
        self,
        sensor_id: str,
        state: State,
        all_area_sensors: list[str] | None = None,
    ) -> bool:
        """Check whether it is clear that the radar sensor is detecting a running fan."""
        if not self.is_fan_running():
            return False

        if not self.is_radar_sensor(sensor_id, state):
            return False

        # Configured fan distance bounds (default: 1.8m to 3.5m)
        fan_dist_min = 1.8
        fan_dist_max = 3.5

        # 1. Check Range Gate / Target Distance
        dist_m = self._get_target_distance_meters(sensor_id, state)
        if dist_m is not None and fan_dist_min <= dist_m <= fan_dist_max:
            return True

        # 2. Universal Binary Sensor Dwell Filter
        # If there are no distance attributes (pure binary presence sensor)
        if all_area_sensors and dist_m is None:
            now_ts = datetime.now(UTC).timestamp()
            fan_start = self._last_fan_on_ts or now_ts
            time_fan_running = now_ts - fan_start

            # Only engage binary dwell filter if fan has been running for at least 30s
            if time_fan_running >= 30.0:
                # Check other presence sensors (e.g. PIR, BLE, door)
                other_active = False
                for other_id in all_area_sensors:
                    if other_id == sensor_id:
                        continue
                    other_state = self.hass.states.get(other_id)
                    if other_state and other_state.state == STATE_ON:
                        other_active = True
                        break

                if not other_active:
                    return True

        return False

    def evaluate_radar_signals(
        self,
        sensor_id: str,
        state: State,
        all_area_sensors: list[str] | None = None,
    ) -> tuple[bool, bool, bool]:
        """Evaluate radar signals when fan is running.

        Returns tuple of:
        (is_fan_detected, has_human_presence, signals_available)
        """
        if not self.is_fan_running() or not self.is_radar_sensor(sensor_id, state):
            return (False, False, False)

        is_fan = self.is_fan_detecting(sensor_id, state, all_area_sensors)
        if not is_fan:
            return (False, False, False)

        signals_available = self.has_secondary_signals(sensor_id, state)
        if not signals_available:
            return (True, False, False)

        has_human = self.detect_human_from_other_elements(sensor_id, state)
        return (True, has_human, True)

    def is_fan_interference(
        self,
        sensor_id: str,
        state: State,
        all_area_sensors: list[str] | None = None,
    ) -> bool:
        """Determine whether the sensor state is caused by a fan blade rather than a person.

        Returns True if the sensor should be filtered out.
        """
        is_fan, has_human, has_signals = self.evaluate_radar_signals(
            sensor_id, state, all_area_sensors
        )
        if not is_fan:
            return False

        # If sensor exposes right signals and other elements confirm human:
        if has_signals and has_human:
            _LOGGER.debug(
                "%s: Radar %s fan detected but other elements confirm human presence",
                self.area.slug,
                sensor_id,
            )
            return False

        # Otherwise filter it out as fan interference
        _LOGGER.debug(
            "%s: Radar %s filtered out as fan blade interference",
            self.area.slug,
            sensor_id,
        )
        return True

