"""Test for saving the entity."""

import logging

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN, ColorMode
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN, SensorStateClass
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant

from ..config.area_state import AreaState
from ..const import DOMAIN
from .mocks import MockFan, MockSensor

_LOGGER = logging.getLogger(__name__)


async def test_save_select(
    hass: HomeAssistant, config_entry: MockConfigEntry, _setup_integration
) -> None:
    """Test loading the integration."""
    assert config_entry.state is ConfigEntryState.LOADED

    # Validate the right enties were created.
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )

    assert area_binary_sensor is not None
    assert area_binary_sensor.state == AreaState.AREA_STATE_CLEAR
    assert area_binary_sensor.attributes == {
        "active_sensors": [],
        "friendly_name": "kitchen kitchen State (Simply Magic Areas)",
        "last_active_sensors": [],
        "presence_sensors": [],
        "state": AreaState.AREA_STATE_CLEAR,
        "type": "interior",
        "device_class": "enum",
        "options": [
            AreaState.AREA_STATE_CLEAR,
            AreaState.AREA_STATE_OCCUPIED,
            AreaState.AREA_STATE_EXTENDED,
            AreaState.AREA_STATE_BRIGHT,
            AreaState.AREA_STATE_SLEEP,
            AreaState.AREA_STATE_ACCENTED,
            AreaState.AREA_STATE_MANUAL,
        ],
    }

    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_save_system_control(
    hass: HomeAssistant, config_entry: MockConfigEntry, _setup_integration: None
) -> None:
    """Test loading the integration."""
    assert config_entry.state is ConfigEntryState.LOADED

    # Validate the right enties were created.
    area_binary_sensor = hass.states.get(
        f"{SWITCH_DOMAIN}.simply_magic_areas_system_control_kitchen"
    )

    assert area_binary_sensor is not None
    assert area_binary_sensor.state == STATE_ON
    assert area_binary_sensor.attributes == {
        "friendly_name": "kitchen kitchen System Control (Simply Magic Areas)",
        "icon": "mdi:head-cog",
        "device_class": "switch",
    }

    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_sensor_humidity_statistics(
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
        f"{SENSOR_DOMAIN}.simply_magic_areas_humidity_statistics_kitchen"
    )

    assert area_binary_sensor is not None
    assert area_binary_sensor.state == STATE_UNAVAILABLE
    assert area_binary_sensor.attributes == {
        "friendly_name": "kitchen kitchen Humidity Trend (Simply Magic Areas)",
        "icon": "mdi:calculator",
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
        "friendly_name": "kitchen kitchen Humidity Sensor (Simply Magic Areas)",
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
        "friendly_name": "kitchen kitchen Illuminance Sensor (Simply Magic Areas)",
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
        "friendly_name": "kitchen kitchen Lights (Simply Magic Areas)",
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
        "icon": "mdi:fan-auto",
        "last_update_from_entity": False,
    }

    await hass.async_block_till_done()
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)
    assert config_entry.state is ConfigEntryState.NOT_LOADED
