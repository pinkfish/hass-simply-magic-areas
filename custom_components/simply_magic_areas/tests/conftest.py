"""Fixtures for tests."""

from collections.abc import Generator
import logging

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.binary_sensor import (
    DOMAIN as BINARY_SENSOR_DOMAIN,
    BinarySensorDeviceClass,
)
from homeassistant.components.cover import DOMAIN as COVER_DOMAIN, CoverDeviceClass
from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.components.light import (
    ATTR_SUPPORTED_COLOR_MODES,
    DOMAIN as LIGHT_DOMAIN,
    ColorMode,
)
from homeassistant.components.media_player import DOMAIN as MEDIA_PLAYER_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN, SensorDeviceClass
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PLATFORM, LIGHT_LUX, PERCENTAGE, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.area_registry import async_get as async_get_ar
from homeassistant.helpers.entity_registry import async_get as async_get_er
from homeassistant.setup import async_setup_component

from ..const import (
    AREA_TYPE_INTERIOR,
    CONF_CLEAR_TIMEOUT,
    CONF_ENABLED_FEATURES,
    CONF_EXTENDED_TIMEOUT,
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
from .common import setup_mqtt_room_component_platform, setup_test_component_platform
from .mocks import MockBinarySensor, MockCover, MockFan, MockSensor

AREA_NAME = "kitchen"
_LOGGER = logging.getLogger(__name__)

CONFIG_ENTRY_DATA = {
    CONF_NAME: AREA_NAME,
    CONF_ID: AREA_NAME,
    CONF_CLEAR_TIMEOUT: 3,
    CONF_EXTENDED_TIMEOUT: 2,
    CONF_TYPE: AREA_TYPE_INTERIOR,
    CONF_LIGHT_CONTROL: True,
    CONF_FAN_CONTROL: True,
    "bright_entity": "",
    "bright_state_dim": 0,
    "sleep_entity": "",
    "sleep_state_dim": 30,
    "occupied_state_dim": 100,
    CONF_ENABLED_FEATURES: {
        CONF_FEATURE_ADVANCED_LIGHT_GROUPS: {
            CONF_INCLUDE_ENTITIES: [],
            CONF_ON_STATES: [STATE_ON],
            CONF_UPDATE_INTERVAL: 60,
            CONF_PRESENCE_DEVICE_PLATFORMS: [
                MEDIA_PLAYER_DOMAIN,
                BINARY_SENSOR_DOMAIN,
            ],
            CONF_PRESENCE_SENSOR_DEVICE_CLASS: [
                BinarySensorDeviceClass.MOTION,
                BinarySensorDeviceClass.OCCUPANCY,
                BinarySensorDeviceClass.PRESENCE,
            ],
            "extended_state_dim": 0,
            "clear_state_dim": 0,
            "accented_entity": "",
            "accented_state_dim": 0,
            "accented_lights": [],
            "accented_state_check": STATE_ON,
            "bright_lights": [],
            "bright_state_check": STATE_ON,
            "clear_lights": [],
            "clear_state_check": STATE_ON,
            "sleep_lights": [],
            "sleep_state_check": STATE_ON,
            "extended_lights": [],
            "extended_state_check": STATE_ON,
            "occupied_lights": [],
            "occupied_state_check": STATE_ON,
        },
    },
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None, None, None]:
    """Enable custom integration."""
    _ = enable_custom_integrations  # unused
    yield


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Return snapshot assertion fixture with the Home Assistant extension."""
    return snapshot.use_extension(HomeAssistantSnapshotExtension)


@pytest.fixture(name="config_entry")
def mock_config_entry() -> MockConfigEntry:
    """Fixture for mock configuration entry."""
    data = dict(CONFIG_ENTRY_DATA)
    return MockConfigEntry(domain=DOMAIN, data=data)


@pytest.fixture(name="disable_config_entry")
def mock_disable_config_entry() -> MockConfigEntry:
    """Fixture for mock configuration entry."""
    data = dict(CONFIG_ENTRY_DATA)
    data[CONF_LIGHT_CONTROL] = False
    data[CONF_FAN_CONTROL] = False
    return MockConfigEntry(domain=DOMAIN, data=data)


@pytest.fixture(name="config_entry_entities")
def mock_config_entry_entities() -> MockConfigEntry:
    """Fixture for mock configuration entry."""
    data = {
        CONF_NAME: AREA_NAME,
        CONF_ID: AREA_NAME,
        CONF_CLEAR_TIMEOUT: 30,
        CONF_EXTENDED_TIMEOUT: 20,
        CONF_TYPE: AREA_TYPE_INTERIOR,
        CONF_LIGHT_CONTROL: True,
        CONF_FAN_CONTROL: True,
        "sleep_entity": "binary_sensor.sleep",
        "bright_entity": "binary_sensor.bright",
        CONF_ENABLED_FEATURES: {
            CONF_FEATURE_ADVANCED_LIGHT_GROUPS: {
                CONF_UPDATE_INTERVAL: 1800,
                CONF_PRESENCE_SENSOR_DEVICE_CLASS: [BinarySensorDeviceClass.MOTION],
                CONF_ON_STATES: [STATE_ON],
                "accented_entity": "binary_sensor.accent",
            },
        },
    }
    return MockConfigEntry(domain=DOMAIN, data=data)


@pytest.fixture(name="one_motion")
async def setup_one_sensor(hass: HomeAssistant) -> list[MockBinarySensor]:
    """Create one mock sensor and setup the system with ti."""
    mock_binary_sensor_entities = [
        MockBinarySensor(
            name="motion sensor",
            is_on=True,
            unique_id="unique_motion",
            device_class=BinarySensorDeviceClass.MOTION,
        ),
        MockBinarySensor(
            name="sleep",
            is_on=True,
            unique_id="sleep",
            device_class=BinarySensorDeviceClass.BATTERY,
        ),
        MockBinarySensor(
            name="bright",
            is_on=True,
            unique_id="bright",
            device_class=BinarySensorDeviceClass.BATTERY,
        ),
        MockBinarySensor(
            name="accent",
            is_on=True,
            unique_id="accent",
            device_class=BinarySensorDeviceClass.BATTERY,
        ),
    ]
    setup_test_component_platform(
        hass, BINARY_SENSOR_DOMAIN, mock_binary_sensor_entities
    )
    assert await async_setup_component(
        hass, BINARY_SENSOR_DOMAIN, {BINARY_SENSOR_DOMAIN: {CONF_PLATFORM: "test"}}
    )
    await hass.async_block_till_done()
    entity_registry = async_get_er(hass)
    entity_registry.async_update_entity(
        "binary_sensor.motion_sensor",
        area_id=AREA_NAME,
    )
    return mock_binary_sensor_entities


@pytest.fixture(name="one_cover")
async def setup_one_cover(hass: HomeAssistant) -> list[MockCover]:
    """Create one mock cover and setup the system with ti."""
    mock_cover_entities = [
        MockCover(
            name="cover",
            is_opened=False,
            is_opening=False,
            is_closing=False,
            unique_id="unique_copver",
            device_class=CoverDeviceClass.AWNING,
        ),
    ]
    setup_test_component_platform(hass, COVER_DOMAIN, mock_cover_entities)
    assert await async_setup_component(
        hass, COVER_DOMAIN, {COVER_DOMAIN: {CONF_PLATFORM: "test"}}
    )
    await hass.async_block_till_done()
    entity_registry = async_get_er(hass)
    entity_registry.async_update_entity(
        "cover.cover",
        area_id=AREA_NAME,
    )
    return mock_cover_entities


@pytest.fixture(name="one_sensor_light")
async def setup_one_light_sensor(hass: HomeAssistant) -> list[MockSensor]:
    """Create one mock sensor and setup the system with ti."""
    mock_binary_sensor_entities = [
        MockSensor(
            name="light sensor",
            native_value=1.0,
            unique_id="unique_light",
            device_class=SensorDeviceClass.ILLUMINANCE,
            suggested_unit_of_measurement=LIGHT_LUX,
            unit_of_measurement=LIGHT_LUX,
        ),
    ]
    setup_test_component_platform(hass, SENSOR_DOMAIN, mock_binary_sensor_entities)
    assert await async_setup_component(
        hass, SENSOR_DOMAIN, {SENSOR_DOMAIN: {CONF_PLATFORM: "test"}}
    )
    await hass.async_block_till_done()
    entity_registry = async_get_er(hass)
    entity_registry.async_update_entity(
        "sensor.light_sensor",
        area_id=AREA_NAME,
    )
    return mock_binary_sensor_entities


@pytest.fixture(name="one_mqtt_room_sensor")
async def setup_one_mqtt_room_sensor(hass: HomeAssistant) -> list[MockSensor]:
    """Create one mock sensor and setup the system with ti."""
    mock_binary_sensor_entities = [
        MockSensor(
            name="mqqt room sensor",
            native_value=1.0,
            unique_id="unique_mqtt_room",
        ),
    ]
    setup_mqtt_room_component_platform(hass, SENSOR_DOMAIN, mock_binary_sensor_entities)
    assert await async_setup_component(
        hass, SENSOR_DOMAIN, {SENSOR_DOMAIN: {CONF_PLATFORM: "mqtt_room"}}
    )
    await hass.async_block_till_done()
    return mock_binary_sensor_entities


@pytest.fixture(name="one_sensor_humidity")
async def setup_one_humidity_sensor(hass: HomeAssistant) -> list[MockSensor]:
    """Create one mock sensor and setup the system with ti."""
    mock_binary_sensor_entities = [
        MockSensor(
            name="humidity sensor",
            native_value=1.0,
            unique_id="unique_humidity",
            device_class=SensorDeviceClass.HUMIDITY,
            native_unit_of_measurement=PERCENTAGE,
            unit_of_measurement=PERCENTAGE,
            extra_state_attributes={
                "unit_of_measurement": PERCENTAGE,
            },
        ),
    ]

    setup_test_component_platform(hass, SENSOR_DOMAIN, mock_binary_sensor_entities)
    assert await async_setup_component(
        hass, SENSOR_DOMAIN, {SENSOR_DOMAIN: {CONF_PLATFORM: "test"}}
    )
    await hass.async_block_till_done()
    entity_registry = async_get_er(hass)
    entity_registry.async_update_entity(
        "sensor.humidity_sensor",
        area_id=AREA_NAME,
    )
    return mock_binary_sensor_entities


@pytest.fixture(name="one_light")
async def setup_one_light(hass: HomeAssistant) -> list[str]:
    """Create one mock light and setup the system with ti."""
    entity_registry = async_get_er(hass)
    entity_registry.async_get_or_create(
        LIGHT_DOMAIN,
        "test",
        "5678",
    )
    entity_registry.async_update_entity(
        "light.test_5678",
        area_id=AREA_NAME,
    )
    hass.states.async_set(
        "light.test_5678", "off", {ATTR_SUPPORTED_COLOR_MODES: [ColorMode.HS]}
    )
    return ["light.test_5678"]


@pytest.fixture(name="one_fan")
async def setup_one_fan(hass: HomeAssistant) -> list[MockFan]:
    """Create one mock fan and setup the system with ti."""
    mock_fan = MockFan(name="test 5678", is_on=False, unique_id="fan_5678")
    setup_test_component_platform(hass, FAN_DOMAIN, [mock_fan])
    assert await async_setup_component(
        hass, FAN_DOMAIN, {FAN_DOMAIN: {CONF_PLATFORM: "test"}}
    )
    await hass.async_block_till_done()

    entity_registry = async_get_er(hass)
    entity_registry.async_update_entity(
        "fan.test_5678",
        area_id=AREA_NAME,
    )
    hass.states.async_set(
        "fan.test_5678", "off", {ATTR_SUPPORTED_COLOR_MODES: [ColorMode.HS]}
    )
    return [mock_fan]


@pytest.fixture(name="two_lights")
def setup_two_lights(hass: HomeAssistant) -> list[str]:
    """Mock a test component platform for tests."""
    entity_registry = async_get_er(hass)
    entity_registry.async_get_or_create(
        LIGHT_DOMAIN,
        "test",
        "5678",
    )
    entity_registry.async_get_or_create(
        LIGHT_DOMAIN,
        "test",
        "5679",
    )
    entity_registry.async_update_entity(
        "light.test_5678",
        area_id=AREA_NAME,
    )
    entity_registry.async_update_entity(
        "light.test_5679",
        area_id=AREA_NAME,
    )

    return ["light.test_5678", "light.test_5679"]


@pytest.fixture(name="_setup_integration")
async def setup_integration(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
) -> None:
    """Set up the integration."""
    registry = async_get_ar(hass)
    registry.async_get_or_create(AREA_NAME)

    config_entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    yield

    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)
    assert config_entry.state is ConfigEntryState.NOT_LOADED
    _LOGGER.info("Unloading integration ")


@pytest.fixture(name="_setup_integration_disable_control")
async def setup_integration_disable_control(
    hass: HomeAssistant,
    disable_config_entry: MockConfigEntry,
) -> None:
    """Set up the integration."""
    registry = async_get_ar(hass)
    registry.async_get_or_create(AREA_NAME)
    disable_config_entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    yield

    await hass.config_entries.async_unload(disable_config_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)
    assert disable_config_entry.state is ConfigEntryState.NOT_LOADED
    _LOGGER.info("Unloading disable integration ")


@pytest.fixture(name="_setup_integration_entities")
async def setup_entities_integration(
    hass: HomeAssistant,
    config_entry_entities: MockConfigEntry,
) -> None:
    """Set up the integration."""
    registry = async_get_ar(hass)
    registry.async_get_or_create(AREA_NAME)
    config_entry_entities.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    yield

    await hass.config_entries.async_unload(config_entry_entities.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)
    assert config_entry_entities.state is ConfigEntryState.NOT_LOADED
    _LOGGER.info("Unloading entities integration ")
