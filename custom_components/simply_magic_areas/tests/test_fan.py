"""Test for the fans."""

import asyncio
import logging
from datetime import UTC, datetime

import pytest
from _pytest.monkeypatch import MonkeyPatch
from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.area_registry import async_get as async_get_ar
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from ..const import (
    CONF_ENABLED_FEATURES,
    CONF_FEATURE_HUMIDITY,
    CONF_HUMIDITY_DEADBAND,
    CONF_HUMIDITY_DELTA_SHUTOFF,
    CONF_HUMIDITY_DELTA_THRESHOLD,
    CONF_HUMIDITY_MAX_FAN_TIME,
    CONF_HUMIDITY_PRESENCE_GATE_TIME,
    CONF_HUMIDITY_ZERO_WAIT_TIME,
    CONF_REFERENCE_HUMIDITY_SENSOR,
    DOMAIN,
)
from .common import async_mock_service
from .conftest import AREA_NAME, CONFIG_ENTRY_DATA
from .mocks import MockBinarySensor, MockFan, MockSensor

_LOGGER = logging.getLogger(__name__)


@pytest.mark.parametrize(("automated", "state"), [(False, STATE_OFF), (True, STATE_ON)])
async def test_fan_on_off(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_fan: list[MockFan],
    one_motion: list[MockBinarySensor],
    _setup_integration: None,
    automated: bool,
    state: str,
    monkeypatch: MonkeyPatch,
) -> None:
    """Test loading the integration."""
    # Validate the right enties were created.
    control_entity = hass.states.get(
        f"{SWITCH_DOMAIN}.simply_magic_areas_system_control_kitchen"
    )
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )

    calls = async_mock_service(hass, FAN_DOMAIN, "turn_on")
    calls_off = async_mock_service(hass, FAN_DOMAIN, "turn_off")

    assert control_entity is not None
    assert area_binary_sensor is not None
    for fan in one_fan:
        assert not fan.is_on
    assert control_entity.state == STATE_ON
    assert area_binary_sensor.state == "clear"

    # Make the sensor on to make the area occupied and setup automated.
    if automated:
        service_data = {
            ATTR_ENTITY_ID: f"{SWITCH_DOMAIN}.simply_magic_areas_system_control_kitchen",
        }
        await hass.services.async_call(SWITCH_DOMAIN, SERVICE_TURN_ON, service_data)
    else:
        service_data = {
            ATTR_ENTITY_ID: f"{SWITCH_DOMAIN}.simply_magic_areas_system_control_kitchen",
        }
        await hass.services.async_call(SWITCH_DOMAIN, SERVICE_TURN_OFF, service_data)
    one_motion[0].turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(1)

    # Reload the sensors and they should have changed.
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    if automated:
        assert area_binary_sensor.state == "occupied"
    else:
        assert area_binary_sensor.state == "manual"
    assert len(calls) == 0

    # Delay for a while and it should go into extended mode.
    one_motion[0].turn_off()
    await hass.async_block_till_done()
    await asyncio.sleep(4)
    await hass.async_block_till_done()
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    if automated:
        assert area_binary_sensor.state == "extended"
    else:
        assert area_binary_sensor.state == "manual"

    if automated:
        assert len(calls) == 1
        assert calls[0].data == {
            "entity_id": f"{FAN_DOMAIN}.simply_magic_areas_fan_kitchen",
        }
        assert calls[0].service == SERVICE_TURN_ON
        # Turn on the underlying entity.
        one_fan[0].turn_on()
    else:
        assert len(calls) == 0

    await asyncio.sleep(3)
    await hass.async_block_till_done()
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    if automated:
        assert area_binary_sensor.state == "clear"
        assert len(calls_off) == 1
    else:
        assert area_binary_sensor.state == "manual"


async def test_fan_on_off_humidity(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_fan: list[MockFan],
    one_motion: list[MockBinarySensor],
    one_sensor_humidity: list[MockSensor],
    _setup_integration: None,
) -> None:
    """Test loading the integration."""
    # Validate the right enties were created.
    control_entity = hass.states.get(
        f"{SWITCH_DOMAIN}.simply_magic_areas_system_control_kitchen"
    )
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    area_humidity_statistics = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_humidity_statistics_kitchen"
    )

    calls = async_mock_service(hass, FAN_DOMAIN, "turn_on")

    assert control_entity is not None
    assert area_binary_sensor is not None
    assert area_humidity_statistics is not None
    for fan in one_fan:
        assert not fan.is_on
    assert control_entity.state == STATE_ON
    assert area_binary_sensor.state == "clear"
    assert area_humidity_statistics.state == STATE_UNAVAILABLE

    # Make the sensor on to make the area occupied and setup automated.
    service_data = {
        ATTR_ENTITY_ID: f"{SWITCH_DOMAIN}.simply_magic_areas_system_control_kitchen",
    }
    await hass.services.async_call(SWITCH_DOMAIN, SERVICE_TURN_ON, service_data)
    await hass.async_block_till_done()
    hass.states.async_set(
        one_sensor_humidity[0].entity_id,
        str(10.0),
        attributes={"unit_of_measurement": "%"},
    )
    await hass.async_block_till_done()
    hass.states.async_set(
        one_sensor_humidity[0].entity_id,
        str(30.0),
        attributes={"unit_of_measurement": "%"},
    )
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)

    # Reload the sensors and they should have changed.
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    area_humidity_statistics = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_humidity_statistics_kitchen"
    )
    assert float(area_humidity_statistics.state) > 100.0
    assert area_binary_sensor.state == "occupied"
    assert len(calls) == 1
    assert calls[0].data == {
        "entity_id": f"{FAN_DOMAIN}.simply_magic_areas_fan_kitchen",
    }

    # Push events down, should turn on the down trending sensor.
    hass.states.async_set(
        one_sensor_humidity[0].entity_id,
        str(5),
        attributes={"unit_of_measurement": "%"},
    )
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)
    area_humidity_statistics = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_humidity_statistics_kitchen"
    )
    assert float(area_humidity_statistics.state) < -10
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    assert area_binary_sensor.state == "occupied"
    assert len(calls) == 1


async def test_fan_control_disabled(
    hass: HomeAssistant,
    disable_config_entry: MockConfigEntry,
    one_fan: list[MockFan],
    one_motion: list[MockBinarySensor],
    one_sensor_humidity: list[MockSensor],
    _setup_integration_disable_control: None,
) -> None:
    """Test loading the integration."""
    # Validate the right enties were created.
    control_entity = hass.states.get(
        f"{SWITCH_DOMAIN}.simply_magic_areas_system_control_kitchen"
    )
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    area_humidity_statistics = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_humidity_statistics_kitchen"
    )

    calls = async_mock_service(hass, FAN_DOMAIN, "turn_on")

    assert control_entity is not None
    assert area_binary_sensor is not None
    for fan in one_fan:
        assert not fan.is_on
    assert control_entity.state == STATE_ON
    assert area_binary_sensor.state == "clear"
    assert area_humidity_statistics.state == STATE_UNAVAILABLE

    # Make the sensor on to make the area occupied and setup automated.
    service_data = {
        ATTR_ENTITY_ID: f"{SWITCH_DOMAIN}.simply_magic_areas_system_control_kitchen",
    }
    await hass.services.async_call(SWITCH_DOMAIN, SERVICE_TURN_ON, service_data)
    await hass.async_block_till_done()
    hass.states.async_set(
        one_sensor_humidity[0].entity_id,
        str(10.0),
        attributes={"unit_of_measurement": "%"},
    )
    await hass.async_block_till_done()
    hass.states.async_set(
        one_sensor_humidity[0].entity_id,
        str(20.0),
        attributes={"unit_of_measurement": "%"},
    )
    await hass.async_block_till_done()
    hass.states.async_set(
        one_sensor_humidity[0].entity_id,
        str(30.0),
        attributes={"unit_of_measurement": "%"},
    )
    await hass.async_block_till_done()

    # Reload the sensors and they should have changed.
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    area_humidity_statistics = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_humidity_statistics_kitchen"
    )
    assert float(area_humidity_statistics.state) > 1000.0
    assert area_binary_sensor.state == "occupied"
    # Fans should not have changed, since they are disabled.
    assert len(calls) == 0


@pytest.fixture(name="differential_humidity_config_entry")
def mock_differential_humidity_config_entry() -> MockConfigEntry:
    """Fixture for differential humidity configuration."""
    data = dict(CONFIG_ENTRY_DATA)
    data[CONF_ENABLED_FEATURES] = dict(data[CONF_ENABLED_FEATURES])
    data[CONF_ENABLED_FEATURES][CONF_FEATURE_HUMIDITY] = {
        CONF_REFERENCE_HUMIDITY_SENSOR: "sensor.reference_humidity",
        CONF_HUMIDITY_DELTA_THRESHOLD: 10.0,
        CONF_HUMIDITY_DELTA_SHUTOFF: 3.0,
        CONF_HUMIDITY_DEADBAND: 0.001,
        CONF_HUMIDITY_ZERO_WAIT_TIME: 10,
        CONF_HUMIDITY_MAX_FAN_TIME: 60,
        CONF_HUMIDITY_PRESENCE_GATE_TIME: 30,
    }
    return MockConfigEntry(domain=DOMAIN, data=data)


@pytest.fixture(name="_setup_differential_humidity")
async def setup_differential_humidity(
    hass: HomeAssistant,
    differential_humidity_config_entry: MockConfigEntry,
) -> None:
    """Set up the area with differential humidity."""
    registry = async_get_ar(hass)
    registry.async_get_or_create(AREA_NAME)
    differential_humidity_config_entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    yield
    await hass.config_entries.async_unload(differential_humidity_config_entry.entry_id)
    await hass.async_block_till_done()


async def test_fan_differential_humidity_control(
    hass: HomeAssistant,
    differential_humidity_config_entry: MockConfigEntry,
    one_fan: list[MockFan],
    one_sensor_humidity: list[MockSensor],
    _setup_differential_humidity: None,
) -> None:
    """Test differential humidity logic: trigger above delta, ignore below delta, and shut off near baseline."""
    # Set reference humidity entity and initial room humidity to 50%
    hass.states.async_set(
        "sensor.reference_humidity",
        "50.0",
        attributes={"unit_of_measurement": "%"},
    )
    hass.states.async_set(
        one_sensor_humidity[0].entity_id,
        "50.0",
        attributes={"unit_of_measurement": "%"},
    )
    await hass.async_block_till_done()

    calls = async_mock_service(hass, FAN_DOMAIN, "turn_on")

    # Step 1: Rise to 54% (delta = 4%, which is < 10% threshold).
    # Even if derivative spikes, fan should NOT activate.
    hass.states.async_set(
        one_sensor_humidity[0].entity_id,
        "54.0",
        attributes={"unit_of_measurement": "%"},
    )
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)

    assert len(calls) == 0

    # Step 2: Rise to 65% (delta = 15%, which is >= 10% threshold).
    # Fan should activate!
    hass.states.async_set(
        one_sensor_humidity[0].entity_id,
        "65.0",
        attributes={"unit_of_measurement": "%"},
    )
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)

    assert len(calls) == 1
    assert calls[0].data == {
        "entity_id": f"{FAN_DOMAIN}.simply_magic_areas_fan_kitchen",
    }
    one_fan[0].turn_on()

    # Step 3: Room humidity drops down to 52% (delta = 2%, which is <= 3% shutoff threshold).
    # Fan should shut off.
    calls_off = async_mock_service(hass, FAN_DOMAIN, "turn_off")
    hass.states.async_set(
        one_sensor_humidity[0].entity_id,
        "52.0",
        attributes={"unit_of_measurement": "%"},
    )
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)

    assert len(calls_off) == 1


async def test_fan_deadband_flatline(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_fan: list[MockFan],
    one_sensor_humidity: list[MockSensor],
    _setup_integration: None,
) -> None:
    """Test deadband flatline shut-off when rate-of-change hovers near zero."""
    calls = async_mock_service(hass, FAN_DOMAIN, "turn_on")
    calls_off = async_mock_service(hass, FAN_DOMAIN, "turn_off")

    # Initial humidity and shower spike
    hass.states.async_set(
        one_sensor_humidity[0].entity_id,
        "10.0",
        attributes={"unit_of_measurement": "%"},
    )
    await hass.async_block_till_done()
    hass.states.async_set(
        one_sensor_humidity[0].entity_id,
        "30.0",
        attributes={"unit_of_measurement": "%"},
    )
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)

    assert len(calls) == 1
    one_fan[0].turn_on()

    # Find the fan group entity
    fan_group = None
    for entity in hass.data["entity_components"]["fan"].entities:
        if entity.entity_id == f"{FAN_DOMAIN}.simply_magic_areas_fan_kitchen":
            fan_group = entity
            break
    assert fan_group is not None
    assert fan_group._attr_extra_state_attributes.get("humidity_on") is True

    # Simulate flatline derivative within deadband (0.0002 <= 0.001)
    stats_entity_id = f"{SENSOR_DOMAIN}.simply_magic_areas_humidity_statistics_kitchen"
    hass.states.async_set(stats_entity_id, "0.0002")
    await hass.async_block_till_done()

    # Deadband tracking should have recorded the zero timestamp
    assert fan_group._attr_extra_state_attributes.get("humidity_zero_ts") is not None

    # Advance the zero timestamp past the zero wait duration (e.g. 25 minutes ago)
    fan_group._attr_extra_state_attributes["humidity_zero_ts"] = (
        datetime.now(UTC).timestamp() - 1500
    )

    # Next trend update within deadband triggers shut-off
    hass.states.async_set(stats_entity_id, "0.0001")
    await hass.async_block_till_done()

    assert fan_group._attr_extra_state_attributes.get("humidity_on") is False
    assert len(calls_off) == 1


async def test_fan_max_runtime_cutoff(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_fan: list[MockFan],
    one_sensor_humidity: list[MockSensor],
    _setup_integration: None,
) -> None:
    """Test safety cutoff turns off fan when max humidity run duration is exceeded."""
    calls = async_mock_service(hass, FAN_DOMAIN, "turn_on")
    calls_off = async_mock_service(hass, FAN_DOMAIN, "turn_off")

    hass.states.async_set(
        one_sensor_humidity[0].entity_id,
        "10.0",
        attributes={"unit_of_measurement": "%"},
    )
    await hass.async_block_till_done()
    hass.states.async_set(
        one_sensor_humidity[0].entity_id,
        "30.0",
        attributes={"unit_of_measurement": "%"},
    )
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)

    assert len(calls) >= 1
    one_fan[0].turn_on()

    fan_group = None
    for entity in hass.data["entity_components"]["fan"].entities:
        if entity.entity_id == f"{FAN_DOMAIN}.simply_magic_areas_fan_kitchen":
            fan_group = entity
            break
    assert fan_group is not None
    assert fan_group._attr_extra_state_attributes.get("humidity_start_ts") is not None

    # Age start_ts past max runtime (> 3600s)
    fan_group._attr_extra_state_attributes["humidity_start_ts"] = (
        datetime.now(UTC).timestamp() - 4000
    )

    # Trigger a trend event
    stats_entity_id = f"{SENSOR_DOMAIN}.simply_magic_areas_humidity_statistics_kitchen"
    hass.states.async_set(stats_entity_id, "10.0")
    await hass.async_block_till_done()

    # Safety cutoff triggers and fan turns off
    assert fan_group._attr_extra_state_attributes.get("humidity_on") is False
    assert len(calls_off) == 1


async def test_fan_presence_gating(
    hass: HomeAssistant,
    differential_humidity_config_entry: MockConfigEntry,
    one_fan: list[MockFan],
    one_sensor_humidity: list[MockSensor],
    _setup_differential_humidity: None,
) -> None:
    """Test presence gating prevents fan from turning on if area has been clear for too long."""
    fan_group = None
    for entity in hass.data["entity_components"]["fan"].entities:
        if entity.entity_id == f"{FAN_DOMAIN}.simply_magic_areas_fan_kitchen":
            fan_group = entity
            break
    assert fan_group is not None

    # Set baseline reference humidity and room humidity
    hass.states.async_set(
        "sensor.reference_humidity",
        "50.0",
        attributes={"unit_of_measurement": "%"},
    )
    hass.states.async_set(
        one_sensor_humidity[0].entity_id,
        "50.0",
        attributes={"unit_of_measurement": "%"},
    )
    await hass.async_block_till_done()

    # Simulate area being clear for 1 hour (gate window is 30s)
    fan_group._last_occupied_ts = datetime.now(UTC).timestamp() - 3600

    calls = async_mock_service(hass, FAN_DOMAIN, "turn_on")

    # Sudden steep humidity rise
    hass.states.async_set(
        one_sensor_humidity[0].entity_id,
        "80.0",
        attributes={"unit_of_measurement": "%"},
    )
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)

    # Fan should NOT turn on because of presence gating
    assert len(calls) == 0
    assert fan_group._attr_extra_state_attributes.get("humidity_on", False) is False


async def test_extended_state_sensor_change_moves_to_occupied(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_fan: list[MockFan],
    one_motion: list[MockBinarySensor],
    _setup_integration: None,
) -> None:
    """Test that if area state is extended, any sensor change moves it back to occupied."""
    calls_fan_on = async_mock_service(hass, FAN_DOMAIN, SERVICE_TURN_ON)
    calls_fan_off = async_mock_service(hass, FAN_DOMAIN, SERVICE_TURN_OFF)

    area_state_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    assert area_state_sensor is not None
    assert area_state_sensor.state == "clear"

    # 1. Turn on motion -> area occupied
    one_motion[0].turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(1)

    area_state_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    assert area_state_sensor.state == "occupied"

    # 2. Turn off motion and wait for clear_timeout (2s) to transition to extended
    one_motion[0].turn_off()
    await hass.async_block_till_done()
    await asyncio.sleep(4)
    await hass.async_block_till_done()

    area_state_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    assert area_state_sensor.state == "extended"
    # In extended state, exhaust fan turns on
    assert len(calls_fan_on) >= 1
    one_fan[0].turn_on()

    # 3. While in extended state, motion sensor changes (turns ON)
    calls_fan_off.clear()
    one_motion[0].turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(1)

    area_state_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    # MUST move back to occupied!
    assert area_state_sensor.state == "occupied"
    # Fan MUST turn off upon returning to occupied
    assert len(calls_fan_off) >= 1

    # 4. Now turn off motion again, wait 4s to re-enter extended
    calls_fan_on.clear()
    calls_fan_off.clear()
    one_motion[0].turn_off()
    await hass.async_block_till_done()
    await asyncio.sleep(4)
    await hass.async_block_till_done()

    area_state_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    assert area_state_sensor.state == "extended"

    # 5. Simulate momentary sensor toggle (e.g. pulse or state change) while in extended
    one_motion[0].turn_on()
    await hass.async_block_till_done()
    one_motion[0].turn_off()
    await hass.async_block_till_done()
    await asyncio.sleep(0.5)

    area_state_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    # Area moved back to occupied and reset clear countdown
    assert area_state_sensor.state == "occupied"


