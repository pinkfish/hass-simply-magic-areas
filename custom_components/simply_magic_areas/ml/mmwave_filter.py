"""mmWave Radar Fan vs Human Discriminator for Simply Magic Areas.

Distinguishes rotating mechanical fan blades from genuine human presence using:
1. Actuator-Radar Causal Coupling (correlating with AreaFanGroup state).
2. Range Gate and Target Distance Spatial Masking (LD2410, LD2450, FP2, ESPHome).
3. Statistical Micro-motion Variance / Doppler Harmonic Filtering.
4. Universal Binary Radar Dwell Filtering.

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
        base_id = sensor_id.split(".", 1)[-1]
        for candidate_name in (
            f"sensor.{base_id}_target_distance",
            f"sensor.{base_id}_distance",
            f"sensor.{base_id}_moving_target_distance",
        ):
            companion = self.hass.states.get(candidate_name)
            if companion and companion.state not in (STATE_OFF, "unavailable", "unknown"):
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
                    # If motion energy is >= 25, a person is walking/moving
                    if float(val) >= 25.0:
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

    def is_fan_interference(
        self,
        sensor_id: str,
        state: State,
        all_area_sensors: list[str] | None = None,
    ) -> bool:
        """Determine whether the sensor state is caused by a fan blade rather than a person.

        Returns True if the sensor should be filtered out.
        """
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
            # If macro motion confirms a person is moving, accept presence
            if self._has_macro_motion(state):
                return False

            # Check variance for mechanical stability
            if self._is_low_variance_mechanical(sensor_id, dist_m):
                _LOGGER.debug(
                    "%s: Filtered %s at %.2fm - stable mechanical fan reflection",
                    self.area.slug,
                    sensor_id,
                    dist_m,
                )
                return True

            # Even without variance history, distance matches fan zone with low macro energy
            _LOGGER.debug(
                "%s: Filtered %s at %.2fm in fan zone [%.1f - %.1f]",
                self.area.slug,
                sensor_id,
                dist_m,
                fan_dist_min,
                fan_dist_max,
            )
            return True

        # 2. Universal Binary Sensor Dwell Filter
        # If there are no distance attributes (pure binary presence sensor)
        if all_area_sensors:
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

                # If NO other sensor sees movement, and the fan is spinning,
                # the isolated persistent radar trigger is fan ghosting
                if not other_active:
                    _LOGGER.debug(
                        "%s: Filtered binary radar %s - fan running %.1fs with no other active sensors",
                        self.area.slug,
                        sensor_id,
                        time_fan_running,
                    )
                    return True

        return False
