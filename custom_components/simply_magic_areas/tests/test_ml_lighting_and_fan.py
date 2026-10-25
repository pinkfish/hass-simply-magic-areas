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
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import (
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import MockConfigEntry

from ..config.area_state import AreaState
from ..const import (
    ATTR_DRYING_RATE_K,
    ATTR_ESTIMATED_DRY_TS,
    ATTR_FILTERED_FAN_SENSORS,
    ATTR_HUMIDITY_ON,
    ATTR_MANUAL_CONTROL,
    ATTR_RADAR_FAN_OVERRIDE_SENSORS,
    ATTR_SHOWER_PROBABILITY,
    DATA_AREA_OBJECT,
    MODULE_DATA,
)
from ..ml.lighting_learner import OnlineBayesianLightingLearner
from ..ml.mmwave_filter import MMWaveFanFilter
from ..ml.shower_predictor import MoistureEvacuationModel, ShowerClassifier
from .common import async_mock_service
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


async def test_mmwave_secondary_signals_multi_target_and_unmasked_gates(
    hass: HomeAssistant,
) -> None:
    """Test using other elements on mmWave sensor when fan is detected."""
    mock_area = MagicMock()
    mock_area.slug = "bedroom"
    mock_area.entities = {FAN_DOMAIN: ["fan.bedroom_ceiling_fan"]}

    radar_filter = MMWaveFanFilter(hass, mock_area)
    hass.states.async_set("fan.bedroom_ceiling_fan", STATE_ON)

    radar_id = "binary_sensor.bedroom_radar_occupancy"

    # 1. Target 1 detects fan (2.4m) with 0 motion energy, but Target 2 detects human at 1.2m
    dual_target_state = State(
        radar_id,
        STATE_ON,
        {"target_distance": 2.4, "motion_energy": 0, "target_2_distance": 1.2},
    )
    is_fan, has_human, has_signals = radar_filter.evaluate_radar_signals(
        radar_id, dual_target_state
    )
    assert is_fan is True
    assert has_signals is True
    assert has_human is True
    assert radar_filter.is_fan_interference(radar_id, dual_target_state) is False

    # 2. Target 1 detects fan (2.4m), but unmasked Gate 1 (0.75m - 1.5m) has movement energy
    gate_state = State(
        radar_id,
        STATE_ON,
        {"target_distance": 2.4, "motion_energy": 0, "gate_1_move_energy": 45},
    )
    assert radar_filter.is_fan_interference(radar_id, gate_state) is False

    # 3. Target 1 detects fan (2.4m), but companion motion binary sensor is active
    motion_companion_id = "binary_sensor.bedroom_radar_motion"
    hass.states.async_set(motion_companion_id, STATE_ON)
    fan_only_attr_state = State(
        radar_id,
        STATE_ON,
        {"target_distance": 2.4, "motion_energy": 0},
    )
    assert radar_filter.is_fan_interference(radar_id, fan_only_attr_state) is False
    hass.states.async_set(motion_companion_id, STATE_OFF)

    # 4. Multi-target list (e.g. FP2 / LD2450) with secondary target outside fan zone
    multi_target_list_state = State(
        radar_id,
        STATE_ON,
        {
            "target_distance": 2.4,
            "motion_energy": 0,
            "targets": [{"distance": 2.4}, {"distance": 4.2}],
        },
    )
    assert (
        radar_filter.is_fan_interference(radar_id, multi_target_list_state) is False
    )

    # 5. Dumb binary sensor without secondary signals
    dumb_sensor = "binary_sensor.bedroom_dumb_radar"
    dumb_state = State(dumb_sensor, STATE_ON, {})
    assert radar_filter.has_secondary_signals(dumb_sensor, dumb_state) is False


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

    # 3. While fan is still ON, a person enters! Sensor reports target_2_distance outside fan zone (1.2m)
    hass.states.async_set(
        radar_id,
        STATE_ON,
        {"target_distance": 2.5, "motion_energy": 0, "target_2_distance": 1.2},
    )
    await hass.async_block_till_done()

    if state_sensor is not None:
        state_sensor._update_state()
        await hass.async_block_till_done()

    state_obj = hass.states.get(state_sensor_id)
    assert radar_id not in state_obj.attributes.get(ATTR_FILTERED_FAN_SENSORS, [])
    assert radar_id in state_obj.attributes.get(ATTR_RADAR_FAN_OVERRIDE_SENSORS, [])
    assert radar_id in state_obj.attributes.get("active_sensors", [])
    assert state_obj.state == AreaState.AREA_STATE_OCCUPIED


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


async def test_shower_classifier_and_storm_rejection(hass: HomeAssistant) -> None:
    """Test multi-sensor shower classification and storm front rejection."""
    classifier = ShowerClassifier()

    # 1. Warm shower event: rapid rising trend, positive thermal flux, local delta
    # Record initial baseline state
    classifier.update_temperature(temp=21.0, ts=100.0)
    p_shower = classifier.compute_probability(
        trend=0.035,
        ts=130.0,
        area_humidity=65.0,
        ref_humidity=45.0,
        area_temp=22.5,
        door_closed=True,
        historical_baseline=45.0,
    )
    # Probability should be very high (> 0.90)
    assert p_shower > 0.90

    # 2. Storm front event: humidity rises equally across both areas, temperature drops
    classifier_storm = ShowerClassifier()
    classifier_storm.update_temperature(temp=22.0, ts=100.0)
    p_storm = classifier_storm.compute_probability(
        trend=0.025,
        ts=130.0,
        area_humidity=65.0,
        ref_humidity=65.0,  # delta_meta = 0.0 (both rooms rose equally)
        area_temp=21.0,  # dT/dt <= 0
        door_closed=False,
        historical_baseline=50.0,
    )
    # Storm front probability is strongly penalized (< 0.20)
    assert p_storm < 0.20


async def test_moisture_evacuation_model_fitting(hass: HomeAssistant) -> None:
    """Test exponential decay curve fitting and shutoff prediction."""
    model = MoistureEvacuationModel()
    t_peak = 1000.0
    baseline = 50.0
    delta_shutoff = 3.0  # target shutoff when excess <= 3.0% (humidity 53.0%)

    # Record peak moisture: 70.0% humidity (excess = 20.0%)
    model.record_peak(peak_delta=20.0, peak_ts=t_peak)

    # Samples along downward drying curve: y(t) = 20.0 * exp(-0.015 * t)
    # At t = 30s: y = 20 * exp(-0.45) = 12.75 -> humidity = 62.75
    model.record_sample(
        current_humidity=62.75,
        baseline_humidity=baseline,
        now_ts=t_peak + 30.0,
        delta_shutoff=delta_shutoff,
    )

    # At t = 60s: y = 20 * exp(-0.90) = 8.13 -> humidity = 58.13
    model.record_sample(
        current_humidity=58.13,
        baseline_humidity=baseline,
        now_ts=t_peak + 60.0,
        delta_shutoff=delta_shutoff,
    )

    # At t = 90s: y = 20 * exp(-1.35) = 5.18 -> humidity = 55.18
    fitted_k, est_dry_ts = model.record_sample(
        current_humidity=55.18,
        baseline_humidity=baseline,
        now_ts=t_peak + 90.0,
        delta_shutoff=delta_shutoff,
    )

    # Verify fitted decay rate k is around 0.015
    assert fitted_k is not None
    assert 0.010 <= fitted_k <= 0.020
    assert est_dry_ts is not None
    assert est_dry_ts > t_peak + 90.0

    # Before reaching estimated dry ts: not complete
    assert (
        model.is_evacuation_complete(
            current_humidity=55.18,
            baseline_humidity=baseline,
            now_ts=t_peak + 90.0,
            delta_shutoff=delta_shutoff,
        )
        is False
    )

    # When timestamp passes est_dry_ts and humidity reaches shutoff threshold
    assert (
        model.is_evacuation_complete(
            current_humidity=53.2,
            baseline_humidity=baseline,
            now_ts=est_dry_ts + 5.0,
            delta_shutoff=delta_shutoff,
        )
        is True
    )


async def test_fan_ml_shower_and_trajectory_evacuation_attributes(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_fan: list[MockFan],
    _setup_integration: None,
) -> None:
    """Test that AreaFanGroup computes shower probability and exposes ML attributes."""
    async_mock_service(hass, FAN_DOMAIN, "turn_on")
    async_mock_service(hass, FAN_DOMAIN, "turn_off")
    fan_entity_id = f"{FAN_DOMAIN}.simply_magic_areas_fan_kitchen"

    fan_entity = None
    for entity in hass.data[MODULE_DATA][config_entry.entry_id][
        DATA_AREA_OBJECT
    ].hass.data["fan"].entities:
        if getattr(entity, "entity_id", None) == fan_entity_id:
            fan_entity = entity
            break

    assert fan_entity is not None

    # Setup humidity sensor and stats sensor for kitchen
    humidity_sensor_id = fan_entity.area.simply_magic_entity_id(
        SENSOR_DOMAIN, SensorDeviceClass.HUMIDITY
    )
    trend_sensor_id = "sensor.simply_magic_areas_humidity_statistics_kitchen"
    hass.states.async_set(humidity_sensor_id, "50.0")
    hass.states.async_set(trend_sensor_id, "0.0")
    await hass.async_block_till_done()

    # Baseline learning
    fan_entity._humidity_fan_control()
    await hass.async_block_till_done()

    # Trigger shower: rising humidity (68%) and trend (0.035)
    hass.states.async_set(humidity_sensor_id, "68.0")
    hass.states.async_set(trend_sensor_id, "0.035")
    fan_entity._humidity_fan_control()
    await hass.async_block_till_done()

    fan_state = hass.states.get(fan_entity_id)
    assert fan_state.attributes.get(ATTR_HUMIDITY_ON) is True
    assert fan_state.attributes.get(ATTR_SHOWER_PROBABILITY) is not None
    assert fan_state.attributes.get(ATTR_SHOWER_PROBABILITY) > 0.80

    # Start downward evacuation slope
    hass.states.async_set(trend_sensor_id, "-0.010")
    fan_entity._evacuation_model._fitted_k = 0.0123
    fan_entity._evacuation_model._estimated_dry_ts = 1800.0

    # Evaluate fan control
    fan_entity._humidity_fan_control()
    await hass.async_block_till_done()

    fan_state = hass.states.get(fan_entity_id)
    assert fan_state.attributes.get(ATTR_DRYING_RATE_K) == 0.0123
    assert fan_state.attributes.get(ATTR_ESTIMATED_DRY_TS) == 1800.0
