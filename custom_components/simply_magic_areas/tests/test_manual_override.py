"""Tests for manual override occupancy-hold logic."""

import asyncio
import logging

from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from ..const import ATTR_MANUAL_CONTROL
from .common import async_mock_service
from .mocks import MockBinarySensor, MockFan

_LOGGER = logging.getLogger(__name__)


async def test_manual_light_turn_off_while_occupied_holds_until_clear(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_light: list[str],
    one_motion: list[MockBinarySensor],
    _setup_integration: None,
) -> None:
    """Test that turning off a light while occupied holds it OFF until area clears."""
    area_state = hass.states.get(f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen")
    assert area_state.state == "clear"

    calls_on = async_mock_service(hass, LIGHT_DOMAIN, "turn_on")
    async_mock_service(hass, LIGHT_DOMAIN, "turn_off")

    # 1. Enter room -> Occupied -> Lights turn ON automatically
    one_motion[0].turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(0.5)

    area_state = hass.states.get(f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen")
    assert area_state.state == "occupied"
    assert len(calls_on) == 1
    calls_on.clear()

    # Underlying light is now ON
    hass.states.async_set(one_light[0], STATE_ON)
    await hass.async_block_till_done()

    # 2. User manually turns off light via wall switch (external change)
    hass.states.async_set(one_light[0], STATE_OFF)
    await hass.async_block_till_done()
    await asyncio.sleep(0.5)

    light_state = hass.states.get(f"{LIGHT_DOMAIN}.simply_magic_areas_light_kitchen")
    assert light_state.state == STATE_OFF
    # Manual override must be active
    assert light_state.attributes.get(ATTR_MANUAL_CONTROL) is True
    # Crucial: Automation must NOT re-assert turn on!
    assert len(calls_on) == 0

    # 3. Motion continues while in room -> Area remains occupied -> Lights MUST stay OFF
    one_motion[0].turn_off()
    await hass.async_block_till_done()
    one_motion[0].turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(0.5)

    area_state = hass.states.get(f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen")
    assert area_state.state == "occupied"
    light_state = hass.states.get(f"{LIGHT_DOMAIN}.simply_magic_areas_light_kitchen")
    assert light_state.state == STATE_OFF
    assert light_state.attributes.get(ATTR_MANUAL_CONTROL) is True
    assert len(calls_on) == 0

    # 4. User leaves room -> Motion off -> Area enters extended then clear
    one_motion[0].turn_off()
    await hass.async_block_till_done()
    await asyncio.sleep(4)  # extended
    await hass.async_block_till_done()
    await asyncio.sleep(3)  # clear
    await hass.async_block_till_done()

    area_state = hass.states.get(f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen")
    assert area_state.state == "clear"

    light_state = hass.states.get(f"{LIGHT_DOMAIN}.simply_magic_areas_light_kitchen")
    # Occupied state change occurred (occupied -> clear) -> Manual override cleared!
    assert light_state.attributes.get(ATTR_MANUAL_CONTROL) is None

    # 5. User re-enters room later -> Area occupied -> Automation resumes and turns lights ON!
    calls_on.clear()
    one_motion[0].turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(0.5)

    area_state = hass.states.get(f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen")
    assert area_state.state == "occupied"
    assert len(calls_on) == 1


async def test_manual_light_turn_on_while_clear_holds_until_occupied(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_light: list[str],
    one_motion: list[MockBinarySensor],
    _setup_integration: None,
) -> None:
    """Test that manually turning on a light while clear holds until occupancy changes."""
    area_state = hass.states.get(f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen")
    assert area_state.state == "clear"

    async_mock_service(hass, LIGHT_DOMAIN, "turn_on")
    calls_off = async_mock_service(hass, LIGHT_DOMAIN, "turn_off")

    # Manually turn on light while area is clear
    hass.states.async_set(one_light[0], STATE_ON)
    await hass.async_block_till_done()
    await asyncio.sleep(0.5)

    light_state = hass.states.get(f"{LIGHT_DOMAIN}.simply_magic_areas_light_kitchen")
    assert light_state.state == STATE_ON
    assert light_state.attributes.get(ATTR_MANUAL_CONTROL) is True
    assert len(calls_off) == 0

    # Person walks into the room -> Area transitions clear -> occupied
    one_motion[0].turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(0.5)

    area_state = hass.states.get(f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen")
    assert area_state.state == "occupied"

    light_state = hass.states.get(f"{LIGHT_DOMAIN}.simply_magic_areas_light_kitchen")
    # Occupancy change reset manual hold
    assert light_state.attributes.get(ATTR_MANUAL_CONTROL) is None

    # Person leaves room -> Area clears -> Lights turn off automatically
    calls_off.clear()
    one_motion[0].turn_off()
    await hass.async_block_till_done()
    await asyncio.sleep(4)
    await hass.async_block_till_done()
    await asyncio.sleep(3)
    await hass.async_block_till_done()

    area_state = hass.states.get(f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen")
    assert area_state.state == "clear"
    assert len(calls_off) >= 1


async def test_manual_fan_turn_off_while_occupied_holds_until_clear(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_fan: list[MockFan],
    one_motion: list[MockBinarySensor],
    _setup_integration: None,
) -> None:
    """Test that manually turning off a fan holds OFF until occupied state changes."""
    calls_on = async_mock_service(hass, FAN_DOMAIN, "turn_on")
    async_mock_service(hass, FAN_DOMAIN, "turn_off")

    # Make area occupied then extended (where fans normally turn on)
    one_motion[0].turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(0.5)
    one_motion[0].turn_off()
    await hass.async_block_till_done()
    await asyncio.sleep(4.5)
    await hass.async_block_till_done()

    area_state = hass.states.get(f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen")
    assert area_state.state == "extended"
    assert len(calls_on) == 1
    calls_on.clear()

    # Underlying fan was commanded on by automation
    one_fan[0].turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(0.5)

    # User manually turns fan OFF (e.g. wall switch)
    one_fan[0].turn_off()
    await hass.async_block_till_done()
    await asyncio.sleep(0.5)

    fan_state = hass.states.get(f"{FAN_DOMAIN}.simply_magic_areas_fan_kitchen")
    assert fan_state.state == STATE_OFF
    assert fan_state.attributes.get(ATTR_MANUAL_CONTROL) is True
    assert len(calls_on) == 0

    # Wait for area to clear -> Occupancy change resets manual hold
    await asyncio.sleep(3.5)
    await hass.async_block_till_done()

    area_state = hass.states.get(f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen")
    assert area_state.state == "clear"

    fan_state = hass.states.get(f"{FAN_DOMAIN}.simply_magic_areas_fan_kitchen")
    assert fan_state.attributes.get(ATTR_MANUAL_CONTROL) is None


async def test_system_control_switch_manual_override(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_light: list[str],
    one_motion: list[MockBinarySensor],
    _setup_integration: None,
) -> None:
    """Test that the system control switch puts area into manual and restores automation."""
    control_switch = f"{SWITCH_DOMAIN}.simply_magic_areas_system_control_kitchen"
    state_sensor = f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"

    assert hass.states.get(control_switch).state == STATE_ON
    assert hass.states.get(state_sensor).state == "clear"

    # Toggle switch OFF -> Area state becomes "manual"
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: control_switch},
    )
    await hass.async_block_till_done()
    await asyncio.sleep(0.5)

    assert hass.states.get(control_switch).state == STATE_OFF
    assert hass.states.get(state_sensor).state == "manual"

    # Motion detected while in manual mode -> No automated light command
    calls_on = async_mock_service(hass, LIGHT_DOMAIN, "turn_on")
    one_motion[0].turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(0.5)

    assert len(calls_on) == 0
    assert hass.states.get(state_sensor).state == "manual"

    # Toggle switch ON -> Area returns to presence automation and turns on lights
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: control_switch},
    )
    await hass.async_block_till_done()
    await asyncio.sleep(0.5)

    assert hass.states.get(control_switch).state == STATE_ON
    assert hass.states.get(state_sensor).state == "occupied"
    assert len(calls_on) == 1
