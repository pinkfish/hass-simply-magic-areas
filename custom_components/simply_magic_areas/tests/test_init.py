"""Test initializing the system."""

import logging

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant

from ..const import DOMAIN
from .mocks import MockLight, MockSensor

_LOGGER = logging.getLogger(__name__)


async def test_init_no_devices(
    hass: HomeAssistant, config_entry: MockConfigEntry, _setup_integration
) -> None:
    """Test loading the integration."""
    assert config_entry.state is ConfigEntryState.LOADED

    # Validate the right enties were created.
    control_entity = hass.states.get(
        f"{SWITCH_DOMAIN}.simply_magic_areas_system_control_kitchen"
    )
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    occupied_lights = hass.states.get(
        f"{LIGHT_DOMAIN}.simply_magic_areas_light_kitchen"
    )
    humidity_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_humidity_kitchen"
    )
    humidity_statistics = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_humidity_statistics_kitchen"
    )

    assert control_entity is not None
    assert area_binary_sensor is not None
    assert occupied_lights is None
    assert humidity_sensor is None
    assert humidity_statistics is None
    assert control_entity.state == STATE_ON
    assert area_binary_sensor.state == "clear"

    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_init_with_lights(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_light: MockLight,
    _setup_integration,
) -> None:
    """Test loading the integration."""
    assert config_entry.state is ConfigEntryState.LOADED

    # Validate the right enties were created.
    control_entity = hass.states.get(
        f"{SWITCH_DOMAIN}.simply_magic_areas_system_control_kitchen"
    )
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    occupied_lights = hass.states.get(
        f"{LIGHT_DOMAIN}.simply_magic_areas_light_kitchen"
    )

    assert control_entity is not None
    assert area_binary_sensor is not None
    assert occupied_lights is not None
    assert control_entity.state == STATE_ON
    assert area_binary_sensor.state == "clear"

    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_init_with_light_sensor(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_sensor_light: list[MockSensor],
    _setup_integration,
) -> None:
    """Test loading the integration."""
    assert config_entry.state is ConfigEntryState.LOADED

    # Validate the right enties were created.
    control_entity = hass.states.get(
        f"{SWITCH_DOMAIN}.simply_magic_areas_system_control_kitchen"
    )
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    occupied_lights = hass.states.get(
        f"{LIGHT_DOMAIN}.simply_magic_areas_light_kitchen"
    )
    light_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_illuminance_kitchen"
    )

    assert control_entity is not None
    assert area_binary_sensor is not None
    assert occupied_lights is None
    assert light_sensor is not None
    assert control_entity.state == STATE_ON
    assert area_binary_sensor.state == "clear"

    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_init_with_humidity_sensor(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_sensor_humidity: list[MockSensor],
    _setup_integration,
) -> None:
    """Test loading the integration."""
    assert config_entry.state is ConfigEntryState.LOADED

    # Validate the right enties were created.
    control_entity = hass.states.get(
        f"{SWITCH_DOMAIN}.simply_magic_areas_system_control_kitchen"
    )
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    occupied_lights = hass.states.get(
        f"{LIGHT_DOMAIN}.simply_magic_areas_light_kitchen"
    )
    humidity_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_humidity_kitchen"
    )
    humidity_statistics = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_humidity_statistics_kitchen"
    )

    assert control_entity is not None
    assert area_binary_sensor is not None
    assert occupied_lights is None
    assert humidity_sensor is not None
    assert humidity_statistics is not None
    assert control_entity.state == STATE_ON
    assert area_binary_sensor.state == "clear"
    await hass.async_block_till_done()

    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_init_with_lights_and_sensor(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_light: str,
    one_motion: str,
    _setup_integration,
) -> None:
    """Test loading the integration."""
    assert config_entry.state is ConfigEntryState.LOADED

    # Validate the right enties were created.
    control_entity = hass.states.get(
        f"{SWITCH_DOMAIN}.simply_magic_areas_system_control_kitchen"
    )
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    occupied_lights = hass.states.get(
        f"{LIGHT_DOMAIN}.simply_magic_areas_light_kitchen"
    )

    assert control_entity is not None
    assert area_binary_sensor is not None
    assert occupied_lights is not None
    assert control_entity.state == STATE_ON
    assert area_binary_sensor.state == "clear"
    await hass.async_block_till_done()

    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)
    assert config_entry.state is ConfigEntryState.NOT_LOADED
