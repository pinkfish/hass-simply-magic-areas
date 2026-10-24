"""Tests for exterior nighttime lighting control, motion reactivity, and arrival triggers."""

import asyncio
from datetime import UTC, datetime, timedelta

from homeassistant.components.binary_sensor import (
    DOMAIN as BINARY_SENSOR_DOMAIN,
)
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
)
from homeassistant.components.light import (
    ATTR_SUPPORTED_COLOR_MODES,
    ColorMode,
)
from homeassistant.components.light import (
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.const import (
    CONF_PLATFORM,
    STATE_HOME,
    STATE_NOT_HOME,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.area_registry import async_get as async_get_ar
from homeassistant.helpers.entity_registry import async_get as async_get_er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
)

from ..const import (
    AREA_TYPE_EXTERIOR,
    ATTR_EXTERIOR_LIGHTING_ACTIVE,
    ATTR_EXTERIOR_TIMER_TS,
    ATTR_EXTERIOR_TRIGGER_REASON,
    CAMERA_DOMAIN,
    CONF_CLEAR_TIMEOUT,
    CONF_ENABLED_FEATURES,
    CONF_EXTENDED_TIMEOUT,
    CONF_EXTERIOR_ARRIVAL_TRIGGER,
    CONF_EXTERIOR_LIGHTING_TIMEOUT,
    CONF_EXTERIOR_SUNSET_TRIGGER,
    CONF_FAN_CONTROL,
    CONF_FEATURE_ADVANCED_LIGHT_GROUPS,
    CONF_ID,
    CONF_INCLUDE_ENTITIES,
    CONF_LIGHT_CONTROL,
    CONF_NAME,
    CONF_ON_STATES,
    CONF_PRESENCE_DEVICE_PLATFORMS,
    CONF_PRESENCE_SENSOR_DEVICE_CLASS,
    CONF_TYPE,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
)
from .common import async_mock_service, setup_test_component_platform
from .mocks import MockBinarySensor


async def setup_exterior_area(
    hass: HomeAssistant,
    area_name: str = "patio",
    timeout: int = 7200,
    sunset_trigger: bool = True,
    arrival_trigger: bool = True,
) -> tuple[MockBinarySensor, MockConfigEntry]:
    """Set up an exterior area with a motion sensor and light."""
    motion_sensor = MockBinarySensor(
        name=f"{area_name}_motion",
        is_on=False,
        device_class=BinarySensorDeviceClass.MOTION,
        unique_id=f"{area_name}_motion",
    )
    setup_test_component_platform(hass, BINARY_SENSOR_DOMAIN, [motion_sensor])
    assert await async_setup_component(
        hass, BINARY_SENSOR_DOMAIN, {BINARY_SENSOR_DOMAIN: {CONF_PLATFORM: "test"}}
    )
    await hass.async_block_till_done()

    entity_registry = async_get_er(hass)
    entity_registry.async_get_or_create(LIGHT_DOMAIN, "test", f"{area_name}_light")
    entity_registry.async_update_entity(
        f"light.test_{area_name}_light", area_id=area_name
    )
    entity_registry.async_update_entity(
        f"binary_sensor.{area_name}_motion", area_id=area_name
    )
    hass.states.async_set(
        f"light.test_{area_name}_light",
        STATE_OFF,
        {ATTR_SUPPORTED_COLOR_MODES: [ColorMode.HS]},
    )

    ar = async_get_ar(hass)
    ar.async_get_or_create(area_name)

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_NAME: area_name,
            CONF_ID: area_name,
            CONF_CLEAR_TIMEOUT: 5,
            CONF_EXTENDED_TIMEOUT: 5,
            CONF_TYPE: AREA_TYPE_EXTERIOR,
            CONF_LIGHT_CONTROL: True,
            CONF_FAN_CONTROL: False,
            CONF_ENABLED_FEATURES: {
                CONF_FEATURE_ADVANCED_LIGHT_GROUPS: {
                    CONF_INCLUDE_ENTITIES: [],
                    CONF_ON_STATES: [STATE_ON],
                    CONF_UPDATE_INTERVAL: 60,
                    CONF_PRESENCE_DEVICE_PLATFORMS: [BINARY_SENSOR_DOMAIN],
                    CONF_PRESENCE_SENSOR_DEVICE_CLASS: [BinarySensorDeviceClass.MOTION],
                    CONF_EXTERIOR_LIGHTING_TIMEOUT: timeout,
                    CONF_EXTERIOR_SUNSET_TRIGGER: sunset_trigger,
                    CONF_EXTERIOR_ARRIVAL_TRIGGER: arrival_trigger,
                }
            },
        },
    )
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    return motion_sensor, entry


async def test_exterior_lighting_sunset_trigger(hass: HomeAssistant) -> None:
    """Test exterior lights turn on at sunset and initialize 2-hour stay-on clock."""
    # Start during daytime
    hass.states.async_set("sun.sun", "above_horizon", {"elevation": 20.0})

    _motion_sensor, _entry = await setup_exterior_area(hass, "patio")
    calls = async_mock_service(hass, LIGHT_DOMAIN, "turn_on")

    # Sunset occurs
    hass.states.async_set("sun.sun", "below_horizon", {"elevation": -2.0})
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)

    light_state = hass.states.get("light.simply_magic_areas_light_patio")
    assert light_state is not None
    assert light_state.attributes.get(ATTR_EXTERIOR_LIGHTING_ACTIVE) is True
    assert light_state.attributes.get(ATTR_EXTERIOR_TRIGGER_REASON) == "sunset"
    timer_ts = light_state.attributes.get(ATTR_EXTERIOR_TIMER_TS)
    assert timer_ts is not None
    now_ts = datetime.now(UTC).timestamp()
    assert abs((timer_ts - now_ts) - 7200) < 10
    assert len(calls) >= 1


async def test_exterior_lighting_motion_restart_and_stay_on_after_clear(
    hass: HomeAssistant,
) -> None:
    """Test exterior motion restarts countdown and prevents lights shutting off when area clears."""
    # Start at night
    hass.states.async_set("sun.sun", "below_horizon", {"elevation": -10.0})

    motion_sensor, _entry = await setup_exterior_area(hass, "patio")
    light_entity = "light.simply_magic_areas_light_patio"

    # Simulate occupant moving outside at night
    motion_sensor.turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)

    light_state = hass.states.get(light_entity)
    assert light_state is not None
    assert light_state.attributes.get(ATTR_EXTERIOR_LIGHTING_ACTIVE) is True
    assert light_state.attributes.get(ATTR_EXTERIOR_TRIGGER_REASON) == "motion"

    first_timer_ts = light_state.attributes.get(ATTR_EXTERIOR_TIMER_TS)

    # Fast forward 5 seconds and motion clears
    await asyncio.sleep(0.5)
    motion_sensor.turn_off()
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)

    # When motion ceases at night, lights MUST STAY ON and timer restarts from clearance
    light_state_after_clear = hass.states.get(light_entity)
    assert light_state_after_clear.attributes.get(ATTR_EXTERIOR_LIGHTING_ACTIVE) is True
    assert light_state_after_clear.attributes.get(ATTR_EXTERIOR_TRIGGER_REASON) == "motion"
    second_timer_ts = light_state_after_clear.attributes.get(ATTR_EXTERIOR_TIMER_TS)
    assert second_timer_ts >= first_timer_ts


async def test_exterior_lighting_camera_motion_restart(hass: HomeAssistant) -> None:
    """Test camera activity in the exterior area restarts countdown clock."""
    hass.states.async_set("sun.sun", "below_horizon", {"elevation": -15.0})

    _motion_sensor, _entry = await setup_exterior_area(hass, "front_porch")
    light_entity = "light.simply_magic_areas_light_front_porch"

    # Assign camera entity to front porch area
    entity_registry = async_get_er(hass)
    entity_registry.async_get_or_create(CAMERA_DOMAIN, "test", "porch_cam")
    entity_registry.async_update_entity(
        "camera.test_porch_cam", area_id="front_porch"
    )
    hass.states.async_set("camera.test_porch_cam", "idle", {"motion_detected": False})
    await hass.async_block_till_done()

    # Camera detects motion
    hass.states.async_set(
        "camera.test_porch_cam", "recording", {"motion_detected": True}
    )
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)

    light_state = hass.states.get(light_entity)
    assert light_state is not None
    assert light_state.attributes.get(ATTR_EXTERIOR_LIGHTING_ACTIVE) is True
    assert light_state.attributes.get(ATTR_EXTERIOR_TRIGGER_REASON) == "motion"


async def test_exterior_lighting_resident_arrival(hass: HomeAssistant) -> None:
    """Test person arriving home at night triggers exterior illumination."""
    hass.states.async_set("sun.sun", "below_horizon", {"elevation": -20.0})
    hass.states.async_set("person.alice", STATE_NOT_HOME)

    _motion_sensor, _entry = await setup_exterior_area(hass, "driveway")
    light_entity = "light.simply_magic_areas_light_driveway"

    # Alice arrives home
    hass.states.async_set("person.alice", STATE_HOME)
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)

    light_state = hass.states.get(light_entity)
    assert light_state is not None
    assert light_state.attributes.get(ATTR_EXTERIOR_LIGHTING_ACTIVE) is True
    assert light_state.attributes.get(ATTR_EXTERIOR_TRIGGER_REASON) == "arrival"


async def test_exterior_lighting_daytime_suppression(hass: HomeAssistant) -> None:
    """Test exterior lights remain off during daytime despite motion or arrivals."""
    hass.states.async_set("sun.sun", "above_horizon", {"elevation": 45.0})
    hass.states.async_set("person.bob", STATE_NOT_HOME)

    motion_sensor, _entry = await setup_exterior_area(hass, "backyard")
    calls = async_mock_service(hass, LIGHT_DOMAIN, "turn_on")

    # Daytime motion
    motion_sensor.turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)

    # Bob arrives home during daytime
    hass.states.async_set("person.bob", STATE_HOME)
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)

    light_state = hass.states.get("light.simply_magic_areas_light_backyard")
    assert light_state is not None
    assert light_state.attributes.get(ATTR_EXTERIOR_LIGHTING_ACTIVE) is False
    assert len(calls) == 0


async def test_exterior_lighting_timer_expiration(hass: HomeAssistant) -> None:
    """Test timer expiration turns off lights when no motion is active."""
    hass.states.async_set("sun.sun", "below_horizon", {"elevation": -30.0})

    # Set timeout to 10 seconds for testing
    _motion_sensor, _entry = await setup_exterior_area(hass, "patio", timeout=10)
    light_entity = "light.simply_magic_areas_light_patio"
    turn_off_calls = async_mock_service(hass, LIGHT_DOMAIN, "turn_off")

    # Trigger via sunset
    hass.states.async_set("sun.sun", "above_horizon", {"elevation": 1.0})
    await hass.async_block_till_done()
    hass.states.async_set("sun.sun", "below_horizon", {"elevation": -1.0})
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)

    light_state = hass.states.get(light_entity)
    assert light_state.attributes.get(ATTR_EXTERIOR_LIGHTING_ACTIVE) is True

    # Advance time past 10 seconds
    future = datetime.now(UTC) + timedelta(seconds=12)
    async_fire_time_changed(hass, future)
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)

    light_state_after = hass.states.get(light_entity)
    assert light_state_after.attributes.get(ATTR_EXTERIOR_LIGHTING_ACTIVE) is False
    assert len(turn_off_calls) >= 1


async def test_exterior_lighting_sunrise_clearance(hass: HomeAssistant) -> None:
    """Test sunrise immediately clears active timer and shuts off exterior lights."""
    hass.states.async_set("sun.sun", "below_horizon", {"elevation": -5.0})

    _motion_sensor, _entry = await setup_exterior_area(hass, "patio")
    light_entity = "light.simply_magic_areas_light_patio"
    turn_off_calls = async_mock_service(hass, LIGHT_DOMAIN, "turn_off")

    # Person arrives home at night -> lights turn on
    hass.states.async_set("person.carol", STATE_NOT_HOME)
    await hass.async_block_till_done()
    hass.states.async_set("person.carol", STATE_HOME)
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)

    assert (
        hass.states.get(light_entity).attributes.get(ATTR_EXTERIOR_LIGHTING_ACTIVE)
        is True
    )

    # Sunrise occurs
    hass.states.async_set("sun.sun", "above_horizon", {"elevation": 1.0})
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)

    light_state_sunrise = hass.states.get(light_entity)
    assert (
        light_state_sunrise.attributes.get(ATTR_EXTERIOR_LIGHTING_ACTIVE) is False
    )
    assert len(turn_off_calls) >= 1


async def test_exterior_lighting_state_restoration(hass: HomeAssistant) -> None:
    """Test exterior lighting active state and countdown are restored after HA restart."""
    hass.states.async_set("sun.sun", "below_horizon", {"elevation": -10.0})

    target_ts = datetime.now(UTC).timestamp() + 3600
    mock_restore_cache(
        hass,
        [
            State(
                "light.simply_magic_areas_light_patio",
                STATE_ON,
                {
                    ATTR_EXTERIOR_LIGHTING_ACTIVE: True,
                    ATTR_EXTERIOR_TIMER_TS: target_ts,
                    ATTR_EXTERIOR_TRIGGER_REASON: "sunset",
                },
            )
        ],
    )

    _motion_sensor, _entry = await setup_exterior_area(hass, "patio")
    light_entity = "light.simply_magic_areas_light_patio"

    light_state = hass.states.get(light_entity)
    assert light_state is not None
    assert light_state.attributes.get(ATTR_EXTERIOR_LIGHTING_ACTIVE) is True
    assert light_state.attributes.get(ATTR_EXTERIOR_TRIGGER_REASON) == "sunset"
    assert light_state.attributes.get(ATTR_EXTERIOR_TIMER_TS) == target_ts

