"""Test for saving the entity."""

import logging

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN, ColorMode
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN, SensorStateClass
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant

from ..const import DOMAIN, AreaState
from .mocks import MockFan, MockSensor

_LOGGER = logging.getLogger(__name__)


async def test_save_select(
    hass: HomeAssistant, config_entry: MockConfigEntry, _setup_integration
) -> None:
    """Test loading the integration."""
    assert config_entry.state is ConfigEntryState.LOADED

    # Validate the right enties were created.
    area_binary_sensor = hass.states.get(
        f"{SELECT_DOMAIN}.simply_magic_areas_state_kitchen"
    )

    assert area_binary_sensor is not None
    assert area_binary_sensor.state == AreaState.AREA_STATE_CLEAR
    assert area_binary_sensor.attributes == {
        "active_sensors": [],
        "friendly_name": "kitchen kitchen State (Simply Magic Areas)",
        "icon": "mdi:home-search",
        "last_active_sensors": [],
        "presence_sensors": [],
        "state": AreaState.AREA_STATE_CLEAR,
        "type": "interior",
        "device_class": "select",
        "options": [
            AreaState.AREA_STATE_CLEAR,
            AreaState.AREA_STATE_OCCUPIED,
            AreaState.AREA_STATE_EXTENDED,
            AreaState.AREA_STATE_BRIGHT,
            AreaState.AREA_STATE_SLEEP,
            AreaState.AREA_STATE_ACCENTED,
        ],
    }

    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_save_light_control(
    hass: HomeAssistant, config_entry: MockConfigEntry, _setup_integration
) -> None:
    """Test loading the integration."""
    assert config_entry.state is ConfigEntryState.LOADED

    # Validate the right enties were created.
    area_binary_sensor = hass.states.get(
        f"{SWITCH_DOMAIN}.simply_magic_areas_light_control_kitchen"
    )

    assert area_binary_sensor is not None
    assert area_binary_sensor.state == STATE_OFF
    assert area_binary_sensor.attributes == {
        "friendly_name": "kitchen kitchen Light Control (Simply Magic Areas)",
        "icon": "mdi:lightbulb-auto-outline",
        "device_class": "switch",
    }

    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_save_manual_control(
    hass: HomeAssistant, config_entry: MockConfigEntry, _setup_integration: None
) -> None:
    """Test loading the integration."""
    assert config_entry.state is ConfigEntryState.LOADED

    # Validate the right enties were created.
    area_binary_sensor = hass.states.get(
        f"{SWITCH_DOMAIN}.simply_magic_areas_manual_override_kitchen"
    )

    assert area_binary_sensor is not None
    assert area_binary_sensor.state == STATE_OFF
    assert area_binary_sensor.attributes == {
        "friendly_name": "kitchen kitchen Manual Override (Simply Magic Areas)",
        "icon": "mdi:lightbulb-auto-outline",
        "device_class": "switch",
    }

    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_sensor_humdity(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_fan: list[MockFan],
    one_sensor_humidity: list[MockSensor],
    _setup_integration: None,
) -> None:
    """Test loading the integration."""
    assert config_entry.state is ConfigEntryState.LOADED

    # Validate the right enties were created.
    area_binary_sensor = hass.states.get(
        f"{BINARY_SENSOR_DOMAIN}.simply_magic_areas_humidity_occupancy_kitchen"
    )

    assert area_binary_sensor is not None
    assert area_binary_sensor.state == STATE_OFF
    assert area_binary_sensor.attributes == {
        "friendly_name": "kitchen kitchen Humidity Occupancy (Simply Magic Areas)",
        "device_class": "moisture",
    }

    await hass.async_block_till_done()
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_sensor_humdity_empty(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_fan: list[MockFan],
    one_sensor_humidity: list[MockSensor],
    _setup_integration: None,
) -> None:
    """Test loading the integration."""
    assert config_entry.state is ConfigEntryState.LOADED

    # Validate the right enties were created.
    area_binary_sensor = hass.states.get(
        f"{BINARY_SENSOR_DOMAIN}.simply_magic_areas_humidity_empty_kitchen"
    )

    assert area_binary_sensor is not None
    assert area_binary_sensor.state == STATE_OFF
    assert area_binary_sensor.attributes == {
        "friendly_name": "kitchen kitchen Humidity Empty (Simply Magic Areas)",
        "device_class": "moisture",
    }

    await hass.async_block_till_done()
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_sensor_humdity_sensor(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_fan: list[MockFan],
    one_sensor_humidity: list[MockSensor],
    _setup_integration: None,
) -> None:
    """Test loading the integration."""
    assert config_entry.state is ConfigEntryState.LOADED

    # Validate the right enties were created.
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_humidity_kitchen"
    )

    assert area_binary_sensor is not None
    assert area_binary_sensor.state == "1.0"
    assert area_binary_sensor.attributes == {
        "friendly_name": "kitchen Humidity Sensor (Simply Magic Areas)",
        "device_class": "humidity",
        "unit_of_measurement": "%",
        "entity_id": ["sensor.humidity_sensor"],
        "state_class": SensorStateClass.MEASUREMENT,
    }

    await hass.async_block_till_done()
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_sensor_illuminance_sensor(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_sensor_light: list[MockSensor],
    _setup_integration: None,
) -> None:
    """Test loading the integration."""
    assert config_entry.state is ConfigEntryState.LOADED

    # Validate the right enties were created.
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_illuminance_kitchen"
    )

    assert area_binary_sensor is not None
    assert area_binary_sensor.state == "1.0"
    assert area_binary_sensor.attributes == {
        "friendly_name": "kitchen Illuminance Sensor (Simply Magic Areas)",
        "device_class": "illuminance",
        "unit_of_measurement": "lx",
        "entity_id": ["sensor.light_sensor"],
        "state_class": SensorStateClass.MEASUREMENT,
    }

    await hass.async_block_till_done()
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_light(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_light: list[MockSensor],
    _setup_integration: None,
) -> None:
    """Test loading the integration."""
    assert config_entry.state is ConfigEntryState.LOADED

    # Validate the right enties were created.
    area_binary_sensor = hass.states.get(
        f"{LIGHT_DOMAIN}.simply_magic_areas_light_kitchen"
    )

    assert area_binary_sensor is not None
    assert area_binary_sensor.state == STATE_OFF
    assert area_binary_sensor.attributes == {
        "friendly_name": "kitchen Lights (Simply Magic Areas)",
        "lights": ["light.test_5678"],
        "entity_id": ["light.test_5678"],
        "color_mode": None,
        "hs_color": None,
        "brightness": None,
        "xy_color": None,
        "device_class": "light",
        "supported_color_modes": [ColorMode.HS],
        "icon": "mdi:ceiling-light",
        "rgb_color": None,
        "supported_features": 0,
        "last_update_from_entity": False,
    }

    await hass.async_block_till_done()
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_fan(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_fan: list[MockFan],
    _setup_integration: None,
) -> None:
    """Test loading the integration."""
    assert config_entry.state is ConfigEntryState.LOADED

    # Validate the right enties were created.
    area_binary_sensor = hass.states.get(f"{FAN_DOMAIN}.simply_magic_areas_fan_kitchen")

    assert area_binary_sensor is not None
    assert area_binary_sensor.state == STATE_OFF
    assert area_binary_sensor.attributes == {
        "friendly_name": "kitchen kitchen Fans (Simply Magic Areas)",
        "fans": ["fan.test_5678"],
        "entity_id": ["fan.test_5678"],
        "supported_features": 0,
        "icon": "mdi:fan",
        "last_update_from_entity": False,
    }

    await hass.async_block_till_done()
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)
    assert config_entry.state is ConfigEntryState.NOT_LOADED
