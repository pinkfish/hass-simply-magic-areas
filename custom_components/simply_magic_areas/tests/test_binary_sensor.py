import asyncio
import logging

from pytest_homeassistant_custom_component.common import MockConfigEntry

_LOGGER = logging.getLogger(__name__)

from homeassistant.components.binary_sensor import (
    DOMAIN as BINARY_SENSOR_DOMAIN,
    BinarySensorDeviceClass,
)
from homeassistant.const import CONF_PLATFORM, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.area_registry import async_get as async_get_ar
from homeassistant.helpers.entity_registry import async_get as async_get_er
from homeassistant.setup import async_setup_component

from ..const import (
    CONF_AGGREGATES_MIN_ENTITIES,
    CONF_AGGREGATION,
    CONF_ENABLED_FEATURES,
    CONF_FEATURE_GROUP_CREATION,
    CONF_FEATURE_HEALTH,
    DOMAIN,
)
from .common import setup_test_component_platform
from .conftest import AREA_NAME, CONFIG_ENTRY_DATA
from .mocks import MockBinarySensor


async def test_binary_sensor_aggregation_and_health(
    hass: HomeAssistant,
) -> None:
    """Test binary sensor aggregate groups and health sensors."""
    # Create two problem/smoke sensors and two door sensors
    mock_sensors = [
        MockBinarySensor(
            name="smoke 1",
            is_on=False,
            unique_id="smoke_1",
            device_class=BinarySensorDeviceClass.SMOKE,
        ),
        MockBinarySensor(
            name="smoke 2",
            is_on=False,
            unique_id="smoke_2",
            device_class=BinarySensorDeviceClass.SMOKE,
        ),
        MockBinarySensor(
            name="door 1",
            is_on=False,
            unique_id="door_1",
            device_class=BinarySensorDeviceClass.DOOR,
        ),
        MockBinarySensor(
            name="door 2",
            is_on=False,
            unique_id="door_2",
            device_class=BinarySensorDeviceClass.DOOR,
        ),
    ]

    setup_test_component_platform(hass, BINARY_SENSOR_DOMAIN, mock_sensors)
    assert await async_setup_component(
        hass, BINARY_SENSOR_DOMAIN, {BINARY_SENSOR_DOMAIN: {CONF_PLATFORM: "test"}}
    )
    await hass.async_block_till_done()

    entity_registry = async_get_er(hass)
    entity_registry.async_update_entity("binary_sensor.smoke_1", area_id=AREA_NAME)
    entity_registry.async_update_entity("binary_sensor.smoke_2", area_id=AREA_NAME)
    entity_registry.async_update_entity("binary_sensor.door_1", area_id=AREA_NAME)
    entity_registry.async_update_entity("binary_sensor.door_2", area_id=AREA_NAME)

    data = dict(CONFIG_ENTRY_DATA)
    data[CONF_ENABLED_FEATURES] = {
        **data.get(CONF_ENABLED_FEATURES, {}),
        CONF_AGGREGATION: {},
        CONF_FEATURE_HEALTH: {},
        CONF_FEATURE_GROUP_CREATION: {
            CONF_AGGREGATES_MIN_ENTITIES: 2,
        },
    }
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    registry = async_get_ar(hass)
    registry.async_get_or_create(AREA_NAME)

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    # Verify aggregate door sensor group created
    door_group = hass.states.get(
        f"{BINARY_SENSOR_DOMAIN}.simply_magic_areas_door_{AREA_NAME}"
    )
    assert door_group is not None
    assert door_group.state == STATE_OFF

    # Turn on one door -> group should be on
    mock_sensors[2].turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)
    door_group = hass.states.get(
        f"{BINARY_SENSOR_DOMAIN}.simply_magic_areas_door_{AREA_NAME}"
    )
    assert door_group.state == STATE_ON

    # Verify health sensor (problem) created for distress classes (smoke)
    health_sensor = hass.states.get(
        f"{BINARY_SENSOR_DOMAIN}.simply_magic_areas_problem_{AREA_NAME}"
    )
    assert health_sensor is not None
    assert health_sensor.state == STATE_OFF

    # Trigger smoke -> health sensor becomes on
    mock_sensors[0].turn_on()
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)
    health_sensor = hass.states.get(
        f"{BINARY_SENSOR_DOMAIN}.simply_magic_areas_problem_{AREA_NAME}"
    )
    assert health_sensor.state == STATE_ON

    # Clean up
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
