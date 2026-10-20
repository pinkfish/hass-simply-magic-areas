"""Test direct lux evaluation, hysteresis, and reference area brightness."""

import asyncio
import logging

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
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import CONF_PLATFORM, LIGHT_LUX, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.area_registry import async_get as async_get_ar
from homeassistant.helpers.entity_registry import async_get as async_get_er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from ..const import (
    AREA_TYPE_INTERIOR,
    CONF_BRIGHT_LUX_HYSTERESIS,
    CONF_BRIGHT_LUX_THRESHOLD,
    CONF_CLEAR_TIMEOUT,
    CONF_ENABLED_FEATURES,
    CONF_EXTENDED_TIMEOUT,
    CONF_FAN_CONTROL,
    CONF_FEATURE_ADVANCED_LIGHT_GROUPS,
    CONF_ID,
    CONF_INCLUDE_ENTITIES,
    CONF_LIGHT_CONTROL,
    CONF_MAX_BRIGHTNESS_LEVEL,
    CONF_MIN_BRIGHTNESS_LEVEL,
    CONF_NAME,
    CONF_ON_STATES,
    CONF_PRESENCE_DEVICE_PLATFORMS,
    CONF_PRESENCE_SENSOR_DEVICE_CLASS,
    CONF_REFERENCE_BRIGHT_AREA,
    CONF_REFERENCE_ILLUMINANCE_SENSOR,
    CONF_TYPE,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
)
from .common import async_mock_service, setup_test_component_platform
from .mocks import MockBinarySensor, MockSensor

_LOGGER = logging.getLogger(__name__)


async def test_direct_lux_threshold_and_hysteresis(
    hass: HomeAssistant,
) -> None:
    """Test direct lux threshold evaluation and hysteresis."""
    area_name = "kitchen"
    motion_sensor = MockBinarySensor(
        name="kitchen_motion",
        is_on=False,
        device_class=BinarySensorDeviceClass.MOTION,
        unique_id="kitchen_motion",
    )
    lux_sensor = MockSensor(
        name="kitchen_illuminance",
        device_class=SensorDeviceClass.ILLUMINANCE,
        unique_id="kitchen_illuminance",
        native_unit_of_measurement=LIGHT_LUX,
        native_value=200,
    )

    setup_test_component_platform(hass, BINARY_SENSOR_DOMAIN, [motion_sensor])
    assert await async_setup_component(
        hass, BINARY_SENSOR_DOMAIN, {BINARY_SENSOR_DOMAIN: {CONF_PLATFORM: "test"}}
    )
    setup_test_component_platform(hass, SENSOR_DOMAIN, [lux_sensor])
    assert await async_setup_component(
        hass, SENSOR_DOMAIN, {SENSOR_DOMAIN: {CONF_PLATFORM: "test"}}
    )
    await hass.async_block_till_done()

    entity_registry = async_get_er(hass)
    entity_registry.async_update_entity(
        "binary_sensor.kitchen_motion", area_id=area_name
    )
    entity_registry.async_update_entity(
        "sensor.kitchen_illuminance", area_id=area_name
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
            CONF_TYPE: AREA_TYPE_INTERIOR,
            CONF_LIGHT_CONTROL: False,
            CONF_FAN_CONTROL: False,
            CONF_BRIGHT_LUX_THRESHOLD: 300,
            CONF_BRIGHT_LUX_HYSTERESIS: 25,
            CONF_ENABLED_FEATURES: {
                CONF_FEATURE_ADVANCED_LIGHT_GROUPS: {
                    CONF_INCLUDE_ENTITIES: [],
                    CONF_ON_STATES: [STATE_ON],
                    CONF_UPDATE_INTERVAL: 60,
                    CONF_PRESENCE_DEVICE_PLATFORMS: [BINARY_SENSOR_DOMAIN],
                    CONF_PRESENCE_SENSOR_DEVICE_CLASS: [BinarySensorDeviceClass.MOTION],
                }
            },
        },
    )
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    state_sensor_id = f"{SENSOR_DOMAIN}.simply_magic_areas_state_{area_name}"
    profile_sensor_id = f"{SENSOR_DOMAIN}.simply_magic_areas_lighting_profile_{area_name}"

    # 1. Initially clear
    assert hass.states.get(state_sensor_id).state == "clear"
    assert hass.states.get(profile_sensor_id).state == "standard"

    # 2. Motion triggered with lux = 200 (< threshold 300) -> state is occupied, profile is standard
    motion_sensor.turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)
    assert hass.states.get(state_sensor_id).state == "occupied"
    assert hass.states.get(profile_sensor_id).state == "standard"

    # 3. Lux increases to 300 (>= threshold 300) -> profile transitions to bright, state remains occupied
    lux_sensor.set_state(300)
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)
    assert hass.states.get(state_sensor_id).state == "occupied"
    assert hass.states.get(profile_sensor_id).state == "bright"
    assert hass.states.get(state_sensor_id).attributes["lighting_profile"] == "bright"

    # 4. Lux drops to 285. Since it's already bright and 285 >= (300 - 25 = 275), hysteresis holds bright!
    lux_sensor.set_state(285)
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)
    assert hass.states.get(state_sensor_id).state == "occupied"
    assert hass.states.get(profile_sensor_id).state == "bright"

    # 5. Lux drops to 270 (< 275) -> below hysteresis margin, profile drops back to standard
    lux_sensor.set_state(270)
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)
    assert hass.states.get(state_sensor_id).state == "occupied"
    assert hass.states.get(profile_sensor_id).state == "standard"
    assert hass.states.get(state_sensor_id).attributes["lighting_profile"] == "standard"


async def test_reference_illuminance_sensor(
    hass: HomeAssistant,
) -> None:
    """Test inheriting illuminance from an external reference sensor."""
    area_name = "hallway"
    motion_sensor = MockBinarySensor(
        name="hallway_motion",
        is_on=False,
        device_class=BinarySensorDeviceClass.MOTION,
        unique_id="hallway_motion",
    )
    external_lux_sensor = MockSensor(
        name="outdoor_lux",
        device_class=SensorDeviceClass.ILLUMINANCE,
        unique_id="outdoor_lux",
        native_unit_of_measurement=LIGHT_LUX,
        native_value=150,
    )

    setup_test_component_platform(hass, BINARY_SENSOR_DOMAIN, [motion_sensor])
    assert await async_setup_component(
        hass, BINARY_SENSOR_DOMAIN, {BINARY_SENSOR_DOMAIN: {CONF_PLATFORM: "test"}}
    )
    setup_test_component_platform(hass, SENSOR_DOMAIN, [external_lux_sensor])
    assert await async_setup_component(
        hass, SENSOR_DOMAIN, {SENSOR_DOMAIN: {CONF_PLATFORM: "test"}}
    )
    await hass.async_block_till_done()

    entity_registry = async_get_er(hass)
    entity_registry.async_update_entity(
        "binary_sensor.hallway_motion", area_id=area_name
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
            CONF_TYPE: AREA_TYPE_INTERIOR,
            CONF_LIGHT_CONTROL: False,
            CONF_FAN_CONTROL: False,
            CONF_REFERENCE_ILLUMINANCE_SENSOR: "sensor.outdoor_lux",
            CONF_BRIGHT_LUX_THRESHOLD: 250,
            CONF_BRIGHT_LUX_HYSTERESIS: 20,
            CONF_ENABLED_FEATURES: {
                CONF_FEATURE_ADVANCED_LIGHT_GROUPS: {
                    CONF_INCLUDE_ENTITIES: [],
                    CONF_ON_STATES: [STATE_ON],
                    CONF_UPDATE_INTERVAL: 60,
                    CONF_PRESENCE_DEVICE_PLATFORMS: [BINARY_SENSOR_DOMAIN],
                    CONF_PRESENCE_SENSOR_DEVICE_CLASS: [BinarySensorDeviceClass.MOTION],
                }
            },
        },
    )
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    state_sensor_id = f"{SENSOR_DOMAIN}.simply_magic_areas_state_{area_name}"
    profile_sensor_id = f"{SENSOR_DOMAIN}.simply_magic_areas_lighting_profile_{area_name}"

    motion_sensor.turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)
    # Lux is 150 < 250 -> occupied, standard profile
    assert hass.states.get(state_sensor_id).state == "occupied"
    assert hass.states.get(profile_sensor_id).state == "standard"

    # External lux sensor rises to 260 >= 250 -> profile becomes bright
    external_lux_sensor.set_state(260)
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)
    assert hass.states.get(state_sensor_id).state == "occupied"
    assert hass.states.get(profile_sensor_id).state == "bright"
    assert hass.states.get(state_sensor_id).attributes["lighting_profile"] == "bright"


async def test_reference_bright_area(
    hass: HomeAssistant,
) -> None:
    """Test inheriting brightness state from a neighboring area."""
    living_room_name = "living_room"
    hallway_name = "hallway"

    lr_motion = MockBinarySensor(
        name="lr_motion",
        is_on=False,
        device_class=BinarySensorDeviceClass.MOTION,
        unique_id="lr_motion",
    )
    lr_bright_switch = MockBinarySensor(
        name="lr_bright_switch",
        is_on=False,
        unique_id="lr_bright_switch",
    )
    hw_motion = MockBinarySensor(
        name="hw_motion",
        is_on=False,
        device_class=BinarySensorDeviceClass.MOTION,
        unique_id="hw_motion",
    )

    setup_test_component_platform(
        hass,
        BINARY_SENSOR_DOMAIN,
        [lr_motion, lr_bright_switch, hw_motion],
    )
    assert await async_setup_component(
        hass, BINARY_SENSOR_DOMAIN, {BINARY_SENSOR_DOMAIN: {CONF_PLATFORM: "test"}}
    )
    await hass.async_block_till_done()

    entity_registry = async_get_er(hass)
    entity_registry.async_update_entity(
        "binary_sensor.lr_motion", area_id=living_room_name
    )
    entity_registry.async_update_entity(
        "binary_sensor.lr_bright_switch", area_id=living_room_name
    )
    entity_registry.async_update_entity(
        "binary_sensor.hw_motion", area_id=hallway_name
    )

    ar = async_get_ar(hass)
    ar.async_get_or_create(living_room_name)
    ar.async_get_or_create(hallway_name)

    # Living room configured with binary bright_entity
    lr_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_NAME: living_room_name,
            CONF_ID: living_room_name,
            CONF_CLEAR_TIMEOUT: 5,
            CONF_EXTENDED_TIMEOUT: 5,
            CONF_TYPE: AREA_TYPE_INTERIOR,
            CONF_LIGHT_CONTROL: False,
            CONF_FAN_CONTROL: False,
            "bright_entity": "binary_sensor.lr_bright_switch",
            CONF_ENABLED_FEATURES: {
                CONF_FEATURE_ADVANCED_LIGHT_GROUPS: {
                    CONF_INCLUDE_ENTITIES: [],
                    CONF_ON_STATES: [STATE_ON],
                    CONF_UPDATE_INTERVAL: 60,
                    CONF_PRESENCE_DEVICE_PLATFORMS: [BINARY_SENSOR_DOMAIN],
                    CONF_PRESENCE_SENSOR_DEVICE_CLASS: [BinarySensorDeviceClass.MOTION],
                }
            },
        },
    )
    lr_entry.add_to_hass(hass)

    # Hallway configured with reference_bright_area: living_room
    hw_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_NAME: hallway_name,
            CONF_ID: hallway_name,
            CONF_CLEAR_TIMEOUT: 5,
            CONF_EXTENDED_TIMEOUT: 5,
            CONF_TYPE: AREA_TYPE_INTERIOR,
            CONF_LIGHT_CONTROL: False,
            CONF_FAN_CONTROL: False,
            CONF_REFERENCE_BRIGHT_AREA: living_room_name,
            CONF_ENABLED_FEATURES: {
                CONF_FEATURE_ADVANCED_LIGHT_GROUPS: {
                    CONF_INCLUDE_ENTITIES: [],
                    CONF_ON_STATES: [STATE_ON],
                    CONF_UPDATE_INTERVAL: 60,
                    CONF_PRESENCE_DEVICE_PLATFORMS: [BINARY_SENSOR_DOMAIN],
                    CONF_PRESENCE_SENSOR_DEVICE_CLASS: [BinarySensorDeviceClass.MOTION],
                }
            },
        },
    )
    hw_entry.add_to_hass(hass)

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    lr_state_id = f"{SENSOR_DOMAIN}.simply_magic_areas_state_{living_room_name}"
    hw_state_id = f"{SENSOR_DOMAIN}.simply_magic_areas_state_{hallway_name}"
    lr_profile_id = f"{SENSOR_DOMAIN}.simply_magic_areas_lighting_profile_{living_room_name}"
    hw_profile_id = f"{SENSOR_DOMAIN}.simply_magic_areas_lighting_profile_{hallway_name}"

    # Both occupied
    lr_motion.turn_on()
    hw_motion.turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)

    assert hass.states.get(lr_state_id).state == "occupied"
    assert hass.states.get(hw_state_id).state == "occupied"
    assert hass.states.get(lr_profile_id).state == "standard"
    assert hass.states.get(hw_profile_id).state == "standard"

    # Living room bright switch turned ON -> Living room profile becomes bright
    lr_bright_switch.turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)

    assert hass.states.get(lr_state_id).state == "occupied"
    assert hass.states.get(lr_profile_id).state == "bright"
    # Hallway inherits bright profile from living_room reference area!
    assert hass.states.get(hw_state_id).state == "occupied"
    assert hass.states.get(hw_profile_id).state == "bright"


async def test_light_illuminance_reference_sensor(
    hass: HomeAssistant,
) -> None:
    """Test light controller adapts brightness using a reference illuminance sensor."""
    area_name = "corridor"

    motion_sensor = MockBinarySensor(
        name="corridor_motion",
        is_on=False,
        device_class=BinarySensorDeviceClass.MOTION,
        unique_id="corridor_motion",
    )
    outdoor_lux = MockSensor(
        name="outdoor_lux_sensor",
        device_class=SensorDeviceClass.ILLUMINANCE,
        unique_id="outdoor_lux_sensor",
        native_unit_of_measurement=LIGHT_LUX,
        native_value=150,
    )

    setup_test_component_platform(hass, BINARY_SENSOR_DOMAIN, [motion_sensor])
    assert await async_setup_component(
        hass, BINARY_SENSOR_DOMAIN, {BINARY_SENSOR_DOMAIN: {CONF_PLATFORM: "test"}}
    )
    setup_test_component_platform(hass, SENSOR_DOMAIN, [outdoor_lux])
    assert await async_setup_component(
        hass, SENSOR_DOMAIN, {SENSOR_DOMAIN: {CONF_PLATFORM: "test"}}
    )
    await hass.async_block_till_done()

    entity_registry = async_get_er(hass)
    entity_registry.async_get_or_create(LIGHT_DOMAIN, "test", "corridor_bulb")
    entity_registry.async_update_entity(
        "light.test_corridor_bulb", area_id=area_name
    )
    entity_registry.async_update_entity(
        "binary_sensor.corridor_motion", area_id=area_name
    )
    hass.states.async_set(
        "light.test_corridor_bulb", "off", {ATTR_SUPPORTED_COLOR_MODES: [ColorMode.HS]}
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
            CONF_TYPE: AREA_TYPE_INTERIOR,
            CONF_LIGHT_CONTROL: True,
            CONF_FAN_CONTROL: False,
            CONF_MIN_BRIGHTNESS_LEVEL: 100,
            CONF_MAX_BRIGHTNESS_LEVEL: 200,
            CONF_REFERENCE_ILLUMINANCE_SENSOR: "sensor.outdoor_lux_sensor",
            CONF_ENABLED_FEATURES: {
                CONF_FEATURE_ADVANCED_LIGHT_GROUPS: {
                    CONF_INCLUDE_ENTITIES: [],
                    CONF_ON_STATES: [STATE_ON],
                    CONF_UPDATE_INTERVAL: 60,
                    CONF_PRESENCE_DEVICE_PLATFORMS: [BINARY_SENSOR_DOMAIN],
                    CONF_PRESENCE_SENSOR_DEVICE_CLASS: [BinarySensorDeviceClass.MOTION],
                }
            },
        },
    )
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    calls = async_mock_service(hass, LIGHT_DOMAIN, "turn_on")

    motion_sensor.turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)

    # Light was turned on using adaptive calculation from the reference sensor (150 lux adapts 255 -> 127)
    assert len(calls) >= 1
    assert calls[-1].data["brightness"] == 127
