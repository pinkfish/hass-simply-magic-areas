"""Tests for upstream improvements pulled into Simply Magic Areas."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    CONF_PLATFORM,
    STATE_OFF,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.area_registry import async_get as async_get_ar
from homeassistant.helpers.device_registry import async_get as async_get_dr
from homeassistant.helpers.entity_registry import async_get as async_get_er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from ..base.area_state_sensor import AreaStateSensor
from ..config.area_state import AreaState
from ..const import (
    CONF_AGGREGATES_MIN_ENTITIES,
    CONF_ENABLED_FEATURES,
    CONF_FEATURE_GROUP_CREATION,
    DATA_AREA_OBJECT,
    DOMAIN,
    MODULE_DATA,
)
from .common import setup_test_component_platform
from .conftest import AREA_NAME, CONFIG_ENTRY_DATA
from .mocks import MockBinarySensor, MockSensor

_LOGGER = logging.getLogger(__name__)


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_aggregates_sensor_aqi_unitless(
    hass: HomeAssistant,
) -> None:
    """Test unitless AQI sensors are aggregated properly."""
    registry = async_get_ar(hass)
    registry.async_get_or_create(AREA_NAME)

    # Set up 2 AQI sensors with no unit of measurement
    mock_aqi_1 = MockSensor(
        name="aqi_1",
        unique_id="aqi_1",
        native_value=40,
        device_class=SensorDeviceClass.AQI,
    )
    mock_aqi_2 = MockSensor(
        name="aqi_2",
        unique_id="aqi_2",
        native_value=60,
        device_class=SensorDeviceClass.AQI,
    )
    setup_test_component_platform(hass, SENSOR_DOMAIN, [mock_aqi_1, mock_aqi_2])
    assert await async_setup_component(
        hass, SENSOR_DOMAIN, {SENSOR_DOMAIN: {CONF_PLATFORM: "test"}}
    )
    await hass.async_block_till_done()

    entity_registry = async_get_er(hass)
    entity_registry.async_update_entity("sensor.aqi_1", area_id=AREA_NAME)
    entity_registry.async_update_entity("sensor.aqi_2", area_id=AREA_NAME)

    data = dict(CONFIG_ENTRY_DATA)
    data[CONF_ENABLED_FEATURES] = {
        **data.get(CONF_ENABLED_FEATURES, {}),
        CONF_FEATURE_GROUP_CREATION: {
            CONF_AGGREGATES_MIN_ENTITIES: 2,
        },
    }
    config_entry = MockConfigEntry(domain=DOMAIN, data=data)
    config_entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    aggregate_entity_id = f"{SENSOR_DOMAIN}.simply_magic_areas_aqi_{AREA_NAME}"
    state = hass.states.get(aggregate_entity_id)
    assert state is not None
    assert round(float(state.state)) == 50
    assert state.attributes.get("unit_of_measurement") is None
    assert state.attributes.get("state_class") == SensorStateClass.MEASUREMENT


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_aggregates_sensor_gas_total_increasing(
    hass: HomeAssistant,
) -> None:
    """Test gas aggregate sensors use TOTAL_INCREASING state class."""
    registry = async_get_ar(hass)
    registry.async_get_or_create(AREA_NAME)

    mock_gas_1 = MockSensor(
        name="gas_1",
        unique_id="gas_1",
        native_value=12.5,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        device_class=SensorDeviceClass.GAS,
    )
    mock_gas_2 = MockSensor(
        name="gas_2",
        unique_id="gas_2",
        native_value=7.5,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        device_class=SensorDeviceClass.GAS,
    )
    setup_test_component_platform(hass, SENSOR_DOMAIN, [mock_gas_1, mock_gas_2])
    assert await async_setup_component(
        hass, SENSOR_DOMAIN, {SENSOR_DOMAIN: {CONF_PLATFORM: "test"}}
    )
    await hass.async_block_till_done()

    entity_registry = async_get_er(hass)
    entity_registry.async_update_entity("sensor.gas_1", area_id=AREA_NAME)
    entity_registry.async_update_entity("sensor.gas_2", area_id=AREA_NAME)

    data = dict(CONFIG_ENTRY_DATA)
    data[CONF_ENABLED_FEATURES] = {
        **data.get(CONF_ENABLED_FEATURES, {}),
        CONF_FEATURE_GROUP_CREATION: {
            CONF_AGGREGATES_MIN_ENTITIES: 2,
        },
    }
    config_entry = MockConfigEntry(domain=DOMAIN, data=data)
    config_entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    aggregate_entity_id = f"{SENSOR_DOMAIN}.simply_magic_areas_gas_{AREA_NAME}"
    state = hass.states.get(aggregate_entity_id)
    assert state is not None
    assert round(float(state.state), 1) == 20.0
    assert state.attributes.get("state_class") == SensorStateClass.TOTAL_INCREASING


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_device_registry_update_reloads_area(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    _setup_integration,
) -> None:
    """Test moving a device in device registry triggers area update."""
    device_registry = async_get_dr(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "test_device_move")},
        name="Test Move Device",
    )
    await hass.async_block_till_done()

    old_ts = config_entry.data.get("entity_ts")

    # Update device's area
    device_registry.async_update_device(device.id, area_id=AREA_NAME)
    await hass.async_block_till_done()

    new_ts = config_entry.data.get("entity_ts")
    assert new_ts is not None
    assert new_ts != old_ts


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_stuck_occupied_self_healing_consistency(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_motion: list[MockBinarySensor],
    _setup_integration,
) -> None:
    """Test self-healing consistency check when an area is stuck occupied with no sensors."""
    area = hass.data[MODULE_DATA][config_entry.entry_id][DATA_AREA_OBJECT]
    state_sensor_id = f"{SENSOR_DOMAIN}.simply_magic_areas_state_{AREA_NAME}"

    # Motion sensor is OFF
    hass.states.async_set(one_motion[0].entity_id, STATE_OFF)
    await hass.async_block_till_done()

    # Find the AreaStateSensor entity object
    state_sensor: AreaStateSensor | None = None
    for entity in hass.data["entity_components"]["sensor"].entities:
        if isinstance(entity, AreaStateSensor):
            state_sensor = entity
            break

    assert state_sensor is not None

    # Simulate an edge case where area is marked OCCUPIED, but no timeout pending and sensors are off
    area.state = AreaState.AREA_STATE_OCCUPIED
    state_sensor._attr_native_value = AreaState.AREA_STATE_OCCUPIED
    state_sensor._remove_clear_timeout()
    state_sensor._remove_extended_timeout()

    # Set last_off_time to well past clear timeout
    state_sensor._last_off_time = datetime.now(UTC) - timedelta(seconds=120)

    # Calling _update_state should trigger self-healing consistency validation
    state_sensor._update_state()
    await hass.async_block_till_done()

    # The inconsistency must be detected and resolved to CLEAR
    assert area.state == AreaState.AREA_STATE_CLEAR
    assert state_sensor._attr_native_value == AreaState.AREA_STATE_CLEAR
    current_state = hass.states.get(state_sensor_id)
    assert current_state is not None
    assert current_state.state == AreaState.AREA_STATE_CLEAR
