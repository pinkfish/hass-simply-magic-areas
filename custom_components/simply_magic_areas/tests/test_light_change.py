"""Test for handling lights in the various modes."""

import asyncio
import logging

import pytest
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    LIGHT_LUX,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .common import async_mock_service
from .mocks import MockBinarySensor, MockSensor

_LOGGER = logging.getLogger(__name__)


@pytest.mark.parametrize(("automated", "state"), [(False, STATE_OFF), (True, STATE_ON)])
async def test_light_on_off(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_light: list[str],
    one_motion: list[MockBinarySensor],
    _setup_integration: None,
    automated: bool,
    state: str,
) -> None:
    """Test loading the integration."""
    # Validate the right enties were created.
    control_entity = hass.states.get(
        f"{SWITCH_DOMAIN}.simply_magic_areas_system_control_kitchen"
    )
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )

    calls = async_mock_service(hass, LIGHT_DOMAIN, "turn_on")

    assert control_entity is not None
    assert area_binary_sensor is not None
    for light in one_light:
        e = hass.states.get(light)
        assert e.state == STATE_OFF
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
    if automated:
        assert len(calls) == 1
        assert calls[0].data == {
            "entity_id": f"{LIGHT_DOMAIN}.simply_magic_areas_light_kitchen",
            "brightness": 255,
        }
        assert calls[0].service == SERVICE_TURN_ON
    else:
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
    await asyncio.sleep(3)
    await hass.async_block_till_done()
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    if automated:
        assert area_binary_sensor.state == "clear"
    else:
        assert area_binary_sensor.state == "manual"


async def test_light_entity_change(
    hass: HomeAssistant,
    config_entry_entities: MockConfigEntry,
    one_light: list[str],
    one_motion: list[MockBinarySensor],
    _setup_integration_entities: None,
) -> None:
    """Test loading the integration."""
    assert config_entry_entities.state is ConfigEntryState.LOADED

    # Validate the right enties were created.
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    service_data = {
        ATTR_ENTITY_ID: f"{SWITCH_DOMAIN}.simply_magic_areas_system_control_kitchen",
    }
    await hass.services.async_call(SWITCH_DOMAIN, SERVICE_TURN_ON, service_data)

    calls = async_mock_service(hass, LIGHT_DOMAIN, "turn_on")
    await hass.async_block_till_done()

    # Reload the sensors and they should have changed.
    one_motion[0].turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(1)
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    assert area_binary_sensor.state == "occupied"
    assert len(calls) == 1
    assert calls[0].data == {
        "entity_id": f"{LIGHT_DOMAIN}.simply_magic_areas_light_kitchen",
        "brightness": 255,
    }
    assert calls[0].service == SERVICE_TURN_ON
    await hass.async_block_till_done()
    await asyncio.sleep(1)

    # Set the sleep entity on.
    one_motion[1].turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(1)
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    assert area_binary_sensor.state == "sleep"

    # Set the bright entity on.
    one_motion[1].turn_off()
    one_motion[2].turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(1)
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    assert area_binary_sensor.state == "bright"

    # Set the accented entity on.
    one_motion[2].turn_off()
    one_motion[3].turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(1)
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    assert area_binary_sensor.state == "accented"


@pytest.mark.parametrize(
    ("luminesnce", "brightness"), [(0.0, 255), (200.0, 0), (175.0, 63), (300.0, 0)]
)
async def test_light_on_off_with_light_sensor(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_light: list[str],
    one_motion: list[MockBinarySensor],
    one_sensor_light: list[MockSensor],
    _setup_integration: None,
    luminesnce: float,
    brightness: int,
) -> None:
    """Test loading the integration."""
    service_data = {
        ATTR_ENTITY_ID: f"{SWITCH_DOMAIN}.simply_magic_areas_system_control_kitchen",
    }
    await hass.services.async_call(SWITCH_DOMAIN, SERVICE_TURN_OFF, service_data)
    await hass.async_block_till_done()
    await asyncio.sleep(1)

    # Validate the right enties were created.
    control_entity = hass.states.get(
        f"{SWITCH_DOMAIN}.simply_magic_areas_system_control_kitchen"
    )
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    light_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_illuminance_kitchen"
    )

    calls = async_mock_service(hass, LIGHT_DOMAIN, "turn_on")
    off_calls = async_mock_service(hass, LIGHT_DOMAIN, "turn_off")

    assert control_entity is not None
    assert area_binary_sensor is not None
    assert light_sensor is not None
    for light in one_light:
        e = hass.states.get(light)
        assert e.state == STATE_OFF
    assert control_entity.state == STATE_OFF
    assert area_binary_sensor.state == "manual"

    # Make the sensor on to make the area occupied and setup automated, leave the light low to get the brightness correct.
    service_data = {
        ATTR_ENTITY_ID: f"{SWITCH_DOMAIN}.simply_magic_areas_system_control_kitchen",
    }
    await hass.services.async_call(SWITCH_DOMAIN, SERVICE_TURN_ON, service_data)
    one_motion[0].turn_on()
    hass.states.async_set(
        one_sensor_light[0].entity_id, luminesnce, {"unit_of_measurement": LIGHT_LUX}
    )
    await hass.async_block_till_done()
    await asyncio.sleep(1)

    # Reload the sensors and they should have changed.
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    assert area_binary_sensor.state == "occupied"
    assert len(off_calls) == 0
    if brightness != 0:
        assert len(calls) == 1
        assert calls[0].data == {
            "entity_id": f"{LIGHT_DOMAIN}.simply_magic_areas_light_kitchen",
            "brightness": brightness,
        }
        assert calls[0].service == SERVICE_TURN_ON
        # Turn on the underlying entity.
        hass.states.async_set(
            one_light[0],
            STATE_ON,
        )


async def test_light_disabled(
    hass: HomeAssistant,
    disable_config_entry: MockConfigEntry,
    one_light: list[str],
    one_motion: list[MockBinarySensor],
    _setup_integration_disable_control: None,
) -> None:
    """Test loading the integration."""
    assert disable_config_entry.state is ConfigEntryState.LOADED

    # Validate the right enties were created.
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    service_data = {
        ATTR_ENTITY_ID: f"{SWITCH_DOMAIN}.simply_magic_areas_system_control_kitchen",
    }
    await hass.services.async_call(SWITCH_DOMAIN, SERVICE_TURN_ON, service_data)

    calls = async_mock_service(hass, LIGHT_DOMAIN, "turn_on")
    await hass.async_block_till_done()

    # Reload the sensors and they should have changed.
    one_motion[0].turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(1)
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    assert area_binary_sensor.state == "occupied"
    assert len(calls) == 0


async def test_light_on_off_with_mqtt_room(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_light: list[str],
    one_motion: list[MockBinarySensor],
    _setup_integration: None,
    one_mqtt_room_sensor: list[MockSensor],
) -> None:
    """Test loading the integration."""
    await hass.async_block_till_done()
    # Validate the right enties were created.
    control_entity = hass.states.get(
        f"{SWITCH_DOMAIN}.simply_magic_areas_system_control_kitchen"
    )
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )

    assert control_entity is not None
    assert area_binary_sensor is not None
    assert area_binary_sensor.state == "clear"

    # Make the sensor on to make the area occupied and setup automated, leave the light low to get the brightness correct.
    # hass.states.async_set(one_mqtt_room_sensor[0].entity_id, "kitchen")
    await hass.async_block_till_done()
    await asyncio.sleep(1)

    # Reload the sensors and they should have changed.
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    assert area_binary_sensor.state == "clear"


async def test_substate_transition_lights_off_vs_on(
    hass: HomeAssistant,
    config_entry_entities: MockConfigEntry,
    one_light: list[str],
    one_motion: list[MockBinarySensor],
    _setup_integration_entities: None,
) -> None:
    """Test that transitioning between substates when lights are off does not turn them on, but updates when on."""
    # Ensure system control is on
    service_data = {
        ATTR_ENTITY_ID: f"{SWITCH_DOMAIN}.simply_magic_areas_system_control_kitchen",
    }
    await hass.services.async_call(SWITCH_DOMAIN, SERVICE_TURN_ON, service_data)
    await hass.async_block_till_done()

    # Ensure motion sensor is off initially
    one_motion[0].turn_off()
    one_motion[1].turn_off()
    one_motion[2].turn_off()
    one_motion[3].turn_off()
    await hass.async_block_till_done()
    await asyncio.sleep(1)

    area_state_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    assert area_state_sensor.state == "clear"

    calls = async_mock_service(hass, LIGHT_DOMAIN, "turn_on")

    # Transition bright -> sleep while lights are off/clear: lights must not turn on
    one_motion[2].turn_on()  # bright
    await hass.async_block_till_done()
    await asyncio.sleep(0.5)

    one_motion[2].turn_off()
    one_motion[1].turn_on()  # sleep
    await hass.async_block_till_done()
    await asyncio.sleep(0.5)

    # No light turn_on calls should have occurred because lights were not on
    assert len(calls) == 0

    # Now someone enters the room -> occupied in sleep state
    one_motion[0].turn_on()  # motion on
    await hass.async_block_till_done()
    await asyncio.sleep(1)

    area_state_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    assert area_state_sensor.state == "sleep"
    assert len(calls) == 1
    # Sleep mode sets dim_level 30% -> 76 brightness
    assert calls[-1].data["brightness"] == 76

    # Now while lights are on, change from sleep to bright
    one_motion[1].turn_off()  # sleep off
    one_motion[2].turn_on()   # bright on
    await hass.async_block_till_done()
    await asyncio.sleep(1)

    area_state_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    assert area_state_sensor.state == "bright"
    # Lights update to bright values (dim level 0% -> turns off)
    assert len(calls) >= 1


async def test_rapid_unoccupied_to_occupied_no_race_condition(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_light: list[str],
    one_motion: list[MockBinarySensor],
    _setup_integration: None,
) -> None:
    """Test that rapid unoccupied -> occupied transition does not leave lights turned off."""
    calls_on = async_mock_service(hass, LIGHT_DOMAIN, "turn_on")
    calls_off = async_mock_service(hass, LIGHT_DOMAIN, "turn_off")

    area_state_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    assert area_state_sensor.state == "clear"

    # Simulate presence sensor pulsing off then immediately on
    one_motion[0].turn_off()
    await hass.async_block_till_done()
    one_motion[0].turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(1)

    area_state_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    assert area_state_sensor.state == "occupied"
    # Lights MUST be commanded ON and not ended up turned off
    assert len(calls_on) >= 1
    assert calls_on[-1].data == {
        "entity_id": f"{LIGHT_DOMAIN}.simply_magic_areas_light_kitchen",
        "brightness": 255,
    }

    # Now simulate rapid off -> on while in occupied
    one_motion[0].turn_off()
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)
    one_motion[0].turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(1)

    area_state_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    assert area_state_sensor.state == "occupied"
    # No turn_off call should have been triggered to extinguish the lights
    assert len(calls_off) == 0
    # The last dispatched command was to turn ON the lights
    assert calls_on[-1].service == SERVICE_TURN_ON

