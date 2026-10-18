"""Test for the fans."""

import logging

from _pytest.monkeypatch import MonkeyPatch
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.cover import DOMAIN as COVER_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant

from ..const import DOMAIN
from .mocks import MockCover

_LOGGER = logging.getLogger(__name__)


async def test_cover_no_aggregation(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_cover: list[MockCover],
    _setup_integration: None,
    monkeypatch: MonkeyPatch,
) -> None:
    """Test loading the integration."""
    # Validate the right enties were created.
    control_entity = hass.states.get(
        f"{SWITCH_DOMAIN}.simply_magic_areas_system_control_kitchen"
    )
    area_binary_sensor = hass.states.get(
        f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    )
    area_cover = hass.states.get(
        f"{COVER_DOMAIN}.simply_magic_areas_cover_something_kitchen"
    )

    assert control_entity is not None
    assert area_binary_sensor is not None
    assert area_cover is None
    for fan in one_cover:
        assert not fan.is_closed
    assert control_entity.state == STATE_ON
    assert area_binary_sensor.state == "clear"


async def test_cover_with_groups(
    hass: HomeAssistant,
    one_cover: list[MockCover],
) -> None:
    """Test loading cover with aggregation enabled."""
    from homeassistant.helpers.area_registry import async_get as async_get_ar
    from homeassistant.setup import async_setup_component
    from .conftest import AREA_NAME, CONFIG_ENTRY_DATA
    from ..const import CONF_COVER_GROUPS, CONF_ENABLED_FEATURES

    data = dict(CONFIG_ENTRY_DATA)
    data[CONF_ENABLED_FEATURES] = {
        **data.get(CONF_ENABLED_FEATURES, {}),
        CONF_COVER_GROUPS: {},
    }
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    registry = async_get_ar(hass)
    registry.async_get_or_create(AREA_NAME)

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    cover_entity = hass.states.get(
        f"{COVER_DOMAIN}.simply_magic_areas_cover_awning_kitchen"
    )
    assert cover_entity is not None

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
