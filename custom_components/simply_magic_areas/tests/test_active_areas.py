"""Tests for get_active_areas functionality."""

import pytest
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.area_registry import async_get as async_get_ar
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from ..config.area_state import AreaState
from ..const import (
    AREA_TYPE_INTERIOR,
    AREA_TYPE_META,
    ATTR_ACTIVE_AREAS,
    CONF_ENABLED_FEATURES,
    CONF_ID,
    CONF_NAME,
    CONF_TYPE,
    DATA_AREA_OBJECT,
    DOMAIN,
    META_AREA_GLOBAL,
    MODULE_DATA,
)
from .conftest import CONFIG_ENTRY_DATA


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_regular_area_get_active_areas_always_empty(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    _setup_integration: None,
) -> None:
    """Test that regular (non-meta) areas always return empty list for get_active_areas."""
    area = hass.data[MODULE_DATA][config_entry.entry_id][DATA_AREA_OBJECT]

    assert not area.is_meta()
    assert area.get_active_areas() == []

    # Even when area state changes, regular area has no active child areas
    area.state = AreaState.AREA_STATE_OCCUPIED
    assert area.get_active_areas() == []


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_meta_area_get_active_areas_tracks_child_areas(
    hass: HomeAssistant,
) -> None:
    """Test that meta areas track active child areas according to their states."""
    # 1. Setup child areas
    kitchen_data = dict(CONFIG_ENTRY_DATA)
    kitchen_data[CONF_NAME] = "Kitchen"
    kitchen_data[CONF_ID] = "kitchen"
    kitchen_data[CONF_TYPE] = AREA_TYPE_INTERIOR
    kitchen_entry = MockConfigEntry(domain=DOMAIN, data=kitchen_data)
    kitchen_entry.add_to_hass(hass)

    living_data = dict(CONFIG_ENTRY_DATA)
    living_data[CONF_NAME] = "Living Room"
    living_data[CONF_ID] = "living_room"
    living_data[CONF_TYPE] = AREA_TYPE_INTERIOR
    living_entry = MockConfigEntry(domain=DOMAIN, data=living_data)
    living_entry.add_to_hass(hass)

    # 2. Setup Meta Area
    global_data = {
        CONF_NAME: META_AREA_GLOBAL,
        CONF_ID: META_AREA_GLOBAL.lower(),
        CONF_TYPE: AREA_TYPE_META,
        CONF_ENABLED_FEATURES: {},
    }
    global_entry = MockConfigEntry(domain=DOMAIN, data=global_data)
    global_entry.add_to_hass(hass)

    registry = async_get_ar(hass)
    registry.async_get_or_create("Kitchen")
    registry.async_get_or_create("Living Room")

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    kitchen_area = hass.data[MODULE_DATA][kitchen_entry.entry_id][DATA_AREA_OBJECT]
    living_area = hass.data[MODULE_DATA][living_entry.entry_id][DATA_AREA_OBJECT]
    meta_area = hass.data[MODULE_DATA][global_entry.entry_id][DATA_AREA_OBJECT]

    assert meta_area.is_meta()
    assert set(meta_area.get_child_areas()) == {"kitchen", "living_room"}

    # Initially both child areas are clear
    assert meta_area.get_active_areas() == []

    # Kitchen becomes occupied
    kitchen_area.state = AreaState.AREA_STATE_OCCUPIED
    assert meta_area.get_active_areas() == ["kitchen"]

    # Living room becomes extended
    living_area.state = AreaState.AREA_STATE_EXTENDED
    assert set(meta_area.get_active_areas()) == {"kitchen", "living_room"}

    # Living room switches to bright
    living_area.state = AreaState.AREA_STATE_BRIGHT
    assert set(meta_area.get_active_areas()) == {"kitchen", "living_room"}

    # Kitchen clears
    kitchen_area.state = AreaState.AREA_STATE_CLEAR
    assert meta_area.get_active_areas() == ["living_room"]

    # Living room clears
    living_area.state = AreaState.AREA_STATE_CLEAR
    assert meta_area.get_active_areas() == []


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_meta_area_state_sensor_active_areas_attribute(
    hass: HomeAssistant,
) -> None:
    """Test that the meta area state sensor attributes properly reflect get_active_areas."""
    kitchen_data = dict(CONFIG_ENTRY_DATA)
    kitchen_data[CONF_NAME] = "Kitchen"
    kitchen_data[CONF_ID] = "kitchen"
    kitchen_data[CONF_TYPE] = AREA_TYPE_INTERIOR
    kitchen_entry = MockConfigEntry(domain=DOMAIN, data=kitchen_data)
    kitchen_entry.add_to_hass(hass)

    global_data = {
        CONF_NAME: META_AREA_GLOBAL,
        CONF_ID: META_AREA_GLOBAL.lower(),
        CONF_TYPE: AREA_TYPE_META,
        CONF_ENABLED_FEATURES: {},
    }
    global_entry = MockConfigEntry(domain=DOMAIN, data=global_data)
    global_entry.add_to_hass(hass)

    registry = async_get_ar(hass)
    registry.async_get_or_create("Kitchen")

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    kitchen_area = hass.data[MODULE_DATA][kitchen_entry.entry_id][DATA_AREA_OBJECT]
    meta_area = hass.data[MODULE_DATA][global_entry.entry_id][DATA_AREA_OBJECT]

    meta_state_sensor_id = f"{SENSOR_DOMAIN}.simply_magic_areas_state_global"
    state_obj = hass.states.get(meta_state_sensor_id)
    assert state_obj is not None
    assert ATTR_ACTIVE_AREAS in state_obj.attributes
    assert state_obj.attributes[ATTR_ACTIVE_AREAS] == []

    # When child area state changes, meta area reflects active areas
    kitchen_area.state = AreaState.AREA_STATE_OCCUPIED
    assert meta_area.get_active_areas() == ["kitchen"]
