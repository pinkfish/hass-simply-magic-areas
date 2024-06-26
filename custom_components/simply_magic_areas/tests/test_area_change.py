"""Test for area changes and how the system handles it."""

import logging

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.area_registry import async_get as async_get_ar
from homeassistant.helpers.entity_registry import async_get as async_get_er

from ..const import DOMAIN
from .mocks import MockBinarySensor

_LOGGER = logging.getLogger(__name__)


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_area_change(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_light: list[str],
    one_motion: list[MockBinarySensor],
    _setup_integration,
) -> None:
    """Test loading the integration."""
    assert config_entry.state is ConfigEntryState.LOADED

    registry = async_get_ar(hass)
    registry.async_get_or_create("frog")

    # Validate the right enties were created.
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )

    assert area_binary_sensor is not None
    assert area_binary_sensor.state == "clear"
    await hass.async_block_till_done()

    entity_registry = async_get_er(hass)
    entity_registry.async_update_entity(
        one_light[0],
        area_id="frog",
    )
    await hass.async_block_till_done()

    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)
    assert config_entry.state is ConfigEntryState.NOT_LOADED
