"""Tests for the config flow."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.components.binary_sensor import (
    DOMAIN as BINARY_SENSOR_DOMAIN,
    BinarySensorDeviceClass,
)
from homeassistant.components.media_player import DOMAIN as MEDIA_PLAYER_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_OPEN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.area_registry import async_get as async_get_ar

from ..const import (
    AREA_TYPE_INTERIOR,
    CONF_CLEAR_TIMEOUT,
    CONF_ENABLED_FEATURES,
    CONF_EXCLUDE_ENTITIES,
    CONF_EXTENDED_TIMEOUT,
    CONF_FAN_CONTROL,
    CONF_FEATURE_ADVANCED_LIGHT_GROUPS,
    CONF_FEATURE_HUMIDITY,
    CONF_HUMIDITY_TREND_DOWN_CUT_OFF,
    CONF_HUMIDITY_TREND_UP_CUT_OFF,
    CONF_HUMIDITY_ZERO_WAIT_TIME,
    CONF_ICON,
    CONF_ID,
    CONF_INCLUDE_ENTITIES,
    CONF_LIGHT_CONTROL,
    CONF_MAX_BRIGHTNESS_LEVEL,
    CONF_MIN_BRIGHTNESS_LEVEL,
    CONF_MQTT_ROOM_PRESENCE,
    CONF_NAME,
    CONF_ON_STATES,
    CONF_PRESENCE_DEVICE_PLATFORMS,
    CONF_PRESENCE_SENSOR_DEVICE_CLASS,
    CONF_TYPE,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
)


async def test_form(hass: HomeAssistant) -> None:
    """Test we get the form."""
    # Create an area in the registry.
    registry = async_get_ar(hass)
    registry.async_get_or_create("kitchen")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {}

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "kitchen"},
    )
    await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == "kitchen"
    assert result2["data"] == {
        CONF_NAME: "kitchen",
        CONF_CLEAR_TIMEOUT: 360,
        CONF_ENABLED_FEATURES: {},
        CONF_ICON: "mdi:texture-box",
        CONF_ID: "kitchen",
        CONF_TYPE: AREA_TYPE_INTERIOR,
        CONF_EXTENDED_TIMEOUT: 360,
        CONF_MQTT_ROOM_PRESENCE: False,
        "bright_entity": "",
        "sleep_entity": "",
        CONF_LIGHT_CONTROL: True,
        CONF_FAN_CONTROL: True,
        CONF_MAX_BRIGHTNESS_LEVEL: 200,
        CONF_MIN_BRIGHTNESS_LEVEL: 100,
    }
    # assert len(mock_setup_entry.mock_calls) == 1
    await hass.async_block_till_done()


async def test_options(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Test we get the form."""
    # Create an area in the registry.
    registry = async_get_ar(hass)
    registry.async_get_or_create("kitchen")
    config_entry.add_to_hass(hass)

    # Load the integration
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED

    # show initial form
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    # submit form with options
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_CLEAR_TIMEOUT: 12, CONF_EXTENDED_TIMEOUT: 60},
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "select_features"
    assert result["errors"] is None
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    # assert result["title"] == "kitchen"
    assert result["data"] == {
        CONF_LIGHT_CONTROL: True,
        CONF_FAN_CONTROL: True,
        CONF_CLEAR_TIMEOUT: 12,
        CONF_EXTENDED_TIMEOUT: 60,
        CONF_ENABLED_FEATURES: {},
        CONF_ICON: "mdi:texture-box",
        CONF_TYPE: AREA_TYPE_INTERIOR,
        CONF_MQTT_ROOM_PRESENCE: False,
        CONF_MAX_BRIGHTNESS_LEVEL: 200,
        CONF_MIN_BRIGHTNESS_LEVEL: 100,
        "bright_entity": "",
        "sleep_entity": "",
    }

    await hass.async_block_till_done()
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_options_enable_advanced_lights(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Test we get the form."""
    # Create an area in the registry.
    registry = async_get_ar(hass)
    registry.async_get_or_create("kitchen")
    config_entry.add_to_hass(hass)

    # Load the integration
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED

    # show initial form
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "area_config"
    assert result["data_schema"]({}) == {
        "bright_entity": "",
        CONF_MQTT_ROOM_PRESENCE: False,
        CONF_EXTENDED_TIMEOUT: 360.0,
        CONF_ICON: "mdi:texture-box",
        CONF_CLEAR_TIMEOUT: 360.0,
        "sleep_entity": "",
        CONF_TYPE: "interior",
        CONF_LIGHT_CONTROL: True,
        CONF_FAN_CONTROL: True,
    }

    # submit form with options
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_CLEAR_TIMEOUT: 12}
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "select_features"
    assert result["errors"] is None

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_FEATURE_ADVANCED_LIGHT_GROUPS: True},
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "feature_conf_advanced_light_groups"
    assert result["errors"] == {}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"accented_state_check": STATE_OFF},
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    # assert result["title"] == "kitchen"
    assert result["data"] == {
        CONF_CLEAR_TIMEOUT: 12,
        CONF_EXTENDED_TIMEOUT: 360,
        CONF_LIGHT_CONTROL: True,
        CONF_FAN_CONTROL: True,
        CONF_ENABLED_FEATURES: {
            CONF_FEATURE_ADVANCED_LIGHT_GROUPS: {
                "accented_entity": "",
                "accented_state_dim": 0.0,
                "accented_lights": [],
                "extended_state_dim": 0.0,
                "accented_state_check": STATE_OFF,
                "clear_state_dim": 0.0,
                "bright_lights": [],
                "bright_state_check": STATE_ON,
                "bright_state_dim": 0.0,
                "clear_lights": [],
                "clear_state_check": STATE_ON,
                "sleep_lights": [],
                "sleep_state_check": STATE_ON,
                "sleep_state_dim": 30.0,
                "extended_lights": [],
                "extended_state_check": STATE_ON,
                "occupied_lights": [],
                "occupied_state_check": STATE_ON,
                "occupied_state_dim": 100.0,
                CONF_UPDATE_INTERVAL: 1800,
                CONF_EXCLUDE_ENTITIES: [],
                CONF_PRESENCE_DEVICE_PLATFORMS: [
                    MEDIA_PLAYER_DOMAIN,
                    BINARY_SENSOR_DOMAIN,
                ],
                CONF_PRESENCE_SENSOR_DEVICE_CLASS: [
                    BinarySensorDeviceClass.MOTION,
                    BinarySensorDeviceClass.OCCUPANCY,
                    BinarySensorDeviceClass.PRESENCE,
                ],
                CONF_ON_STATES: [STATE_ON, STATE_OPEN],
                CONF_INCLUDE_ENTITIES: [],
            },
        },
        CONF_ICON: "mdi:texture-box",
        CONF_TYPE: AREA_TYPE_INTERIOR,
        CONF_MQTT_ROOM_PRESENCE: False,
        CONF_MIN_BRIGHTNESS_LEVEL: 100,
        CONF_MAX_BRIGHTNESS_LEVEL: 200,
        "bright_entity": "",
        "sleep_entity": "",
        CONF_MAX_BRIGHTNESS_LEVEL: 200,
        CONF_MIN_BRIGHTNESS_LEVEL: 100,
    }
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_options_enable_humidity(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Test we get the form."""
    # Create an area in the registry.
    registry = async_get_ar(hass)
    registry.async_get_or_create("kitchen")
    config_entry.add_to_hass(hass)

    # Load the integration
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED

    # show initial form
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "area_config"
    assert result["data_schema"]({}) == {
        "bright_entity": "",
        CONF_MQTT_ROOM_PRESENCE: False,
        CONF_EXTENDED_TIMEOUT: 360.0,
        CONF_ICON: "mdi:texture-box",
        CONF_CLEAR_TIMEOUT: 360.0,
        "sleep_entity": "",
        CONF_TYPE: "interior",
        CONF_LIGHT_CONTROL: True,
        CONF_FAN_CONTROL: True,
    }

    # submit form with options
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_CLEAR_TIMEOUT: 12}
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "select_features"
    assert result["errors"] is None

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_FEATURE_HUMIDITY: True},
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "feature_conf_humidity"
    assert result["errors"] == {}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_HUMIDITY_ZERO_WAIT_TIME: 50},
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    # assert result["title"] == "kitchen"
    assert result["data"] == {
        CONF_CLEAR_TIMEOUT: 12,
        CONF_EXTENDED_TIMEOUT: 360,
        CONF_LIGHT_CONTROL: True,
        CONF_FAN_CONTROL: True,
        CONF_ENABLED_FEATURES: {
            CONF_FEATURE_HUMIDITY: {
                CONF_HUMIDITY_ZERO_WAIT_TIME: 50,
                CONF_HUMIDITY_TREND_UP_CUT_OFF: 0.03,
                CONF_HUMIDITY_TREND_DOWN_CUT_OFF: -0.015,
            },
        },
        CONF_ICON: "mdi:texture-box",
        CONF_TYPE: AREA_TYPE_INTERIOR,
        CONF_MQTT_ROOM_PRESENCE: False,
        "bright_entity": "",
        "sleep_entity": "",
        CONF_MAX_BRIGHTNESS_LEVEL: 200,
        CONF_MIN_BRIGHTNESS_LEVEL: 100,
    }
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
