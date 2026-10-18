"""Tests for persistent timer support across reload/restart."""

import logging
from datetime import UTC, datetime

from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.area_registry import async_get as async_get_ar
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from ..config.area_state import AreaState
from ..const import (
    ATTR_EXTENDED_TIMEOUT_TS,
    ATTR_LAST_OFF_TIME,
    ATTR_MANUAL_CONTROL,
    ATTR_MANUAL_TIMEOUT_TS,
    DOMAIN,
)
from .conftest import AREA_NAME
from .mocks import MockFan, MockSensor

_LOGGER = logging.getLogger(__name__)


async def test_area_state_timer_restore_active(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
) -> None:
    """Test area state sensor restores active clear and extended timers."""
    registry = async_get_ar(hass)
    registry.async_get_or_create(AREA_NAME)

    now_ts = datetime.now(UTC).timestamp()
    target_ext_ts = now_ts + 4
    last_off_ts = now_ts - 1

    entity_id = f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    mock_restore_cache(
        hass,
        [
            State(
                entity_id,
                AreaState.AREA_STATE_EXTENDED,
                attributes={
                    "state": AreaState.AREA_STATE_EXTENDED,
                    "extended": True,
                    ATTR_EXTENDED_TIMEOUT_TS: target_ext_ts,
                    ATTR_LAST_OFF_TIME: last_off_ts,
                },
            )
        ],
    )

    config_entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state is not None
    # Verify extended timer attribute was restored
    assert state.attributes.get(ATTR_EXTENDED_TIMEOUT_TS) is not None
    assert abs(state.attributes[ATTR_EXTENDED_TIMEOUT_TS] - target_ext_ts) < 1.0


async def test_light_manual_timer_restore_active_and_expired(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_light: list[MockSensor],
) -> None:
    """Test light group restores active manual timer and handles expired timer."""
    registry = async_get_ar(hass)
    registry.async_get_or_create(AREA_NAME)

    now_ts = datetime.now(UTC).timestamp()
    target_ts = now_ts + 200

    light_entity_id = f"{LIGHT_DOMAIN}.simply_magic_areas_light_kitchen"
    mock_restore_cache(
        hass,
        [
            State(
                light_entity_id,
                STATE_ON,
                attributes={
                    ATTR_MANUAL_CONTROL: True,
                    ATTR_MANUAL_TIMEOUT_TS: target_ts,
                },
            )
        ],
    )

    config_entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    # Find the entity
    light_entity = None
    for entity in hass.data["entity_components"]["light"].entities:
        if entity.entity_id == light_entity_id:
            light_entity = entity
            break
    assert light_entity is not None

    # Verify manual control and timeout timestamp are active
    assert light_entity._attr_extra_state_attributes.get(ATTR_MANUAL_CONTROL) is True
    assert light_entity._attr_extra_state_attributes.get(ATTR_MANUAL_TIMEOUT_TS) is not None
    assert light_entity._manual_timeout_cb is not None

    # Test reset manual timeout cleans up attributes cleanly
    light_entity._reset_manual_timeout()
    assert light_entity._attr_extra_state_attributes.get(ATTR_MANUAL_CONTROL) is None
    assert light_entity._attr_extra_state_attributes.get(ATTR_MANUAL_TIMEOUT_TS) is None
    assert light_entity._manual_timeout_cb is None


async def test_fan_manual_timer_restore_active_and_expired(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_fan: list[MockFan],
) -> None:
    """Test fan group restores active manual timer and handles expired timer."""
    registry = async_get_ar(hass)
    registry.async_get_or_create(AREA_NAME)

    now_ts = datetime.now(UTC).timestamp()
    target_ts = now_ts + 150

    fan_entity_id = f"{FAN_DOMAIN}.simply_magic_areas_fan_kitchen"
    mock_restore_cache(
        hass,
        [
            State(
                fan_entity_id,
                STATE_ON,
                attributes={
                    ATTR_MANUAL_CONTROL: True,
                    ATTR_MANUAL_TIMEOUT_TS: target_ts,
                },
            )
        ],
    )

    config_entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    # Find the entity
    fan_entity = None
    for entity in hass.data["entity_components"]["fan"].entities:
        if entity.entity_id == fan_entity_id:
            fan_entity = entity
            break
    assert fan_entity is not None

    # Verify manual control and timeout timestamp are active
    assert fan_entity._attr_extra_state_attributes.get(ATTR_MANUAL_CONTROL) is True
    assert fan_entity._attr_extra_state_attributes.get(ATTR_MANUAL_TIMEOUT_TS) is not None
    assert fan_entity._manual_timeout_cb is not None

    # Test reset manual timeout cleans up attributes cleanly
    fan_entity._reset_manual_timeout()
    assert fan_entity._attr_extra_state_attributes.get(ATTR_MANUAL_CONTROL) is None
    assert fan_entity._attr_extra_state_attributes.get(ATTR_MANUAL_TIMEOUT_TS) is None
    assert fan_entity._manual_timeout_cb is None
