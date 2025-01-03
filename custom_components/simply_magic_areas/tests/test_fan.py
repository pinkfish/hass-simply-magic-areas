"""Test for the fans."""

import asyncio
import logging

from _pytest.monkeypatch import MonkeyPatch
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN
)
from homeassistant.core import HomeAssistant

from ..const import DOMAIN
from .common import async_mock_service
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
