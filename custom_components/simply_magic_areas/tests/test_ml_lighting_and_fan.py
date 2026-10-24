"""Tests for ML adaptive lighting preference learning and mmWave fan vs human discrimination."""

import asyncio
from unittest.mock import MagicMock

from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
)
from homeassistant.components.light import (
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import (
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import MockConfigEntry

from ..const import (
    ATTR_FILTERED_FAN_SENSORS,
    ATTR_MANUAL_CONTROL,
    DATA_AREA_OBJECT,
    MODULE_DATA,
)
from ..ml.lighting_learner import OnlineBayesianLightingLearner
from ..ml.mmwave_filter import MMWaveFanFilter
from .mocks import MockBinarySensor, MockFan


async def test_online_bayesian_lighting_learner_math(hass: HomeAssistant) -> None:
    """Test the local Bayesian regression math, update step, and prediction blending."""
    learner = OnlineBayesianLightingLearner(hass, "living_room")
    assert learner.sample_count == 0
    assert learner.confidence == 0.0

    # With zero samples, returns default heuristic brightness
    pred_0 = learner.predict_brightness(
        lux=50.0, profile="standard", solar_elevation=25.0, default_brightness=120
    )
    assert pred_0 == 120

    # User manually adjusts dimmer slider to 200 brightness at 50 lux
    learner.learn_preference(
        lux=50.0, profile="standard", solar_elevation=25.0, target_brightness=200
    )
    assert learner.sample_count == 1
    assert learner.confidence == 0.2

    # Prediction should shift towards 200
    pred_1 = learner.predict_brightness(
        lux=50.0, profile="standard", solar_elevation=25.0, default_brightness=120
    )
    assert pred_1 > 120

    # Learn 4 more times at 200 brightness -> reaches full confidence (1.0)
    for _ in range(4):
        learner.learn_preference(
            lux=50.0, profile="standard", solar_elevation=25.0, target_brightness=200
        )
    assert learner.sample_count == 5
    assert learner.confidence == 1.0

    # Prediction should now be close to 200
    pred_5 = learner.predict_brightness(
        lux=50.0, profile="standard", solar_elevation=25.0, default_brightness=120
    )
    assert abs(pred_5 - 200) < 15

    # Full daylight cutoff invariant: if default rule shut lights off (0), prediction must remain 0
    pred_sun = learner.predict_brightness(
        lux=800.0, profile="standard", solar_elevation=50.0, default_brightness=0
    )
    assert pred_sun == 0

    # Verify attributes
    attrs = learner.get_attributes()
    assert attrs["ml_adaptive_lighting_active"] is True
    assert attrs["ml_preference_samples"] == 5
    assert attrs["ml_confidence"] == 1.0
    assert attrs["ml_predicted_brightness"] == 0


async def test_mmwave_filter_gated_and_variance(hass: HomeAssistant) -> None:
    """Test mmWave radar fan discrimination using distance gating and low variance."""
    mock_area = MagicMock()
    mock_area.slug = "bedroom"
    mock_area.entities = {FAN_DOMAIN: ["fan.bedroom_ceiling_fan"]}

    # Create MMWaveFanFilter
    radar_filter = MMWaveFanFilter(hass, mock_area)

    # 1. When fan is OFF: no radar interference possible
    hass.states.async_set("fan.bedroom_ceiling_fan", STATE_OFF)
    radar_state = State(
        "binary_sensor.bedroom_radar_occupancy",
        STATE_ON,
        {"target_distance": 2.4, "motion_energy": 5},
    )
    assert (
        radar_filter.is_fan_interference(
            "binary_sensor.bedroom_radar_occupancy", radar_state
        )
        is False
    )

    # 2. Fan turns ON: radar detects target at 2.4m (fan distance zone 1.8m - 3.5m)
    hass.states.async_set("fan.bedroom_ceiling_fan", STATE_ON)
    assert (
        radar_filter.is_fan_interference(
            "binary_sensor.bedroom_radar_occupancy", radar_state
        )
        is True
    )

    # 3. Target distance outside fan zone (e.g. person at 1.2m or 4.5m)
    human_close_state = State(
        "binary_sensor.bedroom_radar_occupancy",
        STATE_ON,
        {"target_distance": 1.2, "motion_energy": 5},
    )
    assert (
        radar_filter.is_fan_interference(
            "binary_sensor.bedroom_radar_occupancy", human_close_state
        )
        is False
    )

    # 4. Target distance in fan zone (2.4m) BUT high macro motion energy (person walking)
    human_walking_state = State(
        "binary_sensor.bedroom_radar_occupancy",
        STATE_ON,
        {"target_distance": 2.4, "motion_energy": 60},
    )
    assert (
        radar_filter.is_fan_interference(
            "binary_sensor.bedroom_radar_occupancy", human_walking_state
        )
        is False
    )


async def test_mmwave_fan_filter_integration_area_clears(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_fan: list[MockFan],
    _setup_integration: None,
) -> None:
    """Test that AreaStateSensor filters out mmWave fan reflection and updates attributes."""
    radar_id = "binary_sensor.kitchen_radar_occupancy"
    # Set radar state with 2.5m distance and 0 motion energy (fan blade signature)
    hass.states.async_set(
        radar_id,
        STATE_ON,
        {"target_distance": 2.5, "motion_energy": 0},
    )
    await hass.async_block_till_done()

    # Track this sensor in the state sensor
    state_sensor_id = f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    state_sensor = None
    for entity in hass.data[MODULE_DATA][config_entry.entry_id][
        DATA_AREA_OBJECT
    ].hass.data["sensor"].entities:
        if getattr(entity, "entity_id", None) == state_sensor_id:
            state_sensor = entity
            break

    if state_sensor is not None:
        state_sensor._sensors.append(radar_id)

    # 1. When fan is OFF: radar is not filtered
    one_fan[0].turn_off()
    await hass.async_block_till_done()
    await asyncio.sleep(0.5)

    if state_sensor is not None:
        state_sensor._update_state()
        await hass.async_block_till_done()

    state_obj = hass.states.get(state_sensor_id)
    assert radar_id not in state_obj.attributes.get(ATTR_FILTERED_FAN_SENSORS, [])

    # 2. When fan turns ON: radar at 2.5m is recognized as fan reflection and filtered!
    one_fan[0].turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(0.5)

    if state_sensor is not None:
        state_sensor._update_state()
        await hass.async_block_till_done()

    state_obj = hass.states.get(state_sensor_id)
    assert radar_id in state_obj.attributes.get(ATTR_FILTERED_FAN_SENSORS, [])


async def test_light_learns_user_brightness_override(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_light: list[str],
    one_motion: list[MockBinarySensor],
    _setup_integration: None,
) -> None:
    """Test that manual brightness slider overrides are learned by the local Bayesian model."""
    light_entity_id = f"{LIGHT_DOMAIN}.simply_magic_areas_light_kitchen"

    # Step into room -> motion on -> lights on
    one_motion[0].turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(0.5)

    # Light is on at 100
    hass.states.async_set(
        light_entity_id,
        STATE_ON,
        {
            ATTR_BRIGHTNESS: 100,
            "friendly_name": "kitchen kitchen Lights (Simply Magic Areas)",
        },
    )
    await hass.async_block_till_done()
    await asyncio.sleep(0.5)

    # User manually drags the brightness slider from 100 to 220
    hass.states.async_set(
        light_entity_id,
        STATE_ON,
        {
            ATTR_BRIGHTNESS: 220,
            "friendly_name": "kitchen kitchen Lights (Simply Magic Areas)",
        },
    )
    await hass.async_block_till_done()
    await asyncio.sleep(0.5)

    light_state = hass.states.get(light_entity_id)
    assert light_state.attributes.get(ATTR_MANUAL_CONTROL) is True
    assert light_state.attributes.get("ml_adaptive_lighting_active") is True
    assert light_state.attributes.get("ml_preference_samples") == 2
    assert light_state.attributes.get("ml_confidence") == 0.4
