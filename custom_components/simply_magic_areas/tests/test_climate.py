"""Tests for climate platform."""

from _pytest.monkeypatch import MonkeyPatch
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_FAN_MODE,
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    ATTR_SWING_MODE,
    ATTR_TEMPERATURE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_SWING_MODE,
    SERVICE_SET_TEMPERATURE,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.area_registry import async_get as async_get_ar
from homeassistant.setup import async_setup_component

from ..config.area_state import AreaState
from ..config.entity_names import EntityNames
from ..const import (
    CONF_CLIMATE_GROUPS_TURN_ON_STATE,
    CONF_ENABLED_FEATURES,
    CONF_FEATURE_CLIMATE_GROUPS,
    DOMAIN,
)
from .conftest import AREA_NAME, CONFIG_ENTRY_DATA
from .mocks import MockClimate


async def test_climate_no_feature(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_climate: list[MockClimate],
    _setup_integration: None,
    monkeypatch: MonkeyPatch,
) -> None:
    """Test climate group not created when feature disabled."""
    area_climate = hass.states.get(f"{CLIMATE_DOMAIN}.simply_magic_areas_climate_kitchen")
    assert area_climate is None


async def test_climate_group_creation_and_services(
    hass: HomeAssistant,
    one_climate: list[MockClimate],
) -> None:
    """Test climate group creation, state aggregation, and service delegation."""
    data = dict(CONFIG_ENTRY_DATA)
    data[CONF_ENABLED_FEATURES] = {
        **data.get(CONF_ENABLED_FEATURES, {}),
        CONF_FEATURE_CLIMATE_GROUPS: {
            CONF_CLIMATE_GROUPS_TURN_ON_STATE: AreaState.AREA_STATE_OCCUPIED,
        },
    }
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    registry = async_get_ar(hass)
    registry.async_get_or_create(AREA_NAME)

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    group_entity_id = f"{CLIMATE_DOMAIN}.simply_magic_areas_climate_kitchen"
    state = hass.states.get(group_entity_id)
    assert state is not None
    assert state.state == HVACMode.OFF
    assert state.attributes.get(ATTR_CURRENT_TEMPERATURE) == 21.0
    assert state.attributes.get(ATTR_TEMPERATURE) == 20.0

    # Test setting temperature
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {
            ATTR_ENTITY_ID: group_entity_id,
            ATTR_TEMPERATURE: 23.5,
        },
        blocking=True,
    )
    await hass.async_block_till_done()
    assert one_climate[0].target_temperature == 23.5

    # Test setting HVAC mode
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {
            ATTR_ENTITY_ID: group_entity_id,
            ATTR_HVAC_MODE: HVACMode.HEAT,
        },
        blocking=True,
    )
    await hass.async_block_till_done()
    assert one_climate[0].hvac_mode == HVACMode.HEAT

    # Test setting temperature with HVAC mode
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {
            ATTR_ENTITY_ID: group_entity_id,
            ATTR_TEMPERATURE: 22.0,
            ATTR_HVAC_MODE: HVACMode.COOL,
        },
        blocking=True,
    )
    await hass.async_block_till_done()
    assert one_climate[0].hvac_mode == HVACMode.COOL
    assert one_climate[0].target_temperature == 22.0

    # Test setting fan mode, swing mode, preset mode
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_FAN_MODE,
        {
            ATTR_ENTITY_ID: group_entity_id,
            ATTR_FAN_MODE: "auto",
        },
        blocking=True,
    )
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_SWING_MODE,
        {
            ATTR_ENTITY_ID: group_entity_id,
            ATTR_SWING_MODE: "on",
        },
        blocking=True,
    )
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {
            ATTR_ENTITY_ID: group_entity_id,
            ATTR_PRESET_MODE: "eco",
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    # Unload
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_climate_area_state_change(
    hass: HomeAssistant,
    one_climate: list[MockClimate],
) -> None:
    """Test climate group turns on when occupied and off when clear."""
    data = dict(CONFIG_ENTRY_DATA)
    data[CONF_ENABLED_FEATURES] = {
        **data.get(CONF_ENABLED_FEATURES, {}),
        CONF_FEATURE_CLIMATE_GROUPS: {
            CONF_CLIMATE_GROUPS_TURN_ON_STATE: AreaState.AREA_STATE_OCCUPIED,
        },
    }
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    registry = async_get_ar(hass)
    registry.async_get_or_create(AREA_NAME)

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    group_entity_id = f"{CLIMATE_DOMAIN}.simply_magic_areas_climate_kitchen"
    state_select_entity_id = f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"

    # Initially off
    assert one_climate[0].hvac_mode == HVACMode.OFF

    # Trigger occupied state change
    hass.states.async_set(
        state_select_entity_id,
        AreaState.AREA_STATE_OCCUPIED,
    )
    await hass.async_block_till_done()

    # Climate should have turned on (HEAT or COOL or HEAT_COOL)
    assert one_climate[0].hvac_mode in (HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL)

    # Set action to heating
    one_climate[0]._attr_hvac_action = HVACAction.HEATING
    one_climate[0].async_write_ha_state()
    await hass.async_block_till_done()

    # Trigger clear state change
    hass.states.async_set(
        state_select_entity_id,
        AreaState.AREA_STATE_CLEAR,
    )
    await hass.async_block_till_done()

    # Climate should have turned off
    assert one_climate[0].hvac_mode == HVACMode.OFF

    # Unload
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
