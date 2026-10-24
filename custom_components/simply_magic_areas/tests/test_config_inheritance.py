"""Tests for hierarchical meta-area configuration inheritance."""

import pytest
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.area_registry import async_get as async_get_ar
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .. import async_update_options
from ..config.area_state import AreaState, LightingProfile
from ..const import (
    AREA_TYPE_EXTERIOR,
    AREA_TYPE_INTERIOR,
    AREA_TYPE_META,
    CONF_ENABLED_FEATURES,
    CONF_FAN_CONTROL,
    CONF_FAN_SUPPRESS_IN_SLEEP,
    CONF_ID,
    CONF_LIGHT_CONTROL,
    CONF_NAME,
    CONF_TYPE,
    DATA_AREA_OBJECT,
    DOMAIN,
    FAN_DOMAIN,
    META_AREA_GLOBAL,
    META_AREA_INTERIOR,
    MODULE_DATA,
)
from .conftest import AREA_NAME, CONFIG_ENTRY_DATA, MockFan


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_global_sleep_entity_inherited_by_interior_area(
    hass: HomeAssistant,
) -> None:
    """Test that a child interior room inherits sleep_entity from Global meta-area."""
    # 1. Setup Global Meta Area with sleep_entity configured
    global_data = {
        CONF_NAME: META_AREA_GLOBAL,
        CONF_ID: META_AREA_GLOBAL.lower(),
        CONF_TYPE: AREA_TYPE_META,
        CONF_ENABLED_FEATURES: {},
        "sleep_entity": "input_boolean.sleep_mode",
    }
    global_entry = MockConfigEntry(domain=DOMAIN, data=global_data)
    global_entry.add_to_hass(hass)

    # 2. Setup Living Room (Interior) with empty sleep_entity
    living_data = dict(CONFIG_ENTRY_DATA)
    living_data[CONF_NAME] = "Living Room"
    living_data[CONF_ID] = "living_room"
    living_data[CONF_TYPE] = AREA_TYPE_INTERIOR
    living_data["sleep_entity"] = ""
    living_entry = MockConfigEntry(domain=DOMAIN, data=living_data)
    living_entry.add_to_hass(hass)

    registry = async_get_ar(hass)
    registry.async_get_or_create("Living Room")

    # Seed sleep helper entity
    hass.states.async_set("input_boolean.sleep_mode", STATE_OFF)

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    living_area = hass.data[MODULE_DATA][living_entry.entry_id][DATA_AREA_OBJECT]

    # Verify inheritance via config.get and state config
    assert living_area.config.get("sleep_entity") == "input_boolean.sleep_mode"
    assert living_area.has_configured_state(AreaState.AREA_STATE_SLEEP)
    assert (
        living_area.state_config(AreaState.AREA_STATE_SLEEP).entity
        == "input_boolean.sleep_mode"
    )

    # Verify lighting profile sensor switches to SLEEP when helper turns ON
    profile_sensor_id = (
        f"{SENSOR_DOMAIN}.simply_magic_areas_lighting_profile_living_room"
    )
    profile_state = hass.states.get(profile_sensor_id)
    assert profile_state is not None
    assert profile_state.state == LightingProfile.STANDARD

    # Activate Sleep mode globally
    hass.states.async_set("input_boolean.sleep_mode", STATE_ON)
    await hass.async_block_till_done()

    profile_state = hass.states.get(profile_sensor_id)
    assert profile_state.state == LightingProfile.SLEEP


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_interior_meta_area_overrides_global_sleep_entity(
    hass: HomeAssistant,
) -> None:
    """Test that Interior meta-area overrides Global meta-area for interior rooms only."""
    # 1. Setup Global Meta Area
    global_data = {
        CONF_NAME: META_AREA_GLOBAL,
        CONF_ID: META_AREA_GLOBAL.lower(),
        CONF_TYPE: AREA_TYPE_META,
        CONF_ENABLED_FEATURES: {},
        "sleep_entity": "binary_sensor.global_sleep",
    }
    global_entry = MockConfigEntry(domain=DOMAIN, data=global_data)
    global_entry.add_to_hass(hass)

    # 2. Setup Interior Meta Area
    interior_data = {
        CONF_NAME: META_AREA_INTERIOR,
        CONF_ID: META_AREA_INTERIOR.lower(),
        CONF_TYPE: AREA_TYPE_META,
        CONF_ENABLED_FEATURES: {},
        "sleep_entity": "binary_sensor.interior_sleep",
    }
    interior_entry = MockConfigEntry(domain=DOMAIN, data=interior_data)
    interior_entry.add_to_hass(hass)

    # 3. Setup Bedroom (interior) and Patio (exterior)
    bedroom_data = dict(CONFIG_ENTRY_DATA)
    bedroom_data[CONF_NAME] = "Bedroom"
    bedroom_data[CONF_ID] = "bedroom"
    bedroom_data[CONF_TYPE] = AREA_TYPE_INTERIOR
    bedroom_data["sleep_entity"] = ""
    bedroom_entry = MockConfigEntry(domain=DOMAIN, data=bedroom_data)
    bedroom_entry.add_to_hass(hass)

    patio_data = dict(CONFIG_ENTRY_DATA)
    patio_data[CONF_NAME] = "Patio"
    patio_data[CONF_ID] = "patio"
    patio_data[CONF_TYPE] = AREA_TYPE_EXTERIOR
    patio_data["sleep_entity"] = ""
    patio_entry = MockConfigEntry(domain=DOMAIN, data=patio_data)
    patio_entry.add_to_hass(hass)

    registry = async_get_ar(hass)
    registry.async_get_or_create("Bedroom")
    registry.async_get_or_create("Patio")

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    bedroom_area = hass.data[MODULE_DATA][bedroom_entry.entry_id][DATA_AREA_OBJECT]
    patio_area = hass.data[MODULE_DATA][patio_entry.entry_id][DATA_AREA_OBJECT]

    # Interior room inherits from Interior Meta-Area (closest parent)
    assert bedroom_area.config.get("sleep_entity") == "binary_sensor.interior_sleep"
    # Exterior space inherits from Global Meta-Area
    assert patio_area.config.get("sleep_entity") == "binary_sensor.global_sleep"


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_local_room_explicit_override_wins(
    hass: HomeAssistant,
) -> None:
    """Test that explicit room-level configuration overrides meta-area settings."""
    # 1. Setup Global Meta Area
    global_data = {
        CONF_NAME: META_AREA_GLOBAL,
        CONF_ID: META_AREA_GLOBAL.lower(),
        CONF_TYPE: AREA_TYPE_META,
        CONF_ENABLED_FEATURES: {},
        "sleep_entity": "binary_sensor.house_sleep",
    }
    global_entry = MockConfigEntry(domain=DOMAIN, data=global_data)
    global_entry.add_to_hass(hass)

    # 2. Setup Nursery with explicit local sleep switch
    nursery_data = dict(CONFIG_ENTRY_DATA)
    nursery_data[CONF_NAME] = "Nursery"
    nursery_data[CONF_ID] = "nursery"
    nursery_data[CONF_TYPE] = AREA_TYPE_INTERIOR
    nursery_data["sleep_entity"] = "input_boolean.nursery_sleep"
    nursery_entry = MockConfigEntry(domain=DOMAIN, data=nursery_data)
    nursery_entry.add_to_hass(hass)

    registry = async_get_ar(hass)
    registry.async_get_or_create("Nursery")

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    nursery_area = hass.data[MODULE_DATA][nursery_entry.entry_id][DATA_AREA_OBJECT]

    # Explicit override takes precedence
    assert nursery_area.config.get("sleep_entity") == "input_boolean.nursery_sleep"
    assert (
        nursery_area.state_config(AreaState.AREA_STATE_SLEEP).entity
        == "input_boolean.nursery_sleep"
    )


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_local_room_explicit_opt_out(
    hass: HomeAssistant,
) -> None:
    """Test that setting 'none' or 'disabled' explicitly opts out of inheritance."""
    global_data = {
        CONF_NAME: META_AREA_GLOBAL,
        CONF_ID: META_AREA_GLOBAL.lower(),
        CONF_TYPE: AREA_TYPE_META,
        CONF_ENABLED_FEATURES: {},
        "sleep_entity": "binary_sensor.global_sleep",
    }
    global_entry = MockConfigEntry(domain=DOMAIN, data=global_data)
    global_entry.add_to_hass(hass)

    hallway_data = dict(CONFIG_ENTRY_DATA)
    hallway_data[CONF_NAME] = "Hallway"
    hallway_data[CONF_ID] = "hallway"
    hallway_data[CONF_TYPE] = AREA_TYPE_INTERIOR
    hallway_data["sleep_entity"] = "none"
    hallway_entry = MockConfigEntry(domain=DOMAIN, data=hallway_data)
    hallway_entry.add_to_hass(hass)

    registry = async_get_ar(hass)
    registry.async_get_or_create("Hallway")

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    hallway_area = hass.data[MODULE_DATA][hallway_entry.entry_id][DATA_AREA_OBJECT]

    # Explicit opt out results in None and exclusion
    assert hallway_area.config.get("sleep_entity") is None
    assert "sleep_entity" not in hallway_area.config


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_inherited_sleep_state_dim_level(
    hass: HomeAssistant,
) -> None:
    """Test that custom sleep dim level set on meta-area is inherited by child rooms."""
    global_data = {
        CONF_NAME: META_AREA_GLOBAL,
        CONF_ID: META_AREA_GLOBAL.lower(),
        CONF_TYPE: AREA_TYPE_META,
        CONF_ENABLED_FEATURES: {},
        "sleep_entity": "binary_sensor.sleep",
        "sleep_state_dim": 15.0,
    }
    global_entry = MockConfigEntry(domain=DOMAIN, data=global_data)
    global_entry.add_to_hass(hass)

    bedroom_data = dict(CONFIG_ENTRY_DATA)
    bedroom_data.pop("sleep_state_dim", None)
    bedroom_data[CONF_NAME] = "Bedroom"
    bedroom_data[CONF_ID] = "bedroom"
    bedroom_data[CONF_TYPE] = AREA_TYPE_INTERIOR
    bedroom_data["sleep_entity"] = ""
    bedroom_entry = MockConfigEntry(domain=DOMAIN, data=bedroom_data)
    bedroom_entry.add_to_hass(hass)

    registry = async_get_ar(hass)
    registry.async_get_or_create("Bedroom")

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    bedroom_area = hass.data[MODULE_DATA][bedroom_entry.entry_id][DATA_AREA_OBJECT]

    assert bedroom_area.state_config(AreaState.AREA_STATE_SLEEP).dim_level == 15


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_meta_area_option_update_reloads_child_areas(
    hass: HomeAssistant,
) -> None:
    """Test that saving options on a Meta-Area dynamically reloads subordinate child areas."""
    # 1. Setup Global Meta-Area with no initial sleep entity
    global_data = {
        CONF_NAME: META_AREA_GLOBAL,
        CONF_ID: META_AREA_GLOBAL.lower(),
        CONF_TYPE: AREA_TYPE_META,
        CONF_ENABLED_FEATURES: {},
        "sleep_entity": "",
    }
    global_entry = MockConfigEntry(domain=DOMAIN, data=global_data)
    global_entry.add_to_hass(hass)

    # 2. Setup Living Room
    living_data = dict(CONFIG_ENTRY_DATA)
    living_data[CONF_NAME] = "Living Room"
    living_data[CONF_ID] = "living_room"
    living_data[CONF_TYPE] = AREA_TYPE_INTERIOR
    living_data["sleep_entity"] = ""
    living_entry = MockConfigEntry(domain=DOMAIN, data=living_data)
    living_entry.add_to_hass(hass)

    registry = async_get_ar(hass)
    registry.async_get_or_create("Living Room")

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    living_area = hass.data[MODULE_DATA][living_entry.entry_id][DATA_AREA_OBJECT]
    assert living_area.config.get("sleep_entity") in (None, "")

    # 3. User configures sleep entity on Global Meta-Area via options
    hass.config_entries.async_update_entry(
        global_entry,
        options={"sleep_entity": "input_boolean.household_sleep"},
    )
    await async_update_options(hass, global_entry)
    await hass.async_block_till_done()

    # Verify living room was reloaded and now has the updated sleep entity
    reloaded_living_area = hass.data[MODULE_DATA][living_entry.entry_id][
        DATA_AREA_OBJECT
    ]
    assert (
        reloaded_living_area.config.get("sleep_entity")
        == "input_boolean.household_sleep"
    )
    assert (
        reloaded_living_area.state_config(AreaState.AREA_STATE_SLEEP).entity
        == "input_boolean.household_sleep"
    )


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_fan_suppression_with_inherited_sleep_entity(
    hass: HomeAssistant,
    one_fan: list[MockFan],
) -> None:
    """Test that kitchen fan suppresses in sleep mode using inherited meta-area sleep_entity."""
    # 1. Setup Global Meta Area with sleep entity
    global_data = {
        CONF_NAME: META_AREA_GLOBAL,
        CONF_ID: META_AREA_GLOBAL.lower(),
        CONF_TYPE: AREA_TYPE_META,
        CONF_ENABLED_FEATURES: {},
        "sleep_entity": "binary_sensor.house_sleep",
    }
    global_entry = MockConfigEntry(domain=DOMAIN, data=global_data)
    global_entry.add_to_hass(hass)

    # 2. Setup Kitchen with fan control and fan_suppress_in_sleep enabled
    kitchen_data = dict(CONFIG_ENTRY_DATA)
    kitchen_data[CONF_FAN_CONTROL] = True
    kitchen_data[CONF_LIGHT_CONTROL] = True
    kitchen_data[CONF_FAN_SUPPRESS_IN_SLEEP] = True
    kitchen_data["sleep_entity"] = ""
    kitchen_entry = MockConfigEntry(domain=DOMAIN, data=kitchen_data)
    kitchen_entry.add_to_hass(hass)

    registry = async_get_ar(hass)
    registry.async_get_or_create(AREA_NAME)

    # Set up sleep sensor
    hass.states.async_set("binary_sensor.house_sleep", STATE_OFF)

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    kitchen_area = hass.data[MODULE_DATA][kitchen_entry.entry_id][DATA_AREA_OBJECT]
    assert kitchen_area.config.get("sleep_entity") == "binary_sensor.house_sleep"

    # Verify fan entity is tracking the inherited sleep_entity
    fan_entity_id = f"{FAN_DOMAIN}.simply_magic_areas_fan_kitchen"
    fan_state = hass.states.get(fan_entity_id)
    assert fan_state is not None
